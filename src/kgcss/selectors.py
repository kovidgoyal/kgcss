# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Parsed selectors (Selectors Level 4 and CSS Nesting).

Selectors are parsed in C (csrc/selectors.c) into a flat array of nodes.
This module turns that into a tree of immutable objects, on demand. Every
node has span, its (start, end) in the text of its selector, so that edits
can change parts of the text and keep the rest exactly as written. Spans
are not compared when nodes are compared.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, IntEnum, unique
from functools import lru_cache

from kgcss._kgcss import CompiledSelector, SelectorParseError
from kgcss._kgcss import parse_selectors as c_parse_selectors
from kgcss.cssom.exceptions import SelectorSyntaxError
from kgcss.escaping import serialize_identifier, serialize_string
from kgcss.tokenizer import TokenType, tokenize

__all__ = [
    'INAPPROPRIATE_PSEUDO_CLASSES',
    'AttributeSelector',
    'ClassSelector',
    'Combinator',
    'ComplexSelector',
    'CompoundSelector',
    'FunctionalPseudoClass',
    'IdSelector',
    'LangPseudoClass',
    'NestingSelector',
    'NthPseudoClass',
    'PseudoClass',
    'PseudoElement',
    'SelectorPseudoClass',
    'SimpleSelector',
    'TypeSelector',
    'compile_selectors',
    'rename_classes',
    'rename_ids',
]


@unique
class NodeKind(IntEnum):
    """Kinds of the nodes made by the C parser. Keep in sync with sn_kind in csrc/selectors.c."""

    COMPLEX = 1
    COMPOUND = 2
    TYPE = 3
    ID = 4
    CLASS = 5
    ATTRIBUTE = 6
    PSEUDO_CLASS = 7
    SELECTOR_PSEUDO = 8
    NTH = 9
    LANG = 10
    FUNCTIONAL_PSEUDO = 11
    PSEUDO_ELEMENT = 12
    NESTING = 13


@unique
class Combinator(Enum):
    DESCENDANT = ' '
    CHILD = '>'
    NEXT_SIBLING = '+'
    SUBSEQUENT_SIBLING = '~'


# Indexed by the op of a compound node and of an attribute node, see csrc/selectors.c
COMBINATORS = (None, Combinator.DESCENDANT, Combinator.CHILD, Combinator.NEXT_SIBLING, Combinator.SUBSEQUENT_SIBLING)
ATTRIBUTE_OPERATORS = (None, '=', '~=', '|=', '^=', '$=', '*=')
CASE_FLAGS = (None, 'i', 's')
F_LEGACY = 1
F_FUNCTIONAL = 2
F_HAS_OF = 1

type Span = tuple[int, int]


def _span() -> Span:
    return (0, 0)


@dataclass(frozen=True, slots=True)
class TypeSelector:
    """A type selector, or the universal selector when name is '*'. namespace is None without a
    prefix, '*' for any namespace and '' for no namespace."""

    name: str
    namespace: str | None = None
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        prefix = '' if self.namespace is None else ('*' if self.namespace == '*' else _ident(self.namespace)) + '|'
        return prefix + ('*' if self.name == '*' else _ident(self.name))


@dataclass(frozen=True, slots=True)
class IdSelector:
    name: str
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return '#' + _ident(self.name)


@dataclass(frozen=True, slots=True)
class ClassSelector:
    name: str
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return '.' + _ident(self.name)


@dataclass(frozen=True, slots=True)
class AttributeSelector:
    """[name], or [name op value] with an optional case flag ('i' or 's'). operator is None for
    presence. namespace is as for TypeSelector."""

    name: str
    namespace: str | None = None
    operator: str | None = None
    value: str | None = None
    case: str | None = None
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        prefix = '' if self.namespace is None else ('*' if self.namespace == '*' else _ident(self.namespace)) + '|'
        if self.operator is None:
            return f'[{prefix}{_ident(self.name)}]'
        flag = f' {self.case}' if self.case else ''
        return f'[{prefix}{_ident(self.name)}{self.operator}{serialize_string(self.value or "")}{flag}]'


@dataclass(frozen=True, slots=True)
class PseudoClass:
    """A pseudo-class without arguments, its name lowercased."""

    name: str
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return ':' + _ident(self.name)


@dataclass(frozen=True, slots=True)
class SelectorPseudoClass:
    """:is(), :where(), :matches(), :not() or :has(), whose argument is a selector list. The
    selectors of :has() are relative: their first combinator is never None."""

    name: str
    selectors: tuple[ComplexSelector, ...]
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return f':{self.name}({", ".join(s.to_css() for s in self.selectors)})'


@dataclass(frozen=True, slots=True)
class NthPseudoClass:
    """An :nth-child(), :nth-last-child(), :nth-of-type() or :nth-last-of-type(): An+B, and for the
    first two optionally the selectors of 'of S'."""

    name: str
    a: int
    b: int
    of: tuple[ComplexSelector, ...] | None = None
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        if self.a == 0:
            anb = str(self.b)
        else:
            anb = {1: 'n', -1: '-n'}.get(self.a, f'{self.a}n')
            if self.b:
                anb += f'{self.b:+d}'
        of = f' of {", ".join(s.to_css() for s in self.of)}' if self.of is not None else ''
        return f':{self.name}({anb}{of})'


@dataclass(frozen=True, slots=True)
class LangPseudoClass:
    ranges: tuple[str, ...]
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return f':lang({", ".join(serialize_string(r) for r in self.ranges)})'


@dataclass(frozen=True, slots=True)
class FunctionalPseudoClass:
    """Any other functional pseudo-class, with its arguments as written."""

    name: str
    arguments: str
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return f':{self.name}({self.arguments})'


@dataclass(frozen=True, slots=True)
class NestingSelector:
    """The nesting selector &."""

    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        return '&'


@dataclass(frozen=True, slots=True)
class PseudoElement:
    """A pseudo-element, its name lowercased. legacy is True for :before, :after, :first-line and
    :first-letter written with one colon. arguments are those of a functional pseudo-element, as
    written. pseudo_classes are those that follow it (::before:hover)."""

    name: str
    arguments: str | None = None
    legacy: bool = False
    pseudo_classes: tuple[SimpleSelector, ...] = ()
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def to_css(self) -> str:
        args = f'({self.arguments})' if self.arguments is not None else ''
        head = (':' if self.legacy else '::') + _ident(self.name) + args
        return head + ''.join(p.to_css() for p in self.pseudo_classes)


type SimpleSelector = (
    TypeSelector | IdSelector | ClassSelector | AttributeSelector | PseudoClass | SelectorPseudoClass
    | NthPseudoClass | LangPseudoClass | FunctionalPseudoClass | NestingSelector | PseudoElement
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class CompoundSelector:
    """Simple selectors with no combinator between them: the type selector if any, then ids, classes,
    attributes, pseudo-classes and &, then pseudo-elements."""

    type: TypeSelector | None
    subclasses: tuple[SimpleSelector, ...] = ()
    pseudo_elements: tuple[PseudoElement, ...] = ()
    span: Span = field(default_factory=_span, compare=False, repr=False)

    def __iter__(self) -> Iterator[SimpleSelector]:
        """The simple selectors at the top level of this compound, in order."""
        if self.type is not None:
            yield self.type
        yield from self.subclasses
        yield from self.pseudo_elements

    def to_css(self) -> str:
        return ''.join(s.to_css() for s in self)


@dataclass(frozen=True, slots=True)
class ComplexSelector:
    """Compound selectors and the combinators between them, left to right. Each combinator is the
    one before its compound, so the first is None, except in relative selectors (in :has() and in
    nested rules), where it says how the selector relates to the element it is relative to."""

    parts: tuple[tuple[Combinator | None, CompoundSelector], ...]
    span: Span = field(default_factory=_span, compare=False, repr=False)

    @property
    def pseudo_element(self) -> PseudoElement | None:
        """The first pseudo-element of the selector, which can only be in its last compound."""
        pe = self.parts[-1][1].pseudo_elements
        return pe[0] if pe else None

    def walk(self) -> Iterator[SimpleSelector]:
        """Every simple selector, at any depth: also those inside :is(), :not(), :has(), 'of S' and
        those following a pseudo-element, in document order."""
        stack: list[Iterator[SimpleSelector]] = [self._simple()]
        while stack:
            s = next(stack[-1], None)
            if s is None:
                stack.pop()
                continue
            yield s
            if isinstance(s, SelectorPseudoClass):
                stack.append(_simple_of_all(s.selectors))
            elif isinstance(s, NthPseudoClass) and s.of is not None:
                stack.append(_simple_of_all(s.of))
            elif isinstance(s, PseudoElement):
                stack.append(iter(s.pseudo_classes))

    def class_names(self) -> set[str]:
        """The class names, at any depth."""
        return {s.name for s in self.walk() if isinstance(s, ClassSelector)}

    def ids(self) -> set[str]:
        """The IDs, at any depth."""
        return {s.name for s in self.walk() if isinstance(s, IdSelector)}

    def pseudo_classes(self) -> set[str]:
        """The names of the pseudo-classes, at any depth, lowercased: hover, not, nth-child, lang..."""
        names = set()
        for s in self.walk():
            if isinstance(s, LangPseudoClass):
                names.add('lang')
            elif isinstance(s, PseudoClass | SelectorPseudoClass | NthPseudoClass | FunctionalPseudoClass):
                names.add(s.name)
        return names

    def pseudo_elements(self) -> set[str]:
        """The names of the pseudo-elements, lowercased, including the legacy ones such as :before."""
        return {s.name for s in self.walk() if isinstance(s, PseudoElement)}

    def has_pseudo(self, names: Iterable[str]) -> bool:
        """Whether any of the pseudo-classes or pseudo-elements of this selector, at any depth, has one of
        these names (compared ASCII case-insensitively). For example has_pseudo(INAPPROPRIATE_PSEUDO_CLASSES)
        says whether the selector depends on user interaction or generated content."""
        wanted = {_ascii_lower(n) for n in names}
        return not wanted.isdisjoint(self.pseudo_classes()) or not wanted.isdisjoint(self.pseudo_elements())

    def _simple(self) -> Iterator[SimpleSelector]:
        for _, compound in self.parts:
            yield from compound

    def to_css(self) -> str:
        out: list[str] = []
        for i, (combinator, compound) in enumerate(self.parts):
            if i == 0:
                # A leading descendant combinator, as in :has(a), goes without saying
                if combinator is not None and combinator is not Combinator.DESCENDANT:
                    out.append(combinator.value + ' ')
            elif combinator is Combinator.DESCENDANT:
                out.append(' ')
            elif combinator is not None:
                out.append(f' {combinator.value} ')
            out.append(compound.to_css())
        return ''.join(out)


def _simple_of_all(selectors: tuple[ComplexSelector, ...]) -> Iterator[SimpleSelector]:
    for s in selectors:
        yield from s._simple()


def _ident(name: str) -> str:
    return serialize_identifier(name)


def _ascii_lower(text: str) -> str:
    return text.lower() if text.isascii() else ''.join(c.lower() if c.isascii() else c for c in text)


# Pseudo-classes and pseudo-elements that depend on user interaction or rendering, so they cannot
# be matched against a document. The same set as css_selectors.
INAPPROPRIATE_PSEUDO_CLASSES = frozenset((
    'active', 'after', 'disabled', 'visited', 'link', 'before', 'focus', 'first-letter', 'enabled', 'first-line',
    'hover', 'checked', 'target', 'focus-within', 'focus-visible', 'target-within',
))  # fmt: skip


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------

type NameMap = Mapping[str, str] | Callable[[str], str]


def _mapper(mapping: NameMap) -> Callable[[str], str]:
    if isinstance(mapping, Mapping):
        return lambda name: mapping.get(name, name)
    return mapping


def _rename(text: str, kind: type[ClassSelector | IdSelector], mapping: NameMap, nested: bool) -> str:
    fn = _mapper(mapping)
    prefix = '.' if kind is ClassSelector else '#'
    edits: list[tuple[int, int, str]] = []
    for compiled in compile_selectors(text, nested):
        for s in build_tree(compiled).walk():
            if type(s) is kind and (new := fn(s.name)) != s.name:
                if not new:
                    raise ValueError(f'Cannot rename {prefix}{s.name} to an empty name')
                start, end = compiled.start + s.span[0], compiled.start + s.span[1]
                edits.append((start, end, prefix + serialize_identifier(new)))
            elif (
                kind is IdSelector
                and type(s) is AttributeSelector
                and s.operator == '='
                and s.namespace is None
                and _ascii_lower(s.name) == 'id'
                and s.value is not None
                and (new := fn(s.value)) != s.value
            ):
                # [id="x"] and [id=x] select by id too: replace the value, as a string
                if not new:
                    raise ValueError(f'Cannot rename [id={s.value!r}] to an empty name')
                start = compiled.start + s.span[0]
                value_span = _attribute_value_span(text[start : compiled.start + s.span[1]])
                if value_span is not None:
                    edits.append((start + value_span[0], start + value_span[1], serialize_string(new)))
    if not edits:
        return text
    parts = []
    pos = 0
    for start, end, replacement in sorted(edits):
        parts.append(text[pos:start])
        parts.append(replacement)
        pos = end
    parts.append(text[pos:])
    return ''.join(parts)


def _attribute_value_span(text: str) -> tuple[int, int] | None:
    """The span of the value in the text of an attribute selector, [name op value flag]"""
    tokens = [t for t in tokenize(text) if t.type not in (TokenType.WHITESPACE, TokenType.COMMENT)]
    for i, t in enumerate(tokens[:-1]):
        if t.is_delim('='):
            value = tokens[i + 1]
            if value.type in (TokenType.STRING, TokenType.IDENT):
                return value.pos, value.end
    return None


def rename_classes(text: str, mapping: NameMap, *, nested: bool = True) -> str:
    """The selector list text with class names renamed, at any depth, by a mapping of old names to new
    ones or a function. Only the renamed class selectors change, everything else stays exactly as
    written. Names are unescaped: rename {'1a': 'b'} turns .\\31 a into .b. Raises
    SelectorSyntaxError if the text is not a selector list. With nested, the selectors may start with a
    combinator, as in nested style rules."""
    return _rename(text, ClassSelector, mapping, nested)


def rename_ids(text: str, mapping: NameMap, *, nested: bool = True) -> str:
    """Like rename_classes(), for IDs: in #id selectors, and in [id=value] attribute selectors, whose value
    is written back as a string, as calibre's rename_ids_in_selector() does. [id~=value] and the other
    operators are left alone."""
    return _rename(text, IdSelector, mapping, nested)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@lru_cache(maxsize=2048)
def compile_selectors(text: str, nested: bool = False) -> tuple[CompiledSelector, ...]:
    """Parse a selector list, cached. With nested, selectors may start with a combinator, as in a
    nested style rule. Raises SelectorSyntaxError."""
    try:
        return c_parse_selectors(text, nested)
    except SelectorParseError as e:
        message, got, pos = e.args
        raise SelectorSyntaxError(message if got is None else f'{message}, got {got}', text, pos) from None


def build_tree(compiled: CompiledSelector) -> ComplexSelector:
    """The tree for a compiled selector."""
    nodes = compiled.nodes()

    def children(i: int) -> Iterator[int]:
        k = i + 1
        end = nodes[i][5]
        while k < end:
            yield k
            k = nodes[k][5]

    def complex_list(i: int) -> tuple[ComplexSelector, ...]:
        return tuple(complex_selector(k) for k in children(i))

    def complex_selector(i: int) -> ComplexSelector:
        parts = []
        for k in children(i):
            parts.append((COMBINATORS[nodes[k][1]], compound(k)))
        _, _, _, start, end, *_ = nodes[i]
        return ComplexSelector(tuple(parts), span=(start, end))

    def compound(i: int) -> CompoundSelector:
        type_selector = None
        subclasses: list[SimpleSelector] = []
        pseudo_elements: list[PseudoElement] = []
        for k in children(i):
            s = simple(k)
            if isinstance(s, TypeSelector):
                type_selector = s
            elif isinstance(s, PseudoElement):
                pseudo_elements.append(s)
            else:
                subclasses.append(s)
        return CompoundSelector(type_selector, tuple(subclasses), tuple(pseudo_elements), span=nodes[i][3:5])

    def simple(i: int) -> SimpleSelector:
        kind, op, flags, start, end, _, a, b, name, value, ns = nodes[i]
        span = (start, end)
        match NodeKind(kind):
            case NodeKind.TYPE:
                return TypeSelector(name if name is not None else '*', ns, span=span)
            case NodeKind.ID:
                return IdSelector(name, span=span)
            case NodeKind.CLASS:
                return ClassSelector(name, span=span)
            case NodeKind.ATTRIBUTE:
                return AttributeSelector(name, ns, ATTRIBUTE_OPERATORS[op], value, CASE_FLAGS[flags], span=span)
            case NodeKind.PSEUDO_CLASS:
                return PseudoClass(name, span=span)
            case NodeKind.SELECTOR_PSEUDO:
                return SelectorPseudoClass(name, complex_list(i), span=span)
            case NodeKind.NTH:
                return NthPseudoClass(name, a, b, complex_list(i) if flags & F_HAS_OF else None, span=span)
            case NodeKind.LANG:
                return LangPseudoClass(value, span=span)
            case NodeKind.FUNCTIONAL_PSEUDO:
                return FunctionalPseudoClass(name, value, span=span)
            case NodeKind.NESTING:
                return NestingSelector(span=span)
            case NodeKind.PSEUDO_ELEMENT:
                following = tuple(simple(k) for k in children(i))
                return PseudoElement(
                    name,
                    value if flags & F_FUNCTIONAL else None,
                    bool(flags & F_LEGACY),
                    following,
                    span=span,
                )
        raise AssertionError(f'Unexpected node kind: {kind}')

    return complex_selector(0)
