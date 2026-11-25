# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Parsed selectors (Selectors Level 4 and CSS Nesting), see docs/plan-03-selectors.md.

Selectors are parsed in C (csrc/selectors.c) into a flat array of nodes.
This module turns that into a tree of immutable objects, on demand. Every
node has span, its (start, end) in the text of its selector, so that edits
can change parts of the text and keep the rest exactly as written. Spans
are not compared when nodes are compared.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum, IntEnum, unique
from functools import lru_cache

from kgcss._kgcss import CompiledSelector, SelectorParseError
from kgcss._kgcss import parse_selectors as c_parse_selectors
from kgcss.cssom.exceptions import SelectorSyntaxError
from kgcss.escaping import serialize_identifier, serialize_string

__all__ = [
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
    """:nth-child(), :nth-last-child(), :nth-of-type() or :nth-last-of-type(): An+B, and for the
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
