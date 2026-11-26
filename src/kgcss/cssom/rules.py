# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Rule classes of the CSS object model."""

from collections.abc import Callable, Iterable, Iterator, MutableSequence, Sequence
from enum import Enum, unique
from typing import TYPE_CHECKING, ClassVar, Self, final, overload

from kgcss.cssom.declarations import DeclarationBlock, block_from_nodes, parse_declarations
from kgcss.cssom.exceptions import CSSSyntaxError, SelectorSyntaxError
from kgcss.cssom.selector_list import Selector, SelectorList, validate_selector_list
from kgcss.cssom.state import SheetState, SourceLike
from kgcss.selectors import compile_selectors
from kgcss.syntax import AtRule, Declaration, QualifiedRule, Token

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

    from kgcss.cssom.stylesheet import Stylesheet
    from kgcss.media import MediaFeature, MediaQuery

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


class Tracked[T]:
    """An attribute stored in the slot _<name>. Assigning it marks the rule as modified,
    so that preserve serialization does not write stale source text."""

    __slots__ = ('private',)

    def __set_name__(self, owner: type, name: str) -> None:
        self.private = '_' + name

    @overload
    def __get__(self, obj: None, objtype: type | None = None) -> Tracked[T]: ...
    @overload
    def __get__(self, obj: Rule, objtype: type | None = None) -> T: ...
    def __get__(self, obj: Rule | None, objtype: type | None = None) -> T | Tracked[T]:
        if obj is None:
            return self
        value: T = getattr(obj, self.private)
        return value

    def __set__(self, obj: Rule, value: T) -> None:
        setattr(obj, self.private, value)
        obj._changed()


class Rule:
    """Base class of all rules."""

    __slots__ = ('_dirty', '_source', '_state', 'parent_rule', 'parent_style_sheet')
    type: ClassVar[RuleType]

    def __init__(self) -> None:
        self.parent_rule: Rule | None = None
        self.parent_style_sheet: Stylesheet | None = None
        self._source: SourceLike | None = None
        self._state: SheetState | None = None
        # True once this rule or anything in it was modified, so its source text is stale
        self._dirty = False

    def _changed(self) -> None:
        """Mark this rule, its ancestors and its sheet as modified."""
        rule: Rule | None = self
        sheet = None
        while rule is not None:
            rule._dirty = True
            sheet = rule.parent_style_sheet
            rule = rule.parent_rule
        if sheet is not None:
            sheet._dirty = True

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

    def _children(self) -> Sequence[Rule] | None:
        """Like child_rules(), for walking the rules: it need not create a RuleList."""
        return self.child_rules()

    @property
    def css_text(self) -> str:
        """This rule serialized in the pretty format. Assigning reparses: the text must be exactly one
        rule of the same type, otherwise CSSSyntaxError is raised. The rule is updated in place."""
        from kgcss.cssom.serializer import serialize

        return serialize(self)

    @css_text.setter
    def css_text(self, text: str) -> None:
        new = _build.parse_rule_text(text, context_of(self))
        if type(new) is not type(self):
            raise CSSSyntaxError(f'Expected the text of a {type(self).__name__}, got a {type(new).__name__}: {text!r}')
        self._assign(new)

    def _assign(self, new: Self) -> None:
        """Take over the contents of a newly parsed rule of the same type."""
        for cls in type(self).__mro__:
            for slot in getattr(cls, '__slots__', ()):
                if slot not in ('parent_rule', 'parent_style_sheet', '_dirty'):
                    setattr(self, slot, getattr(new, slot))
        self._adopt_contents()
        # Its source is now the assigned text, which it still matches
        self._changed()
        self._dirty = False

    def _adopt_contents(self) -> None:
        """Point anything this rule contains back at it, after _assign()."""

    def copy(self) -> Self:
        """An unattached deep copy of this rule. Unchanged parts keep their exact text."""
        from kgcss.cssom.serializer import SerializeOptions, serialize

        text = serialize(self, SerializeOptions(style='preserve', drop_empty_rules=False))
        new = _build.parse_rule_text(text, context_of(self))
        assert type(new) is type(self), (text, new)
        return new

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self.copy()

    def __repr__(self) -> str:
        where = '' if self._source is None else f' at {self._source.line}:{self._source.column}'
        return f'<{type(self).__name__}{self._repr_detail()}{where}>'

    def _repr_detail(self) -> str:
        return ''


def set_sheet(rule: Rule, sheet: Stylesheet | None) -> None:
    """Set parent_style_sheet on a rule and on every rule already built below it."""
    stack = [rule]
    while stack:
        r = stack.pop()
        r.parent_style_sheet = sheet
        if isinstance(r, GroupingRule):
            stack.extend(r.rules)
        elif isinstance(r, LazyBlockRule) and r._rules:
            stack.extend(r._rules)


def context_of(rule: Rule) -> _build.Context:
    """Where a rule is: what is valid there, and how text for it is parsed."""
    parent = rule.parent_rule
    if parent is None:
        # Margin rules are only valid in @page rules
        return _build.Context.PAGE if isinstance(rule, MarginRule) else _build.Context.TOP
    return context_inside(parent)


def context_inside(container: Rule) -> _build.Context:
    """The context of rules directly inside container."""
    if isinstance(container, PageRule):
        return _build.Context.PAGE
    r: Rule | None = container
    while r is not None:
        if isinstance(r, StyleRule):
            return _build.Context.NESTED
        r = r.parent_rule
    return _build.Context.GROUPING


class RuleList(MutableSequence[Rule]):
    """The rules of a stylesheet or of a rule that contains rules. Changes keep the parent pointers
    up to date. Use insert_rule() on the owner to check where a rule may go and to copy rules that
    belong elsewhere; these list operations raise ValueError for a rule owned by another list."""

    __slots__ = ('_items', '_owner')

    def __init__(self, items: Iterable[Rule] = (), owner: Rule | Stylesheet | None = None) -> None:
        # Items are expected to have their parents set already
        self._items: list[Rule] = list(items)
        self._owner = owner

    def _adopt(self, rule: Rule) -> None:
        if (rule.parent_rule is not None or rule.parent_style_sheet is not None) and not any(
            r is rule for r in self._items
        ):
            raise ValueError('The rule belongs to another rule or sheet: remove it first, or use insert_rule()')
        owner = self._owner
        if isinstance(owner, Rule):
            rule.parent_rule = owner
            set_sheet(rule, owner.parent_style_sheet)
        else:
            rule.parent_rule = None
            set_sheet(rule, owner)

    def _release(self, rule: Rule) -> None:
        rule.parent_rule = None
        set_sheet(rule, None)

    def _changed(self) -> None:
        owner = self._owner
        if isinstance(owner, Rule):
            owner._changed()
        elif owner is not None:
            owner._dirty = True

    @overload
    def __getitem__(self, index: int) -> Rule: ...
    @overload
    def __getitem__(self, index: slice) -> list[Rule]: ...
    def __getitem__(self, index: int | slice) -> Rule | list[Rule]:
        return self._items[index]

    @overload
    def __setitem__(self, index: int, value: Rule) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[Rule]) -> None: ...
    def __setitem__(self, index: int | slice, value: Rule | Iterable[Rule]) -> None:
        if isinstance(index, slice):
            assert not isinstance(value, Rule)
            new = list(value)
            old = self._items[index]
            for r in new:
                if not any(r is o for o in old):
                    self._adopt(r)
            for r in old:
                if not any(r is n for n in new):
                    self._release(r)
            self._items[index] = new
        else:
            assert isinstance(value, Rule)
            old_rule = self._items[index]
            if value is not old_rule:
                self._adopt(value)
                self._release(old_rule)
                self._items[index] = value
        self._changed()

    def __delitem__(self, index: int | slice) -> None:
        for r in self._items[index] if isinstance(index, slice) else [self._items[index]]:
            self._release(r)
        del self._items[index]
        self._changed()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._items)

    def insert(self, index: int, value: Rule) -> None:
        self._adopt(value)
        self._items.insert(index, value)
        self._changed()

    def index(self, value: object, start: int = 0, stop: int | None = None) -> int:
        """The index of a rule, compared by identity."""
        for i in range(start, len(self._items) if stop is None else stop):
            if self._items[i] is value:
                return i
        raise ValueError('rule is not in the list')

    def sort(self, *, key: Callable[[Rule], SupportsRichComparison], reverse: bool = False) -> None:
        self._items.sort(key=key, reverse=reverse)
        self._changed()

    def of_type(self, *types: RuleType) -> list[Rule]:
        """The rules in this list (not recursive) of any of the given types."""
        return [r for r in self._items if r.type in types]

    def __repr__(self) -> str:
        return f'<RuleList of {len(self._items)} rules>'


# Rules that must come first in a sheet, in this order
STATEMENTS_ORDER = {RuleType.CHARSET: 0, RuleType.IMPORT: 1, RuleType.NAMESPACE: 2}


def placement_error(rules: Iterable[Rule], index: int, rule: Rule) -> str | None:
    """Why rule cannot be inserted at index in the top level rules of a sheet, or None.

    @charset comes first, then @import (which @layer statements may precede), then
    @namespace, then everything else. Comments can go anywhere.
    """
    if rule.type is RuleType.COMMENT:
        return None
    if rule.type is RuleType.LAYER_STATEMENT:
        # Allowed anywhere, including before @import, but not before @charset
        if any(r.type is RuleType.CHARSET for r in list(rules)[index:]):
            return 'layer-statement rules cannot come before @charset rules'
        return None
    rank = STATEMENTS_ORDER.get(rule.type, 3)
    for i, other in enumerate(rules):
        if other.type in (RuleType.COMMENT, RuleType.LAYER_STATEMENT):
            continue
        other_rank = STATEMENTS_ORDER.get(other.type, 3)
        if i < index and other_rank > rank:
            return f'@{rule.type.value} rules must come before {other.type.value} rules'
        if i >= index and other_rank < rank:
            return f'{rule.type.value} rules cannot come before @{other.type.value} rules'
        if rule.type is RuleType.CHARSET and other.type is RuleType.CHARSET:
            return 'There can only be one @charset rule'
    return None


# The rule types allowed in each context, by Context name
ALLOWED = {
    'TOP': frozenset(RuleType) - {RuleType.MARGIN, RuleType.NESTED_DECLARATIONS},
    'GROUPING': frozenset(RuleType) - {RuleType.MARGIN, RuleType.NESTED_DECLARATIONS} - set(STATEMENTS_ORDER),
    'NESTED': frozenset(RuleType) - {RuleType.MARGIN} - set(STATEMENTS_ORDER),
    'PAGE': frozenset((RuleType.MARGIN, RuleType.COMMENT)),
}


def insert_into(
    rules: RuleList, rule: Rule | str, index: int | None, context: _build.Context, check_placement: bool = True
) -> Rule:
    """Insert a rule or the text of one. A rule that belongs elsewhere is copied. Returns the inserted rule."""
    if isinstance(rule, str):
        rule = _build.parse_rule_text(rule, context)
    elif rule.parent_rule is not None or rule.parent_style_sheet is not None:
        rule = rule.copy()
    if rule.type not in ALLOWED[context.name]:
        raise ValueError(f'A {rule.type.value} rule is not allowed here')
    if index is None:
        index = len(rules)
    elif not 0 <= index <= len(rules):
        raise IndexError(f'Rule index {index} out of range')
    if check_placement and context is _build.Context.TOP and (error := placement_error(rules, index, rule)):
        raise ValueError(error)
    rules.insert(index, rule)
    return rule


def delete_from(rules: RuleList, rule_or_index: Rule | int) -> None:
    del rules[rules.index(rule_or_index) if isinstance(rule_or_index, Rule) else rule_or_index]


class RuleContainer(Rule):
    """Base of rules that contain rules."""

    __slots__ = ()

    def child_rules(self) -> RuleList:
        raise NotImplementedError

    def insert_rule(self, rule: Rule | str, index: int | None = None) -> Rule:
        """Insert a rule, or the text of one, at index (default: at the end). A rule that belongs to
        another rule or sheet is copied. Returns the inserted rule. Raises ValueError for a rule that
        is not allowed here, and CSSSyntaxError for invalid text."""
        return insert_into(self.child_rules(), rule, index, context_inside(self))

    def delete_rule(self, rule_or_index: Rule | int) -> None:
        """Remove a rule, given as the rule or its index."""
        delete_from(self.child_rules(), rule_or_index)


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


class MediaList(MutableSequence[str]):
    """The media query list of an @media or @import rule. The text is kept exactly as written;
    list operations work on the queries and rewrite the text."""

    __slots__ = ('_media_text', '_owner')

    def __init__(self, media_text: str = '', owner: Rule | None = None) -> None:
        self._media_text = media_text
        self._owner = owner

    @property
    def media_text(self) -> str:
        return self._media_text

    @media_text.setter
    def media_text(self, value: str) -> None:
        self._media_text = value
        if self._owner is not None:
            self._owner._changed()

    @property
    def _queries(self) -> list[str]:
        return split_on_commas(self._media_text)

    def _set_queries(self, queries: list[str]) -> None:
        self.media_text = ', '.join(queries)

    @overload
    def __getitem__(self, index: int) -> str: ...
    @overload
    def __getitem__(self, index: slice) -> list[str]: ...
    def __getitem__(self, index: int | slice) -> str | list[str]:
        return self._queries[index]

    @overload
    def __setitem__(self, index: int, value: str) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[str]) -> None: ...
    def __setitem__(self, index: int | slice, value: str | Iterable[str]) -> None:
        q = self._queries
        if isinstance(index, slice):
            assert not isinstance(value, str)
            q[index] = list(value)
        else:
            assert isinstance(value, str)
            q[index] = value
        self._set_queries(q)

    def __delitem__(self, index: int | slice) -> None:
        q = self._queries
        del q[index]
        self._set_queries(q)

    def insert(self, index: int, value: str) -> None:
        q = self._queries
        q.insert(index, value)
        self._set_queries(q)

    def __len__(self) -> int:
        return len(self._queries)

    def __bool__(self) -> bool:
        return bool(self._media_text.strip(CSS_WHITESPACE))

    def __repr__(self) -> str:
        return f'<MediaList {self._media_text!r}>'

    @property
    def queries(self) -> tuple[MediaQuery, ...]:
        """The parsed queries (kgcss.media), cached by text. Queries that do not parse are kept, with
        valid=False. Reading them does not change media_text."""
        from kgcss.media import parse_media_queries

        return parse_media_queries(self._media_text)

    def matches(self, *, media_type: Callable[[str], bool], feature: Callable[[MediaFeature], bool | None]) -> bool:
        """Whether the list matches, with the callbacks of kgcss.media.media_matches(). An empty list matches."""
        from kgcss.media import media_matches

        return media_matches(self._media_text, media_type=media_type, feature=feature)


def adopt_block(rule: Rule, value: str | DeclarationBlock) -> DeclarationBlock:
    """The block to use as rule.style: parsed from text, the block itself, or a copy if it belongs to another rule."""
    if isinstance(value, str):
        # Problems are positions in the assigned text, not in the sheet, so they
        # stay with the block rather than going to the sheet's errors
        block, errors = parse_declarations(value, rule, None)
        block.errors = errors
        return block
    if value.parent_rule is not None and value.parent_rule is not rule:
        value = value.copy()
    value.parent_rule = rule
    for item in value.items:
        if isinstance(item, Comment):
            item.parent_rule = rule
    return value


class LazyBlockRule(Rule):
    """A rule whose block (declarations and, for some rules, nested rules) is parsed on first access."""

    __slots__ = ('_block_owner', '_declaration_items', '_rules', '_style')

    def __init__(self) -> None:
        super().__init__()
        # The syntax node whose {} block holds this rule's contents
        self._block_owner: QualifiedRule | AtRule | None = None
        # The parsed declarations and comments, until style is built from them
        self._declaration_items: Sequence[Declaration | Token] = ()
        # None until the block is parsed. Then the nested rules, or an empty tuple if there are none:
        # most blocks have none, and not creating a RuleList for them saves time and memory.
        self._rules: RuleList | tuple[()] | None = None
        self._style: DeclarationBlock | None = None

    @property
    def style(self) -> DeclarationBlock:
        """The declarations, parsed on first access. Assign text or a DeclarationBlock to replace them."""
        style = self._style
        if style is not None:
            return style
        if self._state is None:
            self._load()
            self._style = style = DeclarationBlock(parent_rule=self)
            return style
        with self._state.lock:  # one lock for both steps, as this is hot
            self._load_locked()
            if self._style is None:
                self._style = block_from_nodes(self._declaration_items, self)
                self._declaration_items = ()
            return self._style

    @style.setter
    def style(self, value: str | DeclarationBlock) -> None:
        self._load()
        self._style = adopt_block(self, value)
        self._changed()

    def _load(self) -> RuleList | tuple[()]:
        """Parse the block, if not done yet. Returns the nested rules, an empty tuple if there are none."""
        rules = self._rules
        if rules is not None:
            return rules
        if self._state is None:
            return self._load_locked()
        with self._state.lock:
            return self._load_locked()

    def _load_locked(self) -> RuleList | tuple[()]:
        """_load(), with the sheet lock held."""
        rules = self._rules
        if rules is None:
            owner = self._block_owner
            block = None if owner is None else owner.block
            if block is None or self._state is None:
                rules = ()
            else:
                items, children = _build.build_block(self, block, self._state)
                self._declaration_items = items
                rules = RuleList(children, self) if children else ()
            self._rules = rules
        return rules

    def _materialize(self) -> RuleList:
        """The nested rules as a RuleList, parsing the block if not done yet."""
        rules = self._rules
        if type(rules) is RuleList:
            return rules
        self._load()
        if self._state is None:
            if not isinstance(self._rules, RuleList):
                self._rules = RuleList(owner=self)
            return self._rules
        with self._state.lock:
            if not isinstance(self._rules, RuleList):
                self._rules = RuleList(owner=self)
            return self._rules

    def _adopt_contents(self) -> None:
        if self._style is not None:
            self._style.parent_rule = self
        if isinstance(self._rules, RuleList):
            self._rules._owner = self
            for r in self._rules:
                r.parent_rule = self
                set_sheet(r, self.parent_style_sheet)


@final
class StyleRule(LazyBlockRule, RuleContainer):
    """A style rule: a selector list and a declaration block, plus any nested rules (CSS nesting)."""

    __slots__ = ('_selector_text', '_selectors')
    type = RuleType.STYLE

    def __init__(self, selector_text: str = '') -> None:
        super().__init__()
        # Checked as for a nested rule, which also accepts every selector valid at the top level
        self._selector_text: str | None = (
            ', '.join(validate_selector_list(selector_text, True)) if selector_text else ''
        )
        self._selectors: SelectorList | None = None

    @property
    def selector_text(self) -> str:
        """The selector list as text, with normalized whitespace (a, b and a > b) but escapes, case
        and comments kept. Assigning checks the text, raising SelectorSyntaxError (a CSSSyntaxError) if
        it is not a valid selector list."""
        text = self._selector_text
        if text is None:
            from kgcss.cssom.serializer import normalize_prelude

            owner = self._block_owner
            assert isinstance(owner, QualifiedRule)
            self._selector_text = text = normalize_prelude(owner.prelude)
        return text

    @selector_text.setter
    def selector_text(self, value: str) -> None:
        parts = validate_selector_list(value, context_of(self) is _build.Context.NESTED)
        self._selector_text = ', '.join(parts)
        if self._selectors is not None:
            self._selectors._replace_all(parts)
        self._changed()

    @property
    def selectors(self) -> SelectorList:
        """The selectors, as a mutable list. Changes update selector_text."""
        selectors = self._selectors
        if selectors is not None:
            return selectors

        def create() -> SelectorList:
            text = self.selector_text
            if not text.strip(CSS_WHITESPACE):
                return SelectorList(self, [])
            nested = self.parent_rule is not None and context_of(self) is _build.Context.NESTED
            try:
                compiled = compile_selectors(text, nested)
            except SelectorSyntaxError:
                # Kept as text: a selector that is not valid raises when it is parsed
                return SelectorList(self, [Selector._from_normalized(t) for t in split_on_commas(text)])
            # Hot: one parse gives each selector, and its text is exactly its span in the list
            return SelectorList(self, [Selector._from_normalized(text[c.start : c.end], c, nested) for c in compiled])

        if self._state is None:
            self._selectors = selectors = create()
            return selectors
        # Under the sheet lock, so that concurrent readers all get the same list
        with self._state.lock:
            if self._selectors is None:
                self._selectors = create()
            return self._selectors

    @property
    def rules(self) -> RuleList:
        """Nested rules, parsed on first access."""
        return self._materialize()

    def child_rules(self) -> RuleList:
        return self.rules

    def _children(self) -> Sequence[Rule]:
        return self._load()

    def _adopt_contents(self) -> None:
        super()._adopt_contents()
        self._selectors = None

    def _repr_detail(self) -> str:
        return f' {self.selector_text!r}'


class GroupingRule(RuleContainer):
    """Base of rules that contain a list of rules, such as @media and @supports."""

    __slots__ = ('_keyword', '_prelude_text', 'rules')
    default_keyword: ClassVar[str] = ''
    prelude_text = Tracked[str]()

    def __init__(self, prelude_text: str = '') -> None:
        super().__init__()
        self._prelude_text = prelude_text
        self.rules = RuleList(owner=self)
        # The at-keyword as written, lowercased, for rules with more than one (@-moz-document)
        self._keyword: str | None = None

    @property
    def at_keyword_name(self) -> str:
        """The at-keyword without the @, such as 'media' or '-moz-document'."""
        return self._keyword or self.default_keyword

    def child_rules(self) -> RuleList:
        return self.rules

    def _adopt_contents(self) -> None:
        self.rules._owner = self
        for r in self.rules:
            r.parent_rule = self
            set_sheet(r, self.parent_style_sheet)

    def _repr_detail(self) -> str:
        return f' {self.prelude_text!r}'


@final
class MediaRule(GroupingRule):
    """An @media rule. Its prelude is the media query list, media."""

    __slots__ = ('media',)
    type = RuleType.MEDIA
    default_keyword = 'media'

    def __init__(self, media_text: str = '') -> None:
        super().__init__()
        self.media = MediaList(media_text, self)

    @property
    def prelude_text(self) -> str:
        return self.media.media_text

    @prelude_text.setter
    def prelude_text(self, value: str) -> None:
        self.media.media_text = value

    def _adopt_contents(self) -> None:
        super()._adopt_contents()
        self.media._owner = self


@final
class SupportsRule(GroupingRule):
    """An @supports rule. The condition is prelude_text, also available as condition_text."""

    __slots__ = ()
    type = RuleType.SUPPORTS
    default_keyword = 'supports'

    @property
    def condition_text(self) -> str:
        return self.prelude_text

    @condition_text.setter
    def condition_text(self, value: str) -> None:
        self.prelude_text = value


@final
class ContainerRule(GroupingRule):
    """An @container rule."""

    __slots__ = ()
    type = RuleType.CONTAINER
    default_keyword = 'container'


@final
class LayerBlockRule(GroupingRule):
    """An @layer rule with a block. The layer name (possibly empty) is prelude_text."""

    __slots__ = ()
    type = RuleType.LAYER_BLOCK
    default_keyword = 'layer'

    @property
    def name(self) -> str:
        return self.prelude_text


@final
class ScopeRule(GroupingRule):
    """An @scope rule."""

    __slots__ = ()
    type = RuleType.SCOPE
    default_keyword = 'scope'


@final
class StartingStyleRule(GroupingRule):
    """An @starting-style rule."""

    __slots__ = ()
    type = RuleType.STARTING_STYLE
    default_keyword = 'starting-style'


@final
class DocumentRule(GroupingRule):
    """An @document or @-moz-document rule."""

    __slots__ = ()
    type = RuleType.DOCUMENT
    default_keyword = 'document'


@final
class NestedDeclarationsRule(Rule):
    """Declarations that follow nested rules inside a style rule (CSS nesting)."""

    __slots__ = ('_declaration_items', '_style')
    type = RuleType.NESTED_DECLARATIONS

    def __init__(self) -> None:
        super().__init__()
        self._declaration_items: Sequence[Declaration | Token] = ()
        self._style: DeclarationBlock | None = None

    @property
    def style(self) -> DeclarationBlock:
        """The declarations."""
        style = self._style
        if style is not None:
            return style
        if self._state is None:
            self._style = style = DeclarationBlock(parent_rule=self)
            return style
        with self._state.lock:
            if self._style is None:
                self._style = block_from_nodes(self._declaration_items, self)
                self._declaration_items = ()
            return self._style

    @style.setter
    def style(self, value: str | DeclarationBlock) -> None:
        self._style = adopt_block(self, value)
        self._changed()

    @property
    def css_text(self) -> str:
        """The declarations. Assigning replaces them."""
        return self.style.css_text

    @css_text.setter
    def css_text(self, text: str) -> None:
        self.style = text

    def copy(self) -> NestedDeclarationsRule:
        new = NestedDeclarationsRule()
        new.style = self.style.copy()
        new._dirty = False
        return new


@final
class LayerStatementRule(Rule):
    """An @layer rule without a block, declaring layer names."""

    __slots__ = ('_names',)
    type = RuleType.LAYER_STATEMENT
    names = Tracked[tuple[str, ...]]()

    def __init__(self, names: Iterable[str] = ()) -> None:
        super().__init__()
        self._names = tuple(names)

    def _repr_detail(self) -> str:
        return f' {", ".join(self.names)}'


@final
class ImportRule(Rule):
    """An @import rule."""

    __slots__ = ('_href', '_href_is_string', '_layer', '_loaded', '_supports', 'media')
    type = RuleType.IMPORT
    # None if no layer was given, '' for an anonymous layer
    layer = Tracked[str | None]()
    supports = Tracked[str | None]()

    def __init__(
        self, href: str = '', media_text: str = '', layer: str | None = None, supports: str | None = None
    ) -> None:
        super().__init__()
        self._href = href
        self.media = MediaList(media_text, self)
        self._layer = layer
        self._supports = supports
        # @import "a" is written back as a string, @import url(a) as url()
        self._href_is_string = False
        # (sheet it was loaded for, imported sheet), None until loaded
        self._loaded: tuple[Stylesheet, Stylesheet | None] | None = None

    @property
    def href(self) -> str:
        """The URL of the imported sheet, as written. Changing it unloads any loaded sheet."""
        return self._href

    @href.setter
    def href(self, value: str) -> None:
        self._href = value
        self._loaded = None
        self._changed()

    @property
    def style_sheet(self) -> Stylesheet | None:
        """The imported sheet, fetched and parsed on first access with the fetcher of the sheet this
        rule is in. None if there is no fetcher, or the sheet cannot be loaded, in
        which case the problem is reported to the sheet's errors and log handler. It is loaded once,
        even by concurrent threads, and again if href changes or the rule moves to another sheet."""
        sheet = self.parent_style_sheet
        if sheet is None:
            return None
        loaded = self._loaded
        if loaded is not None and loaded[0] is sheet:
            return loaded[1]
        with sheet._state.lock:
            loaded = self._loaded
            if loaded is None or loaded[0] is not sheet:
                loaded = self._loaded = (sheet, _imports.load_import(self))
        return loaded[1]

    def _adopt_contents(self) -> None:
        self.media._owner = self
        self._loaded = None

    def _repr_detail(self) -> str:
        return f' {self.href!r}'


@final
class NamespaceRule(Rule):
    """An @namespace rule. prefix is None for the default namespace."""

    __slots__ = ('_namespace_uri', '_prefix')
    type = RuleType.NAMESPACE
    namespace_uri = Tracked[str]()
    prefix = Tracked[str | None]()

    def __init__(self, namespace_uri: str = '', prefix: str | None = None) -> None:
        super().__init__()
        self._namespace_uri = namespace_uri
        self._prefix = prefix

    def _repr_detail(self) -> str:
        return f' {self.prefix!r} {self.namespace_uri!r}'


@final
class CharsetRule(Rule):
    """An @charset rule."""

    __slots__ = ('_encoding',)
    type = RuleType.CHARSET
    encoding = Tracked[str]()

    def __init__(self, encoding: str = 'utf-8') -> None:
        super().__init__()
        self._encoding = encoding

    def _repr_detail(self) -> str:
        return f' {self.encoding!r}'


@final
class FontFaceRule(LazyBlockRule):
    """An @font-face rule."""

    __slots__ = ()
    type = RuleType.FONT_FACE


@final
class PageRule(LazyBlockRule, RuleContainer):
    """An @page rule: a page selector, declarations and margin rules."""

    __slots__ = ('_selector_text',)
    type = RuleType.PAGE
    selector_text = Tracked[str]()

    def __init__(self, selector_text: str = '') -> None:
        super().__init__()
        self._selector_text = selector_text

    @property
    def rules(self) -> RuleList:
        """The margin rules (@top-left and so on), parsed on first access."""
        return self._materialize()

    def child_rules(self) -> RuleList:
        return self.rules

    def _children(self) -> Sequence[Rule]:
        return self._load()

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

    __slots__ = ('_name',)
    type = RuleType.MARGIN
    name = Tracked[str]()

    def __init__(self, name: str = 'top-left') -> None:
        super().__init__()
        self._name = name

    def _repr_detail(self) -> str:
        return f' {self.name}'


@final
class Comment(Rule):
    """A comment between rules. text is the content, without the ``/*`` and ``*/``."""

    __slots__ = ('_text',)
    type = RuleType.COMMENT

    def __init__(self, text: str = '') -> None:
        super().__init__()
        if '*/' in text:
            raise ValueError('A comment cannot contain */')
        self._text = text

    @property
    def text(self) -> str:
        """The text between ``/*`` and ``*/``."""
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        if '*/' in value:
            raise ValueError('A comment cannot contain */')
        self._text = value
        self._changed()

    def copy(self) -> Comment:
        return Comment(self._text)

    def _repr_detail(self) -> str:
        return f' {self.text[:30]!r}'


@final
class UnknownAtRule(Rule):
    """An at-rule this library does not model. It is kept exactly as written."""

    __slots__ = ('_block_text', '_name', '_prelude_text')
    type = RuleType.UNKNOWN
    name = Tracked[str]()
    prelude_text = Tracked[str]()
    # The text inside the {} block, or None for an at-rule ended by a semicolon
    block_text = Tracked[str | None]()

    def __init__(self, name: str = '', prelude_text: str = '', block_text: str | None = None) -> None:
        super().__init__()
        self._name = name
        self._prelude_text = prelude_text
        self._block_text = block_text

    @property
    def at_keyword(self) -> str:
        return '@' + self.name

    def _repr_detail(self) -> str:
        return f' @{self.name}'


# At the end, since build imports this module. Used through the module object, so
# it works whichever of the two modules is imported first.
import kgcss.cssom.build as _build  # noqa: E402
import kgcss.cssom.imports as _imports  # noqa: E402
