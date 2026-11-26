# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Building CSSOM objects from the generic syntax tree (plan 01)."""

from collections.abc import Iterable, Sequence
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

from kgcss.cssom.rules import (
    CSS_WHITESPACE,
    MARGIN_RULE_NAMES,
    CharsetRule,
    Comment,
    ContainerRule,
    DocumentRule,
    FontFaceRule,
    GroupingRule,
    ImportRule,
    LayerBlockRule,
    LayerStatementRule,
    LazyBlockRule,
    MarginRule,
    MediaRule,
    NamespaceRule,
    NestedDeclarationsRule,
    PageRule,
    Rule,
    RuleList,
    RuleType,
    ScopeRule,
    StartingStyleRule,
    StyleRule,
    SupportsRule,
    UnknownAtRule,
    split_on_commas,
)
from kgcss.cssom.state import SYNTAX_ERROR_KINDS, ErrorHandler, SheetState, Source
from kgcss.syntax import (
    AtRule,
    Block,
    ComponentValueList,
    Declaration,
    Function,
    Node,
    NodeList,
    QualifiedRule,
    Token,
    parse_block_contents,
)
from kgcss.tokenizer import TokenStream, TokenType

if TYPE_CHECKING:
    from kgcss.cssom.stylesheet import Stylesheet


class Context(Enum):
    """Where rules are being built, which decides what is valid."""

    TOP = auto()  # top level of a stylesheet
    GROUPING = auto()  # inside a grouping rule that is not inside a style rule
    NESTED = auto()  # inside a style rule, at any depth
    PAGE = auto()  # inside an @page rule, where margin rules are


GROUPING_RULES: dict[str, type[GroupingRule]] = {
    'media': MediaRule,
    'supports': SupportsRule,
    'container': ContainerRule,
    'layer': LayerBlockRule,
    'scope': ScopeRule,
    'starting-style': StartingStyleRule,
    'document': DocumentRule,
    '-moz-document': DocumentRule,
}
# Rules that may come before @import at the top level of a sheet
BEFORE_IMPORT = frozenset((RuleType.CHARSET, RuleType.IMPORT, RuleType.LAYER_STATEMENT, RuleType.COMMENT))
# Rules that may come before @namespace at the top level of a sheet
BEFORE_NAMESPACE = BEFORE_IMPORT | {RuleType.NAMESPACE}


def significant(values: ComponentValueList) -> list[Token | Block | Function]:
    """The component values that are not whitespace or comments."""
    return [v for v in values if not (isinstance(v, Token) and v.type in (TokenType.WHITESPACE, TokenType.COMMENT))]


def ascii_lower(name: str) -> str:
    return name.lower() if name.isascii() else name


def url_value(item: Token | Block | Function) -> str | None:
    """The URL of a url() token, a string, or a url("...") function."""
    if isinstance(item, Token) and item.type in (TokenType.URL, TokenType.STRING):
        assert isinstance(item.value, str)
        return item.value
    if isinstance(item, Function) and ascii_lower(item.name) == 'url':
        args = significant(item.arguments)
        if len(args) == 1 and isinstance(args[0], Token) and args[0].type is TokenType.STRING:
            assert isinstance(args[0].value, str)
            return args[0].value
    return None


class Builder:
    """Builds the rules of one stylesheet, reporting problems to its state."""

    def __init__(self, state: SheetState, stream: TokenStream, sheet: Stylesheet | None) -> None:
        self.state = state
        self.stream = stream
        self.source = stream.source
        self.sheet = sheet

    def invalid(self, node: Node, message: str, kind: str = 'invalid-rule') -> None:
        self.state.report(kind, message, self.stream, node.pos)

    def init(self, rule: Rule, node: Node, parent: Rule | None) -> None:
        rule.parent_rule = parent
        rule.parent_style_sheet = self.sheet
        rule._source = node
        rule._state = self.state

    def report_block_errors(self, nodes: NodeList) -> None:
        # Tokenizer errors were already reported for the whole sheet
        for error in nodes.errors:
            if error.kind in SYNTAX_ERROR_KINDS:
                self.state.add(error)

    def build_rules(self, nodes: Iterable[Node], parent: Rule | None, context: Context) -> list[Rule]:
        rules: list[Rule] = []
        pending: NestedDeclarationsRule | None = None
        for node in nodes:
            rule: Rule | None
            if isinstance(node, Declaration):
                if context is not Context.NESTED:
                    self.invalid(node, f'Declaration of {node.name!r} is not allowed here', 'invalid-declaration')
                    continue
                if pending is None:
                    pending = NestedDeclarationsRule()
                    self.init(pending, node, parent)
                    pending._source = Source(self.stream, node.pos, node.end)
                    pending._declaration_items = []
                    rules.append(pending)
                assert isinstance(pending._declaration_items, list)
                pending._declaration_items.append(node)
                assert isinstance(pending._source, Source)
                pending._source.end = node.end
                continue
            pending = None
            if isinstance(node, Token):
                rule = Comment(str(node.value))
                self.init(rule, node, parent)
            elif isinstance(node, QualifiedRule):
                rule = self.style_rule(node, parent)
            else:
                rule = self.at_rule(node, parent, context)
            if rule is not None:
                rules.append(rule)
        return rules

    def style_rule(self, node: QualifiedRule, parent: Rule | None) -> StyleRule:
        rule = StyleRule()
        # Both the selector text and the block are read from the node on first use
        rule._selector_text = None
        self.init(rule, node, parent)
        rule._block_owner = node
        return rule

    def at_rule(self, node: AtRule, parent: Rule | None, context: Context) -> Rule | None:
        name = ascii_lower(node.name)
        block = node.block
        if name in ('charset', 'import', 'namespace'):
            if context is not Context.TOP:
                self.invalid(node, f'@{name} is only allowed at the top level of a stylesheet')
                return None
            return {'charset': self.charset_rule, 'import': self.import_rule, 'namespace': self.namespace_rule}[name](
                node
            )
        if name == 'layer' and block is None:
            names = split_on_commas(node.prelude.text)
            if not names or not all(names):
                self.invalid(node, 'Invalid @layer statement')
                return None
            rule: Rule = LayerStatementRule(names)
        elif (cls := GROUPING_RULES.get(name)) is not None:
            if block is None:
                self.invalid(node, f'@{name} must have a block')
                return None
            grouping = cls(node.prelude.text.strip(CSS_WHITESPACE))
            grouping._keyword = name
            self.init(grouping, node, parent)
            nodes = parse_block_contents(block)
            self.report_block_errors(nodes)
            child_context = Context.NESTED if context is Context.NESTED else Context.GROUPING
            grouping.rules = RuleList(self.build_rules(nodes, grouping, child_context), grouping)
            return grouping
        elif name in ('font-face', 'page'):
            if block is None:
                self.invalid(node, f'@{name} must have a block')
                return None
            lazy: LazyBlockRule
            lazy = FontFaceRule() if name == 'font-face' else PageRule(node.prelude.text.strip(CSS_WHITESPACE))
            lazy._block_owner = node
            rule = lazy
        else:
            prelude = node.prelude.text
            rule = UnknownAtRule(
                node.name, prelude.strip(CSS_WHITESPACE), None if block is None else block.content.text
            )
        self.init(rule, node, parent)
        return rule

    def charset_rule(self, node: AtRule) -> Rule | None:
        values = significant(node.prelude)
        if node.block is not None or len(values) != 1 or not isinstance(values[0], Token):
            self.invalid(node, 'Invalid @charset rule')
            return None
        tok = values[0]
        if tok.type is not TokenType.STRING:
            self.invalid(node, 'Invalid @charset rule')
            return None
        rule = CharsetRule(str(tok.value))
        self.init(rule, node, None)
        return rule

    def import_rule(self, node: AtRule) -> Rule | None:
        values = significant(node.prelude)
        href = url_value(values[0]) if values and node.block is None else None
        if href is None:
            self.invalid(node, 'Invalid @import rule, it must start with a URL or string')
            return None
        layer: str | None = None
        supports: str | None = None
        rest = values[1:]
        if (
            rest
            and isinstance(rest[0], Token)
            and rest[0].type is TokenType.IDENT
            and ascii_lower(str(rest[0].value)) == 'layer'
        ):
            layer, rest = '', rest[1:]
        elif rest and isinstance(rest[0], Function) and ascii_lower(rest[0].name) == 'layer':
            layer, rest = rest[0].arguments.text.strip(CSS_WHITESPACE), rest[1:]
        if rest and isinstance(rest[0], Function) and ascii_lower(rest[0].name) == 'supports':
            supports, rest = rest[0].arguments.text.strip(CSS_WHITESPACE), rest[1:]
        media_text = self.stream.source[rest[0].pos : node.prelude.end].strip(CSS_WHITESPACE) if rest else ''
        rule = ImportRule(href, media_text, layer, supports)
        rule._href_is_string = isinstance(values[0], Token) and values[0].type is TokenType.STRING
        self.init(rule, node, None)
        return rule

    def namespace_rule(self, node: AtRule) -> Rule | None:
        values = significant(node.prelude)
        prefix: str | None = None
        if len(values) == 2 and isinstance(values[0], Token) and values[0].type is TokenType.IDENT:
            prefix = str(values[0].value)
            values = values[1:]
        uri = url_value(values[0]) if len(values) == 1 and node.block is None else None
        if uri is None:
            self.invalid(node, 'Invalid @namespace rule')
            return None
        rule = NamespaceRule(uri, prefix)
        self.init(rule, node, None)
        return rule

    def block_contents(self, owner: LazyBlockRule, nodes: NodeList) -> tuple[list[Declaration | Token], list[Rule]]:
        """Split the parsed contents of a lazily parsed block into its declarations and its rules."""
        self.report_block_errors(nodes)
        items: list[Declaration | Token] = []
        if isinstance(owner, StyleRule):
            # Declarations and comments before the first nested rule belong to the style
            all_nodes = list(nodes)
            first_rule = len(all_nodes)
            for i, n in enumerate(all_nodes):
                if type(n) is not Declaration and type(n) is not Token:
                    first_rule = i
                    break
            items = cast('list[Declaration | Token]', all_nodes[:first_rule])
            rest = all_nodes[first_rule:]
            return items, self.build_rules(rest, owner, Context.NESTED)
        rules: list[Rule] = []
        for node in nodes:
            if isinstance(node, Declaration | Token):
                items.append(node)
            elif (
                isinstance(owner, PageRule) and isinstance(node, AtRule) and ascii_lower(node.name) in MARGIN_RULE_NAMES
            ):
                if node.block is None:
                    self.invalid(node, f'@{node.name} must have a block')
                    continue
                margin = MarginRule(ascii_lower(node.name))
                self.init(margin, node, owner)
                margin._block_owner = node
                rules.append(margin)
            else:
                self.invalid(node, f'Rule not allowed in {owner.type.value} rule')
        return items, rules


def build_block(
    owner: LazyBlockRule, block: Block, state: SheetState
) -> tuple[Sequence[Declaration | Token], Sequence[Rule]]:
    """Parse the block of a rule whose contents are materialized lazily."""
    nodes = parse_block_contents(block)
    if not nodes.errors and not nodes.has_rules:
        # Hot: most blocks only have declarations and comments. The node list is used as it is,
        # which saves allocating objects the garbage collector has to track.
        return cast('Sequence[Declaration | Token]', nodes), ()
    return Builder(state, block.stream, owner.parent_style_sheet).block_contents(owner, nodes)


def build_top_level(nodes: NodeList, sheet: Stylesheet) -> list[Rule]:
    """The top level rules of a sheet, applying the placement rules for @charset, @import and @namespace."""
    state = sheet._state
    builder = Builder(state, nodes.stream, sheet)
    # Errors from parsing and from building are reported together, in source order
    state.begin_batch()
    for error in nodes.errors:
        state.add(error)
    rules: list[Rule] = []
    previous: set[RuleType] = set()  # types of the rules kept so far
    for rule in builder.build_rules(nodes, None, Context.TOP):
        allowed: frozenset[RuleType] | None = None
        if rule.type is RuleType.CHARSET:
            allowed = frozenset({RuleType.COMMENT})
        elif rule.type is RuleType.IMPORT:
            allowed = BEFORE_IMPORT
        elif rule.type is RuleType.NAMESPACE:
            allowed = BEFORE_NAMESPACE
        if allowed is not None and not previous <= allowed:
            assert rule._source is not None
            state.report(
                'misplaced-rule',
                f'@{rule.type.value} rule ignored, it is not allowed after other rules',
                nodes.stream,
                rule._source.pos,
            )
            continue
        rules.append(rule)
        previous.add(rule.type)
    state.end_batch()
    return rules


def parse_rule_text(text: str, context: Context, log: ErrorHandler | None = None) -> Rule:
    """Parse the text of exactly one rule, valid in context. Raises CSSSyntaxError otherwise.

    The text of a single comment gives a Comment rule.
    """
    from kgcss.cssom.exceptions import CSSSyntaxError
    from kgcss.syntax import parse_one_rule
    from kgcss.tokenizer import tokenize

    significant = [t for t in tokenize(text) if t.type is not TokenType.WHITESPACE]
    if len(significant) == 1 and significant[0].type is TokenType.COMMENT:
        if not significant[0].closed:
            raise CSSSyntaxError(f'Unclosed comment: {text!r}')
        comment = Comment(str(significant[0].value))
        comment._source = significant[0]
        return comment
    node = parse_one_rule(text)
    if not isinstance(node, AtRule | QualifiedRule):
        raise CSSSyntaxError(f'{node.message}: {text!r}')
    state = SheetState(log, None)
    builder = Builder(state, node.stream, None)
    rule: Rule | None
    if context is Context.PAGE:
        rule = None
        if isinstance(node, AtRule) and ascii_lower(node.name) in MARGIN_RULE_NAMES and node.block is not None:
            margin = MarginRule(ascii_lower(node.name))
            builder.init(margin, node, None)
            margin._block_owner = node
            rule = margin
        if rule is None:
            raise CSSSyntaxError(f'Only margin rules are allowed in @page rules: {text!r}')
        return rule
    rule = builder.style_rule(node, None) if isinstance(node, QualifiedRule) else builder.at_rule(node, None, context)
    if rule is None:
        message = state.errors[-1].message if state.errors else 'Invalid rule'
        raise CSSSyntaxError(f'{message}: {text!r}')
    return rule
