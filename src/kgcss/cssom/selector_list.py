# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The selector list of a style rule.

Selectors are kept as text, with normalized whitespace but escapes, case
and comments as written. They are parsed (kgcss.selectors) when their tree,
specificity or pseudo-element is first needed.
"""

from collections.abc import Callable, Iterable, Iterator, MutableSequence
from typing import overload

from kgcss._kgcss import CompiledSelector
from kgcss.cssom.exceptions import SelectorSyntaxError
from kgcss.cssom.serializer import normalize_selector
from kgcss.cssom.state import SupportsRichComparison
from kgcss.selectors import (
    ComplexSelector,
    NameMap,
    NestingSelector,
    PseudoElement,
    SimpleSelector,
    build_tree,
    compile_selectors,
    rename_classes,
    rename_ids,
)
from kgcss.syntax import Block, Function, parse_component_value_list
from kgcss.tokenizer import TokenType

CSS_WHITESPACE = ' \t\n\r\f'
INVALID_IN_SELECTORS = frozenset((
    TokenType.SEMICOLON, TokenType.RIGHT_CURLY, TokenType.RIGHT_PAREN, TokenType.RIGHT_SQUARE,
    TokenType.BAD_STRING, TokenType.BAD_URL, TokenType.CDO, TokenType.CDC,
))  # fmt: skip


def check_selector_list(text: str) -> list[str]:
    """The normalized selectors of a selector list, split at its top level commas. Raises
    SelectorSyntaxError for an empty or malformed list: an empty selector, unbalanced brackets, an
    unclosed string, or a {}, ; or stray closing bracket. The selectors themselves are not parsed."""
    values = parse_component_value_list(text)
    for error in values.stream.errors:
        raise SelectorSyntaxError(f'Invalid selector {text!r}: {error.message}', text, error.offset)
    parts: list[str] = []
    start = 0
    for item in values:
        if isinstance(item, Block | Function):
            if not item.closed or (isinstance(item, Block) and item.opener == '{'):
                raise SelectorSyntaxError(f'Invalid selector {text!r}: unbalanced or {{}} block', text, item.pos)
        elif item.type in INVALID_IN_SELECTORS:
            raise SelectorSyntaxError(f'Invalid selector {text!r}: unexpected {item.source_text!r}', text, item.pos)
        elif item.type is TokenType.COMMA:
            parts.append(text[start : item.pos])
            start = item.end
    parts.append(text[start:])
    normalized = [normalize_selector(p) for p in parts]
    if not all(normalized):
        raise SelectorSyntaxError(f'Invalid selector list {text!r}: empty selector', text, 0)
    return normalized


def validate_selector_list(text: str, nested: bool) -> list[str]:
    """check_selector_list(), and also parse each selector, raising SelectorSyntaxError if one is not
    valid. With nested, selectors may also have the syntax of nested style rules (> a)."""
    parts = check_selector_list(text)
    for part in parts:
        compile_selectors(part, nested)
    return parts


class Selector:
    """One selector of a selector list.

    Callers can keep their own data in the dict data (for example
    selector.data['used'] = True), which copy() copies. Selectors compare and
    hash by identity, so they can also be the keys of a dict of the caller's.
    Attributes of the caller's own can be set too (they are kept by copy()),
    but type checkers reject them. data and __dict__ are only created when
    they are first used: there can be tens of thousands of selectors.

    The text is parsed when the tree (parsed), specificity or pseudo_element
    are first needed. A selector of a parsed stylesheet that is not valid
    raises SelectorSyntaxError then; text that is assigned is checked at once.
    """

    __slots__ = ('__dict__', '_compiled', '_data', '_owner', '_text', '_tree')

    def __init__(self, text: str) -> None:
        parts = validate_selector_list(text, True)
        if len(parts) != 1:
            raise SelectorSyntaxError(f'Not a single selector: {text!r}', text, 0)
        self._text = parts[0]
        self._data: dict[str, object] | None = None
        self._owner: SelectorList | None = None
        self._compiled: tuple[bool, CompiledSelector] | None = None
        self._tree: tuple[CompiledSelector, ComplexSelector] | None = None

    @classmethod
    def _from_normalized(cls, text: str, compiled: CompiledSelector | None = None, nested: bool = True) -> Selector:
        s = cls.__new__(cls)
        s._text = text
        s._data = None
        s._owner = None
        s._compiled = None if compiled is None else (nested, compiled)
        s._tree = None
        return s

    @property
    def text(self) -> str:
        return self._text

    @property
    def data(self) -> dict[str, object]:
        """A dict for the caller's own data about this selector, created when first used. copy() copies it
        (a shallow copy). kgcss never reads or writes it."""
        data = self._data
        if data is None:
            self._data = data = {}
        return data

    @text.setter
    def text(self, value: str) -> None:
        parts = validate_selector_list(value, self._is_nested())
        if len(parts) != 1:
            raise SelectorSyntaxError(f'Not a single selector: {value!r}', value, 0)
        self._text = parts[0]
        self._compiled = self._tree = None
        if self._owner is not None:
            self._owner._changed()

    def _rule(self) -> StyleRule | None:
        return None if self._owner is None else self._owner._rule

    def _is_nested(self) -> bool:
        """Whether the selector is parsed as in a nested style rule. That is also the case for selectors
        that are not in a rule, as it accepts every selector that the top level accepts."""
        rule = self._rule()
        if rule is None:
            return True
        if rule.parent_rule is None:
            return False
        from kgcss.cssom.build import Context
        from kgcss.cssom.rules import context_of

        return context_of(rule) is Context.NESTED

    def _compiled_selector(self) -> CompiledSelector:
        nested = self._is_nested()
        cached = self._compiled
        if cached is not None and cached[0] == nested:
            return cached[1]
        compiled = compile_selectors(self._text, nested)
        if len(compiled) != 1:
            raise SelectorSyntaxError(f'Not a single selector: {self._text!r}', self._text, 0)
        self._compiled = (nested, compiled[0])
        return compiled[0]

    @property
    def parsed(self) -> ComplexSelector:
        """The parsed selector, as a tree. Raises SelectorSyntaxError if the text is not a valid selector."""
        compiled = self._compiled_selector()
        tree = self._tree
        if tree is None or tree[0] is not compiled:
            self._tree = tree = (compiled, build_tree(compiled))
        return tree[1]

    @property
    def specificity(self) -> tuple[int, int, int]:
        """The specificity (a, b, c) of Selectors Level 4. Raises SelectorSyntaxError if the text is not a
        valid selector.

        For a selector of a nested style rule, it is the specificity of resolved(): CSS Nesting defines
        & as :is() of the parent rule's selectors, including for specificity, and implies & before
        selectors that start with a combinator or have none. Elsewhere & counts as :scope.
        """
        compiled = self._compiled_selector()
        if self._parent_style_rule() is None:
            return compiled.specificity()
        return self.resolved()._compiled_selector().specificity()

    def _parent_style_rule(self) -> StyleRule | None:
        """The style rule this selector's rule is nested in, if any."""
        from kgcss.cssom.rules import StyleRule

        rule = self._rule()
        parent = None if rule is None else rule.parent_rule
        while parent is not None and not isinstance(parent, StyleRule):
            parent = parent.parent_rule
        return parent

    def resolved(self) -> Selector:
        """For a selector of a nested style rule, the equivalent selector outside of it, as CSS Nesting
        defines it: & is replaced by :is() of the parent rule's selectors, resolved in turn. A selector
        that starts with a combinator or has no & is relative to the parent: & is implied before it, so
        > a becomes :is(.p) > a, and a becomes :is(.p) a. For any other selector, the selector itself.
        Raises SelectorSyntaxError if this selector or one of the parent rule is not valid."""
        parent = self._parent_style_rule()
        if parent is None:
            return self
        compiled = self._compiled_selector()
        replacement = f':is({", ".join(s.resolved().text for s in parent.selectors)})'
        text = self._text
        if compiled.has_nesting:
            spans = sorted(s.span for s in self.parsed.walk() if isinstance(s, NestingSelector))
            parts = []
            pos = 0
            for start, end in spans:
                parts.append(text[pos:start])
                parts.append(replacement)
                pos = end
            parts.append(text[pos:])
            text = ''.join(parts)
        if compiled.relative or not compiled.has_nesting:
            text = f'{replacement} {text}'
        return Selector(text)

    def class_names(self) -> set[str]:
        """The class names, at any depth (including inside :is(), :not(), :has() and 'of S')."""
        return self.parsed.class_names()

    def ids(self) -> set[str]:
        """The IDs, at any depth."""
        return self.parsed.ids()

    def pseudo_classes(self) -> set[str]:
        """The names of the pseudo-classes, at any depth, lowercased."""
        return self.parsed.pseudo_classes()

    def pseudo_elements(self) -> set[str]:
        """The names of the pseudo-elements, lowercased, including the legacy ones such as :before."""
        return self.parsed.pseudo_elements()

    def has_pseudo(self, names: Iterable[str]) -> bool:
        """Whether a pseudo-class or pseudo-element of this selector, at any depth, has one of these names.
        For example has_pseudo(INAPPROPRIATE_PSEUDO_CLASSES) says whether it depends on user interaction
        or generated content."""
        return self.parsed.has_pseudo(names)

    def rename_classes(self, mapping: NameMap) -> bool:
        """Rename classes, at any depth, by a mapping of old names to new ones or a function, changing only
        the renamed class selectors in the text (see kgcss.selectors.rename_classes). Returns True if
        anything changed. Raises SelectorSyntaxError if the selector is not valid."""
        return self._set_renamed(rename_classes(self._text, mapping, nested=self._is_nested()))

    def rename_ids(self, mapping: NameMap) -> bool:
        """Like rename_classes(), for IDs."""
        return self._set_renamed(rename_ids(self._text, mapping, nested=self._is_nested()))

    def _set_renamed(self, text: str) -> bool:
        if text == self._text:
            return False
        self.text = text
        return True

    @property
    def pseudo_element(self) -> PseudoElement | None:
        """The pseudo-element of this selector, if it has one, such as ::before."""
        compiled = self._compiled_selector()
        return self.parsed.pseudo_element if compiled.has_pseudo_element else None

    def walk(self) -> Iterator[SimpleSelector]:
        """Every simple selector, at any depth, see ComplexSelector.walk()."""
        return self.parsed.walk()

    def copy(self) -> Selector:
        """An unattached copy, with a copy of data and any attributes attached by callers."""
        s = Selector._from_normalized(self._text)
        s._compiled, s._tree = self._compiled, self._tree
        if self._data:
            s._data = self._data.copy()
        if self.__dict__:
            s.__dict__.update(self.__dict__)
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

    def rename_classes(self, mapping: NameMap) -> bool:
        """Rename classes in every selector, see Selector.rename_classes(). Selectors that are not valid,
        which browsers ignore, are left as they are. Returns True if anything changed."""
        return self._rename(Selector.rename_classes, mapping)

    def rename_ids(self, mapping: NameMap) -> bool:
        """Like rename_classes(), for IDs."""
        return self._rename(Selector.rename_ids, mapping)

    def _rename(self, method: Callable[[Selector, NameMap], bool], mapping: NameMap) -> bool:
        changed = False
        for selector in list(self._items):
            try:
                changed = method(selector, mapping) or changed
            except SelectorSyntaxError:
                continue
        return changed

    def sort(self, *, key: Callable[[Selector], SupportsRichComparison] | None = None, reverse: bool = False) -> None:
        """Sort the selectors, by default by their text."""
        self._items.sort(key=key or (lambda s: s.text), reverse=reverse)
        self._changed()

    def __repr__(self) -> str:
        return f'<SelectorList {self.text!r}>'


def parse_selectors(text: str, *, nested: bool = False) -> SelectorList:
    """Parse a selector list into a new SelectorList that is not in any rule. Raises SelectorSyntaxError.
    With nested, selectors may start with a combinator, as in a nested style rule (> a). Parsing is
    cached, so parsing the same text again is cheap."""
    selectors = []
    for part in check_selector_list(text):
        compiled = compile_selectors(part, nested)
        # Valid in nested mode too, which is what selectors that are not in a rule use
        selectors.append(Selector._from_normalized(part, compiled[0]))
    return SelectorList(None, selectors)


def invalid_selectors(sheet: Stylesheet) -> list[tuple[StyleRule, Selector, SelectorSyntaxError]]:
    """The selectors of the style rules of a sheet, at any depth, that are not valid, with the error
    for each. Its pos is the offset in the selector's text; rule.line and rule.column say where the
    rule is. Parsing a sheet keeps such rules, for fidelity, and they raise when they are matched."""
    from kgcss.cssom.rules import RuleType, StyleRule

    ans = []
    for rule in sheet.iter_rules(RuleType.STYLE):
        assert isinstance(rule, StyleRule)
        for selector in rule.selectors:
            try:
                selector._compiled_selector()
            except SelectorSyntaxError as e:
                ans.append((rule, selector, e))
    return ans


# Names used only in annotations. Imported at the end, after this module is complete, because of
# import cycles, so that annotations can be evaluated at run time (inspect.signature(), help())
from kgcss.cssom.rules import StyleRule  # noqa: E402
from kgcss.cssom.stylesheet import Stylesheet  # noqa: E402
