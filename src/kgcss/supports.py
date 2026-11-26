# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""@supports conditions, as CSS Conditional Rules Level 4 and 5 define them.

parse_supports_condition() parses the condition of an @supports rule, or of the supports() of
an @import, into a SupportsCondition. It never raises: a condition that does not parse is kept,
with valid=False, and matches nothing, as browsers drop such rules. Results are cached by text.

>>> c = parse_supports_condition('(display: grid) and (not (display: inline-grid))')
>>> [(d.name, d.value) for d in c.declarations()]
[('display', 'grid'), ('display', 'inline-grid')]
>>> c.to_css()
'(display: grid) and (not (display: inline-grid))'
>>> parse_supports_condition('(a: b) and (c: d) or (e: f)').valid
False

What is supported is up to the caller: supports_matches() and SupportsCondition.matches() ask a
callback about each declaration, and another about the functions selector(), font-tech(),
font-format() and at-rule(). Unlike media queries, there is no unknown: anything else in
parentheses, such as (foo bar), is false.

>>> known = {'display': {'block', 'grid'}, 'color': {'red'}}
>>> def declaration(d: SupportsDeclaration) -> bool:
...     return d.value in known.get(d.name, ())
>>> supports_matches('(display: grid) and (color: red)', declaration=declaration)
True
>>> supports_matches('(display: flex) or (foo bar)', declaration=declaration)
False
>>> supports_matches('selector(a > b)', declaration=declaration)
True
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Final, final

from kgcss.cssom.declarations import normalize_name
from kgcss.cssom.exceptions import SelectorSyntaxError
from kgcss.escaping import serialize_identifier
from kgcss.media import GeneralEnclosed, keyword, significant
from kgcss.selectors import compile_selectors
from kgcss.syntax import Block, Declaration, Token, parse_component_value_list, parse_one_declaration
from kgcss.syntax import Function as SyntaxFunction

__all__ = [
    'SupportsAnd',
    'SupportsCondition',
    'SupportsDeclaration',
    'SupportsFunction',
    'SupportsNot',
    'SupportsOr',
    'parse_supports_condition',
    'supports_matches',
]

type Item = Token | Block | SyntaxFunction
# The functions of <supports-feature>, other than declarations. Other functions are <general-enclosed>.
FUNCTIONS: Final = frozenset(('selector', 'font-tech', 'font-format', 'at-rule'))


@final
@dataclass(frozen=True, slots=True)
class SupportsDeclaration:
    """A declaration in parentheses, (name: value). name is lowercased, unless it is a custom property,
    and value is the text as written, without surrounding whitespace and !important."""

    name: str
    value: str
    important: bool = False

    def to_css(self) -> str:
        imp = ' !important' if self.important else ''
        sep = ': ' if self.value else ':'
        return f'({serialize_identifier(self.name)}{sep}{self.value}{imp})'


@final
@dataclass(frozen=True, slots=True)
class SupportsFunction:
    """selector(), font-tech(), font-format() or at-rule(). name is lowercased, and argument is the text
    inside the parentheses as written, without surrounding whitespace."""

    name: str
    argument: str

    def to_css(self) -> str:
        return f'{self.name}({self.argument})'


@final
@dataclass(frozen=True, slots=True)
class SupportsNot:
    condition: SupportsNode

    def to_css(self) -> str:
        return f'not {parenthesized(self.condition)}'


@final
@dataclass(frozen=True, slots=True)
class SupportsAnd:
    conditions: tuple[SupportsNode, ...]

    def to_css(self) -> str:
        return ' and '.join(parenthesized(c) for c in self.conditions)


@final
@dataclass(frozen=True, slots=True)
class SupportsOr:
    conditions: tuple[SupportsNode, ...]

    def to_css(self) -> str:
        return ' or '.join(parenthesized(c) for c in self.conditions)


type SupportsNode = SupportsNot | SupportsAnd | SupportsOr | SupportsDeclaration | SupportsFunction | GeneralEnclosed
type DeclarationCallback = Callable[[SupportsDeclaration], bool]
type FunctionCallback = Callable[[SupportsFunction], bool]


def parenthesized(c: SupportsNode) -> str:
    return f'({c.to_css()})' if isinstance(c, SupportsNot | SupportsAnd | SupportsOr) else c.to_css()


def walk_declarations(c: SupportsNode) -> Iterator[SupportsDeclaration]:
    match c:
        case SupportsDeclaration():
            yield c
        case SupportsNot():
            yield from walk_declarations(c.condition)
        case SupportsAnd() | SupportsOr():
            for x in c.conditions:
                yield from walk_declarations(x)


def default_function(f: SupportsFunction) -> bool:
    """selector() is supported when kgcss can parse its argument as one complex selector, so that Select
    can match it. kgcss knows nothing of fonts and at-rules, so the other functions are false."""
    if f.name != 'selector':
        return False
    try:
        return len(compile_selectors(f.argument)) == 1
    except SelectorSyntaxError:
        return False


def evaluate(c: SupportsNode, declaration: DeclarationCallback, function: FunctionCallback) -> bool:
    match c:
        case SupportsDeclaration():
            return bool(declaration(c))
        case SupportsFunction():
            return bool(function(c))
        case GeneralEnclosed():
            return False
        case SupportsNot():
            return not evaluate(c.condition, declaration, function)
        case SupportsAnd():
            return all(evaluate(x, declaration, function) for x in c.conditions)
        case SupportsOr():
            return any(evaluate(x, declaration, function) for x in c.conditions)


@final
@dataclass(frozen=True, slots=True)
class SupportsCondition:
    """The condition of an @supports rule, or of the supports() of an @import.

    text is the condition as written. A condition that does not parse has valid=False and an error
    that says why; it never matches. Otherwise, root is the parsed condition: SupportsNot, SupportsAnd
    or SupportsOr, which combine the others, SupportsDeclaration, SupportsFunction, or
    kgcss.media.GeneralEnclosed for anything else in parentheses, which is false."""

    text: str
    valid: bool = True
    error: str | None = None
    root: SupportsNode | None = None

    def declarations(self) -> Iterator[SupportsDeclaration]:
        """Every declaration of the condition, in order."""
        if self.root is not None:
            yield from walk_declarations(self.root)

    def matches(self, *, declaration: DeclarationCallback, function: FunctionCallback | None = None) -> bool:
        """Whether the condition is true, with declaration(d) deciding whether each declaration is
        supported, and function(f) the functions selector(), font-tech(), font-format() and at-rule().
        Without function, selector() is true when kgcss can parse its selector, and the others are false."""
        if self.root is None:
            return False
        return evaluate(self.root, declaration, function or default_function)

    def to_css(self) -> str:
        """The condition in canonical form: 'not all' for a condition that does not parse, which
        nothing supports, as a media query would be."""
        if self.root is None:
            return 'not all'
        return self.root.to_css()


class Malformed(ValueError):
    pass


class Parser:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.pos = 0

    def peek(self) -> Item | None:
        return self.items[self.pos] if self.pos < len(self.items) else None

    def next(self) -> Item:
        item = self.peek()
        if item is None:
            raise Malformed('unexpected end of the condition')
        self.pos += 1
        return item

    def at_end(self) -> bool:
        return self.pos >= len(self.items)

    def condition(self) -> SupportsNode:
        """<supports-condition>"""
        if keyword(self.peek()) == 'not':
            self.pos += 1
            return SupportsNot(self.in_parens())
        first = self.in_parens()
        op = keyword(self.peek())
        if op not in ('and', 'or'):
            return first
        conditions = [first]
        while (k := keyword(self.peek())) is not None and k in ('and', 'or'):
            if k != op:
                raise Malformed('and and or mixed without parentheses')
            self.pos += 1
            conditions.append(self.in_parens())
        return SupportsAnd(tuple(conditions)) if op == 'and' else SupportsOr(tuple(conditions))

    def in_parens(self) -> SupportsNode:
        """<supports-in-parens>"""
        item = self.next()
        if isinstance(item, SyntaxFunction):
            name = item.name.lower()
            if name in FUNCTIONS:
                return SupportsFunction(name, item.arguments.text.strip(' \t\n\r\f'))
            return GeneralEnclosed(item.source_text)
        if not isinstance(item, Block) or item.opener != '(':
            raise Malformed(f'expected parentheses, not {item.source_text}')
        content = significant(item.content)
        inner = Parser(content)
        try:
            ans = inner.condition()
            if inner.at_end():
                return ans
        except Malformed:
            pass
        if (d := supports_declaration(item.content.text)) is not None:
            return d
        return GeneralEnclosed(item.source_text)


def supports_declaration(text: str) -> SupportsDeclaration | None:
    """<supports-decl>, from the text inside its parentheses, or None if it is not one declaration."""
    decl = parse_one_declaration(text)
    if not isinstance(decl, Declaration) or significant(parse_component_value_list(text[decl.end :])):
        return None
    return SupportsDeclaration(normalize_name(decl.name), decl.value_text, decl.important)


@lru_cache(maxsize=1024)
def parse_supports_condition(text: str) -> SupportsCondition:
    """The parsed condition, the prelude of an @supports rule. Never raises."""
    items = significant(parse_component_value_list(text))
    p = Parser(items)
    try:
        if not items:
            raise Malformed('empty condition')
        root = p.condition()
        if not p.at_end():
            raise Malformed(f'unexpected {p.next().source_text}')
    except Malformed as e:
        return SupportsCondition(text, valid=False, error=str(e))
    return SupportsCondition(text, root=root)


def supports_matches(text: str, *, declaration: DeclarationCallback, function: FunctionCallback | None = None) -> bool:
    """Whether a condition is true, see SupportsCondition.matches(). A condition that does not parse is false."""
    return parse_supports_condition(text).matches(declaration=declaration, function=function)


def import_condition(text: str) -> SupportsCondition:
    """The condition of supports() in @import, which can also be a bare declaration: supports(display: grid)."""
    return replace(parse_supports_condition(f'({text})'), text=text)
