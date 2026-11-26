# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The font helpers.

The cases come from tinycss/tests/fonts3.py and the font tests of calibre's
normalize_css.py. A differential test compares with tinycss.fonts3, from the
benchmark cache.
"""

import doctest
import importlib
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

import kgcss
import kgcss.fonts
from kgcss import FontDict, matches, parse_font, parse_font_family, serialize_font, serialize_font_family

CALIBRE = Path(__file__).parent.parent / 'bench' / '.cache' / 'calibre'
GENERIC = frozenset(('serif', 'sans-serif', 'cursive', 'fantasy', 'monospace'))


@pytest.mark.parametrize(
    ('css', 'expected'),
    [
        ('"1as"', ['1as']),
        ('A B C, serif', ['A B C', 'serif']),
        (r'Red\/Black', ['Red/Black']),
        ('A  B', ['A B']),
        (r'Ahem\!', ['Ahem!']),
        (r'"Ahem!"', ['Ahem!']),
        ('€42', ['€42']),
        (r'Hawaii\ 5-0', ['Hawaii 5-0']),
        (r'"X \"Y"', ['X "Y']),
        ('A B, C D, "E", serif', ['A B', 'C D', 'E', 'serif']),
        ('', []),
        ('"", a', ['a']),
        ('serif', ['serif']),
        ('sans-serif', ['sans-serif']),
        ('A B C', ['A B C']),
        # Beyond tinycss's tests
        ('  A /* c */ B  ,', ['A B']),
        ('"A" B', ['A B']),
        ('"A" "B"', ['A', 'B']),
        ('A, calc(1px), B', ['A', 'B']),
    ],
)
def test_parse_font_family(css: str, expected: list[str]) -> None:
    assert parse_font_family(css) == expected


@pytest.mark.parametrize(
    ('names', 'expected'),
    [
        (['Arial', 'SansSerif', 'SERIF'], 'Arial, sans-serif, serif'),
        (['Helvetica Neue'], '"Helvetica Neue"'),
        (['X "Y'], '"X \\"Y"'),
        (['a\\b'], '"a\\\\b"'),
        (['1as'], '"1as"'),
        (['_a'], '"_a"'),
        # Names that start with and are not quoted (tinycss did, for css_parser); CSS-wide keywords and default are
        (['andale', 'And'], 'andale, And'),
        (['inherit', 'Initial', 'unset', 'revert', 'revert-layer', 'default'], ', '.join(
            f'"{n}"' for n in ('inherit', 'Initial', 'unset', 'revert', 'revert-layer', 'default'))),
        ([], ''),
    ],
)  # fmt: skip
def test_serialize_font_family(names: list[str], expected: str) -> None:
    assert serialize_font_family(names) == expected


def font(**kw: str | list[str]) -> FontDict:
    return {(('line' if k == 'height' else 'font') + '-' + k): v for k, v in kw.items()}  # ty: ignore[invalid-explicit-return]


@pytest.mark.parametrize(
    ('css', 'expected'),
    [
        ('caption', font(family=['sans-serif'])),
        ('12pt/14pt sans-serif', font(size='12pt', height='14pt', family=['sans-serif'])),
        ('80% sans-serif', font(size='80%', family=['sans-serif'])),
        (
            'x-large/110% "new century schoolbook", serif',
            font(size='x-large', height='110%', family=['new century schoolbook', 'serif']),
        ),
        (
            'bold italic large Palatino, serif',
            font(style='italic', weight='bold', size='large', family=['Palatino', 'serif']),
        ),
        (
            'normal small-caps 120%/120% fantasy',
            font(style='normal', variant='small-caps', size='120%', height='120%', family=['fantasy']),
        ),
        (
            'condensed oblique 12pt Helvetica Neue, serif',
            font(style='oblique', stretch='condensed', size='12pt', family=['Helvetica Neue', 'serif']),
        ),
        (
            '300 italic 1.3em/1.7em FB Armada, sans-serif',
            font(style='italic', weight='300', size='1.3em', height='1.7em', family=['FB Armada', 'sans-serif']),
        ),
        # Beyond tinycss's tests. Keywords are case-insensitive, unlike in tinycss.
        ('BOLD 12PT Serif', font(weight='bold', size='12PT', family=['Serif'])),
        ('  Caption ', font(family=['sans-serif'])),
        ('12pt/normal a', font(size='12pt', height='normal', family=['a'])),
        ('12pt / 1.5 a', font(size='12pt', height='1.5', family=['a'])),
        ('12px /**/ / 2 a', font(size='12px', height='2', family=['a'])),
        # normal sets the first longhand that nothing else sets, and there can be four of them
        ('normal 12px a', font(style='normal', size='12px', family=['a'])),
        ('bold normal italic 12px a', font(style='italic', variant='normal', weight='bold', size='12px', family=['a'])),
        (
            'normal normal normal normal 12px a',
            font(style='normal', variant='normal', weight='normal', stretch='normal', size='12px', family=['a']),
        ),
        # CSS Fonts 4
        ('oblique 10deg 12px a', font(style='oblique 10deg', size='12px', family=['a'])),
        ('oblique -1.5rad 12px a', font(style='oblique -1.5rad', size='12px', family=['a'])),
        ('oblique calc(10deg) 12px a', font(style='oblique calc(10deg)', size='12px', family=['a'])),
        ('Left 12px a', font(style='left', size='12px', family=['a'])),
        ('450.5 12px a', font(weight='450.5', size='12px', family=['a'])),
        ('calc(2 * 300) 12px a', font(weight='calc(2 * 300)', size='12px', family=['a'])),
        ('calc(1em + 2px)/calc(1.2) a', font(size='calc(1em + 2px)', height='calc(1.2)', family=['a'])),
        ('xxx-large a', font(size='xxx-large', family=['a'])),
        ('math math, ui-serif', font(size='math', family=['math', 'ui-serif'])),
        ('0 a', font(size='0', family=['a'])),  # a unitless zero is a length
        ('12px/0 a', font(size='12px', height='0', family=['a'])),
        # After the size, without a /, everything is the family
        ('12px normal', font(size='12px', family=['normal'])),
        ('12px bold serif', font(size='12px', family=['bold serif'])),
        ('12px large a', font(size='12px', family=['large a'])),
        ('12px Red\\/Black, "X \\"Y", "", a  b', font(size='12px', family=['Red/Black', 'X "Y', '', 'a b'])),
        ('12px caption', font(size='12px', family=['caption'])),  # system fonts are only keywords alone
        # Invalid values give nothing. These were partly read by tinycss.
        ('', {}),
        ('serif', {}),  # no size
        ('bold serif', {}),
        ('"caption" 12pt', {}),
        ('caption 12px a', {}),
        ('inherit', {}),  # valid, but not a font
        ('12px var(--f)', {}),
        ('var(--f)', {}),
        ('12px inherit', {}),
        ('12px Inherit', {}),
        ('12px unset, x', {}),
        ('12px revert-layer', {}),
        ('12px a, inherit', {}),
        ('12px Inherit Sans', {}),
        ('12px default', {}),
        ('inherit 12px a', {}),
        ('12px 1.4 a', {}),
        ('12px 14px a', {}),
        ('12px 120% a', {}),
        ('12px, a', {}),
        ('12px a,', {}),
        ('12px a,,b', {}),
        ('12px "a" b', {}),
        ('12px a / b', {}),
        ('12px/ a', {}),
        ('12px/inherit a', {}),
        ('12px/-1 a', {}),
        ('12px/1.4/2 a', {}),
        ('12px a!', {}),
        ('12px calc(1px) a', {}),
        ('-1px a', {}),
        ('12deg a', {}),
        ('calc(10deg) a', {}),
        ('italic italic 12px a', {}),
        ('bold 500 12px a', {}),
        ('normal normal normal normal normal 12px a', {}),
        ('bold normal normal normal normal 12px a', {}),
        ('1001 12px a', {}),
        ('oblique 91deg 12px a', {}),
        ('50% 12px a', {}),  # font-width percentages are not in the shorthand
        ('all-small-caps 12px a', {}),  # nor are the font-variant values after CSS 2.1
        ('(x) 12px a', {}),
    ],
)
def test_parse_font(css: str, expected: FontDict) -> None:
    assert parse_font(css) == expected
    if expected and 'font-size' in expected:
        assert parse_font(serialize_font(expected)) == normalized(expected)


def test_css_wide_keywords() -> None:
    assert {'inherit', 'initial', 'unset', 'revert', 'revert-layer'} == kgcss.CSS_WIDE_KEYWORDS
    assert 'CSS_WIDE_KEYWORDS' in kgcss.__all__
    # parse_font_family() cannot tell a keyword from a quoted name: callers check the text first
    assert parse_font_family('inherit') == parse_font_family('"inherit"') == ['inherit']
    assert ' Inherit '.strip().lower() in kgcss.CSS_WIDE_KEYWORDS
    assert '"inherit"'.strip().lower() not in kgcss.CSS_WIDE_KEYWORDS


def test_serialize_font_quotes_keywords() -> None:
    d = font(size='12pt', family=['bold', 'caption', 'Large'])
    assert serialize_font(d) == '12pt "bold", "caption", "Large"'
    assert parse_font(serialize_font(d)) == d
    assert serialize_font_family(['bold']) == 'bold'


# calibre's normalize_font(), from normalize_css.py, with its tests
FONT_DEFAULTS = {
    'font-style': 'normal',
    'font-variant': 'normal',
    'font-weight': 'normal',
    'font-size': 'medium',
    'line-height': 'normal',
    'font-family': 'serif',
}


def normalize_font(val: str) -> dict[str, Any]:
    if val == 'inherit':
        return dict.fromkeys(FONT_DEFAULTS, 'inherit')
    ans: dict[str, Any] = dict(FONT_DEFAULTS)
    if val not in {'caption', 'icon', 'menu', 'message-box', 'small-caption', 'status-bar'}:
        ans.update(parse_font(val))
    if not isinstance(ans['font-family'], str):
        ans['font-family'] = serialize_font_family(ans['font-family'])
    return ans


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        # calibre gave these a font-family, but the font shorthand needs a size: CSS ignores them
        ('some_font', {}),
        ('inherit', dict.fromkeys(FONT_DEFAULTS, 'inherit')),
        ('1.2pt/1.4 A_Font', {'font-family': 'A_Font', 'font-size': '1.2pt', 'line-height': '1.4'}),
        ('bad font', {}),
        ('10% serif', {'font-family': 'serif', 'font-size': '10%'}),
        ('12px "My Font", serif', {'font-family': '"My Font", serif', 'font-size': '12px'}),
        (
            'normal 0.6em/135% arial,sans-serif',
            {'font-family': 'arial, sans-serif', 'font-size': '0.6em', 'line-height': '135%', 'font-style': 'normal'},
        ),
        (
            'bold italic large serif',
            {'font-family': 'serif', 'font-weight': 'bold', 'font-style': 'italic', 'font-size': 'large'},
        ),
        (
            'bold italic small-caps larger/normal serif',
            {
                'font-family': 'serif',
                'font-weight': 'bold',
                'font-style': 'italic',
                'font-size': 'larger',
                'line-height': 'normal',
                'font-variant': 'small-caps',
            },
        ),
        ('2em A B', {'font-family': '"A B"', 'font-size': '2em'}),
    ],
)
def test_calibre_font_normalization(raw: str, expected: dict[str, str]) -> None:
    assert normalize_font(raw) == {**FONT_DEFAULTS, **expected}


def test_doctests() -> None:
    assert doctest.testmod(kgcss.fonts).failed == 0


# Round trips

name_chars = st.characters(exclude_categories=['Cs'], exclude_characters='\x00')


@given(st.lists(st.text(name_chars, min_size=1, max_size=12), max_size=4))
def test_font_family_round_trip(names: list[str]) -> None:
    assume(all(n == n.strip() and n for n in names))
    expected = ['sans-serif' if n.lower() == 'sansserif' else n.lower() if n.lower() in GENERIC else n for n in names]
    assert parse_font_family(serialize_font_family(names)) == expected


def normalized(d: FontDict) -> FontDict:
    """The font with generic family names as serialize_font_family() writes them."""
    ans = dict(d)
    if 'font-family' in d:
        ans['font-family'] = [
            'sans-serif' if n.lower() == 'sansserif' else n.lower() if n.lower() in GENERIC else n
            for n in d['font-family']
        ]
    return cast('FontDict', ans)


WORDS = (
    'normal', 'inherit', 'initial', 'unset', 'revert', 'default', 'italic', 'oblique', 'left', 'small-caps',
    'all-small-caps', 'bold', 'bolder', 'lighter', 'condensed', 'ultra-expanded', 'small', 'x-large', 'xxx-large',
    'larger', 'math', 'caption', 'menu', 'serif', 'sans-serif', 'Arial', 'Helvetica', 'Neue', 'a_b', '€42',
    r'Red\/Black', '100', '400', '900', '450', '1001', '0', '1.5', '+700', '12pt', '1.3em', '80%', '0.6em', '-1px',
    '10deg', '-1rad', '100deg', '/', '/', ',', ',', '"A B"', '"bold"', '""', '"X \\"Y"', 'calc(1px)', 'calc(2 * 300)',
    'calc(1px + 1%)', 'calc(10deg)', 'var(--x)', '(x)', '!',
)  # fmt: skip


@st.composite
def font_texts(draw: st.DrawFn) -> str:
    parts = draw(st.lists(st.sampled_from(WORDS), max_size=8))
    seps = draw(st.lists(st.sampled_from((' ', ' ', '', '  ', '/**/')), min_size=len(parts), max_size=len(parts)))
    return ''.join(s + p for s, p in zip(seps, parts, strict=True)).strip()


BEFORE_SIZE_VALUES = {
    'font-style': ('italic', 'left', 'right', 'oblique', 'oblique 10deg', 'oblique -1.5rad', 'OBLIQUE calc(90deg)'),
    'font-variant': ('small-caps', 'Small-Caps'),
    'font-weight': ('bold', 'bolder', 'lighter', '1', '450.5', '1000', 'calc(2 * 300)'),
    'font-stretch': ('condensed', 'ultra-expanded', 'semi-condensed'),
}
SIZES = ('12pt', '0', '80%', 'x-large', 'xxx-large', 'smaller', 'math', 'calc(1em + 2px)', 'calc(10%)', '1Q')
HEIGHTS = ('normal', '0', '1.4', '120%', '1.2em', 'calc(1.2)')
FAMILY_IDENTS = ('a', 'Arial', 'serif', 'bold', 'normal', 'caption', 'x-large', r'Red\/Black', '€42')
FAMILY_STRINGS = ('"A B"', '"inherit"', '""', '"X \\"Y"')


@st.composite
def valid_fonts(draw: st.DrawFn) -> tuple[str, FontDict]:
    """A valid value of the font shorthand that is not a system font, and what parse_font() gives for it"""
    names = draw(st.lists(st.sampled_from(tuple(BEFORE_SIZE_VALUES)), unique=True))
    explicit = {n: draw(st.sampled_from(BEFORE_SIZE_VALUES[n])) for n in names}
    normals = draw(st.integers(0, 4 - len(explicit)))
    parts = draw(st.permutations([*explicit.values(), *(['NORMAL'] * normals)]))
    expected: dict[str, str | list[str]] = {}
    for name in BEFORE_SIZE_VALUES:
        if name in explicit:
            v = explicit[name]
            expected[name] = v if v[0].isdigit() or v.startswith('calc') else v.lower()
        elif normals:
            normals -= 1
            expected[name] = 'normal'
    expected['font-size'] = size = draw(st.sampled_from(SIZES))
    if draw(st.booleans()):
        expected['line-height'] = height = draw(st.sampled_from(HEIGHTS))
        size += draw(st.sampled_from(('/', ' / ', '/**/ /'))) + height
    parts.append(size)
    families = draw(
        st.lists(st.one_of(st.lists(st.sampled_from(FAMILY_IDENTS), min_size=1, max_size=3),
                           st.sampled_from(FAMILY_STRINGS)), min_size=1, max_size=3)
    )  # fmt: skip
    parts.append(', '.join(f if isinstance(f, str) else ' '.join(f) for f in families))
    # parse_font_family() gives the names, except that it drops an empty one
    expected['font-family'] = [(parse_font_family(f) or [''])[0] if isinstance(f, str) else
                               parse_font_family(' '.join(f))[0] for f in families]  # fmt: skip
    return ' '.join(parts), cast('FontDict', expected)


@st.composite
def nearly_valid_fonts(draw: st.DrawFn) -> str:
    """A valid font with one word replaced or inserted: mostly invalid, but close to the grammar"""
    words = draw(valid_fonts())[0].split(' ')
    i = draw(st.integers(0, len(words)))
    words[i : i + draw(st.integers(0, 1))] = [draw(st.sampled_from(WORDS))]
    return ' '.join(words)


@given(st.one_of(font_texts(), nearly_valid_fonts()))
def test_font_round_trip(text: str) -> None:
    d = parse_font(text)
    if 'font-size' in d:
        assert parse_font(serialize_font(d)) == normalized(d), (text, serialize_font(d))


@given(st.one_of(font_texts(), nearly_valid_fonts()))
def test_font_values_match_longhands(text: str) -> None:
    """Every value that parse_font() reads is valid for its longhand"""
    d = parse_font(text)
    if 'font-size' not in d:
        assert d in ({}, {'font-family': ['sans-serif']})
        return
    for name, value in d.items():
        if name == 'font-family':
            assert matches(name, serialize_font_family(cast('list[str]', value))), text
        else:
            assert matches(name, cast('str', value)), (text, name, value)


@given(valid_fonts())
def test_valid_fonts(case: tuple[str, FontDict]) -> None:
    text, expected = case
    assert parse_font(text) == expected
    assert parse_font(serialize_font(expected)) == normalized(expected)


@given(st.text())
def test_any_text(text: str) -> None:
    parse_font(text)
    parse_font_family(text)


tinycss_fonts: Any = None
if (CALIBRE / 'tinycss').exists():
    sys.path.insert(0, str(CALIBRE))
    try:
        tinycss_fonts = importlib.import_module('tinycss.fonts3')
    except ImportError:
        pass
    finally:
        sys.path.remove(str(CALIBRE))


@pytest.mark.skipif(tinycss_fonts is None, reason='tinycss is not in the benchmark cache')
@given(font_texts())
def test_font_family_same_as_tinycss(text: str) -> None:
    # parse_font() follows the grammar, where tinycss read what it could, so only parse_font_family() is compared
    assert parse_font_family(text) == tinycss_fonts.parse_font_family(text), text
