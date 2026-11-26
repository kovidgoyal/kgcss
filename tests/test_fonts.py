# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The font helpers (docs/plan-05-fonts-grammar.md, §2.1).

The cases come from tinycss/tests/fonts3.py and the font tests of calibre's
normalize_css.py. A differential test compares with tinycss.fonts3, from the
benchmark cache.
"""

import doctest
import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

import kgcss.fonts
from kgcss import FontDict, parse_font, parse_font_family, serialize_font, serialize_font_family

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
        # D2: names that start with and are not quoted; CSS-wide keywords and default are
        (['andale', 'And'], 'andale, And'),
        (['inherit', 'Initial', 'unset', 'revert', 'revert-layer', 'default'], ', '.join(
            f'"{n}"' for n in ('inherit', 'Initial', 'unset', 'revert', 'revert-layer', 'default'))),
        ([], ''),
    ],
)  # fmt: skip
def test_serialize_font_family(names: list[str], expected: str) -> None:
    assert serialize_font_family(names) == expected


def font(**kw: str | list[str]) -> FontDict:
    return {(('line' if k == 'height' else 'font') + '-' + k): v for k, v in kw.items()}  # type: ignore[return-value]


@pytest.mark.parametrize(
    ('css', 'expected'),
    [
        ('caption', font(family=['sans-serif'])),
        ('serif', font(family=['serif'])),
        ('12pt/14pt sans-serif', font(size='12pt', height='14pt', family=['sans-serif'])),
        ('80% sans-serif', font(size='80%', family=['sans-serif'])),
        (
            'x-large/110% "new century schoolbook", serif',
            font(size='x-large', height='110%', family=['new century schoolbook', 'serif']),
        ),
        (
            'bold italic large Palatino, serif',
            font(weight='bold', style='italic', size='large', family=['Palatino', 'serif']),
        ),
        (
            'normal small-caps 120%/120% fantasy',
            font(style='normal', variant='small-caps', size='120%', height='120%', family=['fantasy']),
        ),
        (
            'condensed oblique 12pt Helvetica Neue, serif',
            font(stretch='condensed', style='oblique', size='12pt', family=['Helvetica Neue', 'serif']),
        ),
        (
            '300 italic 1.3em/1.7em FB Armada, sans-serif',
            font(weight='300', style='italic', size='1.3em', height='1.7em', family=['FB Armada', 'sans-serif']),
        ),
        # Beyond tinycss's tests. Keywords are case-insensitive, unlike in tinycss.
        ('BOLD 12PT Serif', font(weight='bold', size='12PT', family=['Serif'])),
        ('12pt/normal a', font(size='12pt', height='normal', family=['a'])),
        ('12pt / 1.5 a', font(size='12pt', height='1.5', family=['a'])),
        ('"caption" 12pt', font(family=['caption'])),
        ('', {}),
    ],
)
def test_parse_font(css: str, expected: FontDict) -> None:
    assert parse_font(css) == expected
    assert normalized(parse_font(serialize_font(expected))) == normalized(expected)


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
        ('some_font', {'font-family': 'some_font'}),
        ('inherit', dict.fromkeys(FONT_DEFAULTS, 'inherit')),
        ('1.2pt/1.4 A_Font', {'font-family': 'A_Font', 'font-size': '1.2pt', 'line-height': '1.4'}),
        ('bad font', {'font-family': '"bad font"'}),
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
        ans['font-family'] = parse_font_family(serialize_font_family(d['font-family']))
    return ans  # type: ignore[return-value]


WORDS = (
    'normal', 'inherit', 'initial', 'unset', 'italic', 'oblique', 'small-caps', 'bold', 'bolder', 'lighter',
    'condensed', 'ultra-expanded', 'small', 'x-large', 'larger', 'caption', 'menu', 'serif', 'sans-serif',
    'Arial', 'Helvetica', 'Neue', 'a_b', '€42', r'Red\/Black',
    '100', '400', '900', '450', '0', '1.5', '+700', '12pt', '1.3em', '80%', '0.6em', '-1px', '/', ',', ',',
    '"A B"', '"bold"', '""', '"X \\"Y"', 'calc(1px)', '(x)', '!',
)  # fmt: skip


@st.composite
def font_texts(draw: st.DrawFn) -> str:
    parts = draw(st.lists(st.sampled_from(WORDS), max_size=8))
    seps = draw(st.lists(st.sampled_from((' ', ' ', '', '  ', '/**/')), min_size=len(parts), max_size=len(parts)))
    return ''.join(s + p for s, p in zip(seps, parts, strict=True)).strip()


@given(font_texts())
def test_font_round_trip(text: str) -> None:
    d = parse_font(text)
    assert normalized(parse_font(serialize_font(d))) == normalized(d), (text, serialize_font(d))


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
def test_same_as_tinycss(text: str) -> None:
    # Differences: tinycss reads a string "caption" as the legacy font; WORDS has no upper case keywords
    assume('"caption"' not in text)
    tinycss_parse_font: Callable[[str], object] = tinycss_fonts.parse_font
    try:
        expected = tinycss_parse_font(text)
    except AttributeError:
        # tinycss fails when the value starts with a function, such as calc(1px)
        return
    assert parse_font(text) == expected, text
    assert parse_font_family(text) == tinycss_fonts.parse_font_family(text), text
