# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The ports of the @csstools parsing libraries against the originals.

kgcss.linter._csstools (css-tokenizer and css-parser-algorithms) and
kgcss.linter._media_query (media-query-list-parser) must give the same
tokens, trees, strings and walks. Skipped unless the oracle is installed (see
tests/csstools_oracle.py).
"""

import math
import re
from collections.abc import Callable, Iterator

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from kgcss.linter import _csstools as C
from kgcss.linter import _media_query as M

from .csstools_oracle import CSSTools, require_oracle, utf16_offsets

require_oracle()

RE_SURROGATE_PAIR = re.compile('[\ud800-\udbff][\udc00-\udfff]')
TOKEN_TYPES = frozenset(t.value for t in C.TokenType)
type State = dict[str, int | str]
type Json = object


@pytest.fixture(scope='module')
def oracle() -> Iterator[CSSTools]:
    with CSSTools() as s:
        yield s


# Dumps, as tests/csstools_oracle.mjs makes them {{{


def num(value: float) -> str:
    return '-0' if value == 0 and math.copysign(1, value) < 0 else C.js_number_to_string(value)


def dump_token(t: C.CSSToken | None) -> Json:
    if t is None:
        return None
    data: dict[str, str] | None = None
    match t.data:
        case C.ValueData():
            data = {'value': t.data.value}
        case C.HashData():
            data = {'type': t.data.type.value, 'value': t.data.value}
        case C.NumericData():
            data = {}
            if t.data.sign_character is not None:
                data['signCharacter'] = t.data.sign_character
            if t.data.type is not None:
                data['type'] = t.data.type.value
            if t.data.unit is not None:
                data['unit'] = t.data.unit
            data['value'] = num(t.data.value)
        case None:
            pass
    return [t.type.value, t.raw, t.start, t.end, data]


def attempt(f: Callable[[], Json]) -> Json:
    try:
        return f()
    except TypeError as err:
        return {'error': str(err)}


CV_GUARDS = {
    'isFunctionNode': C.is_function_node,
    'isSimpleBlockNode': C.is_simple_block_node,
    'isWhitespaceNode': C.is_whitespace_node,
    'isCommentNode': C.is_comment_node,
    'isWhiteSpaceOrCommentNode': C.is_white_space_or_comment_node,
    'isTokenNode': C.is_token_node,
}


def dump_node(node: C.ComponentValue) -> dict[str, Json]:
    ans: dict[str, Json] = {'type': node.type.value, 'str': attempt(node.to_string)}
    ans['tokens'] = [dump_token(t) for t in node.tokens()]
    ans['indices'] = list(C.source_indices(node))
    if isinstance(node, C.FunctionNode):
        ans['name'] = node.get_name()
        ans['nameToken'] = dump_token(node.name)
        ans['endToken'] = dump_token(node.end_token)
        ans['value'] = [dump_node(x) for x in node.value]
    elif isinstance(node, C.SimpleBlockNode):
        ans['startToken'] = dump_token(node.start_token)
        ans['endToken'] = dump_token(node.end_token)
        ans['value'] = [dump_node(x) for x in node.value]
    ans['guards'] = [name for name, f in CV_GUARDS.items() if f(node)]
    return ans


def node_name(node: object) -> str | None:
    if node is None:
        return None
    if isinstance(node, list):
        return 'array'
    t = getattr(node, 'type', None)
    return 'list' if t is None else str(t)


def describe(node: object) -> list[Json]:
    to_string = getattr(node, 'to_string', None)
    assert to_string is not None
    return [node_name(node), attempt(to_string)]


def token_of(type: C.TokenType, raw: str, value: str | None = None) -> C.CSSToken:
    return C.CSSToken(type, raw, -1, -1, None if value is None else C.ValueData(value))


def mutate(node: object, parent: object, index: int | str) -> bool | None:
    """The mutations of tests/csstools_oracle.mjs."""
    items = getattr(parent, 'value', None)
    if not isinstance(index, int) or not isinstance(items, list):
        return None
    if isinstance(node, C.WhitespaceNode):
        del items[index]
    elif isinstance(node, C.TokenNode) and node.value.type is C.TokenType.IDENT:
        value = node.value.string_value
        if value == 'stop':
            return False
        if value == 'x':
            items[index : index + 1] = [
                C.TokenNode(token_of(C.TokenType.IDENT, 'y', 'y')),
                C.TokenNode(token_of(C.TokenType.IDENT, 'z', 'z')),
            ]
        if value == 'drop':
            del items[:]
    elif isinstance(node, C.TokenNode) and node.value.type is C.TokenType.DELIM:
        if node.value.string_value == '+':
            items.insert(index + 1, C.TokenNode(token_of(C.TokenType.DELIM, '*', '*')))
        elif node.value.string_value == '*' and index > 0:
            del items[index - 1]
    elif isinstance(node, C.TokenNode) and node.value.type is C.TokenType.COMMA:
        items[index : index + 1] = [C.WhitespaceNode([token_of(C.TokenType.WHITESPACE, ' ')])]
    return None


MAX_VISITS = 5000


def walker(visits: list[Json], do_mutate: bool) -> Callable[[C.WalkEntry[object, object, State], int | str], object]:
    def cb(entry: C.WalkEntry[object, object, State], index: int | str) -> object:
        state = entry.state
        visits.append([*describe(entry.node), node_name(entry.parent), index, None if state is None else dict(state)])
        if len(visits) > MAX_VISITS:
            return False
        if state is not None:
            depth, path = state['depth'], state['path']
            assert isinstance(depth, int)
            assert isinstance(path, str)
            state['depth'] = depth + 1
            name = node_name(entry.node)
            assert name is not None
            state['path'] = path + name[:3] + '/'
        if do_mutate:
            return mutate(entry.node, entry.parent, index)
        return None

    return cb


def initial_state(state: bool) -> State | None:
    return {'depth': 0, 'path': ''} if state else None


def py_list(css: str, *, comma: bool = False, do_mutate: bool = False, state: bool = True) -> dict[str, Json]:
    tokens = C.tokenize(css)
    errors: list[Json] = []

    def on_error(err: C.ParseError) -> None:
        errors.append({'message': err.message, 'start': err.source_start, 'end': err.source_end})

    if comma:
        lists = C.parse_comma_separated_list_of_component_values(tokens, on_parse_error=on_error)
        visits: list[Json] = []
        results: list[Json] = []
        for nodes in lists:
            results.append(C.walk(nodes, walker(visits, do_mutate), initial_state(state)))
        return {
            'lists': [[dump_node(n) for n in nodes] for nodes in lists],
            'stringify': attempt(lambda: C.stringify_component_value_lists(lists)),
            'visits': visits,
            'results': results,
            'errors': errors,
        }
    nodes = C.parse_list_of_component_values(tokens, on_parse_error=on_error)
    before = [dump_node(n) for n in nodes]
    visits = []
    result = C.walk(nodes, walker(visits, do_mutate), initial_state(state))
    return {
        'nodes': before,
        'after': [dump_node(n) for n in nodes],
        'indices': list(C.source_indices(nodes)),
        'visits': visits,
        'result': result,
        'errors': errors,
    }


MEDIA_GUARDS = {
    'isCustomMedia': M.is_custom_media,
    'isGeneralEnclosed': M.is_general_enclosed,
    'isMediaAnd': M.is_media_and,
    'isMediaCondition': M.is_media_condition,
    'isMediaConditionList': M.is_media_condition_list,
    'isMediaConditionListWithAnd': M.is_media_condition_list_with_and,
    'isMediaConditionListWithOr': M.is_media_condition_list_with_or,
    'isMediaFeature': M.is_media_feature,
    'isMediaFeatureBoolean': M.is_media_feature_boolean,
    'isMediaFeatureName': M.is_media_feature_name,
    'isMediaFeaturePlain': M.is_media_feature_plain,
    'isMediaFeatureRange': M.is_media_feature_range,
    'isMediaFeatureRangeNameValue': M.is_media_feature_range_name_value,
    'isMediaFeatureRangeValueName': M.is_media_feature_range_value_name,
    'isMediaFeatureRangeValueNameValue': M.is_media_feature_range_value_name_value,
    'isMediaFeatureValue': M.is_media_feature_value,
    'isMediaInParens': M.is_media_in_parens,
    'isMediaNot': M.is_media_not,
    'isMediaOr': M.is_media_or,
    'isMediaQuery': M.is_media_query,
    'isMediaQueryInvalid': M.is_media_query_invalid,
    'isMediaQueryWithType': M.is_media_query_with_type,
    'isMediaQueryWithoutType': M.is_media_query_without_type,
}
TOKEN_LIST_FIELDS = {
    'modifier': 'modifier',
    'mediaType': 'media_type',
    'and': 'and_',
    'before': 'before',
    'after': 'after',
    'operator': 'operator',
    'valueOneOperator': 'value_one_operator',
    'valueTwoOperator': 'value_two_operator',
}
NODE_FIELDS = {
    'media': 'media',
    'leading': 'leading',
    'feature': 'feature',
    'name': 'name',
    'value': 'value',
    'valueOne': 'value_one',
    'valueTwo': 'value_two',
}
MEDIA_NODES = (
    M.MediaQueryWithType,
    M.MediaQueryWithoutType,
    M.MediaQueryInvalid,
    M.MediaCondition,
    M.MediaConditionListWithAnd,
    M.MediaConditionListWithOr,
    M.MediaInParens,
    M.MediaNot,
    M.MediaAnd,
    M.MediaOr,
    M.GeneralEnclosed,
    M.MediaFeature,
    M.MediaFeatureBoolean,
    M.MediaFeatureName,
    M.MediaFeaturePlain,
    M.MediaFeatureRangeNameValue,
    M.MediaFeatureRangeValueName,
    M.MediaFeatureRangeValueNameValue,
    M.MediaFeatureValue,
)
CV_TYPES = (C.FunctionNode, C.SimpleBlockNode, C.TokenNode, C.WhitespaceNode, C.CommentNode)


def dump_media(node: object) -> Json:
    if node is None:
        return None
    if isinstance(node, list):
        return [dump_media(x) for x in node]
    if isinstance(node, CV_TYPES):
        return {'componentValue': dump_node(node)}
    assert isinstance(node, MEDIA_NODES)
    ans: dict[str, Json] = {'type': node_name(node), 'str': attempt(node.to_string)}
    ans['tokens'] = [dump_token(t) for t in node.tokens()]
    ans['guards'] = [name for name, f in MEDIA_GUARDS.items() if f(node)]
    for js, py in TOKEN_LIST_FIELDS.items():
        value = getattr(node, py, None)
        if isinstance(value, list):
            ans[js] = [dump_token(t) for t in value]
    if isinstance(node, M.MediaFeaturePlain):
        ans['colon'] = dump_token(node.colon)
    for js, py in NODE_FIELDS.items():
        value = getattr(node, py, None)
        if value is not None:
            ans[js + 'Node'] = dump_media(value)
    if isinstance(node, (M.MediaConditionListWithAnd, M.MediaConditionListWithOr)):
        ans['list'] = [dump_media(x) for x in node.list]
    if (get_name := getattr(node, 'get_name', None)) is not None:
        ans['getName'] = attempt(get_name)
    if (get_name_token := getattr(node, 'get_name_token', None)) is not None:
        ans['getNameToken'] = attempt(lambda: dump_token(get_name_token()))
    if isinstance(node, M.MediaQueryWithType):
        ans['getModifier'] = node.get_modifier()
        ans['getMediaType'] = node.get_media_type()
    if (has_leading_space := getattr(node, 'has_leading_space', None)) is not None:
        ans['hasLeadingSpace'] = attempt(has_leading_space)
    if isinstance(node, (M.MediaFeatureRangeNameValue, M.MediaFeatureRangeValueName)):
        ans['operatorKind'] = kind(node.operator_kind())
    if isinstance(node, M.MediaFeatureRangeValueNameValue):
        ans['valueOneOperatorKind'] = kind(node.value_one_operator_kind())
        ans['valueTwoOperatorKind'] = kind(node.value_two_operator_kind())
    return ans


def kind(k: M.MediaFeatureComparison | bool) -> Json:
    return k if isinstance(k, bool) else k.value


def py_media(css: str, *, preserve: bool = True, state: bool = True) -> dict[str, Json]:
    queries = M.parse(css, preserve_invalid_media_queries=preserve)
    ans = []
    for query in queries:
        visits: list[Json] = []
        result = query.walk(walker(visits, False), initial_state(state))
        tree = dump_media(query)
        ans.append(
            {
                'tree': tree,
                'visits': visits,
                'result': result,
                'negated': attempt(lambda q=query: [x.to_string() for x in q.negate_query()]),
            }
        )
    return {'queries': ans}


def py_custom_media(css: str, *, preserve: bool = True) -> dict[str, Json]:
    custom = M.parse_custom_media(css, preserve_invalid_media_queries=preserve)
    if custom is False:
        return {'custom': False}
    return {
        'custom': {
            'str': attempt(custom.to_string),
            'name': custom.get_name(),
            'nameToken': dump_token(custom.get_name_token()),
            'tokens': attempt(lambda: [dump_token(t) for t in custom.tokens()]),
            'hasMediaQueryList': custom.has_media_query_list(),
            'hasTrueKeyword': custom.has_true_keyword(),
            'hasFalseKeyword': custom.has_false_keyword(),
            'trueOrFalseKeyword': None
            if custom.true_or_false_keyword is None
            else [dump_token(t) for t in custom.true_or_false_keyword],
            'queries': None if custom.media_query_list is None else [dump_media(q) for q in custom.media_query_list],
        }
    }


def py_tokenize(css: str) -> dict[str, Json]:
    errors: list[Json] = []

    def on_error(err: C.ParseError) -> None:
        errors.append(
            {
                'message': err.message,
                'start': err.source_start,
                'end': err.source_end,
                'token': dump_token(err.token) if isinstance(err, C.ParseErrorWithToken) else None,
            }
        )

    tokens = C.tokenize(css, on_parse_error=on_error)
    return {'tokens': [dump_token(t) for t in tokens], 'errors': errors, 'stringify': C.stringify(tokens)}


def py_mutate(css: str, unit: str, ident: str) -> dict[str, Json]:
    tokens = C.tokenize(css)
    try:
        for t in tokens:
            if C.is_token_dimension(t):
                C.mutate_unit(t, unit)
            elif C.is_token_ident(t):
                C.mutate_ident(t, ident)
    except TypeError as err:
        return {'error': str(err)}
    return {'tokens': [dump_token(t) for t in tokens]}


def py_ratio(css: str) -> dict[str, Json]:
    nodes = C.parse_list_of_component_values(C.tokenize(css))
    return {'ratio': [ratio_json(M.matches_ratio(nodes)), ratio_json(M.matches_ratio_exactly(nodes))]}


def ratio_json(r: tuple[int, int] | int) -> Json:
    return list(r) if isinstance(r, tuple) else r


# }}}


def to_code_points(value: Json, offsets: list[int], key: str = '') -> Json:
    """The oracle's response with Python string indexes for its positions, and errors without their text."""

    def pos(i: object) -> object:
        return offsets[i] if isinstance(i, int) and 0 <= i < len(offsets) else i

    if isinstance(value, dict):
        if set(value) == {'error'}:
            return {'error': True}
        ans = {k: to_code_points(v, offsets, k) for k, v in value.items()}
        if 'message' in ans and 'start' in ans:
            ans['start'], ans['end'] = pos(ans['start']), pos(ans['end'])
        return ans
    if isinstance(value, list):
        if len(value) == 5 and isinstance(value[0], str) and value[0] in TOKEN_TYPES and isinstance(value[2], int):
            return [value[0], value[1], pos(value[2]), pos(value[3]), value[4]]
        if key == 'indices':
            return [pos(i) for i in value]
        return [to_code_points(v, offsets) for v in value]
    return value


def without_error_text(value: Json) -> Json:
    if isinstance(value, dict):
        if set(value) == {'error'}:
            return {'error': True}
        return {k: without_error_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_error_text(v) for v in value]
    return value


def compare(css: str, expected: Json, actual: Json) -> None:
    assert without_error_text(actual) == to_code_points(expected, utf16_offsets(css))


def check_tokenize(oracle: CSSTools, css: str) -> None:
    compare(css, oracle.tokenize(css), py_tokenize(css))


def check_list(oracle: CSSTools, css: str, *, comma: bool = False, do_mutate: bool = False, state: bool = True) -> None:
    expected = oracle.parse_list(css, comma=comma, mutate=do_mutate, state=state)
    compare(css, expected, py_list(css, comma=comma, do_mutate=do_mutate, state=state))


def check_media(oracle: CSSTools, css: str, *, preserve: bool = True, state: bool = True) -> None:
    compare(css, oracle.media(css, preserve=preserve, state=state), py_media(css, preserve=preserve, state=state))


def check_custom_media(oracle: CSSTools, css: str) -> None:
    compare(css, oracle.custom_media(css), py_custom_media(css))


def check_mutate(oracle: CSSTools, css: str, unit: str, ident: str) -> None:
    compare(css, oracle.mutate(css, unit, ident), py_mutate(css, unit, ident))


TOKEN_CASES = [
    '',
    'a b',
    'url(  "a")',
    'url( "a")',
    "URL(\t'a')",
    'u\\72l(  "a")',
    'url(a\x00b)',
    'url(\x00)',
    'url( a b )',
    'url(a"b)',
    'url(a\\\n)',
    'url(a\\',
    'url(a\\\\',
    'url(a',
    'url(a\\\nb\\',
    'url(\\)',
    'url(a)b',
    '"ab\r\nc',
    '"a\\\r\nb"',
    '"a\\',
    '"\ud800\U0001f600"',
    '/* x',
    '/**/',
    '\\',
    '\\\n',
    'a\\',
    '#\\',
    '@\\',
    '1\\',
    '1e3 +.5e-2px -0 1\\',
    '-0.0 +0 1E3 1e+3 1e-3 .5 -.5 1.',
    '12345678901234567890123 1e400 -1e400 1e-400',
    '#-a #1a #-- #\\31 #\U0001f600',
    'a\x00b \ud800x \udc00',
    '\\41\r\nx \\0 \\110000 \\d800',
    '<!-- --> <! -- -',
    '@media @ @-a @-1',
    'u+12 U+1?? u+a-f',
    '1px-2 1--x 1-\\61',
    '\U0001f600 \u00b7 \u200c',
    '+ - . +a -a -.5 +.a',
    'a\u00d7b \u00d7( 1\u00f7px #\u2014a #a\u2014 @\ue000 -\u00d7 --\u00d7 a\\\u00d7b \ufffe \u3000a \x80',
    'u\u00d7rl(x) url(\u00d7) "\u00d7" /* \u00d7 */ 1e\u00d7 1-\u00d7 \\41\u00d7',
]


@pytest.mark.parametrize('css', TOKEN_CASES)
def test_tokenize(oracle: CSSTools, css: str) -> None:
    check_tokenize(oracle, css)


LIST_CASES = [
    '',
    'a(b +x) [c]',
    'a(b(c',
    'a(b(c d',
    '(a [b {c',
    '((a)) [) ] {( }',
    'calc(1px+2px) , min( 1 , 2 )',
    'a /**/ b /* c */ /**/ d',
    'x y + z stop w',
    'f(a, x, stop, b) g(x)',
    'drop a b',
    'f(drop a) b',
    'a + b * c',
    ') ] }',
    'a,,b,',
    ',',
    'f(a,b),(c,d)',
    '\n\t a  \n',
]


@pytest.mark.parametrize('css', LIST_CASES)
@pytest.mark.parametrize('comma', [False, True])
@pytest.mark.parametrize('do_mutate', [False, True])
def test_parse_list(oracle: CSSTools, css: str, comma: bool, do_mutate: bool) -> None:
    check_list(oracle, css, comma=comma, do_mutate=do_mutate)
    check_list(oracle, css, comma=comma, do_mutate=do_mutate, state=False)


MEDIA_CASES = [
    '',
    'screen',
    'screen and (min-width: 10px)',
    'screen and (min-width: 10px) and (max-width: 20px)',
    'only screen and (color), print',
    'not screen',
    'not',
    'only',
    'only and',
    'and (a)',
    'screen (a)',
    'screen and (a) or (b)',
    '(a) and (b) or (c)',
    '(a) or (b) or (c)',
    '(a) AND (b)',
    'not (a)',
    'not (a) (b)',
    'not not (a)',
    '((a) and (b))',
    '(width < 10px)',
    '(10px <= width < 20px)',
    '(10px < width > 20px)',
    '(10px > width >= 2px)',
    '(width = 10px)',
    '(10px = width)',
    '(10px = width = 20px)',
    '(width <= 10px)',
    '(width < = 10px)',
    '(width << 10px)',
    '(width <<= 10px)',
    '(a < b)',
    '(1 < 2)',
    '(aspect-ratio: 16 / 9)',
    '(aspect-ratio: 16/9 )',
    '(aspect-ratio: 16 / 9 / 2)',
    '(aspect-ratio > 1 / 2)',
    '(a: calc(1px + 2px))',
    '(a: env(x))',
    '(a: b c)',
    '(a:)',
    '(:b)',
    '(a: 1px',
    '(a',
    '((a',
    '(a) and ((b',
    '[a]',
    '{a}',
    'f(a)',
    '(f(a))',
    '(--x)',
    '(--x: 1)',
    'layer(a)',
    '(a) and layer(b)',
    'screen, , print',
    'screen /* c */ and /**/ (a)',
    '(not (a))',
    '(a and b)',
    '(hover: hover) and (pointer: fine), (min-resolution: 2dppx)',
    'NOT ALL AND (MONOCHROME)',
    'not all and (monochrome), not (a) or (b)',
    '(a) and (b) and (c) and (d)',
    '(\u0131 < 1px)',
    '(width: 1x)',
    '(1px < width < 2px < 3px)',
]


@pytest.mark.parametrize('css', MEDIA_CASES)
def test_media(oracle: CSSTools, css: str) -> None:
    check_media(oracle, css)
    check_media(oracle, css, preserve=False, state=False)
    check_custom_media(oracle, '--name ' + css)


@pytest.mark.parametrize(
    'css', ['', '--a', '--a true', '--a FALSE', '--a true false', '-- a', 'a', '/**/ --a /**/ (b)', '--a (b), c']
)
def test_custom_media(oracle: CSSTools, css: str) -> None:
    check_custom_media(oracle, css)


@pytest.mark.parametrize(
    ('unit', 'ident'),
    [
        ('px', 'a'),
        ('e-x', '-1'),
        ('E3', '--'),
        ('-', '-'),
        ('', 'a'),
        ('px', ''),
        ('\x00a\x00b', '\x00'),
        ('-\x00x', '-\x00'),
        ('a b', '1a'),
        ('\U0001f600', '\ud800'),
        ('--', '-a'),
        ('-1', '-\\'),
    ],
)
def test_mutate(oracle: CSSTools, unit: str, ident: str) -> None:
    check_mutate(oracle, '1px +2e3em -.5e-7q 1e21x 123456789012345678901x 0.1x -0x a', unit, ident)


@pytest.mark.parametrize('css', ['1/2', '1 / 2', '1 / 2 x', 'calc(1)/2', '1/', '/2', '1 2', '1 / 2 / 3', ''])
def test_ratio(oracle: CSSTools, css: str) -> None:
    assert py_ratio(css) == oracle.ratio(css)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        (0.0, '0'),
        (-0.0, '0'),
        (1.0, '1'),
        (-1.5, '-1.5'),
        (1e21, '1e+21'),
        (1e20, '100000000000000000000'),
        (123456789012345680000.0, '123456789012345680000'),
        (1e-7, '1e-7'),
        (1.5e-7, '1.5e-7'),
        (1e-6, '0.000001'),
        (0.1, '0.1'),
        (100.0, '100'),
        (1.7976931348623157e308, '1.7976931348623157e+308'),
        (5e-324, '5e-324'),
        (math.inf, 'Infinity'),
        (-math.inf, '-Infinity'),
        (math.nan, 'NaN'),
    ],
)
def test_js_number_to_string(value: float, expected: str) -> None:
    assert C.js_number_to_string(value) == expected


def test_deep_nesting() -> None:
    depth = 511
    css = 'f(' * depth + 'x' + ')' * depth
    nodes = C.parse_list_of_component_values(C.tokenize(css))
    assert C.stringify(nodes[0].tokens()) == css
    assert str(nodes[0]) == css
    count = 0

    def cb(entry: C.ComponentWalkEntry[dict[str, object]], index: int | str) -> None:
        nonlocal count
        count += 1

    C.walk(nodes, cb)
    assert count == depth + 1
    with pytest.raises(C.NestingDepthError):
        C.parse_list_of_component_values(C.tokenize('(' * 513))


def test_missing_end_token() -> None:
    (node,) = C.parse_list_of_component_values(C.tokenize('a(b(c'))
    assert isinstance(node, C.FunctionNode)
    assert node.end_token is None
    assert node.tokens()[-1] is None
    assert C.source_indices(node) == (0, 0)
    with pytest.raises(TypeError):
        node.to_string()


# Property tests {{{

FRAGMENTS = (
    '1', '-1', '+1.5', '.5e3', '1e-3', '1E+2', '10px', '-2em', '1e3px', '3%', '-.5%', '1px-2', '2px-', 'a-', '1--x',
    '1\\65 m', '1\\', '1e', '1e-', '0', '-0', '1/2', '16 / 9', 'calc(', 'min(', 'var(--x)', 'env(a)', 'f(', 'layer(',
    '(', ')', '[', ']', '{', '}', '#{', '$', ' ', '  ', '\n', '\r\n', '\r', '\t', '\f', ',', ';', ':', '+', '-', '*',
    '/', '<', '>', '=', '<=', '>=', '!', '#a', '#1', '#-', '#\\', '@media', '@', '"s"', "'s'", '"unterminated',
    '"a\nb', '"\\\n"', '"\\', 'url(a)', 'url( a b )', 'url("x")', 'url(  "x")', 'url(a"b)', 'url(a\\\n)', 'url(\x00)',
    'url(a', 'url(\\', 'URL(', 'u+12', '/* c */', '/*', '*/', '<!--', '-->', '\\', '\\41 ', '\\0', '\\\n', '\x00',
    '\u00e9', '\U0001f600', '\ud800', '\udc00', 'x', 'y', 'stop', 'drop', 'ident', '-x', '--y', '\u00b7',
    '\u00d7', '\ue000', '\u2014', '\x85',
)  # fmt: skip
MEDIA_FRAGMENTS = (
    'screen', 'print', 'all', 'only', 'not', 'NOT', 'and', 'AND', 'or', 'Or', '(', ')', ' ', ' ', ' ', ',',
    '(min-width: 10px)', '(width)', '(width < 10px)', '(10px <= width < 20px)', '(10px < width > 20px)', '(a = 1)',
    '(a: 1/2)', '(a: 16 / 9)', '(--x)', '(color)', 'layer(a)', '(hover: hover)', '(a:)', '(:b)', '(a: b c)',
    'width', 'height', '10px', '1', '2', '<', '>', '=', '<=', '>=', '/', ':', '/**/', 'f(x)', '[a]', '(a',
    'calc(1px + 1px)', 'env(x)', '(not (a))', '((a) and (b))', '((a) or (b))', ' and ', ' or ', 'not ',
)  # fmt: skip
characters = st.characters(exclude_categories=())


def css_from(fragments: tuple[str, ...]) -> st.SearchStrategy[str]:
    return st.lists(st.one_of(st.sampled_from(fragments), characters), max_size=30).map(''.join)


css_text = css_from(FRAGMENTS)

# Media query lists from their grammar, with some damage
spaces = st.sampled_from(('', ' ', ' ', '  ', '/**/', ' /* c */ ', '\n'))
names = st.sampled_from(('width', 'min-width', '--x', 'color', 'WIDTH', '\\77 idth', 'hover', 'aspect-ratio'))
values = st.sampled_from((
    '10px', '1', '16 / 9', '1/2', 'calc(1px + 2px)', 'env(x)', 'auto', '-1.5e3em', 'hover', '2dppx', '1 / 2 / 3',
    'MIN(1px, 2px)', '"s"', '',
))  # fmt: skip
operators = st.sampled_from(('<', '<=', '>', '>=', '=', '< =', '=>', '<<', '==', '<>', '>='))


def keyword(word: str) -> st.SearchStrategy[str]:
    return st.sampled_from((word, word.upper(), word.capitalize()))


@st.composite
def features(draw: st.DrawFn) -> str:
    name, value, sp = draw(names), draw(values), lambda: draw(spaces)
    match draw(st.integers(0, 7)):
        case 0:
            inner = name
        case 1:
            inner = f'{name}{sp()}:{sp()}{value}'
        case 2:
            inner = f'{name}{sp()}{draw(operators)}{sp()}{value}'
        case 3:
            inner = f'{value}{sp()}{draw(operators)}{sp()}{name}'
        case 4:
            inner = f'{value}{sp()}{draw(operators)}{sp()}{name}{sp()}{draw(operators)}{sp()}{draw(values)}'
        case 5 | 6:
            ops = draw(st.sampled_from((('<', '<='), ('>', '>='))))
            numbers = st.sampled_from(('1px', '2', '3/4', 'calc(1px)', 'env(x)'))
            one, two = draw(st.sampled_from(ops)), draw(st.sampled_from(ops))
            inner = f'{draw(numbers)}{sp()}{one}{sp()}{name}{sp()}{two}{sp()}{draw(numbers)}'
        case _:
            inner = draw(st.sampled_from(('a b', 'f(x)', '1', ':', 'x:', '', 'not a', '(a)')))
    return f'({sp()}{inner}{sp()})'


def conditions(in_parens: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    def joined(word: str) -> st.SearchStrategy[str]:
        return st.lists(st.tuples(spaces, keyword(word), spaces, in_parens), min_size=1, max_size=3).map(
            lambda parts: ''.join(f'{a}{k}{b or " "}{p}' for a, k, b, p in parts)
        )

    return st.one_of(
        in_parens,
        st.tuples(keyword('not'), spaces, in_parens).map(lambda t: f'{t[0]} {t[1]}{t[2]}'),
        st.tuples(in_parens, joined('and')).map(''.join),
        st.tuples(in_parens, joined('or')).map(''.join),
        st.tuples(in_parens, joined('and'), joined('or')).map(''.join),
    )


in_parens = st.deferred(
    lambda: st.one_of(
        features(),
        features(),
        conditions(in_parens).map(lambda c: f'({c})'),
        st.sampled_from(('f(x)', '[a]', '(a b)', '(1 + 2)', 'layer(a)', '{a}')),
    )
)
media_types = st.sampled_from(('screen', 'print', 'all', 'tv', 'SCREEN', 'unknown', 'and', 'or', 'not', 'only'))
queries = st.one_of(
    conditions(in_parens),
    st.tuples(
        st.sampled_from(('', 'only ', 'not ', 'ONLY ', 'Not ')),
        media_types,
        st.one_of(st.just(''), conditions(in_parens)),
    ).map(lambda t: f'{t[0]}{t[1]}' + (f' and {t[2]}' if t[2] else '')),
)


@st.composite
def media_lists(draw: st.DrawFn) -> str:
    text = ','.join(draw(st.lists(st.tuples(spaces, queries).map(''.join), min_size=1, max_size=3)))
    if draw(st.booleans()):
        cut = draw(st.integers(0, len(text)))
        text = text[:cut] + draw(st.sampled_from(('', '(', ')', ',', ' ', 'x', '<', '/', '\u00d7', '"'))) + text[cut:]
    if draw(st.integers(0, 4)) == 0:
        text = text[: draw(st.integers(0, len(text)))]
    return text


media_text = st.one_of(
    media_lists(),
    media_lists(),
    css_from(MEDIA_FRAGMENTS),
    st.lists(st.sampled_from(MEDIA_FRAGMENTS + FRAGMENTS), max_size=30).map(''.join),
)


def no_pairs(*texts: str) -> None:
    for text in texts:
        assume(not RE_SURROGATE_PAIR.search(text))  # a high and a low surrogate are one character in JavaScript


@given(css_text)
def test_tokenize_property(oracle: CSSTools, css: str) -> None:
    no_pairs(css)
    check_tokenize(oracle, css)


@given(css_text, st.booleans(), st.booleans())
def test_parse_list_property(oracle: CSSTools, css: str, comma: bool, do_mutate: bool) -> None:
    no_pairs(css)
    check_list(oracle, css, comma=comma, do_mutate=do_mutate)


@given(media_text, st.booleans())
def test_media_property(oracle: CSSTools, css: str, preserve: bool) -> None:
    no_pairs(css)
    check_media(oracle, css, preserve=preserve)


@given(media_text)
def test_custom_media_property(oracle: CSSTools, css: str) -> None:
    no_pairs(css)
    check_custom_media(oracle, '--x ' + css)
    check_custom_media(oracle, css)


units = st.lists(st.one_of(st.sampled_from(('e', 'E', '-', '\x00', '1', 'a', ' ', '\\')), characters), max_size=5)
numbers = st.floats(allow_nan=False, allow_infinity=False).map(repr)


@given(st.lists(numbers, min_size=1, max_size=5), units.map(''.join), units.map(''.join))
def test_mutate_property(oracle: CSSTools, values: list[str], unit: str, ident: str) -> None:
    no_pairs(unit, ident)
    css = ' '.join(f'{v}x +{v.lstrip("-")}q' for v in values) + ' a'
    check_mutate(oracle, css, unit, ident)


# }}}
