# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A naive selector matcher, the oracle for the C matcher (docs/plan-03-selectors.md, §7).

It is written directly from the rules of §5, on lxml trees and the parsed
selector tree, without snapshots, indexes or caches: for each element and
each selector it walks the tree recursively. It is slow and meant to be
obviously right.
"""

from collections.abc import Iterable
from typing import Any

from kgcss.selectors import (
    INAPPROPRIATE_PSEUDO_CLASSES,
    AttributeSelector,
    ClassSelector,
    Combinator,
    ComplexSelector,
    CompoundSelector,
    IdSelector,
    NestingSelector,
    PseudoClass,
    PseudoElement,
    SelectorPseudoClass,
    SimpleSelector,
    TypeSelector,
)

type Element = Any
WHITESPACE = ' \t\n\r\f'
SUPPORTED_PSEUDO_CLASSES = frozenset(('root', 'scope', 'any-link', 'empty', 'first-child', 'last-child', 'only-child'))


class Unsupported(Exception):
    pass


def lower(text: str) -> str:
    return ''.join(c.lower() if 'A' <= c <= 'Z' else c for c in text)


def local(name: str) -> str:
    return lower(name.rpartition('}')[2])


def is_element(node: Element) -> bool:
    return isinstance(node.tag, str)


def element_children(e: Element) -> list[Element]:
    return [c for c in e if is_element(c)]


def parent(e: Element) -> Element | None:
    return e.getparent()


def siblings(e: Element) -> list[Element]:
    p = parent(e)
    return [e] if p is None else element_children(p)


def attributes(e: Element) -> list[tuple[str, str]]:
    return [(local(k), v) for k, v in e.items()]


def first_attribute(e: Element, name: str) -> str | None:
    return next((v for k, v in attributes(e) if k == name), None)


class Oracle:
    def __init__(self, root: Element, ignore: bool = False, quirks: bool = False) -> None:
        self.document = root.getroottree().getroot()
        self.scope = root
        self.ignore = ignore
        self.quirks = quirks

    def name(self, text: str) -> str:
        return lower(text) if self.quirks else text

    def select(self, selectors: Iterable[ComplexSelector]) -> list[Element]:
        selectors = list(selectors)
        for s in selectors:
            self.check(s)
        return [e for e in self.document.iter() if is_element(e) and any(self.complex(s, e) for s in selectors)]

    # Whether a selector can be matched at all, as in §5.5
    def check(self, selector: ComplexSelector) -> None:
        for _, compound in selector.parts:
            for s in compound:
                self.check_simple(s)

    def check_simple(self, s: SimpleSelector) -> None:
        match s:
            case PseudoClass():
                if s.name not in SUPPORTED_PSEUDO_CLASSES and not (
                    self.ignore and s.name in INAPPROPRIATE_PSEUDO_CLASSES
                ):
                    raise Unsupported(s.name)
            case SelectorPseudoClass():
                if s.name == 'has':
                    raise Unsupported('has')
                if s.name == 'not':
                    for inner in s.selectors:
                        self.check(inner)
            case PseudoElement():
                if not self.ignore or s.arguments is not None or s.name not in INAPPROPRIATE_PSEUDO_CLASSES:
                    raise Unsupported(s.name)
                for p in s.pseudo_classes:
                    self.check_simple(p)
            case TypeSelector() | IdSelector() | ClassSelector() | AttributeSelector() | NestingSelector():
                pass
            case _:
                raise Unsupported(type(s).__name__)

    def supported(self, selector: ComplexSelector) -> bool:
        try:
            self.check(selector)
        except Unsupported:
            return False
        return True

    def complex(self, selector: ComplexSelector, e: Element) -> bool:
        return self.parts(selector.parts, len(selector.parts) - 1, e)

    def parts(self, parts: tuple[tuple[Combinator | None, CompoundSelector], ...], k: int, e: Element) -> bool:
        combinator, compound = parts[k]
        if not self.compound(compound, e):
            return False
        if k == 0:
            return True
        match combinator:
            case Combinator.CHILD:
                p = parent(e)
                return p is not None and self.parts(parts, k - 1, p)
            case Combinator.DESCENDANT:
                p = parent(e)
                while p is not None:
                    if self.parts(parts, k - 1, p):
                        return True
                    p = parent(p)
                return False
            case Combinator.NEXT_SIBLING:
                sibs = siblings(e)
                i = sibs.index(e)
                return i > 0 and self.parts(parts, k - 1, sibs[i - 1])
            case Combinator.SUBSEQUENT_SIBLING:
                sibs = siblings(e)
                return any(self.parts(parts, k - 1, s) for s in sibs[: sibs.index(e)])
        return False

    def compound(self, compound: CompoundSelector, e: Element) -> bool:
        return all(self.simple(s, e) for s in compound)

    def simple(self, s: SimpleSelector, e: Element) -> bool:
        match s:
            case TypeSelector():
                return s.name == '*' or local(e.tag) == lower(s.name)
            case IdSelector():
                value = first_attribute(e, 'id')
                return value is not None and self.name(value) == self.name(s.name)
            case ClassSelector():
                value = first_attribute(e, 'class') or ''
                return any(self.name(c) == self.name(s.name) for c in split_ws(value))
            case AttributeSelector():
                return any(attribute_matches(s, v) for k, v in attributes(e) if k == lower(s.name))
            case NestingSelector():
                return e is self.scope
            case PseudoClass():
                return self.pseudo_class(s.name, e)
            case SelectorPseudoClass():
                matched = any(
                    self.complex(inner, e) for inner in s.selectors if s.name == 'not' or self.supported(inner)
                )
                return not matched if s.name == 'not' else matched
            case PseudoElement():
                return all(self.simple(p, e) for p in s.pseudo_classes)
        raise AssertionError(s)

    def pseudo_class(self, name: str, e: Element) -> bool:
        sibs = siblings(e)
        match name:
            case 'root':
                return parent(e) is None
            case 'scope':
                return e is self.scope
            case 'any-link':
                return local(e.tag) in ('a', 'area', 'link') and first_attribute(e, 'href') is not None
            case 'empty':
                if element_children(e) or e.text:
                    return False
                return not any(c.tail for c in e)
            case 'first-child':
                return sibs[0] is e
            case 'last-child':
                return sibs[-1] is e
            case 'only-child':
                return len(sibs) == 1
        # An ignored inappropriate pseudo-class
        return True


def split_ws(text: str) -> list[str]:
    for c in WHITESPACE[1:]:
        text = text.replace(c, ' ')
    return [x for x in text.split(' ') if x]


def attribute_matches(s: AttributeSelector, value: str) -> bool:
    want = s.value or ''
    if s.case == 'i':
        value, want = lower(value), lower(want)
    match s.operator:
        case None:
            return True
        case '=':
            return value == want
        case '~=':
            return bool(want) and not any(c in want for c in WHITESPACE) and want in split_ws(value)
        case '|=':
            return value == want or value.startswith(want + '-')
        case '^=':
            return bool(want) and value.startswith(want)
        case '$=':
            return bool(want) and value.endswith(want)
        case '*=':
            return bool(want) and want in value
    raise AssertionError(s.operator)
