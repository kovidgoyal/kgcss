# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Media queries.

The cases come from calibre's tinycss media3 tests and stylizer.media_ok(), and
from Media Queries Level 4. Property tests check the round trip through
to_css() and compare evaluation with an oracle.
"""

import doctest
from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

import kgcss.media
from kgcss import ImportRule, MediaFeature, MediaQuery, MediaRule, media_matches, parse_media_queries, parse_stylesheet
from kgcss.cssom.values import Dimension, Ident
from kgcss.media import GeneralEnclosed, MediaAnd, MediaCondition, MediaNot, MediaOr, Operator, Ratio

# (media_type, negated, [(feature, value text or None)]), or None for a query that does not parse
type Expected = tuple[str, bool, list[tuple[str, str | None]]] | None


def summary(q: MediaQuery) -> Expected:
    if not q.valid:
        return None
    return q.media_type, q.negated, [(f.name, None if f.value is None else f.value.css_text) for f in q.features()]


# tinycss/tests/media3.py. The differences are marked.
MEDIA3: list[tuple[str, list[Expected]]] = [
    ('', []),  # tinycss: [all]. An empty list has no queries, and matches, as all does
    ('all', [('all', False, [])]),
    ('screen', [('screen', False, [])]),
    (', screen', [None, ('screen', False, [])]),  # tinycss: [all, screen]. The spec: the empty query is malformed
    ('screen, ', [('screen', False, []), None]),  # likewise
    ('screen and (color)', [('screen', False, [('color', None)])]),
    ('all and (min-width:500px)', [('all', False, [('min-width', '500px')])]),
    ('(min-width:500px)', [('all', False, [('min-width', '500px')])]),
    ('(orientation: portrait)', [('all', False, [('orientation', 'portrait')])]),
    (
        'screen and (color), projection and (color)',
        [('screen', False, [('color', None)]), ('projection', False, [('color', None)])],
    ),
    ('not screen and (color)', [('screen', True, [('color', None)])]),
    ('only screen and (color)', [('screen', False, [('color', None)])]),
    ('aural and (device-aspect-ratio: 16/9)', [('aural', False, [('device-aspect-ratio', '16/9')])]),
    ('(resolution: 166dpi)', [('all', False, [('resolution', '166dpi')])]),
    ('(min-resolution: 166DPCM)', [('all', False, [('min-resolution', '166DPCM')])]),
    # tinycss: malformed. Level 4: <general-enclosed>, valid but unknown, so it matches nothing either way
    ('(example, all,), speech', [('all', False, []), ('speech', False, [])]),
    ('&test, screen', [None, ('screen', False, [])]),
]


@pytest.mark.parametrize(('css', 'expected'), MEDIA3)
def test_media3(css: str, expected: list[Expected]) -> None:
    assert [summary(q) for q in parse_media_queries(css)] == expected
    # The same through a sheet
    (rule,) = parse_stylesheet(f'@media {css} {{}}').rules
    assert isinstance(rule, MediaRule)
    assert [summary(q) for q in rule.media.queries] == expected


def test_media3_general_enclosed() -> None:
    q = parse_media_queries('(example, all,)')[0]
    assert q.condition == GeneralEnclosed('(example, all,)')
    assert not q.matches(media_type=lambda t: True, feature=lambda f: True)


# calibre's stylizer.media_ok(), on media_matches()
ALLOWED_MEDIA_TYPES = frozenset({'screen', 'all', 'aural', 'amzn-kf8'})
IGNORED_MEDIA_FEATURES = frozenset(
    [
        'width',
        'min-width',
        'max-width',
        'height',
        'min-height',
        'max-height',
        'device-width',
        'min-device-width',
        'max-device-width',
        'device-height',
        'min-device-height',
        'max-device-height',
        'aspect-ratio',
        'min-aspect-ratio',
        'max-aspect-ratio',
        'device-aspect-ratio',
        'min-device-aspect-ratio',
        'max-device-aspect-ratio',
        'color',
        'min-color',
        'max-color',
        'color-index',
        'min-color-index',
        'max-color-index',
        'monochrome',
        'min-monochrome',
        'max-monochrome',
        '-webkit-min-device-pixel-ratio',
        'resolution',
        'min-resolution',
        'max-resolution',
        'scan',
        'grid',
    ]
)


def media_ok(raw: str | None) -> bool:
    if not raw:
        return True
    if raw == 'amzn-mobi':
        return False
    return media_matches(
        raw,
        media_type=ALLOWED_MEDIA_TYPES.__contains__,
        feature=lambda f: f.name not in IGNORED_MEDIA_FEATURES,
    )


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (None, True),
        ('', True),
        ('amzn-mobi', False),
        ('amzn-kf8', True),
        ('screen', True),
        ('only screen', True),
        ('not screen', False),
        ('(device-width:10px)', False),
        ('screen, (device-width:10px)', True),
        ('screen and (device-width:10px)', False),
        # Beyond calibre's tests
        ('SCREEN', True),  # the M3 quirk: media types are case-insensitive
        ('print', False),
        ('not print', True),
        ('screen and (width >= 600px)', False),  # a range is a width feature
        # Features that are not ignored match, as in calibre
        ('screen and (hover)', True),
        ('not screen and (hover)', False),
        ('screen and (orientation: portrait)', True),
        ('(prefers-color-scheme: dark)', True),
        ('screen and not (color)', True),
        ('screen and (foo bar)', False),
        ('&, screen', True),
        ('not all and (monochrome)', True),
    ],
)
def test_calibre_media_ok(raw: str | None, expected: bool) -> None:
    assert media_ok(raw) is expected


def any_type(media_type: str) -> bool:
    return True


def any_feature(feature: MediaFeature) -> bool | None:
    return True


@pytest.mark.parametrize(
    ('css', 'canonical'),
    [
        # Media Queries 4 examples and corners
        ('(width >= 600px)', '(width >= 600px)'),
        ('(600px <= width)', '(width >= 600px)'),
        ('(400px < width <= 700px)', '(width > 400px) and (width <= 700px)'),
        ('(700px >= width > 400px)', '(width <= 700px) and (width > 400px)'),
        ('(width = 600px)', '(width = 600px)'),
        ('(aspect-ratio: 16 / 9)', '(aspect-ratio: 16 / 9)'),
        ('(min-aspect-ratio > 16/9)', '(min-aspect-ratio > 16/9)'),
        ('(color) or (hover)', '(color) or (hover)'),
        ('not (color)', 'not (color)'),
        ('((color) and (hover)) or (pointer: fine)', '((color) and (hover)) or (pointer: fine)'),
        ('screen and ((color) or (hover))', 'screen and ((color) or (hover))'),
        ('screen and not (color)', 'screen and not (color)'),
        ('not screen and (color)', 'not screen and (color)'),
        ('ONLY Screen AND (Min-Width: 1PX)', 'only screen and (min-width: 1PX)'),
        ('(((color)))', '(color)'),
        ('(width: calc(1px + 2px))', '(width: calc(1px + 2px))'),
        ('/* c */ screen /* c */ and (color)', 'screen and (color)'),
        ('\\and', '\\a nd'),  # the ident is newline, n, d: serialized escaped
        ('(\\31 x)', '(\\31 x)'),
        # <general-enclosed>
        ('(foo bar)', '(foo bar)'),
        ('foo(bar)', 'foo(bar)'),
        ('(width < 1px > 2px)', '(width < 1px > 2px)'),
        ('(width < = 1px)', '(width < = 1px)'),  # <= has no space inside
        ('(color) and (not (hover) and (x))', '(color) and (not (hover) and (x))'),
        ('(width: -1/2)', '(width: -1/2)'),
        ('(16/9: width)', '(16/9: width)'),
        ('(color', '(color)'),  # closed by the end of the input
        ('(width>=calc(', '(width>=calc('),  # a function left open is not a value: general-enclosed
        # Malformed: not all
        ('screen and (a) or (b)', 'not all'),
        ('(a) and (b) or (c)', 'not all'),
        ('not (a) and (b)', 'not all'),
        ('only (color)', 'not all'),
        ('not', 'not all'),
        ('only', 'not all'),
        ('and', 'not all'),
        ('layer', 'not all'),
        ('screen print', 'not all'),
        ('screen and', 'not all'),
        ('screen and(color)', 'not all'),
        ('screen or (color)', 'not all'),
        ('[color]', 'not all'),
        ('screen and [color]', 'not all'),
        ('1px', 'not all'),
        ('(color) (hover)', 'not all'),
        ('not not (color)', 'not all'),
    ],
)
def test_level_4(css: str, canonical: str) -> None:
    (q,) = parse_media_queries(css)
    assert q.to_css() == canonical
    assert q.text == css.strip().removeprefix('/* c */ ').removesuffix(' /* c */')
    assert q.valid == (canonical != 'not all')
    if not q.valid:
        assert q.error
        assert not q.matches(media_type=any_type, feature=any_feature)


def test_model() -> None:
    (q,) = parse_media_queries('only screen and (400px < width) and (orientation: portrait) and (aspect-ratio: 4/3)')
    assert (q.only, q.negated, q.media_type) == (True, False, 'screen')
    assert q.condition == MediaAnd(
        (
            MediaFeature('width', '>', Dimension('400px', 400, 'px')),
            MediaFeature('orientation', ':', Ident('portrait', 'portrait')),
            MediaFeature('aspect-ratio', ':', Ratio('4/3', 4, 3)),
        )
    )
    ratio = list(q.features())[2].value
    assert isinstance(ratio, Ratio)
    assert ratio.value == (4, 3)
    assert parse_media_queries('screen') is parse_media_queries('screen')  # cached
    assert parse_media_queries('/**/ ') == ()
    assert media_matches('', media_type=any_type, feature=any_feature)


def test_media_list() -> None:
    text = 'SCREEN and (min-width:1px) ,  & print, print'
    sheet = parse_stylesheet(f'@import "x.css" {text}; @media {text} {{ a {{ color: red }} }}')
    for rule in sheet.rules:
        assert isinstance(rule, MediaRule | ImportRule)
        assert [q.to_css() for q in rule.media.queries] == ['screen and (min-width: 1px)', 'not all', 'print']
        assert rule.media.media_text == text
        assert rule.media.matches(media_type={'print'}.__contains__, feature=lambda f: True)
        assert not rule.media.matches(media_type={'screen'}.__contains__, feature=lambda f: False)
    media = sheet.rules[1]
    assert isinstance(media, MediaRule)
    media.media.append('tv')
    assert [q.media_type for q in media.media.queries][-1] == 'tv'


def test_doctests() -> None:
    assert doctest.testmod(kgcss.media).failed == 0


# Property tests

VALUES = ('600px', '16/9', '4 / 3', 'portrait', '2', '1.5', 'calc(1px + 2px)', '-3em', '1e3dpi')
NAMES = ('width', 'min-width', 'color', 'orientation', 'aspect-ratio', '-webkit-min-device-pixel-ratio', 'hover')
TYPES = ('screen', 'print', 'aural', 'amzn-kf8', 'tv', 'all')


def value_of(text: str) -> kgcss.media.MediaValue:
    f = parse_media_queries(f'(x: {text})')[0].condition
    assert isinstance(f, MediaFeature)
    assert f.value is not None
    return f.value


@st.composite
def features(draw: st.DrawFn) -> MediaCondition:
    name = draw(st.sampled_from(NAMES))
    match draw(st.sampled_from(('boolean', 'plain', 'range', 'double'))):
        case 'boolean':
            return MediaFeature(name)
        case 'plain':
            return MediaFeature(name, ':', value_of(draw(st.sampled_from(VALUES))))
        case 'range':
            op: Operator = draw(st.sampled_from(('<', '<=', '>', '>=', '=')))
            return MediaFeature(name, op, value_of(draw(st.sampled_from(VALUES))))
    pairs: tuple[tuple[Operator, Operator], ...] = (('>', '<'), ('>=', '<='), ('>', '<='), ('<', '>'), ('<=', '>='))
    low, high = draw(st.sampled_from(pairs))
    a, b = (value_of(draw(st.sampled_from(VALUES))) for _ in range(2))
    return MediaAnd((MediaFeature(name, low, a), MediaFeature(name, high, b)))


def conditions(depth: int = 0) -> st.SearchStrategy[MediaCondition]:
    leaves = features() | st.sampled_from((GeneralEnclosed('(foo bar)'), GeneralEnclosed('fn(1, 2)')))
    if depth >= 3:
        return leaves
    sub = st.deferred(lambda: conditions(depth + 1))
    return st.one_of(
        leaves,
        leaves,
        sub.map(MediaNot),
        st.lists(sub, min_size=2, max_size=3).map(lambda cs: MediaAnd(tuple(cs))),
        st.lists(sub, min_size=2, max_size=3).map(lambda cs: MediaOr(tuple(cs))),
    )


@st.composite
def queries(draw: st.DrawFn) -> MediaQuery:
    condition = draw(st.none() | conditions())
    if condition is not None and draw(st.booleans()):
        return MediaQuery('', condition=condition)
    prefix = draw(st.sampled_from(('', 'not', 'only')))
    media_type = draw(st.sampled_from(TYPES))
    return MediaQuery('', negated=prefix == 'not', only=prefix == 'only', media_type=media_type, condition=condition)


def same_query(a: MediaQuery, b: MediaQuery) -> bool:
    return (a.valid, a.negated, a.only, a.media_type, a.condition) == (
        b.valid,
        b.negated,
        b.only,
        b.media_type,
        b.condition,
    )


# The oracle: Kleene logic as min and max over false < unknown < true
FALSE, UNKNOWN, TRUE = 0, 1, 2
LEVELS = {False: FALSE, None: UNKNOWN, True: TRUE}


def oracle(c: MediaCondition, feature: Callable[[MediaFeature], bool | None]) -> int:
    match c:
        case MediaFeature():
            return LEVELS[feature(c)]
        case GeneralEnclosed():
            return UNKNOWN
        case MediaNot():
            return TRUE - oracle(c.condition, feature)
        case MediaAnd():
            return min(oracle(x, feature) for x in c.conditions)
        case MediaOr():
            return max(oracle(x, feature) for x in c.conditions)


def oracle_query(q: MediaQuery, types: frozenset[str], feature: Callable[[MediaFeature], bool | None]) -> bool:
    level = TRUE if q.media_type == 'all' or q.media_type in types else FALSE
    if q.condition is not None:
        level = min(level, oracle(q.condition, feature))
    if q.negated:
        level = TRUE - level
    return level == TRUE


@given(queries())
def test_round_trip(q: MediaQuery) -> None:
    css = q.to_css()
    (again,) = parse_media_queries(css)
    assert same_query(again, q), css
    assert again.text == css
    assert again.to_css() == css


@given(st.lists(queries(), min_size=1, max_size=3), st.frozensets(st.sampled_from(TYPES)), st.data())
def test_evaluation(qs: list[MediaQuery], types: frozenset[str], data: st.DataObject) -> None:
    answers = data.draw(st.fixed_dictionaries({name: st.sampled_from((True, False, None)) for name in NAMES}))

    def feature(f: MediaFeature) -> bool | None:
        return answers[f.name]

    text = ', '.join(q.to_css() for q in qs)
    expected = [oracle_query(q, types, feature) for q in qs]
    got = [q.matches(media_type=types.__contains__, feature=feature) for q in parse_media_queries(text)]
    assert got == expected, text
    assert media_matches(text, media_type=types.__contains__, feature=feature) == any(expected)


PIECES = (
    'screen', 'print', 'not', 'only', 'and', 'or', 'layer', '(', ')', '(color)', '(width', '>=', '<', '=', ':', '600px',
    '16/9', ',', ' ', ' ', '/**/', 'calc(', '[', ']', '{', '}', ';', '!', '"x"', '\\', 'url(x)', '&', '1', 'é',
)  # fmt: skip


@given(st.lists(st.sampled_from(PIECES), max_size=12).map(''.join))
def test_token_soup(text: str) -> None:
    for q in parse_media_queries(text):
        assert q.valid or q.error
        css = q.to_css()
        # The canonical text means the same
        assert [x.to_css() for x in parse_media_queries(css)] == [css], text


@given(st.text())
def test_any_text(text: str) -> None:
    parse_media_queries(text)
