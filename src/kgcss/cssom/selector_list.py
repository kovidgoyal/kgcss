# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The selector list of a style rule (requirements 3.3).

Selectors are kept as text here, with normalized whitespace but escapes,
case and comments as written. Parsing them, specificity and matching come
with the selectors plan (plan 03).
"""

from collections.abc import Callable, Iterable, Iterator, MutableSequence
from typing import TYPE_CHECKING, overload

from kgcss.cssom.exceptions import CSSSyntaxError
from kgcss.cssom.serializer import normalize_selector
from kgcss.syntax import Block, Function, parse_component_value_list
from kgcss.tokenizer import TokenType

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

    from kgcss.cssom.rules import StyleRule

CSS_WHITESPACE = ' \t\n\r\f'
INVALID_IN_SELECTORS = frozenset((
    TokenType.SEMICOLON, TokenType.RIGHT_CURLY, TokenType.RIGHT_PAREN, TokenType.RIGHT_SQUARE,
    TokenType.BAD_STRING, TokenType.BAD_URL, TokenType.CDO, TokenType.CDC,
))  # fmt: skip


def check_selector_list(text: str) -> list[str]:
    """The normalized selectors of a selector list. Raises CSSSyntaxError for an empty or malformed list:
    an empty selector, unbalanced brackets, an unclosed string, or a {}, ; or stray closing bracket."""
    values = parse_component_value_list(text)
    for error in values.stream.errors:
        raise CSSSyntaxError(f'Invalid selector {text!r}: {error.message}')
    parts: list[str] = []
    start = 0
    for item in values:
        if isinstance(item, Block | Function):
            if not item.closed or (isinstance(item, Block) and item.opener == '{'):
                raise CSSSyntaxError(f'Invalid selector {text!r}: unbalanced or {{}} block')
        elif item.type in INVALID_IN_SELECTORS:
            raise CSSSyntaxError(f'Invalid selector {text!r}: unexpected {item.source_text!r}')
        elif item.type is TokenType.COMMA:
            parts.append(text[start : item.pos])
            start = item.end
    parts.append(text[start:])
    normalized = [normalize_selector(p) for p in parts]
    if not all(normalized):
        raise CSSSyntaxError(f'Invalid selector list {text!r}: empty selector')
    return normalized


class Selector:
    """One selector of a selector list.

    There are no __slots__, so callers can attach their own attributes (for
    example calibre's selector.calibre_used). They are kept by copy().
    """

    def __init__(self, text: str) -> None:
        parts = check_selector_list(text)
        if len(parts) != 1:
            raise CSSSyntaxError(f'Not a single selector: {text!r}')
        self._text = parts[0]
        self._owner: SelectorList | None = None

    @classmethod
    def _from_normalized(cls, text: str) -> Selector:
        s = cls.__new__(cls)
        s._text = text
        s._owner = None
        return s

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        parts = check_selector_list(value)
        if len(parts) != 1:
            raise CSSSyntaxError(f'Not a single selector: {value!r}')
        self._text = parts[0]
        if self._owner is not None:
            self._owner._changed()

    @property
    def specificity(self) -> tuple[int, int, int]:
        raise NotImplementedError('Specificity comes with selector parsing (plan 03)')

    def copy(self) -> Selector:
        """An unattached copy, with any attributes attached by callers."""
        s = Selector.__new__(Selector)
        s.__dict__.update(self.__dict__)
        s._owner = None
        return s

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f'<Selector {self._text!r}>'


class SelectorList(MutableSequence[Selector]):
    """The selectors of a style rule. Changes update the rule's selector_text.

    Adding a selector that belongs to another list adds a copy of it.
    """

    __slots__ = ('_items', '_rule')

    def __init__(self, rule: StyleRule | None = None, selectors: Iterable[Selector] = ()) -> None:
        self._rule = rule
        self._items: list[Selector] = []
        for s in selectors:
            self._items.append(self._adopt(s))

    def _adopt(self, selector: Selector) -> Selector:
        if selector._owner is not None and selector._owner is not self:
            selector = selector.copy()
        selector._owner = self
        return selector

    def _changed(self) -> None:
        if self._rule is not None:
            self._rule._selector_text = self.text
            self._rule._changed()

    def _replace_all(self, texts: Iterable[str]) -> None:
        """Replace the selectors with new ones, without notifying the rule (used by selector_text)."""
        for s in self._items:
            s._owner = None
        self._items = [self._adopt(Selector._from_normalized(t)) for t in texts]

    @property
    def text(self) -> str:
        """The whole selector list as text."""
        return ', '.join(s.text for s in self._items)

    @overload
    def __getitem__(self, index: int) -> Selector: ...
    @overload
    def __getitem__(self, index: slice) -> list[Selector]: ...
    def __getitem__(self, index: int | slice) -> Selector | list[Selector]:
        return self._items[index]

    @overload
    def __setitem__(self, index: int, value: Selector) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[Selector]) -> None: ...
    def __setitem__(self, index: int | slice, value: Selector | Iterable[Selector]) -> None:
        if isinstance(index, slice):
            assert not isinstance(value, Selector)
            new = [self._adopt(s) for s in value]
            for s in self._items[index]:
                s._owner = None
            self._items[index] = new
        else:
            assert isinstance(value, Selector)
            self._items[index]._owner = None
            self._items[index] = self._adopt(value)
        self._changed()

    def __delitem__(self, index: int | slice) -> None:
        for s in self._items[index] if isinstance(index, slice) else [self._items[index]]:
            s._owner = None
        del self._items[index]
        self._changed()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Selector]:
        return iter(self._items)

    def insert(self, index: int, value: Selector) -> None:
        self._items.insert(index, self._adopt(value))
        self._changed()

    def sort(self, *, key: Callable[[Selector], SupportsRichComparison] | None = None, reverse: bool = False) -> None:
        """Sort the selectors, by default by their text."""
        self._items.sort(key=key or (lambda s: s.text), reverse=reverse)
        self._changed()

    def __repr__(self) -> str:
        return f'<SelectorList {self.text!r}>'
