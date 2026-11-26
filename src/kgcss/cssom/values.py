# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Parsed property values.

A PropertyValue is an immutable sequence of components. Components are
immutable too, by convention: they are plain slots classes rather than frozen
dataclasses, because frozen dataclasses are twice as slow to create, and values
are created in large numbers. Whitespace and
comments are not components, and neither are separators (commas and /),
which are only in PropertyValue.items. Every component has a type, a value
and its exact css_text, which appears verbatim in the text of the value
containing it.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import ClassVar, overload

from kgcss.syntax import Block, ComponentValueList, Token, parse_component_value_list
from kgcss.syntax import Function as SyntaxFunction
from kgcss.tokenizer import TokenType


@dataclass(frozen=True, slots=True)
class Component:
    """Base of all value components. css_text is the exact source text."""

    css_text: str
    type: ClassVar[str] = ''


@dataclass(frozen=True, slots=True)
class Ident(Component):
    value: str
    type: ClassVar[str] = 'ident'


@dataclass(frozen=True, slots=True)
class String(Component):
    """A quoted string. value is unquoted and unescaped."""

    value: str
    type: ClassVar[str] = 'string'


@dataclass(frozen=True, slots=True)
class Number(Component):
    """A number. value is an int for integers (700 stays 700) and a float otherwise."""

    value: int | float
    type: ClassVar[str] = 'number'


@dataclass(frozen=True, slots=True)
class Percentage(Component):
    value: int | float
    type: ClassVar[str] = 'percentage'


@dataclass(frozen=True, slots=True)
class Dimension(Component):
    """A number with a unit. 1.0E1px has the value 10.0 and the unit px."""

    value: int | float
    unit: str
    type: ClassVar[str] = 'dimension'


@dataclass(frozen=True, slots=True)
class Url(Component):
    """url(x), url("x") or url('x'). value is the URL without quotes or escapes."""

    value: str
    type: ClassVar[str] = 'url'

    @property
    def url(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Hash(Component):
    """#xxx. value has no #. is_id is true if the value is a valid identifier."""

    value: str
    is_id: bool
    type: ClassVar[str] = 'hash'


@dataclass(frozen=True, slots=True)
class UnicodeRange(Component):
    """A range of code points, in a unicode-range descriptor."""

    start: int
    end: int
    type: ClassVar[str] = 'unicode-range'

    @property
    def value(self) -> tuple[int, int]:
        return self.start, self.end


@dataclass(frozen=True, slots=True)
class Operator(Component):
    """A delimiter that is not a separator, such as + in calc() or a :."""

    value: str
    type: ClassVar[str] = 'operator'


@dataclass(frozen=True, slots=True)
class Separator(Component):
    """A comma or /. Separators are in PropertyValue.items, but they are not components."""

    value: str
    type: ClassVar[str] = 'separator'


@dataclass(frozen=True, slots=True)
class Function(Component):
    """A function such as rgb(...) or image-set(...). url("x") is a Url, not a Function."""

    name: str
    arguments: PropertyValue
    type: ClassVar[str] = 'function'

    @property
    def value(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class SimpleBlock(Component):
    """A [], () or {} block."""

    opener: str
    content: PropertyValue
    type: ClassVar[str] = 'block'

    @property
    def value(self) -> PropertyValue:
        return self.content


@dataclass(frozen=True, slots=True)
class Other(Component):
    """Any other token: an at-keyword, a bad string or bad URL, <!--, --> or an unmatched closing bracket."""

    token_type: TokenType
    value: str
    type: ClassVar[str] = 'other'


class PropertyValue(Sequence[Component]):
    """A parsed property value: an immutable sequence of components, without separators.

    items has all the components and separators, and css_text is the
    exact text of the whole value.
    """

    __slots__ = ('_components', '_css_text', '_items', '_spans')

    def __init__(self, css_text: str, items: Sequence[Component] = (), spans: Sequence[int] = ()) -> None:
        self._css_text = css_text
        self._items = items = tuple(items)
        # start and end of each item in css_text, flattened: start0, end0, start1, end1, ...
        self._spans = tuple(spans)
        has_separators = False
        for item in items:
            if isinstance(item, Separator):
                has_separators = True
                break
        self._components = tuple(i for i in items if not isinstance(i, Separator)) if has_separators else items

    @property
    def css_text(self) -> str:
        """The exact text of the whole value."""
        return self._css_text

    @property
    def items(self) -> tuple[Component, ...]:
        """All the components and separators, in order."""
        return self._items

    @overload
    def __getitem__(self, index: int) -> Component: ...
    @overload
    def __getitem__(self, index: slice) -> tuple[Component, ...]: ...
    def __getitem__(self, index: int | slice) -> Component | tuple[Component, ...]:
        return self._components[index]

    def __len__(self) -> int:
        return len(self._components)

    def __iter__(self) -> Iterator[Component]:
        return iter(self._components)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PropertyValue) and other._items == self._items

    def __hash__(self) -> int:
        return hash(self._items)

    def __repr__(self) -> str:
        return f'<PropertyValue {self.css_text!r}>'

    def text_without(self, index: int) -> str:
        """The text of this value with the component at index removed, together with one adjacent
        separator (the following one, else the preceding one). The remaining text is kept as written."""
        target = self._components[index]
        pos = next(i for i, item in enumerate(self._items) if item is target)
        drop = {pos}
        if pos + 1 < len(self._items) and isinstance(self._items[pos + 1], Separator):
            drop.add(pos + 1)
        elif pos > 0 and isinstance(self._items[pos - 1], Separator):
            drop.add(pos - 1)
        kept = [i for i in range(len(self._items)) if i not in drop]
        items, spans, text = self._items, self._spans, self.css_text

        def gap_after(i: int) -> str:
            return text[spans[2 * i + 1] : spans[2 * i + 2] if i + 1 < len(items) else len(text)]

        def is_lone_backslash(i: int) -> bool:
            # A backslash is a delimiter only because a newline follows it. Followed by
            # anything else, or by nothing, it would become an escape, so its gap is kept.
            item = items[i]
            return isinstance(item, Operator) and item.value == '\\'

        parts: list[str] = []
        for n, i in enumerate(kept):
            if n:
                prev = kept[n - 1]
                if i == prev + 1 or is_lone_backslash(prev):
                    parts.append(gap_after(prev))
                elif not isinstance(items[i], Separator):
                    # Always a space otherwise: joining directly could create a comment (/*)
                    # or merge tokens. A hex escape at the end of the left text absorbs one
                    # whitespace character, so it needs a second one.
                    parts.append('  ' if HEX_ESCAPE_AT_END.search(parts[-1]) else ' ')
            parts.append(text[spans[2 * i] : spans[2 * i + 1]])
        if kept and is_lone_backslash(kept[-1]):
            parts.append(gap_after(kept[-1]))
        return ''.join(parts)


# A hex escape at the end of text, not itself escaped (an even number of backslashes before it).
# \Z, since $ also matches before a final newline, which already ends the escape.
HEX_ESCAPE_AT_END = re.compile(r'(?<!\\)(?:\\\\)*\\[0-9a-fA-F]{1,6}\Z')

TOKEN_CLASSES: dict[TokenType, type[Component]] = {
    TokenType.IDENT: Ident,
    TokenType.STRING: String,
    TokenType.NUMBER: Number,
    TokenType.PERCENTAGE: Percentage,
    TokenType.URL: Url,
}
IGNORED = frozenset((TokenType.WHITESPACE, TokenType.COMMENT))


def _component(item: Token | Block | SyntaxFunction) -> Component:
    text = item.source_text
    if isinstance(item, SyntaxFunction):
        name = item.name
        if name.isascii() and name.lower() == 'url':
            args = [a for a in item.arguments if not (isinstance(a, Token) and a.type in IGNORED)]
            if len(args) == 1 and isinstance(args[0], Token) and args[0].type is TokenType.STRING:
                return Url(text, str(args[0].value))
        return Function(text, name, _build(item.arguments))
    if isinstance(item, Block):
        return SimpleBlock(text, item.opener, _build(item.content))
    t = item.type
    if (cls := TOKEN_CLASSES.get(t)) is not None:
        return cls(text, item.value)  # ty: ignore[too-many-positional-arguments]
    match t:
        case TokenType.DIMENSION:
            assert isinstance(item.value, int | float)
            return Dimension(text, item.value, str(item.unit))
        case TokenType.HASH:
            return Hash(text, str(item.value), item.hash_is_id)
        case TokenType.COMMA:
            return Separator(text, ',')
        case TokenType.DELIM:
            return Separator(text, '/') if text == '/' else Operator(text, text)
        case TokenType.COLON:
            return Operator(text, text)
    return Other(text, t, text)


def _build(values: ComponentValueList) -> PropertyValue:
    """The value's text runs from its first to its last significant item, as for declaration values.
    It is never trimmed with str.strip(): the space after a hex escape (\\31 ) belongs to its token."""
    items: list[Component] = []
    positions: list[int] = []
    last: Token | Block | SyntaxFunction | None = None
    for item in values:
        if isinstance(item, Token) and item.type in IGNORED:
            continue
        items.append(_component(item))
        positions.append(item.pos)
        positions.append(item.end)
        last = item
    if last is None:
        return PropertyValue('')
    base = positions[0]
    source = values.stream.source
    text = source[base : protected_end(source, positions[-1], last)]
    return PropertyValue(text, items, [p - base for p in positions])


def protected_end(source: str, end: int, last: Token | Block | SyntaxFunction) -> int:
    """Where the text of a value ending with item last should end.

    A lone backslash is a delimiter only because a newline follows it: without
    the newline it would become an escape. So such a value keeps its newline.
    """
    if isinstance(last, Token) and last.type is TokenType.DELIM and last.value == '\\':
        if source.startswith('\r\n', end):
            return end + 2
        if end < len(source) and source[end] in '\n\r\f':
            return end + 1
    return end


URANGE = re.compile(r'[uU]\+([0-9a-fA-F?]{1,6})(?:-([0-9a-fA-F]{1,6}))?')


def _parse_urange(text: str) -> tuple[int, int] | None:
    m = URANGE.fullmatch(text)
    if m is None:
        return None
    first, last = m.group(1), m.group(2)
    if '?' in first:
        if last is not None or '?' in first.rstrip('?'):
            return None
        start, end = int(first.replace('?', '0'), 16), int(first.replace('?', 'F'), 16)
    else:
        start = int(first, 16)
        end = start if last is None else int(last, 16)
    if end > 0x10FFFF or start > end:
        return None
    return start, end


def _unicode_ranges(text: str) -> PropertyValue | None:
    """Parse the value of a unicode-range descriptor from its text, or None if it is not valid."""
    items: list[Component] = []
    spans: list[int] = []
    pos = 0
    for n, part in enumerate(text.split(',')):
        stripped = part.strip(' \t\n\r\f')
        r = _parse_urange(stripped)
        if r is None:
            return None
        start = pos + part.index(stripped)
        if n:
            items.append(Separator(',', ','))
            spans += (pos - 1, pos)
        items.append(UnicodeRange(stripped, *r))
        spans += (start, start + len(stripped))
        pos += len(part) + 1
    return PropertyValue(text, items, spans)


def parse_value(text: str, *, property_name: str | None = None) -> PropertyValue:
    """Parse a property value. Never raises: anything unrecognised becomes an Other component.

    For property_name 'unicode-range' the value is parsed as unicode ranges.
    """
    if property_name is not None and property_name.lower() == 'unicode-range':
        stripped = text.strip(' \t\n\r\f')  # no escapes are possible in a valid unicode-range
        if r := _unicode_ranges(stripped):
            return r
    return _build(parse_component_value_list(text))


def value_from_components(values: ComponentValueList, property_name: str) -> PropertyValue:
    """The parsed value of a declaration, from its component values."""
    if property_name == 'unicode-range' and (r := _unicode_ranges(values.text)):
        return r
    return _build(values)
