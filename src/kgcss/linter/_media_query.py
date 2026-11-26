# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A port of @csstools/media-query-list-parser 5.0.3, which stylelint's media rules use.

parse() and parse_from_tokens() give a list of media queries: with a type
(MediaQueryWithType), without one (MediaQueryWithoutType) or, with
preserve_invalid_media_queries, the component values of an invalid one
(MediaQueryInvalid). The nodes keep all their tokens, so that to_string()
gives back the text, and walk() visits them as the JavaScript does.

JavaScript behaviours kept here, which rule ports must know about:

- Parsing normalizes the blocks it reads as media-in-parens: a block that the
  input ended in gets a ')' end token, with offsets -1, which is then in
  its tokens() and to_string() (also those of an invalid query).
- In a query with a modifier but no type ('only', 'not'), the media type
  tokens are all the tokens of the query, the modifier included.
- 'and', 'or' and 'not' only match in ASCII case-insensitively.
"""

import copy
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Final, Literal, TypeIs

from ._csstools import (
    ComponentValue,
    ContainerNode,
    CSSToken,
    FunctionNode,
    ListParent,
    OnParseError,
    SimpleBlockNode,
    TokenNode,
    TokenType,
    ValueData,
    WalkEntry,
    clone_tokens,
    is_comment_node,
    is_token_delim,
    is_token_eof,
    is_token_ident,
    is_token_open_paren,
    is_token_white_space_or_comment,
    is_white_space_or_comment_node,
    is_whitespace_node,
    mutate_ident,
    parse_comma_separated_list_of_component_values,
    parse_list_of_component_values,
    stringify,
    tokenize,
    walker_index_generator,
)

type Tokens = list[CSSToken | None]

MAX_MEDIA_QUERY_COMPONENT_VALUES: Final = 30_000


class MediaQueryTooComplexError(Exception):
    """Raised, as the JavaScript throws, for a media query of too many component values."""


class NodeType(StrEnum):
    """The type of a media query node."""

    CUSTOM_MEDIA = 'custom-media'
    GENERAL_ENCLOSED = 'general-enclosed'
    MEDIA_AND = 'media-and'
    MEDIA_CONDITION = 'media-condition'
    MEDIA_CONDITION_LIST_WITH_AND = 'media-condition-list-and'
    MEDIA_CONDITION_LIST_WITH_OR = 'media-condition-list-or'
    MEDIA_FEATURE = 'media-feature'
    MEDIA_FEATURE_BOOLEAN = 'mf-boolean'
    MEDIA_FEATURE_NAME = 'mf-name'
    MEDIA_FEATURE_PLAIN = 'mf-plain'
    MEDIA_FEATURE_RANGE_NAME_VALUE = 'mf-range-name-value'
    MEDIA_FEATURE_RANGE_VALUE_NAME = 'mf-range-value-name'
    MEDIA_FEATURE_RANGE_VALUE_NAME_VALUE = 'mf-range-value-name-value'
    MEDIA_FEATURE_VALUE = 'mf-value'
    MEDIA_IN_PARENS = 'media-in-parens'
    MEDIA_NOT = 'media-not'
    MEDIA_OR = 'media-or'
    MEDIA_QUERY_WITH_TYPE = 'media-query-with-type'
    MEDIA_QUERY_WITHOUT_TYPE = 'media-query-without-type'
    MEDIA_QUERY_INVALID = 'media-query-invalid'


class MediaFeatureComparison(StrEnum):
    """A comparison of a range feature (MediaFeatureLT, MediaFeatureGT and MediaFeatureEQ in JavaScript)."""

    LT = '<'
    LT_OR_EQ = '<='
    GT = '>'
    GT_OR_EQ = '>='
    EQ = '='


class MediaQueryModifier(StrEnum):
    NOT = 'not'
    ONLY = 'only'


class MediaType(StrEnum):
    ALL = 'all'
    PRINT = 'print'
    SCREEN = 'screen'
    TTY = 'tty'
    TV = 'tv'
    PROJECTION = 'projection'
    HANDHELD = 'handheld'
    BRAILLE = 'braille'
    EMBOSSED = 'embossed'
    AURAL = 'aural'
    SPEECH = 'speech'


def _flat(values: list[ComponentValue]) -> Tokens:
    return [t for v in values for t in v.tokens()]


def _ident_value(token: CSSToken | None) -> str:
    assert token is not None
    assert isinstance(token.data, ValueData)
    return token.data.value


def _ascii_lower(text: str) -> str:
    """What a JavaScript regular expression with the i flag (and no u flag) compares with."""
    return text.lower() if text.isascii() else ''.join(c.lower() if c.isascii() else c for c in text)


def _is_ident(cv: object) -> bool:
    return isinstance(cv, TokenNode) and cv.value.type is TokenType.IDENT


_MATH_FUNCTIONS: Final = frozenset({
    'abs', 'acos', 'asin', 'atan', 'atan2', 'calc', 'calc-mix', 'clamp', 'cos', 'exp', 'hypot', 'log', 'max', 'min',
    'mod', 'pow', 'rem', 'round', 'sign', 'sin', 'sqrt', 'tan',
})  # fmt: skip


def _is_number(cv: object) -> bool:
    if isinstance(cv, TokenNode):
        return cv.value.type is TokenType.NUMBER
    return isinstance(cv, FunctionNode) and cv.get_name().lower() in _MATH_FUNCTIONS


def _is_dimension(cv: object) -> bool:
    return isinstance(cv, TokenNode) and cv.value.type is TokenType.DIMENSION


def _is_environment_variable(cv: object) -> bool:
    return isinstance(cv, FunctionNode) and cv.get_name().lower() == 'env'


def _copy_state[S: Mapping[str, object]](state: S | None) -> S | None:
    return None if state is None else copy.copy(state)


# Nodes {{{


class MediaFeatureName:
    """The name of a media feature: an ident token node, and the tokens around it."""

    __slots__ = ('after', 'before', 'name')
    type: Final = NodeType.MEDIA_FEATURE_NAME

    def __init__(self, name: ComponentValue, before: Tokens | None = None, after: Tokens | None = None) -> None:
        self.name = name
        self.before: Tokens = [] if before is None else before
        self.after: Tokens = [] if after is None else after

    def get_name(self) -> str:
        assert isinstance(self.name, TokenNode)
        return _ident_value(self.name.value)

    def get_name_token(self) -> CSSToken:
        assert isinstance(self.name, TokenNode)
        return self.name.value

    def tokens(self) -> Tokens:
        return [*self.before, *self.name.tokens(), *self.after]

    def to_string(self) -> str:
        return stringify(self.before) + self.name.to_string() + stringify(self.after)

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'name' if item is self.name else -1

    def at(self, index: int | str) -> ComponentValue | None:
        return self.name if index == 'name' else None


class MediaFeatureValue:
    """The value of a media feature: a component value or a list of them (a ratio)."""

    __slots__ = ('after', 'before', 'value')
    type: Final = NodeType.MEDIA_FEATURE_VALUE

    def __init__(
        self, value: ComponentValue | list[ComponentValue], before: Tokens | None = None, after: Tokens | None = None
    ) -> None:
        self.value: ComponentValue | list[ComponentValue] = (
            value[0] if isinstance(value, list) and len(value) == 1 else value
        )
        self.before: Tokens = [] if before is None else before
        self.after: Tokens = [] if after is None else after

    def tokens(self) -> Tokens:
        if isinstance(self.value, list):
            return [*self.before, *_flat(self.value), *self.after]
        return [*self.before, *self.value.tokens(), *self.after]

    def to_string(self) -> str:
        middle = ''.join(x.to_string() for x in self.value) if isinstance(self.value, list) else self.value.to_string()
        return stringify(self.before) + middle + stringify(self.after)

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'value' if item is self.value else -1

    def at(self, index: int | str) -> ComponentValue | list[ComponentValue] | None:
        if index == 'value':
            return self.value
        if isinstance(self.value, list) and isinstance(index, int):
            if index < 0:
                index += len(self.value)
            return self.value[index] if 0 <= index < len(self.value) else None
        return None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        if isinstance(self.value, list):
            values = self.value
            if not values:
                return None
            gen = walker_index_generator(values)
            index = 0
            while index < len(self.value):
                child = self.value[index]
                clone = _copy_state(state)
                if cb(WalkEntry(child, self, clone), index) is False:
                    return False
                if isinstance(child, (FunctionNode, SimpleBlockNode)) and any(x is child for x in self.value):
                    child.walk(cb, clone)
                index = gen(self.value, child, index)
                if index == -1:
                    break
            return None
        clone = _copy_state(state)
        if cb(WalkEntry(self.value, self, clone), 'value') is False:
            return False
        if isinstance(self.value, (FunctionNode, SimpleBlockNode)):
            self.value.walk(cb, clone)
        return None


class MediaFeatureBoolean:
    """A media feature without a value: (name)."""

    __slots__ = ('name',)
    type: Final = NodeType.MEDIA_FEATURE_BOOLEAN

    def __init__(self, name: MediaFeatureName) -> None:
        self.name = name

    def get_name(self) -> str:
        return self.name.get_name()

    def get_name_token(self) -> CSSToken:
        return self.name.get_name_token()

    def tokens(self) -> Tokens:
        return self.name.tokens()

    def to_string(self) -> str:
        return self.name.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'name' if item is self.name else -1

    def at(self, index: int | str) -> MediaFeatureName | None:
        return self.name if index == 'name' else None


class MediaFeaturePlain:
    """A media feature with a value: (name: value)."""

    __slots__ = ('colon', 'name', 'value')
    type: Final = NodeType.MEDIA_FEATURE_PLAIN

    def __init__(self, name: MediaFeatureName, colon: CSSToken, value: MediaFeatureValue) -> None:
        self.name = name
        self.colon = colon
        self.value = value

    def get_name(self) -> str:
        return self.name.get_name()

    def get_name_token(self) -> CSSToken:
        return self.name.get_name_token()

    def tokens(self) -> Tokens:
        return [*self.name.tokens(), self.colon, *self.value.tokens()]

    def to_string(self) -> str:
        return self.name.to_string() + self.colon.raw + self.value.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        if item is self.name:
            return 'name'
        return 'value' if item is self.value else -1

    def at(self, index: int | str) -> MediaFeatureName | MediaFeatureValue | None:
        if index == 'name':
            return self.name
        return self.value if index == 'value' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.value, self, clone), 'value') is False:
            return False
        return self.value.walk(cb, clone)


type Operator = list[CSSToken]


def comparison_from_tokens(tokens: Operator) -> MediaFeatureComparison | Literal[False]:
    """The comparison that one or two delim tokens make."""
    if len(tokens) not in (1, 2) or not is_token_delim(tokens[0]):
        return False
    first = _ident_value(tokens[0])
    if len(tokens) == 1:
        match first:
            case '=':
                return MediaFeatureComparison.EQ
            case '<':
                return MediaFeatureComparison.LT
            case '>':
                return MediaFeatureComparison.GT
        return False
    if not is_token_delim(tokens[1]) or _ident_value(tokens[1]) != '=':
        return False
    match first:
        case '<':
            return MediaFeatureComparison.LT_OR_EQ
        case '>':
            return MediaFeatureComparison.GT_OR_EQ
    return False


def invert_comparison(operator: MediaFeatureComparison) -> MediaFeatureComparison | Literal[False]:
    """The comparison with the operands swapped."""
    return _INVERTED.get(operator, False)


_INVERTED: Final = {
    MediaFeatureComparison.EQ: MediaFeatureComparison.EQ,
    MediaFeatureComparison.LT: MediaFeatureComparison.GT,
    MediaFeatureComparison.LT_OR_EQ: MediaFeatureComparison.GT_OR_EQ,
    MediaFeatureComparison.GT: MediaFeatureComparison.LT,
    MediaFeatureComparison.GT_OR_EQ: MediaFeatureComparison.LT_OR_EQ,
}


def matches_comparison(component_values: list[ComponentValue]) -> tuple[int, int] | Literal[False]:
    """The indexes of the first and last delim of the comparison that component_values start with."""
    first = -1
    for i, cv in enumerate(component_values):
        if isinstance(cv, TokenNode) and is_token_delim(cv.value):
            value = _ident_value(cv.value)
            if value == '=':
                return (first, i) if first != -1 else (i, i)
            if value in ('<', '>'):
                first = i
                continue
        break
    return (first, first) if first != -1 else False


class MediaFeatureRangeNameValue:
    """A range feature: (name < value)."""

    __slots__ = ('name', 'operator', 'value')
    type: Final = NodeType.MEDIA_FEATURE_RANGE_NAME_VALUE

    def __init__(self, name: MediaFeatureName, operator: Operator, value: MediaFeatureValue) -> None:
        self.name = name
        self.operator = operator
        self.value = value

    def operator_kind(self) -> MediaFeatureComparison | Literal[False]:
        return comparison_from_tokens(self.operator)

    def get_name(self) -> str:
        return self.name.get_name()

    def get_name_token(self) -> CSSToken:
        return self.name.get_name_token()

    def tokens(self) -> Tokens:
        return [*self.name.tokens(), *self.operator, *self.value.tokens()]

    def to_string(self) -> str:
        return self.name.to_string() + stringify(self.operator) + self.value.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        if item is self.name:
            return 'name'
        return 'value' if item is self.value else -1

    def at(self, index: int | str) -> MediaFeatureName | MediaFeatureValue | None:
        if index == 'name':
            return self.name
        return self.value if index == 'value' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.value, self, clone), 'value') is False:
            return False
        return self.value.walk(cb, clone)


class MediaFeatureRangeValueName:
    """A range feature: (value < name)."""

    __slots__ = ('name', 'operator', 'value')
    type: Final = NodeType.MEDIA_FEATURE_RANGE_VALUE_NAME

    def __init__(self, name: MediaFeatureName, operator: Operator, value: MediaFeatureValue) -> None:
        self.name = name
        self.operator = operator
        self.value = value

    def operator_kind(self) -> MediaFeatureComparison | Literal[False]:
        return comparison_from_tokens(self.operator)

    def get_name(self) -> str:
        return self.name.get_name()

    def get_name_token(self) -> CSSToken:
        return self.name.get_name_token()

    def tokens(self) -> Tokens:
        return [*self.value.tokens(), *self.operator, *self.name.tokens()]

    def to_string(self) -> str:
        return self.value.to_string() + stringify(self.operator) + self.name.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        if item is self.name:
            return 'name'
        return 'value' if item is self.value else -1

    def at(self, index: int | str) -> MediaFeatureName | MediaFeatureValue | None:
        if index == 'name':
            return self.name
        return self.value if index == 'value' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.value, self, clone), 'value') is False:
            return False
        return self.value.walk(cb, clone)


class MediaFeatureRangeValueNameValue:
    """A range feature: (value < name < value)."""

    __slots__ = ('name', 'value_one', 'value_one_operator', 'value_two', 'value_two_operator')
    type: Final = NodeType.MEDIA_FEATURE_RANGE_VALUE_NAME_VALUE

    def __init__(
        self,
        name: MediaFeatureName,
        value_one: MediaFeatureValue,
        value_one_operator: Operator,
        value_two: MediaFeatureValue,
        value_two_operator: Operator,
    ) -> None:
        self.name = name
        self.value_one = value_one
        self.value_one_operator = value_one_operator
        self.value_two = value_two
        self.value_two_operator = value_two_operator

    def value_one_operator_kind(self) -> MediaFeatureComparison | Literal[False]:
        return comparison_from_tokens(self.value_one_operator)

    def value_two_operator_kind(self) -> MediaFeatureComparison | Literal[False]:
        return comparison_from_tokens(self.value_two_operator)

    def get_name(self) -> str:
        return self.name.get_name()

    def get_name_token(self) -> CSSToken:
        return self.name.get_name_token()

    def tokens(self) -> Tokens:
        return [
            *self.value_one.tokens(),
            *self.value_one_operator,
            *self.name.tokens(),
            *self.value_two_operator,
            *self.value_two.tokens(),
        ]

    def to_string(self) -> str:
        return (
            self.value_one.to_string()
            + stringify(self.value_one_operator)
            + self.name.to_string()
            + stringify(self.value_two_operator)
            + self.value_two.to_string()
        )

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        if item is self.name:
            return 'name'
        if item is self.value_one:
            return 'valueOne'
        return 'valueTwo' if item is self.value_two else -1

    def at(self, index: int | str) -> MediaFeatureName | MediaFeatureValue | None:
        if index == 'name':
            return self.name
        if index == 'valueOne':
            return self.value_one
        return self.value_two if index == 'valueTwo' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.value_one, self, clone), 'valueOne') is False:
            return False
        if self.value_one.walk(cb, clone) is False:
            return False
        clone = _copy_state(state)
        if cb(WalkEntry(self.value_two, self, clone), 'valueTwo') is False:
            return False
        if self.value_two.walk(cb, clone) is False:
            return False
        return None


type MediaFeatureRange = MediaFeatureRangeNameValue | MediaFeatureRangeValueName | MediaFeatureRangeValueNameValue


class MediaFeature:
    """A media feature in parentheses: before is the tokens up to and with '(', after from ')'."""

    __slots__ = ('after', 'before', 'feature')
    type: Final = NodeType.MEDIA_FEATURE

    def __init__(
        self,
        feature: MediaFeaturePlain | MediaFeatureBoolean | MediaFeatureRange,
        before: Tokens | None = None,
        after: Tokens | None = None,
    ) -> None:
        self.feature = feature
        self.before: Tokens = [] if before is None else before
        self.after: Tokens = [] if after is None else after

    def get_name(self) -> str:
        """The (unescaped) name of the feature."""
        return self.feature.get_name()

    def get_name_token(self) -> CSSToken:
        """The ident token of the name of the feature."""
        return self.feature.get_name_token()

    def tokens(self) -> Tokens:
        return [*self.before, *self.feature.tokens(), *self.after]

    def to_string(self) -> str:
        return stringify(self.before) + self.feature.to_string() + stringify(self.after)

    def __str__(self) -> str:
        return self.to_string()

    def has_leading_space(self) -> bool:
        return is_token_white_space_or_comment(self.before[0] if self.before else None)

    def index_of(self, item: object) -> int | str:
        return 'feature' if item is self.feature else -1

    def at(self, index: int | str) -> MediaFeaturePlain | MediaFeatureBoolean | MediaFeatureRange | None:
        return self.feature if index == 'feature' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.feature, self, clone), 'feature') is False:
            return False
        if isinstance(self.feature, MediaFeatureBoolean):
            return None
        return self.feature.walk(cb, clone)


class GeneralEnclosed:
    """A block that is not a media feature or condition: <general-enclosed>."""

    __slots__ = ('value',)
    type: Final = NodeType.GENERAL_ENCLOSED

    def __init__(self, value: ComponentValue) -> None:
        self.value = value

    def tokens(self) -> Tokens:
        return self.value.tokens()

    def to_string(self) -> str:
        return self.value.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def has_leading_space(self) -> bool:
        tokens = self.value.tokens()
        return is_token_white_space_or_comment(tokens[0] if tokens else None)

    def index_of(self, item: object) -> int | str:
        return 'value' if item is self.value else -1

    def at(self, index: int | str) -> ComponentValue | None:
        return self.value if index == 'value' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.value, self, clone), 'value') is False:
            return False
        if isinstance(self.value, (FunctionNode, SimpleBlockNode)):
            self.value.walk(cb, clone)
        return None


class MediaInParens:
    """A media feature, condition or general-enclosed in parentheses."""

    __slots__ = ('after', 'before', 'media')
    type: Final = NodeType.MEDIA_IN_PARENS

    def __init__(
        self,
        media: MediaCondition | MediaFeature | GeneralEnclosed,
        before: Tokens | None = None,
        after: Tokens | None = None,
    ) -> None:
        self.media = media
        self.before: Tokens = [] if before is None else before
        self.after: Tokens = [] if after is None else after

    def tokens(self) -> Tokens:
        return [*self.before, *self.media.tokens(), *self.after]

    def to_string(self) -> str:
        return stringify(self.before) + self.media.to_string() + stringify(self.after)

    def __str__(self) -> str:
        return self.to_string()

    def has_leading_space(self) -> bool:
        if not self.before:
            return self.media.has_leading_space()
        return is_token_white_space_or_comment(self.before[0])

    def index_of(self, item: object) -> int | str:
        return 'media' if item is self.media else -1

    def at(self, index: int | str) -> MediaCondition | MediaFeature | GeneralEnclosed | None:
        return self.media if index == 'media' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.media, self, clone), 'media') is False:
            return False
        return self.media.walk(cb, clone)


class _MediaModifierNode:
    """MediaNot, MediaAnd and MediaOr: a keyword (in modifier, with the whitespace around it) and a MediaInParens."""

    __slots__ = ('media', 'modifier')

    def __init__(self, modifier: Tokens, media: MediaInParens) -> None:
        self.modifier = modifier
        self.media = media

    def tokens(self) -> Tokens:
        return [*self.modifier, *self.media.tokens()]

    def to_string(self) -> str:
        return stringify(self.modifier) + self.media.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'media' if item is self.media else -1

    def at(self, index: int | str) -> MediaInParens | None:
        return self.media if index == 'media' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        assert isinstance(self, (MediaNot, MediaAnd, MediaOr))
        if cb(WalkEntry(self.media, self, clone), 'media') is False:
            return False
        return self.media.walk(cb, clone)


class MediaNot(_MediaModifierNode):
    """not (...)"""

    __slots__ = ()
    type: Final = NodeType.MEDIA_NOT

    def has_leading_space(self) -> bool:
        if not self.modifier:
            return self.media.has_leading_space()
        return is_token_white_space_or_comment(self.modifier[0])


class MediaAnd(_MediaModifierNode):
    """and (...)"""

    __slots__ = ()
    type: Final = NodeType.MEDIA_AND

    def has_leading_space(self) -> bool:
        return is_token_white_space_or_comment(self.modifier[0] if self.modifier else None)


class MediaOr(_MediaModifierNode):
    """or (...)"""

    __slots__ = ()
    type: Final = NodeType.MEDIA_OR


class _MediaConditionList[T: (MediaAnd, MediaOr)]:
    """A media-in-parens followed by 'and' or 'or' parts."""

    __slots__ = ('after', 'before', 'leading', 'list')

    def __init__(
        self, leading: MediaInParens, items: list[T], before: Tokens | None = None, after: Tokens | None = None
    ) -> None:
        self.leading = leading
        self.list = items
        self.before: Tokens = [] if before is None else before
        self.after: Tokens = [] if after is None else after

    def tokens(self) -> Tokens:
        return [*self.before, *self.leading.tokens(), *(t for item in self.list for t in item.tokens()), *self.after]

    def to_string(self) -> str:
        return (
            stringify(self.before)
            + self.leading.to_string()
            + ''.join(item.to_string() for item in self.list)
            + stringify(self.after)
        )

    def __str__(self) -> str:
        return self.to_string()

    def has_leading_space(self) -> bool:
        if not self.before:
            return self.leading.has_leading_space()
        return is_token_white_space_or_comment(self.before[0])

    def index_of(self, item: object) -> int | str:
        if item is self.leading:
            return 'leading'
        if isinstance(item, (MediaAnd, MediaOr)) and item.type is self._item_type:
            for i, x in enumerate(self.list):
                if x is item:
                    return i
        return -1

    def at(self, index: int | str) -> MediaInParens | T | None:
        if index == 'leading':
            return self.leading
        if isinstance(index, int):
            if index < 0:
                index += len(self.list)
            return self.list[index] if 0 <= index < len(self.list) else None
        return None

    @property
    def _item_type(self) -> NodeType:
        return NodeType.MEDIA_AND if isinstance(self, MediaConditionListWithAnd) else NodeType.MEDIA_OR

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        assert isinstance(self, (MediaConditionListWithAnd, MediaConditionListWithOr))
        clone = _copy_state(state)
        if cb(WalkEntry(self.leading, self, clone), 'leading') is False:
            return False
        if self.leading.walk(cb, clone) is False:
            return False
        if not self.list:
            return None
        gen = walker_index_generator(self.list)
        index = 0
        while index < len(self.list):
            child = self.list[index]
            if state is not None:
                clone = _copy_state(state)
            if cb(WalkEntry(child, self, clone), index) is False:
                return False
            if any(x is child for x in self.list) and child.walk(cb, clone) is False:
                return False
            index = gen(self.list, child, index)
            if index == -1:
                break
        return None


class MediaConditionListWithAnd(_MediaConditionList[MediaAnd]):
    __slots__ = ()
    type: Final = NodeType.MEDIA_CONDITION_LIST_WITH_AND


class MediaConditionListWithOr(_MediaConditionList[MediaOr]):
    __slots__ = ()
    type: Final = NodeType.MEDIA_CONDITION_LIST_WITH_OR


type MediaConditionList = MediaConditionListWithAnd | MediaConditionListWithOr


class MediaCondition:
    """A media condition: a not, a media-in-parens or a list with and/or."""

    __slots__ = ('media',)
    type: Final = NodeType.MEDIA_CONDITION

    def __init__(self, media: MediaNot | MediaInParens | MediaConditionList) -> None:
        self.media = media

    def tokens(self) -> Tokens:
        return self.media.tokens()

    def to_string(self) -> str:
        return self.media.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def has_leading_space(self) -> bool:
        return self.media.has_leading_space()

    def index_of(self, item: object) -> int | str:
        return 'media' if item is self.media else -1

    def at(self, index: int | str) -> MediaNot | MediaInParens | MediaConditionList | None:
        return self.media if index == 'media' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.media, self, clone), 'media') is False:
            return False
        return self.media.walk(cb, clone)


def _ident_token(value: str) -> CSSToken:
    return CSSToken(TokenType.IDENT, value, -1, -1, ValueData(value))


def _space() -> CSSToken:
    return CSSToken(TokenType.WHITESPACE, ' ', -1, -1)


class MediaQueryWithType:
    """A media query with a media type: modifier tokens, type tokens, and the 'and' tokens and condition."""

    __slots__ = ('and_', 'media', 'media_type', 'modifier')
    type: Final = NodeType.MEDIA_QUERY_WITH_TYPE

    def __init__(
        self, modifier: Tokens, media_type: Tokens, and_: Tokens | None = None, media: MediaCondition | None = None
    ) -> None:
        self.modifier = modifier
        self.media_type = media_type
        self.and_: Tokens | None = None
        self.media: MediaCondition | None = None
        if and_ is not None and media is not None:
            self.and_ = and_
            self.media = media

    def get_modifier(self) -> str:
        for t in self.modifier:
            if is_token_ident(t):
                return _ident_value(t)
        return ''

    def get_media_type(self) -> str:
        for t in self.media_type:
            if is_token_ident(t):
                return _ident_value(t)
        return ''

    def negate_query(self) -> list[MediaQuery]:
        negated = self.get_modifier().lower() == 'not'
        media_type = self.get_media_type()
        if self.and_ is None or self.media is None:
            if negated:
                return [MediaQueryWithType([], [_ident_token(media_type)])]
            return [MediaQueryWithType([_ident_token('not'), _space()], [_ident_token(media_type)])]
        if negated:
            return [MediaQueryWithType([], [_ident_token(media_type)]), MediaQueryWithoutType(self.media)]
        return [
            MediaQueryWithType([_ident_token('not'), _space()], [_ident_token(media_type)]),
            *MediaQueryWithoutType(self.media).negate_query(),
        ]

    def tokens(self) -> Tokens:
        if self.and_ is not None and self.media is not None:
            return [*self.modifier, *self.media_type, *self.and_, *self.media.tokens()]
        return [*self.modifier, *self.media_type]

    def to_string(self) -> str:
        if self.and_ is not None and self.media is not None:
            return stringify(self.modifier) + stringify(self.media_type) + stringify(self.and_) + self.media.to_string()
        return stringify(self.modifier) + stringify(self.media_type)

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'media' if item is self.media else -1

    def at(self, index: int | str) -> MediaCondition | None:
        return self.media if index == 'media' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if self.media is None:
            return None
        if cb(WalkEntry(self.media, self, clone), 'media') is False:
            return False
        return self.media.walk(cb, clone)


class MediaQueryWithoutType:
    """A media query that is a media condition."""

    __slots__ = ('media',)
    type: Final = NodeType.MEDIA_QUERY_WITHOUT_TYPE

    def __init__(self, media: MediaCondition) -> None:
        self.media = media

    def negate_query(self) -> list[MediaQuery]:
        condition = self.media
        if isinstance(condition.media, MediaNot):
            return [MediaQueryWithoutType(MediaCondition(condition.media.media))]
        if isinstance(condition.media, (MediaConditionListWithAnd, MediaConditionListWithOr)):
            condition = MediaCondition(
                MediaInParens(
                    condition,
                    [_space(), CSSToken(TokenType.OPEN_PAREN, '(', -1, -1)],
                    [CSSToken(TokenType.CLOSE_PAREN, ')', -1, -1)],
                )
            )
        media = condition.media
        if (
            isinstance(media, (MediaInParens, MediaConditionListWithAnd, MediaConditionListWithOr))
            and not media.has_leading_space()
        ):
            media.before.insert(0, _space())
        return [
            MediaQueryWithType(
                [_ident_token('not'), _space()],
                [_ident_token('all'), _space()],
                [_ident_token('and')],
                condition,
            )
        ]

    def tokens(self) -> Tokens:
        return self.media.tokens()

    def to_string(self) -> str:
        return self.media.to_string()

    def __str__(self) -> str:
        return self.to_string()

    def index_of(self, item: object) -> int | str:
        return 'media' if item is self.media else -1

    def at(self, index: int | str) -> MediaCondition | None:
        return self.media if index == 'media' else None

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        clone = _copy_state(state)
        if cb(WalkEntry(self.media, self, clone), 'media') is False:
            return False
        return self.media.walk(cb, clone)


class MediaQueryInvalid:
    """The component values of a media query that does not parse."""

    __slots__ = ('media',)
    type: Final = NodeType.MEDIA_QUERY_INVALID

    def __init__(self, media: list[ComponentValue]) -> None:
        self.media = media

    def negate_query(self) -> list[MediaQuery]:
        return [MediaQueryInvalid(self.media)]

    def tokens(self) -> Tokens:
        return _flat(self.media)

    def to_string(self) -> str:
        return ''.join(x.to_string() for x in self.media)

    def __str__(self) -> str:
        return self.to_string()

    def walk[S: Mapping[str, object]](self, cb: MediaWalkCallback[S], state: S | None = None) -> WalkResult:
        if not self.media:
            return None
        gen = walker_index_generator(self.media)
        index = 0
        while index < len(self.media):
            child = self.media[index]
            clone = _copy_state(state)
            if cb(WalkEntry(child, self, clone), index) is False:
                return False
            if isinstance(child, (FunctionNode, SimpleBlockNode)) and any(x is child for x in self.media):
                child.walk(cb, clone)
            index = gen(self.media, child, index)
            if index == -1:
                break
        return None


type MediaQuery = MediaQueryWithType | MediaQueryWithoutType | MediaQueryInvalid


class CustomMedia:
    """A @custom-media prelude: the name tokens, and a media query list or true/false."""

    __slots__ = ('media_query_list', 'name', 'true_or_false_keyword')
    type: Final = NodeType.CUSTOM_MEDIA

    def __init__(
        self,
        name: list[CSSToken],
        media_query_list: list[MediaQuery] | None,
        true_or_false_keyword: list[CSSToken] | None = None,
    ) -> None:
        self.name = name
        self.media_query_list = media_query_list
        self.true_or_false_keyword = true_or_false_keyword

    def get_name(self) -> str:
        for t in self.name:
            if is_token_ident(t):
                return _ident_value(t)
        return ''

    def get_name_token(self) -> CSSToken | None:
        for t in self.name:
            if is_token_ident(t):
                return t
        return None

    def has_media_query_list(self) -> bool:
        return self.media_query_list is not None

    def _keyword(self) -> str | None:
        for t in self.true_or_false_keyword or ():
            if is_token_white_space_or_comment(t):
                continue
            if is_token_ident(t):
                return _ident_value(t).lower()
            return None
        return None

    def has_true_keyword(self) -> bool:
        return self._keyword() == 'true'

    def has_false_keyword(self) -> bool:
        return self._keyword() == 'false'

    def tokens(self) -> Tokens:
        if self.true_or_false_keyword is not None:
            return [*self.name, *self.true_or_false_keyword]
        if self.media_query_list is None:
            return list(self.name)
        ans: Tokens = list(self.name)
        for i, query in enumerate(self.media_query_list):
            if i:
                ans.append(CSSToken(TokenType.COMMA, ',', -1, -1))
            ans.extend(query.tokens())
        return ans

    def to_string(self) -> str:
        return stringify(self.tokens())

    def __str__(self) -> str:
        return self.to_string()


type MediaNode = (
    MediaQueryWithType
    | MediaQueryWithoutType
    | MediaQueryInvalid
    | MediaCondition
    | MediaConditionListWithAnd
    | MediaConditionListWithOr
    | MediaInParens
    | MediaNot
    | MediaAnd
    | MediaOr
    | GeneralEnclosed
    | MediaFeature
    | MediaFeatureBoolean
    | MediaFeatureName
    | MediaFeaturePlain
    | MediaFeatureRangeNameValue
    | MediaFeatureRangeValueName
    | MediaFeatureRangeValueNameValue
    | MediaFeatureValue
)
type MediaWalkEntry[S: Mapping[str, object]] = WalkEntry[
    MediaNode | ComponentValue, MediaNode | ContainerNode | ListParent, S
]
type MediaWalkCallback[S: Mapping[str, object]] = Callable[[MediaWalkEntry[S], int | str], object]
type WalkResult = Literal[False] | None

# }}}

# Parsing {{{


def modifier_from_token(token: CSSToken | None) -> MediaQueryModifier | Literal[False]:
    """The modifier (not or only) that token is, if any."""
    if not is_token_ident(token):
        return False
    match _ident_value(token).lower():
        case 'not':
            return MediaQueryModifier.NOT
        case 'only':
            return MediaQueryModifier.ONLY
    return False


def type_from_token(token: CSSToken | None) -> MediaType | Literal[False]:
    """The media type that token is, if any."""
    if not is_token_ident(token):
        return False
    try:
        return MediaType(_ident_value(token).lower())
    except ValueError:
        return False


def _is_keyword(cv: ComponentValue, keyword: str) -> bool:
    return isinstance(cv, TokenNode) and _ascii_lower(_ident_value(cv.value)) == keyword


def parse_media_query(component_values: list[ComponentValue]) -> MediaQuery | Literal[False]:
    """parseMediaQuery(): a media query from one of the comma separated lists."""
    if len(component_values) > MAX_MEDIA_QUERY_COMPONENT_VALUES:
        raise MediaQueryTooComplexError(
            f'Maximum number of component values in a media query ({MAX_MEDIA_QUERY_COMPONENT_VALUES}) exceeded, '
            'reduce the complexity of your media query'
        )
    condition = _parse_media_condition(component_values)
    if condition is not False:
        return MediaQueryWithoutType(condition)
    modifier_index = type_index = and_index = -1
    for i, cv in enumerate(component_values):
        if is_whitespace_node(cv) or is_comment_node(cv):
            continue
        if isinstance(cv, TokenNode):
            token = cv.value
            ident = is_token_ident(token)
            if modifier_index == -1 and ident and modifier_from_token(token):
                modifier_index = i
                continue
            if type_index == -1 and ident and not modifier_from_token(token):
                type_index = i
                continue
            if and_index == -1 and ident and _ascii_lower(_ident_value(token)) == 'and':
                and_index = i
                if _parse_media_condition_without_or(component_values[i + 1 :]) is False:
                    return False
                break
        return False
    modifier_tokens: Tokens = []
    type_tokens: Tokens = []
    if modifier_index != -1:
        modifier_tokens = _flat(component_values[: modifier_index + 1])
        if type_index != -1:
            type_tokens = _flat(component_values[modifier_index + 1 : type_index + 1])
    elif type_index != -1:
        type_tokens = _flat(component_values[: type_index + 1])
    remainder = component_values[max(modifier_index, type_index, and_index) + 1 :]
    condition = _parse_media_condition_without_or(remainder)
    if condition is False:
        return MediaQueryWithType(modifier_tokens, [*type_tokens, *_flat(component_values[type_index + 1 :])])
    return MediaQueryWithType(
        modifier_tokens, type_tokens, _flat(component_values[type_index + 1 : and_index + 1]), condition
    )


def _parse_media_condition_list(
    component_values: list[ComponentValue], allow_or: bool
) -> MediaConditionList | Literal[False]:
    leading: MediaInParens | None = None
    operator: str | None = None
    items: list[MediaAnd | MediaOr] = []
    first_index = last_index = -1
    i = 0
    while i < len(component_values):
        if leading is not None and operator is None:
            part = _parse_media_and(component_values[i:])
            if part is not False:
                operator = 'and'
                i += part[0]
                items.append(part[1])
                last_index = i
                i += 1
                continue
            if allow_or:
                part = _parse_media_or(component_values[i:])
                if part is not False:
                    operator = 'or'
                    i += part[0]
                    items.append(part[1])
                    last_index = i
                    i += 1
                    continue
        if leading is not None and operator is not None:
            part = (
                _parse_media_and(component_values[i:]) if operator == 'and' else _parse_media_or(component_values[i:])
            )
            if part is not False:
                i += part[0]
                items.append(part[1])
                last_index = i
                i += 1
                continue
        cv = component_values[i]
        if is_white_space_or_comment_node(cv):
            i += 1
            continue
        if leading is not None:
            return False
        if isinstance(cv, SimpleBlockNode):
            cv.normalize()
            parsed = _parse_media_in_parens_from_simple_block(cv)
            if parsed is False:
                return False
            leading = parsed
            first_index = i
            i += 1
            continue
        return False
    if leading is not None and items:
        before = _flat(component_values[:first_index])
        after = _flat(component_values[last_index + 1 :])
        if operator == 'and':
            return MediaConditionListWithAnd(leading, [x for x in items if isinstance(x, MediaAnd)], before, after)
        return MediaConditionListWithOr(leading, [x for x in items if isinstance(x, MediaOr)], before, after)
    return False


def _parse_media_condition(component_values: list[ComponentValue]) -> MediaCondition | Literal[False]:
    media_not = _parse_media_not(component_values)
    if media_not is not False:
        return MediaCondition(media_not)
    in_parens = _parse_media_in_parens(component_values)
    if in_parens is not False:
        return MediaCondition(in_parens)
    condition_list = _parse_media_condition_list(component_values, True)
    if condition_list is not False:
        return MediaCondition(condition_list)
    return False


def _parse_media_condition_without_or(component_values: list[ComponentValue]) -> MediaCondition | Literal[False]:
    media_not = _parse_media_not(component_values)
    if media_not is not False:
        return MediaCondition(media_not)
    in_parens = _parse_media_in_parens(component_values)
    if in_parens is not False:
        return MediaCondition(in_parens)
    condition_list = _parse_media_condition_list(component_values, False)
    if condition_list is not False:
        return MediaCondition(condition_list)
    return False


def _parse_media_in_parens(component_values: list[ComponentValue]) -> MediaInParens | Literal[False]:
    index = -1
    for i, cv in enumerate(component_values):
        if is_white_space_or_comment_node(cv):
            continue
        if isinstance(cv, SimpleBlockNode):
            if index != -1:
                return False
            index = i
            continue
        return False
    if index == -1:
        return False
    block = component_values[index]
    assert isinstance(block, SimpleBlockNode)
    if not is_token_open_paren(block.start_token):
        return False
    block.normalize()
    before: Tokens = [*_flat(component_values[:index]), block.start_token]
    after: Tokens = [block.end_token, *_flat(component_values[index + 1 :])]
    feature = parse_media_feature(block, before, after)
    if feature is not False:
        return MediaInParens(feature)
    condition = _parse_media_condition(block.value)
    if condition is not False:
        return MediaInParens(condition, before, after)
    return MediaInParens(GeneralEnclosed(block), _flat(component_values[:index]), _flat(component_values[index + 1 :]))


def _parse_media_in_parens_from_simple_block(block: SimpleBlockNode) -> MediaInParens | Literal[False]:
    if not is_token_open_paren(block.start_token):
        return False
    feature = parse_media_feature(block, [block.start_token], [block.end_token])
    if feature is not False:
        return MediaInParens(feature)
    condition = _parse_media_condition(block.value)
    if condition is not False:
        return MediaInParens(condition, [block.start_token], [block.end_token])
    return MediaInParens(GeneralEnclosed(block))


def _parse_media_not(component_values: list[ComponentValue]) -> MediaNot | Literal[False]:
    saw_not = False
    node: MediaNot | None = None
    for i, cv in enumerate(component_values):
        if is_white_space_or_comment_node(cv):
            continue
        if _is_ident(cv):
            if _is_keyword(cv, 'not'):
                if saw_not:
                    return False
                saw_not = True
                continue
            return False
        if saw_not and isinstance(cv, SimpleBlockNode):
            cv.normalize()
            media = _parse_media_in_parens_from_simple_block(cv)
            if media is False:
                return False
            node = MediaNot(_flat(component_values[:i]), media)
            continue
        return False
    return False if node is None else node


def _parse_media_and(component_values: list[ComponentValue]) -> tuple[int, MediaAnd] | Literal[False]:
    ans = _parse_media_keyword(component_values, 'and')
    if ans is False:
        return False
    return ans[0], MediaAnd(ans[1], ans[2])


def _parse_media_or(component_values: list[ComponentValue]) -> tuple[int, MediaOr] | Literal[False]:
    ans = _parse_media_keyword(component_values, 'or')
    if ans is False:
        return False
    return ans[0], MediaOr(ans[1], ans[2])


def _parse_media_keyword(
    component_values: list[ComponentValue], keyword: str
) -> tuple[int, Tokens, MediaInParens] | Literal[False]:
    seen = False
    for i, cv in enumerate(component_values):
        if is_white_space_or_comment_node(cv):
            continue
        if _is_ident(cv):
            if _is_keyword(cv, keyword):
                if seen:
                    return False
                seen = True
                continue
            return False
        if seen and isinstance(cv, SimpleBlockNode):
            cv.normalize()
            media = _parse_media_in_parens_from_simple_block(cv)
            if media is False:
                return False
            return i, _flat(component_values[:i]), media
        return False
    return False


def parse_media_feature(
    block: SimpleBlockNode, before: Tokens | None = None, after: Tokens | None = None
) -> MediaFeature | Literal[False]:
    """parseMediaFeature(): the feature in a () block, or False."""
    if not is_token_open_paren(block.start_token):
        return False
    boolean = parse_media_feature_name(block.value)
    if boolean is not False:
        return MediaFeature(MediaFeatureBoolean(boolean), before, after)
    plain = parse_media_feature_plain(block.value)
    if plain is not False:
        return MediaFeature(plain, before, after)
    range_ = parse_media_feature_range(block.value)
    if range_ is not False:
        return MediaFeature(range_, before, after)
    return False


def parse_media_feature_name(component_values: list[ComponentValue]) -> MediaFeatureName | Literal[False]:
    """A single ident with whitespace and comments around it."""
    index = -1
    for i, cv in enumerate(component_values):
        if is_white_space_or_comment_node(cv):
            continue
        if _is_ident(cv):
            if index != -1:
                return False
            index = i
            continue
        return False
    if index == -1:
        return False
    return MediaFeatureName(
        component_values[index], _flat(component_values[:index]), _flat(component_values[index + 1 :])
    )


def parse_media_feature_plain(component_values: list[ComponentValue]) -> MediaFeaturePlain | Literal[False]:
    """name: value"""
    for i, cv in enumerate(component_values):
        if isinstance(cv, TokenNode) and cv.value.type is TokenType.COLON:
            a, b, colon = component_values[:i], component_values[i + 1 :], cv.value
            break
    else:
        return False
    if not a or not b:
        return False
    name = parse_media_feature_name(a)
    if name is False:
        return False
    value = parse_media_feature_value(b)
    if value is False:
        return False
    return MediaFeaturePlain(name, colon, value)


def parse_media_feature_value(
    component_values: list[ComponentValue], in_range_context: bool = False
) -> MediaFeatureValue | Literal[False]:
    """A number, ratio, env(), dimension or (outside of a range) ident with whitespace and comments around it."""
    start = end = -1
    i = 0
    while i < len(component_values):
        cv = component_values[i]
        if is_white_space_or_comment_node(cv):
            i += 1
            continue
        if start != -1:
            return False
        if _is_number(cv):
            ratio = matches_ratio_exactly(component_values[i:])
            if ratio != -1:
                start, end = ratio[0] + i, ratio[1] + i
                i += ratio[1] - ratio[0] + 1
                continue
            start = end = i
        elif _is_environment_variable(cv) or _is_dimension(cv) or (not in_range_context and _is_ident(cv)):
            start = end = i
        else:
            return False
        i += 1
    if start == -1:
        return False
    return MediaFeatureValue(
        component_values[start : end + 1], _flat(component_values[:start]), _flat(component_values[end + 1 :])
    )


def matches_ratio_exactly(component_values: list[ComponentValue]) -> tuple[int, int] | Literal[-1]:
    """The indexes of the two numbers of the ratio that component_values are, with whitespace and comments."""
    result = matches_ratio(component_values)
    if result == -1:
        return -1
    for cv in component_values[result[1] + 1 :]:
        if not is_white_space_or_comment_node(cv):
            return -1
    return result


def matches_ratio(component_values: list[ComponentValue]) -> tuple[int, int] | Literal[-1]:
    """The indexes of the two numbers of the ratio that component_values start with."""
    first = delim = -1
    for i, cv in enumerate(component_values):
        if is_white_space_or_comment_node(cv):
            continue
        if isinstance(cv, TokenNode) and is_token_delim(cv.value) and _ident_value(cv.value) == '/':
            if first == -1 or delim != -1:
                return -1
            delim = i
            continue
        if _is_number(cv):
            if delim != -1:
                return first, i
            if first != -1:
                return -1
            first = i
            continue
        return -1
    return -1


def _operator(component_values: list[ComponentValue], indexes: tuple[int, int]) -> Operator:
    first, last = component_values[indexes[0]], component_values[indexes[1]]
    assert isinstance(first, TokenNode)
    assert isinstance(last, TokenNode)
    return [first.value] if indexes[0] == indexes[1] else [first.value, last.value]


def parse_media_feature_range(component_values: list[ComponentValue]) -> MediaFeatureRange | Literal[False]:
    """name < value, value < name or value < name < value."""
    one: tuple[int, int] | None = None
    two: tuple[int, int] | None = None
    i = 0
    while i < len(component_values):
        cv = component_values[i]
        if isinstance(cv, TokenNode) and is_token_delim(cv.value):
            comparison = matches_comparison(component_values[i:])
            if comparison is not False:
                if one is None:
                    one = comparison[0] + i, comparison[1] + i
                    i += comparison[1]
                else:
                    two = comparison[0] + i, comparison[1] + i
                    break
        i += 1
    if one is None:
        return False
    operator_one = _operator(component_values, one)
    if two is None:
        a = component_values[: one[0]]
        b = component_values[one[1] + 1 :]
        name = parse_media_feature_name(a)
        if name is not False:
            value = parse_media_feature_value(b, True)
            if value is False:
                return False
            return MediaFeatureRangeNameValue(name, operator_one, value)
        name = parse_media_feature_name(b)
        if name is not False:
            value = parse_media_feature_value(a, True)
            if value is False:
                return False
            return MediaFeatureRangeValueName(name, operator_one, value)
        return False
    operator_two = _operator(component_values, two)
    value_a = parse_media_feature_value(component_values[: one[0]], True)
    name_b = parse_media_feature_name(component_values[one[1] + 1 : two[0]])
    value_c = parse_media_feature_value(component_values[two[1] + 1 :], True)
    if value_a is False or name_b is False or value_c is False:
        return False
    kind_one = comparison_from_tokens(operator_one)
    if kind_one is False or kind_one is MediaFeatureComparison.EQ:
        return False
    kind_two = comparison_from_tokens(operator_two)
    if kind_two is False or kind_two is MediaFeatureComparison.EQ:
        return False
    lt = (MediaFeatureComparison.LT, MediaFeatureComparison.LT_OR_EQ)
    gt = (MediaFeatureComparison.GT, MediaFeatureComparison.GT_OR_EQ)
    if (kind_one in lt and kind_two in gt) or (kind_one in gt and kind_two in lt):
        return False
    return MediaFeatureRangeValueNameValue(name_b, value_a, operator_one, value_c, operator_two)


def parse_from_tokens(
    tokens: list[CSSToken],
    *,
    preserve_invalid_media_queries: bool = False,
    on_parse_error: OnParseError | None = None,
) -> list[MediaQuery]:
    """The media query list of tokens, as parseFromTokens()."""
    lists = parse_comma_separated_list_of_component_values(tokens, on_parse_error=on_parse_error)
    ans: list[MediaQuery] = []
    for values in lists:
        query = parse_media_query(values)
        if query is False:
            if preserve_invalid_media_queries:
                ans.append(MediaQueryInvalid(values))
        else:
            ans.append(query)
    return ans


def parse(
    source: str, *, preserve_invalid_media_queries: bool = False, on_parse_error: OnParseError | None = None
) -> list[MediaQuery]:
    """The media query list of source, as parse()."""
    return parse_from_tokens(
        tokenize(source, on_parse_error=on_parse_error),
        preserve_invalid_media_queries=preserve_invalid_media_queries,
        on_parse_error=on_parse_error,
    )


def parse_custom_media_from_tokens(
    tokens: list[CSSToken],
    *,
    preserve_invalid_media_queries: bool = False,
    on_parse_error: OnParseError | None = None,
) -> CustomMedia | Literal[False]:
    """The prelude of a @custom-media rule, as parseCustomMediaFromTokens()."""
    name: list[CSSToken] = []
    remainder = tokens
    for i, t in enumerate(tokens):
        if is_token_white_space_or_comment(t):
            continue
        if is_token_ident(t) and _ident_value(t).startswith('--'):
            name = tokens[: i + 1]
            remainder = tokens[i + 1 :]
            break
        return False
    only_true_or_false = True
    for t in remainder:
        if is_token_white_space_or_comment(t):
            continue
        if is_token_ident(t) and _ident_value(t).lower() in ('true', 'false'):
            continue
        if is_token_eof(t):
            break
        only_true_or_false = False
    if only_true_or_false:
        return CustomMedia(name, None, remainder)
    return CustomMedia(
        name,
        parse_from_tokens(
            clone_tokens(remainder),
            preserve_invalid_media_queries=preserve_invalid_media_queries,
            on_parse_error=on_parse_error,
        ),
    )


def parse_custom_media(
    source: str, *, preserve_invalid_media_queries: bool = False, on_parse_error: OnParseError | None = None
) -> CustomMedia | Literal[False]:
    """The prelude of a @custom-media rule, as parseCustomMedia()."""
    return parse_custom_media_from_tokens(
        tokenize(source, on_parse_error=on_parse_error),
        preserve_invalid_media_queries=preserve_invalid_media_queries,
        on_parse_error=on_parse_error,
    )


def new_media_feature_boolean(name: str) -> MediaFeature:
    """A new (name) feature."""
    token = CSSToken(TokenType.IDENT, '', -1, -1, ValueData(''))
    mutate_ident(token, name)
    return MediaFeature(
        MediaFeatureBoolean(MediaFeatureName(TokenNode(token))),
        [CSSToken(TokenType.OPEN_PAREN, '(', -1, -1)],
        [CSSToken(TokenType.CLOSE_PAREN, ')', -1, -1)],
    )


def new_media_feature_plain(name: str, *value: CSSToken) -> MediaFeature:
    """A new (name: value) feature."""
    token = CSSToken(TokenType.IDENT, '', -1, -1, ValueData(''))
    mutate_ident(token, name)
    values = parse_list_of_component_values(list(value))
    return MediaFeature(
        MediaFeaturePlain(
            MediaFeatureName(TokenNode(token)),
            CSSToken(TokenType.COLON, ':', -1, -1),
            MediaFeatureValue(values[0] if len(values) == 1 else values),
        ),
        [CSSToken(TokenType.OPEN_PAREN, '(', -1, -1)],
        [CSSToken(TokenType.CLOSE_PAREN, ')', -1, -1)],
    )


# }}}

# Type guards {{{


def is_custom_media(x: object) -> TypeIs[CustomMedia]:
    return isinstance(x, CustomMedia)


def is_general_enclosed(x: object) -> TypeIs[GeneralEnclosed]:
    return isinstance(x, GeneralEnclosed)


def is_media_and(x: object) -> TypeIs[MediaAnd]:
    return isinstance(x, MediaAnd)


def is_media_condition(x: object) -> TypeIs[MediaCondition]:
    return isinstance(x, MediaCondition)


def is_media_condition_list(x: object) -> TypeIs[MediaConditionList]:
    return isinstance(x, (MediaConditionListWithAnd, MediaConditionListWithOr))


def is_media_condition_list_with_and(x: object) -> TypeIs[MediaConditionListWithAnd]:
    return isinstance(x, MediaConditionListWithAnd)


def is_media_condition_list_with_or(x: object) -> TypeIs[MediaConditionListWithOr]:
    return isinstance(x, MediaConditionListWithOr)


def is_media_feature(x: object) -> TypeIs[MediaFeature]:
    return isinstance(x, MediaFeature)


def is_media_feature_boolean(x: object) -> TypeIs[MediaFeatureBoolean]:
    return isinstance(x, MediaFeatureBoolean)


def is_media_feature_name(x: object) -> TypeIs[MediaFeatureName]:
    return isinstance(x, MediaFeatureName)


def is_media_feature_plain(x: object) -> TypeIs[MediaFeaturePlain]:
    return isinstance(x, MediaFeaturePlain)


def is_media_feature_range(x: object) -> TypeIs[MediaFeatureRange]:
    return isinstance(x, (MediaFeatureRangeNameValue, MediaFeatureRangeValueName, MediaFeatureRangeValueNameValue))


def is_media_feature_range_name_value(x: object) -> TypeIs[MediaFeatureRangeNameValue]:
    return isinstance(x, MediaFeatureRangeNameValue)


def is_media_feature_range_value_name(x: object) -> TypeIs[MediaFeatureRangeValueName]:
    return isinstance(x, MediaFeatureRangeValueName)


def is_media_feature_range_value_name_value(x: object) -> TypeIs[MediaFeatureRangeValueNameValue]:
    return isinstance(x, MediaFeatureRangeValueNameValue)


def is_media_feature_value(x: object) -> TypeIs[MediaFeatureValue]:
    return isinstance(x, MediaFeatureValue)


def is_media_in_parens(x: object) -> TypeIs[MediaInParens]:
    return isinstance(x, MediaInParens)


def is_media_not(x: object) -> TypeIs[MediaNot]:
    return isinstance(x, MediaNot)


def is_media_or(x: object) -> TypeIs[MediaOr]:
    return isinstance(x, MediaOr)


def is_media_query(x: object) -> TypeIs[MediaQuery]:
    return isinstance(x, (MediaQueryWithType, MediaQueryWithoutType, MediaQueryInvalid))


def is_media_query_with_type(x: object) -> TypeIs[MediaQueryWithType]:
    return isinstance(x, MediaQueryWithType)


def is_media_query_without_type(x: object) -> TypeIs[MediaQueryWithoutType]:
    return isinstance(x, MediaQueryWithoutType)


def is_media_query_invalid(x: object) -> TypeIs[MediaQueryInvalid]:
    return isinstance(x, MediaQueryInvalid)


# }}}
