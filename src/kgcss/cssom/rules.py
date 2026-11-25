# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Rule classes of the CSS object model."""

from collections.abc import Iterable, Iterator, Sequence
from enum import Enum, unique
from typing import TYPE_CHECKING, ClassVar, final, overload

from kgcss.cssom.state import SheetState, SourceLike
from kgcss.syntax import AtRule, Declaration, QualifiedRule, Token

if TYPE_CHECKING:
    from kgcss.cssom.stylesheet import Stylesheet

CSS_WHITESPACE = ' \t\n\r\f'


@unique
class RuleType(Enum):
    """The type of a rule. Look members up by name with ``RuleType['STYLE']``."""

    STYLE = 'style'
    IMPORT = 'import'
    MEDIA = 'media'
    FONT_FACE = 'font-face'
    PAGE = 'page'
    MARGIN = 'margin'
    CHARSET = 'charset'
    NAMESPACE = 'namespace'
    SUPPORTS = 'supports'
    CONTAINER = 'container'
    LAYER_BLOCK = 'layer-block'
    LAYER_STATEMENT = 'layer-statement'
    SCOPE = 'scope'
    STARTING_STYLE = 'starting-style'
    DOCUMENT = 'document'
    NESTED_DECLARATIONS = 'nested-declarations'
    COMMENT = 'comment'
    UNKNOWN = 'unknown'

    # Members are singletons compared by identity, and Enum's own __hash__ is
    # Python code that shows up in profiles of sets of rule types
    __hash__ = object.__hash__


class Rule:
    """Base class of all rules."""

    __slots__ = ('_source', '_state', 'parent_rule', 'parent_style_sheet')
    type: ClassVar[RuleType]

    def __init__(self) -> None:
        self.parent_rule: Rule | None = None
        self.parent_style_sheet: Stylesheet | None = None
        self._source: SourceLike | None = None
        self._state: SheetState | None = None

    @property
    def line(self) -> int | None:
        """1-based line of the start of this rule in its source, None for rules not parsed from text."""
        return None if self._source is None else self._source.line

    @property
    def column(self) -> int | None:
        """1-based column of the start of this rule in its source, None for rules not parsed from text."""
        return None if self._source is None else self._source.column

    def child_rules(self) -> RuleList | None:
        """The rules nested directly in this rule, or None if it cannot contain rules."""
        return None

    def __repr__(self) -> str:
        where = '' if self._source is None else f' at {self._source.line}:{self._source.column}'
        return f'<{type(self).__name__}{self._repr_detail()}{where}>'

    def _repr_detail(self) -> str:
        return ''


class RuleList(Sequence[Rule]):
    """An ordered list of rules, as found in a stylesheet or a grouping rule."""

    __slots__ = ('_items',)

    def __init__(self, items: Iterable[Rule] = ()) -> None:
        self._items: list[Rule] = list(items)

    @overload
    def __getitem__(self, index: int) -> Rule: ...
    @overload
    def __getitem__(self, index: slice) -> list[Rule]: ...
    def __getitem__(self, index: int | slice) -> Rule | list[Rule]:
        return self._items[index]

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._items)

    def index(self, rule: Rule, start: int = 0, stop: int | None = None) -> int:
        """The index of rule, compared by identity."""
        for i in range(start, len(self._items) if stop is None else stop):
            if self._items[i] is rule:
                return i
        raise ValueError('rule is not in the list')

    def of_type(self, *types: RuleType) -> list[Rule]:
        """The rules in this list (not recursive) of any of the given types."""
        return [r for r in self._items if r.type in types]

    def __repr__(self) -> str:
        return f'<RuleList of {len(self._items)} rules>'


def split_on_commas(text: str) -> list[str]:
    """Split text at top level commas, stripping whitespace. Commas in strings, functions and blocks do not count."""
    from kgcss.syntax import parse_component_value_list
    from kgcss.tokenizer import TokenType

    values = parse_component_value_list(text)
    parts: list[str] = []
    start = 0
    for item in values:
        if isinstance(item, Token) and item.type is TokenType.COMMA:
            parts.append(text[start : item.pos].strip(CSS_WHITESPACE))
            start = item.end
    parts.append(text[start:].strip(CSS_WHITESPACE))
    return [] if parts == [''] else parts


class MediaList(Sequence[str]):
    """The media query list of an @media or @import rule. The text is kept exactly as written."""

    __slots__ = ('media_text',)

    def __init__(self, media_text: str = '') -> None:
        self.media_text = media_text

    @property
    def _queries(self) -> list[str]:
        return split_on_commas(self.media_text)

    @overload
    def __getitem__(self, index: int) -> str: ...
    @overload
    def __getitem__(self, index: slice) -> list[str]: ...
    def __getitem__(self, index: int | slice) -> str | list[str]:
        return self._queries[index]

    def __len__(self) -> int:
        return len(self._queries)

    def __bool__(self) -> bool:
        return bool(self.media_text.strip(CSS_WHITESPACE))

    def __repr__(self) -> str:
        return f'<MediaList {self.media_text!r}>'


class LazyBlockRule(Rule):
    """A rule whose block (declarations and, for some rules, nested rules) is parsed on first access."""

    __slots__ = ('_block_owner', '_declaration_items', '_rules')

    def __init__(self) -> None:
        super().__init__()
        # The syntax node whose {} block holds this rule's contents
        self._block_owner: QualifiedRule | AtRule | None = None
        self._declaration_items: list[Declaration | Token] = []
        self._rules: RuleList | None = None

    def _materialize(self) -> RuleList:
        rules = self._rules
        if rules is not None:
            return rules
        owner = self._block_owner
        block = None if owner is None else owner.block
        if block is None or self._state is None:
            self._rules = rules = RuleList()
            return rules
        with self._state.lock:
            if self._rules is None:
                items, children = _build.build_block(self, block, self._state)
                self._declaration_items = items
                self._rules = RuleList(children)
            return self._rules


@final
class StyleRule(LazyBlockRule):
    """A style rule: a selector list and a declaration block, plus any nested rules (CSS nesting)."""

    __slots__ = ('_selector_text',)
    type = RuleType.STYLE

    def __init__(self, selector_text: str = '') -> None:
        super().__init__()
        self._selector_text: str | None = selector_text

    @property
    def selector_text(self) -> str:
        """The selector list, as text. For parsed rules it is read from the source on first access."""
        text = self._selector_text
        if text is None:
            owner = self._block_owner
            assert isinstance(owner, QualifiedRule)
            self._selector_text = text = owner.prelude.text.strip(CSS_WHITESPACE)
        return text

    @selector_text.setter
    def selector_text(self, value: str) -> None:
        self._selector_text = value

    @property
    def rules(self) -> RuleList:
        """Nested rules, parsed on first access."""
        return self._materialize()

    def child_rules(self) -> RuleList:
        return self.rules

    def _repr_detail(self) -> str:
        return f' {self.selector_text!r}'


class GroupingRule(Rule):
    """Base of rules that contain a list of rules, such as @media and @supports."""

    __slots__ = ('prelude_text', 'rules')

    def __init__(self, prelude_text: str = '') -> None:
        super().__init__()
        self.prelude_text = prelude_text
        self.rules = RuleList()

    def child_rules(self) -> RuleList:
        return self.rules

    def _repr_detail(self) -> str:
        return f' {self.prelude_text!r}'


@final
class MediaRule(GroupingRule):
    """An @media rule."""

    __slots__ = ('media',)
    type = RuleType.MEDIA

    def __init__(self, media_text: str = '') -> None:
        super().__init__(media_text)
        self.media = MediaList(media_text)


@final
class SupportsRule(GroupingRule):
    """An @supports rule. The condition is prelude_text, also available as condition_text."""

    __slots__ = ()
    type = RuleType.SUPPORTS

    @property
    def condition_text(self) -> str:
        return self.prelude_text


@final
class ContainerRule(GroupingRule):
    """An @container rule."""

    __slots__ = ()
    type = RuleType.CONTAINER


@final
class LayerBlockRule(GroupingRule):
    """An @layer rule with a block. The layer name (possibly empty) is prelude_text."""

    __slots__ = ()
    type = RuleType.LAYER_BLOCK

    @property
    def name(self) -> str:
        return self.prelude_text


@final
class ScopeRule(GroupingRule):
    """An @scope rule."""

    __slots__ = ()
    type = RuleType.SCOPE


@final
class StartingStyleRule(GroupingRule):
    """An @starting-style rule."""

    __slots__ = ()
    type = RuleType.STARTING_STYLE


@final
class DocumentRule(GroupingRule):
    """An @document or @-moz-document rule."""

    __slots__ = ()
    type = RuleType.DOCUMENT


@final
class NestedDeclarationsRule(Rule):
    """Declarations that follow nested rules inside a style rule (CSS nesting)."""

    __slots__ = ('_declaration_items',)
    type = RuleType.NESTED_DECLARATIONS

    def __init__(self) -> None:
        super().__init__()
        self._declaration_items: list[Declaration | Token] = []


@final
class LayerStatementRule(Rule):
    """An @layer rule without a block, declaring layer names."""

    __slots__ = ('names',)
    type = RuleType.LAYER_STATEMENT

    def __init__(self, names: Iterable[str] = ()) -> None:
        super().__init__()
        self.names = list(names)

    def _repr_detail(self) -> str:
        return f' {", ".join(self.names)}'


@final
class ImportRule(Rule):
    """An @import rule."""

    __slots__ = ('href', 'layer', 'media', 'supports')
    type = RuleType.IMPORT

    def __init__(
        self, href: str = '', media_text: str = '', layer: str | None = None, supports: str | None = None
    ) -> None:
        super().__init__()
        self.href = href
        self.media = MediaList(media_text)
        # None if no layer was given, '' for an anonymous layer
        self.layer = layer
        self.supports = supports

    def _repr_detail(self) -> str:
        return f' {self.href!r}'


@final
class NamespaceRule(Rule):
    """An @namespace rule. prefix is None for the default namespace."""

    __slots__ = ('namespace_uri', 'prefix')
    type = RuleType.NAMESPACE

    def __init__(self, namespace_uri: str = '', prefix: str | None = None) -> None:
        super().__init__()
        self.namespace_uri = namespace_uri
        self.prefix = prefix

    def _repr_detail(self) -> str:
        return f' {self.prefix!r} {self.namespace_uri!r}'


@final
class CharsetRule(Rule):
    """An @charset rule."""

    __slots__ = ('encoding',)
    type = RuleType.CHARSET

    def __init__(self, encoding: str = 'utf-8') -> None:
        super().__init__()
        self.encoding = encoding

    def _repr_detail(self) -> str:
        return f' {self.encoding!r}'


@final
class FontFaceRule(LazyBlockRule):
    """An @font-face rule."""

    __slots__ = ()
    type = RuleType.FONT_FACE


@final
class PageRule(LazyBlockRule):
    """An @page rule: a page selector, declarations and margin rules."""

    __slots__ = ('selector_text',)
    type = RuleType.PAGE

    def __init__(self, selector_text: str = '') -> None:
        super().__init__()
        self.selector_text = selector_text

    @property
    def rules(self) -> RuleList:
        """The margin rules (@top-left and so on), parsed on first access."""
        return self._materialize()

    def child_rules(self) -> RuleList:
        return self.rules

    def _repr_detail(self) -> str:
        return f' {self.selector_text!r}' if self.selector_text else ''


MARGIN_RULE_NAMES = frozenset((
    'top-left-corner', 'top-left', 'top-center', 'top-right', 'top-right-corner',
    'bottom-left-corner', 'bottom-left', 'bottom-center', 'bottom-right', 'bottom-right-corner',
    'left-top', 'left-middle', 'left-bottom', 'right-top', 'right-middle', 'right-bottom',
))  # fmt: skip


@final
class MarginRule(LazyBlockRule):
    """A page margin rule such as @top-left, inside an @page rule."""

    __slots__ = ('name',)
    type = RuleType.MARGIN

    def __init__(self, name: str = 'top-left') -> None:
        super().__init__()
        self.name = name

    def _repr_detail(self) -> str:
        return f' {self.name}'


@final
class Comment(Rule):
    """A comment between rules. text is the content, without the /* and */."""

    __slots__ = ('text',)
    type = RuleType.COMMENT

    def __init__(self, text: str = '') -> None:
        super().__init__()
        self.text = text

    def _repr_detail(self) -> str:
        return f' {self.text[:30]!r}'


@final
class UnknownAtRule(Rule):
    """An at-rule this library does not model. It is kept exactly as written."""

    __slots__ = ('block_text', 'name', 'prelude_text')
    type = RuleType.UNKNOWN

    def __init__(self, name: str = '', prelude_text: str = '', block_text: str | None = None) -> None:
        super().__init__()
        self.name = name
        self.prelude_text = prelude_text
        # The text inside the {} block, or None for an at-rule ended by a semicolon
        self.block_text = block_text

    @property
    def at_keyword(self) -> str:
        return '@' + self.name

    def _repr_detail(self) -> str:
        return f' @{self.name}'


# At the end, since build imports this module. Used through the module object, so
# it works whichever of the two modules is imported first.
import kgcss.cssom.build as _build  # noqa: E402
