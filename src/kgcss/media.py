# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Media queries, as Media Queries Level 4 defines them.

parse_media_queries() parses a media query list, such as the prelude of @media, the
media of @import or the media attribute of <link>, into MediaQuery objects. It never
raises: a query that does not parse is kept, with valid=False, and matches nothing, as
the spec says. Results are cached by text.

>>> q, = parse_media_queries('only screen and (min-width: 600px)')
>>> q.media_type, q.only, [(f.name, f.value.css_text) for f in q.features()]
('screen', True, [('min-width', '600px')])
>>> parse_media_queries('(400px < width <= 700px)')[0].to_css()
'(width > 400px) and (width <= 700px)'
>>> parse_media_queries('screen and (a) or (b)')[0].valid
False

Evaluation follows the spec's three-valued logic. What is true, though, is up to the
caller: media_matches() and MediaQuery.matches() ask two callbacks, one for media types
and one for features. A feature callback returns True, False, or None for unknown.

>>> allowed = {'screen', 'print'}.__contains__
>>> media_matches('print and (color)', media_type=allowed, feature=lambda f: None)
False
>>> media_matches('print, not (color)', media_type=allowed, feature=lambda f: True)
True
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import ClassVar, Final, Literal, final

from kgcss.cssom.values import Component, Dimension, Ident, Number, _component
from kgcss.escaping import serialize_identifier
from kgcss.syntax import Block, Token, parse_component_value_list
from kgcss.syntax import Function as SyntaxFunction
from kgcss.tokenizer import TokenType

__all__ = [
    'GeneralEnclosed',
    'MediaAnd',
    'MediaCondition',
    'MediaFeature',
    'MediaNot',
    'MediaOr',
    'MediaQuery',
    'Ratio',
    'media_matches',
    'parse_media_queries',
]

type Item = Token | Block | SyntaxFunction
type Operator = Literal['', ':', '<', '<=', '>', '>=', '=']
type MediaTypeCallback = Callable[[str], bool]
type FeatureCallback = Callable[['MediaFeature'], bool | None]
# Three-valued logic: None is unknown
type Truth = bool | None

IGNORED: Final = frozenset((TokenType.WHITESPACE, TokenType.COMMENT))
# Words that cannot be media types
RESERVED: Final = frozenset(('only', 'not', 'and', 'or', 'layer'))
FLIPPED: Final[dict[Operator, Operator]] = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '=': '='}


@dataclass(slots=True, unsafe_hash=True)
class Ratio(Component):
    """A ratio such as 16/9, in a media feature."""

    numerator: int | float
    denominator: int | float
    type: ClassVar[str] = 'ratio'

    @property
    def value(self) -> tuple[int | float, int | float]:
        return self.numerator, self.denominator


type MediaValue = Number | Dimension | Ident | Ratio | Component


@final
@dataclass(frozen=True, slots=True)
class MediaFeature:
    """(name), (name: value) or (name <= value). name is lowercased. In a range, the name is always
    first: (600px <= width) is width >= 600px."""

    name: str
    operator: Operator = ''
    value: MediaValue | None = None

    def to_css(self) -> str:
        name = serialize_identifier(self.name)
        if self.value is None:
            return f'({name})'
        sep = ': ' if self.operator == ':' else f' {self.operator} '
        return f'({name}{sep}{self.value.css_text})'


@final
@dataclass(frozen=True, slots=True)
class GeneralEnclosed:
    """Parentheses or a function that are not a media feature or condition, such as (foo bar) or
    foo(1). They are valid, for future syntax, but evaluate to unknown."""

    text: str

    def to_css(self) -> str:
        return self.text


@final
@dataclass(frozen=True, slots=True)
class MediaNot:
    condition: MediaCondition

    def to_css(self) -> str:
        return f'not {parenthesized(self.condition)}'


@final
@dataclass(frozen=True, slots=True)
class MediaAnd:
    conditions: tuple[MediaCondition, ...]

    def to_css(self) -> str:
        return ' and '.join(parenthesized(c) for c in self.conditions)


@final
@dataclass(frozen=True, slots=True)
class MediaOr:
    conditions: tuple[MediaCondition, ...]

    def to_css(self) -> str:
        return ' or '.join(parenthesized(c) for c in self.conditions)


type MediaCondition = MediaNot | MediaAnd | MediaOr | MediaFeature | GeneralEnclosed


def parenthesized(c: MediaCondition) -> str:
    return f'({c.to_css()})' if isinstance(c, MediaNot | MediaAnd | MediaOr) else c.to_css()


def walk_features(c: MediaCondition) -> Iterator[MediaFeature]:
    match c:
        case MediaFeature():
            yield c
        case MediaNot():
            yield from walk_features(c.condition)
        case MediaAnd() | MediaOr():
            for x in c.conditions:
                yield from walk_features(x)


def evaluate(c: MediaCondition, feature: FeatureCallback) -> Truth:
    match c:
        case MediaFeature():
            return feature(c)
        case GeneralEnclosed():
            return None
        case MediaNot():
            v = evaluate(c.condition, feature)
            return None if v is None else not v
        case MediaAnd():
            ans: Truth = True
            for x in c.conditions:
                v = evaluate(x, feature)
                if v is False:
                    return False
                if v is None:
                    ans = None
            return ans
        case MediaOr():
            ans = False
            for x in c.conditions:
                v = evaluate(x, feature)
                if v is True:
                    return True
                if v is None:
                    ans = None
            return ans


@final
@dataclass(frozen=True, slots=True)
class MediaQuery:
    """One query of a media query list.

    text is the query as written. A query that does not parse has valid=False and an error that
    says why; it never matches. Otherwise, media_type is lowercased and is 'all' when there is none,
    negated and only are the not and only before it, and condition is what follows it, or the whole
    query when there is no media type."""

    text: str
    valid: bool = True
    error: str | None = None
    negated: bool = False
    only: bool = False
    media_type: str = 'all'
    condition: MediaCondition | None = field(default=None)

    def features(self) -> Iterator[MediaFeature]:
        """Every media feature of the condition, in order."""
        if self.condition is not None:
            yield from walk_features(self.condition)

    def matches(self, *, media_type: MediaTypeCallback, feature: FeatureCallback) -> bool:
        """Whether the query matches, with media_type(name) deciding media types other than all and
        feature(f) features: True, False or None for unknown. Unknown is false in the end."""
        if not self.valid:
            return False
        ans: Truth = self.media_type == 'all' or media_type(self.media_type)
        if ans and self.condition is not None:
            ans = evaluate(self.condition, feature)
        if self.negated and ans is not None:
            ans = not ans
        return ans is True

    def to_css(self) -> str:
        """The query in canonical form: 'not all' for a query that does not parse."""
        if not self.valid:
            return 'not all'
        prefix = 'not ' if self.negated else 'only ' if self.only else ''
        media_type = serialize_identifier(self.media_type)
        if self.condition is None:
            return prefix + media_type
        if not prefix and self.media_type == 'all':
            return self.condition.to_css()
        return f'{prefix}{media_type} and {parenthesized_without_or(self.condition)}'


def parenthesized_without_or(c: MediaCondition) -> str:
    # After a media type, and, or and not need parentheses only for or
    return f'({c.to_css()})' if isinstance(c, MediaOr) else c.to_css()


class Malformed(ValueError):
    pass


def keyword(item: Item | None) -> str | None:
    """The lowercased name of an ident token, or None."""
    if isinstance(item, Token) and item.type is TokenType.IDENT:
        return str(item.value).lower()
    return None


def significant(items: Iterable[Item]) -> list[Item]:
    return [x for x in items if not (isinstance(x, Token) and x.type in IGNORED)]


class Parser:
    def __init__(self, items: list[Item]) -> None:
        self.items = items
        self.pos = 0

    def peek(self) -> Item | None:
        return self.items[self.pos] if self.pos < len(self.items) else None

    def next(self) -> Item:
        item = self.peek()
        if item is None:
            raise Malformed('unexpected end of the query')
        self.pos += 1
        return item

    def at_end(self) -> bool:
        return self.pos >= len(self.items)

    def condition(self, allow_or: bool) -> MediaCondition:
        """<media-condition>, or <media-condition-without-or>"""
        if keyword(self.peek()) == 'not':
            self.pos += 1
            return MediaNot(self.in_parens())
        first = self.in_parens()
        op = keyword(self.peek())
        if op not in ('and', 'or'):
            return first
        if op == 'or' and not allow_or:
            raise Malformed('or after a media type')
        conditions = [first]
        while (k := keyword(self.peek())) is not None and k in ('and', 'or'):
            if k != op:
                raise Malformed('and and or mixed without parentheses')
            self.pos += 1
            conditions.append(self.in_parens())
        return MediaAnd(tuple(conditions)) if op == 'and' else MediaOr(tuple(conditions))

    def in_parens(self) -> MediaCondition:
        """<media-in-parens>"""
        item = self.next()
        if isinstance(item, SyntaxFunction):
            return GeneralEnclosed(item.source_text)
        if not isinstance(item, Block) or item.opener != '(':
            raise Malformed(f'expected parentheses, not {item.source_text}')
        content = significant(item.content)
        inner = Parser(content)
        try:
            ans = inner.condition(allow_or=True)
            if inner.at_end():
                return ans
        except Malformed:
            pass
        if (f := media_feature(content)) is not None:
            return f
        return GeneralEnclosed(item.source_text)


def comparison(items: list[Item], i: int) -> tuple[Operator, int] | None:
    """The comparison operator at items[i] and the index after it, or None. <= and >= must not have
    whitespace inside, which significant() removed, so it is checked on positions."""
    item = items[i]
    if not isinstance(item, Token) or item.type is not TokenType.DELIM:
        return None
    ch = item.source_text
    if ch == '=':
        return '=', i + 1
    if ch not in ('<', '>'):
        return None
    if i + 1 < len(items):
        nxt = items[i + 1]
        if isinstance(nxt, Token) and nxt.is_delim('=') and nxt.pos == item.end:
            return ('<=' if ch == '<' else '>='), i + 2
    return ('<' if ch == '<' else '>'), i + 1


def media_value(items: list[Item]) -> MediaValue | None:
    """<mf-value>: a number, dimension, ident or ratio. A function such as calc() is kept as is."""
    if len(items) == 1:
        item = items[0]
        if isinstance(item, SyntaxFunction):
            # Left open by the end of the input, it could not be written back as part of a feature
            return _component(item) if item.closed else None
        if isinstance(item, Token) and item.type in (TokenType.NUMBER, TokenType.DIMENSION, TokenType.IDENT):
            return _component(item)
        return None
    if len(items) == 3:
        a, slash, b = items
        if (
            isinstance(a, Token)
            and isinstance(b, Token)
            and a.type is TokenType.NUMBER
            and b.type is TokenType.NUMBER
            and slash.source_text == '/'
        ):
            x, y = a.value, b.value
            assert isinstance(x, int | float)
            assert isinstance(y, int | float)
            if x >= 0 and y >= 0:
                text = a.stream.source[a.pos : b.end]
                return Ratio(text, x, y)
    return None


def feature_name(items: list[Item]) -> str | None:
    if len(items) == 1:
        return keyword(items[0])
    return None


def media_feature(items: list[Item]) -> MediaCondition | None:
    """<media-feature>, from the content of its parentheses: a feature, two features joined by and
    for a double range, or None if it is not one."""
    if not items:
        return None
    if len(items) == 1:
        name = keyword(items[0])
        return None if name is None else MediaFeature(name)
    if isinstance(items[1], Token) and items[1].type is TokenType.COLON:
        name = keyword(items[0])
        value = media_value(items[2:])
        return None if name is None or value is None else MediaFeature(name, ':', value)
    # A range: split on the comparison operators
    segments: list[list[Item]] = [[]]
    ops: list[Operator] = []
    i = 0
    while i < len(items):
        c = comparison(items, i)
        if c is None:
            segments[-1].append(items[i])
            i += 1
        else:
            ops.append(c[0])
            segments.append([])
            i = c[1]
    if any(not s for s in segments):
        return None
    if len(ops) == 1:
        (op,) = ops
        if (name := feature_name(segments[0])) is not None and (value := media_value(segments[1])) is not None:
            return MediaFeature(name, op, value)
        if (name := feature_name(segments[1])) is not None and (value := media_value(segments[0])) is not None:
            return MediaFeature(name, FLIPPED[op], value)
        return None
    if len(ops) == 2:
        lt, gt = all(op in ('<', '<=') for op in ops), all(op in ('>', '>=') for op in ops)
        name = feature_name(segments[1])
        low, high = media_value(segments[0]), media_value(segments[2])
        if (lt or gt) and name is not None and low is not None and high is not None:
            return MediaAnd((MediaFeature(name, FLIPPED[ops[0]], low), MediaFeature(name, ops[1], high)))
    return None


def parse_query(items: list[Item], text: str) -> MediaQuery:
    p = Parser(items)
    try:
        if not items:
            raise Malformed('empty media query')
        first = keyword(p.peek())
        second = keyword(items[1]) if len(items) > 1 else None
        if first is None or (first == 'not' and second is None):
            # <media-condition>
            whole = p.condition(allow_or=True)
            if not p.at_end():
                raise Malformed(f'unexpected {p.next().source_text}')
            return MediaQuery(text, condition=whole)
        negated = only = False
        if first in ('not', 'only'):
            negated, only = first == 'not', first == 'only'
            p.pos += 1
            if second is None:
                raise Malformed(f'expected a media type after {first}')
        media_type = keyword(p.next())
        assert media_type is not None
        if media_type in RESERVED:
            raise Malformed(f'{media_type} is not a media type')
        condition: MediaCondition | None = None
        if not p.at_end():
            if keyword(p.next()) != 'and':
                raise Malformed('expected and after the media type')
            condition = p.condition(allow_or=False)
            if not p.at_end():
                raise Malformed(f'unexpected {p.next().source_text}')
        return MediaQuery(text, negated=negated, only=only, media_type=media_type, condition=condition)
    except Malformed as e:
        return MediaQuery(text, valid=False, error=str(e))


@lru_cache(maxsize=1024)
def parse_media_queries(text: str) -> tuple[MediaQuery, ...]:
    """The queries of a media query list. Never raises. An empty list, '' or only whitespace and
    comments, has no queries, and matches."""
    values = parse_component_value_list(text)
    parts: list[list[Item]] = [[]]
    for item in values:
        if isinstance(item, Token) and item.type is TokenType.COMMA:
            parts.append([])
        else:
            parts[-1].append(item)
    queries = [significant(part) for part in parts]
    if len(queries) == 1 and not queries[0]:
        return ()
    return tuple(parse_query(q, text[q[0].pos : q[-1].end] if q else '') for q in queries)


def media_matches(text: str, *, media_type: MediaTypeCallback, feature: FeatureCallback) -> bool:
    """Whether a media query list matches: any of its queries, or True if it is empty."""
    queries = parse_media_queries(text)
    return not queries or any(q.matches(media_type=media_type, feature=feature) for q in queries)
