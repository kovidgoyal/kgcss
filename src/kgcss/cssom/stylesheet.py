# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The Stylesheet class, parse_stylesheet() and parse_rule()."""

from collections.abc import Iterator, MutableMapping
from typing import TYPE_CHECKING

from kgcss.cssom.rules import (
    CharsetRule,
    NamespaceRule,
    Rule,
    RuleList,
    RuleType,
    delete_from,
    insert_into,
    set_sheet,
)
from kgcss.cssom.state import ErrorHandler, SheetState, Source
from kgcss.decode import Buffer
from kgcss.syntax import parse_stylesheet as parse_syntax_tree
from kgcss.tokenizer import ParseError

if TYPE_CHECKING:
    from kgcss.cssom.serializer import SerializeOptions


class NamespaceMap(MutableMapping[str, str]):
    """The namespaces of a sheet, prefix to URI, backed by its @namespace rules. The default namespace
    has the prefix ''. Setting a prefix adds or changes an @namespace rule, deleting removes it."""

    __slots__ = ('_sheet',)

    def __init__(self, sheet: Stylesheet) -> None:
        self._sheet = sheet

    def _rules(self) -> list[NamespaceRule]:
        return [r for r in self._sheet.rules if isinstance(r, NamespaceRule)]

    def __getitem__(self, prefix: str) -> str:
        for r in reversed(self._rules()):  # the last declaration wins
            if (r.prefix or '') == prefix:
                return r.namespace_uri
        raise KeyError(prefix)

    def __setitem__(self, prefix: str, uri: str) -> None:
        matching = [r for r in self._rules() if (r.prefix or '') == prefix]
        if matching:
            matching[-1].namespace_uri = uri
            for r in matching[:-1]:
                self._sheet.delete_rule(r)
        else:
            self._sheet.add(NamespaceRule(uri, prefix or None))

    def __delitem__(self, prefix: str) -> None:
        matching = [r for r in self._rules() if (r.prefix or '') == prefix]
        if not matching:
            raise KeyError(prefix)
        for r in matching:
            self._sheet.delete_rule(r)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.fromkeys(r.prefix or '' for r in self._rules()))

    def __len__(self) -> int:
        return len({r.prefix or '' for r in self._rules()})

    def __repr__(self) -> str:
        return f'<NamespaceMap {dict(self)!r}>'


class Stylesheet:
    """A parsed or constructed stylesheet: an ordered list of rules."""

    __slots__ = ('_dirty', '_source', '_state', 'encoding', 'href', 'rules')

    def __init__(self, *, href: str | None = None) -> None:
        self.rules = RuleList(owner=self)
        self.href = href
        self.encoding: str | None = None
        self._state = SheetState(None, href)
        self._source: Source | None = None
        # True once anything in the sheet was modified, so its source text is stale
        self._dirty = False

    @property
    def errors(self) -> list[ParseError]:
        """Problems found while parsing, including in blocks parsed later on demand."""
        return self._state.errors

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def namespaces(self) -> NamespaceMap:
        """Prefix to namespace URI, backed by the @namespace rules (requirements P9)."""
        return NamespaceMap(self)

    def insert_rule(self, rule: Rule | str, index: int | None = None) -> Rule:
        """Insert a rule, or the text of one, at index (default: at the end). A rule that belongs to
        another sheet or rule is copied (requirements 3.1). Returns the inserted rule.

        Raises ValueError if the rule cannot go there: @charset comes first, then @import, then
        @namespace, then everything else. Raises CSSSyntaxError for invalid text.
        """
        from kgcss.cssom.build import Context

        return insert_into(self.rules, rule, index, Context.TOP)

    def delete_rule(self, rule_or_index: Rule | int) -> None:
        """Remove a rule, given as the rule or its index."""
        delete_from(self.rules, rule_or_index)

    def add(self, rule: Rule | str) -> Rule:
        """Add a rule in a valid position: @charset first (replacing any existing one), @import after the
        other @import rules, @namespace after the other @import and @namespace rules, anything else at
        the end. A rule that belongs elsewhere is copied. Returns the added rule."""
        from kgcss.cssom.build import Context, parse_rule_text

        if isinstance(rule, str):
            rule = parse_rule_text(rule, Context.TOP)
        rules = self.rules
        index = len(rules)
        match rule.type:
            case RuleType.CHARSET:
                for existing in rules.of_type(RuleType.CHARSET):
                    self.delete_rule(existing)
                index = 0
            case RuleType.IMPORT | RuleType.NAMESPACE:
                before = {RuleType.CHARSET, RuleType.IMPORT, RuleType.LAYER_STATEMENT, RuleType.COMMENT}
                if rule.type is RuleType.NAMESPACE:
                    before.add(RuleType.NAMESPACE)
                index = 0
                for i, r in enumerate(rules):
                    if r.type not in before:
                        break
                    if r.type is not RuleType.COMMENT:
                        index = i + 1
        return insert_into(rules, rule, index, Context.TOP)

    def iter_rules(self, *types: RuleType, recursive: bool = True) -> Iterator[Rule]:
        """All rules in document order, optionally only of the given types.

        With recursive=True this includes rules nested in grouping rules (such
        as @media and @supports), in style rules (CSS nesting) and in @page
        rules, at any depth.
        """
        stack: list[Iterator[Rule]] = [iter(self.rules)]
        while stack:
            rule = next(stack[-1], None)
            if rule is None:
                stack.pop()
                continue
            if not types or rule.type in types:
                yield rule
            if recursive and (children := rule.child_rules()) is not None:
                stack.append(iter(children))

    def browser_index_path(self, rule: Rule) -> tuple[int, ...]:
        """The path of indices to rule in the browser CSSOM (cssRules at each level).

        Browsers have no comment rules and no @charset rule, so those are
        skipped when counting, and asking for the path of one is an error.
        """
        if rule.type in (RuleType.COMMENT, RuleType.CHARSET):
            raise ValueError(f'{rule.type.value} rules do not exist in the browser CSSOM')
        path: list[int] = []
        current = rule
        while True:
            parent = current.parent_rule
            siblings = self.rules if parent is None else parent.child_rules()
            if siblings is None:
                raise ValueError('rule is not part of this stylesheet')
            try:
                siblings.index(current)
            except ValueError:
                raise ValueError('rule is not part of this stylesheet') from None
            index = 0
            for sibling in siblings:
                if sibling is current:
                    break
                if sibling.type not in (RuleType.COMMENT, RuleType.CHARSET):
                    index += 1
            path.append(index)
            if parent is None:
                return tuple(reversed(path))
            current = parent

    @property
    def css_text(self) -> str:
        """The sheet serialized in the pretty format. Assigning reparses the sheet in place."""
        from kgcss.cssom.serializer import serialize

        return serialize(self)

    @css_text.setter
    def css_text(self, text: str) -> None:
        new = parse_stylesheet(text, href=self.href, log=self._state.log)
        self._take_over(new)

    def _take_over(self, new: Stylesheet) -> None:
        for rule in self.rules:
            rule.parent_rule = None
            set_sheet(rule, None)
        rules = list(new.rules)
        for rule in rules:
            set_sheet(rule, self)
        self.rules = RuleList(rules, self)
        self._state, self._source = new._state, new._source
        self._dirty = False

    def copy(self) -> Stylesheet:
        """A deep copy. Unchanged parts keep their exact text."""
        from kgcss.cssom.serializer import SerializeOptions, serialize

        if self._source is not None and not self._dirty:
            text = self._source.source_text
        else:
            text = serialize(self, SerializeOptions(style='preserve', drop_empty_rules=False))
        new = parse_stylesheet(text, href=self.href, log=self._state.log)
        new.encoding = self.encoding
        return new

    def __deepcopy__(self, memo: dict[int, object]) -> Stylesheet:
        return self.copy()

    def encode(self, options: SerializeOptions | None = None) -> bytes:
        """The serialized sheet as bytes, in the encoding of its @charset rule (UTF-8 if none).
        Characters that encoding cannot represent are written as CSS escapes (requirements S4)."""
        from kgcss.cssom.serializer import DEFAULT_OPTIONS, encode_text, serialize

        charset = next((r.encoding for r in self.rules if isinstance(r, CharsetRule)), None)
        return encode_text(serialize(self, options or DEFAULT_OPTIONS), charset)

    def __repr__(self) -> str:
        href = f' {self.href!r}' if self.href else ''
        return f'<Stylesheet{href} with {len(self.rules)} rules>'


def parse_stylesheet(
    source: str | Buffer,
    *,
    href: str | None = None,
    protocol_encoding: str | None = None,
    environment_encoding: str | None = None,
    log: ErrorHandler | None = None,
) -> Stylesheet:
    """Parse a stylesheet into the CSS object model.

    source is text, or bytes decoded as described in decode_stylesheet().
    Parsing never fails on bad CSS. Problems are collected in
    Stylesheet.errors and sent to log, which is a callable taking a
    ParseError or a logging.Logger. Declaration blocks are parsed when first
    accessed, and problems found then are reported the same way.
    """
    from kgcss.cssom.build import build_top_level

    nodes = parse_syntax_tree(source, protocol_encoding=protocol_encoding, environment_encoding=environment_encoding)
    sheet = Stylesheet(href=href)
    sheet.encoding = nodes.encoding
    sheet._state.log = log
    sheet._source = Source(nodes.stream, 0, len(nodes.stream.source))
    sheet.rules = RuleList(build_top_level(nodes, sheet), sheet)
    return sheet


def parse_rule(text: str, *, log: ErrorHandler | None = None) -> Rule:
    """Parse the text of exactly one rule, as at the top level of a sheet (requirements P4).
    The text of a single comment gives a Comment. Raises CSSSyntaxError for anything else."""
    from kgcss.cssom.build import Context, parse_rule_text

    return parse_rule_text(text, Context.TOP, log)
