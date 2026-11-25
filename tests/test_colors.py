# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""parse_rgba() and parse_color(): CSS Color 4 colors (docs/plan-04-media-colors.md, §4).

The cases come from calibre's tinycss color4 tests and its callers' tests, the
color files of the CSS parsing suite, and a differential test against
calibre's C parser, from the benchmark cache.
"""

import dataclasses
import doctest
import importlib
import math
import pickle
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import kgcss.colors
from kgcss import RGBA, Color, parse_color, parse_rgba

from .suite import load_cases

CALIBRE = Path(__file__).parent.parent / 'bench' / '.cache' / 'calibre'

# The 8-bit sRGB values Chromium renders for these colors (tinycss/tests/color4.py)
COLOR4 = [
    ('rebeccapurple', (102, 51, 153, 255)),
    ('RebeccaPurple', (102, 51, 153, 255)),
    ('#f008', (255, 0, 0, 136)),
    ('#ff000080', (255, 0, 0, 128)),
    ('#AbCdEf12', (171, 205, 239, 18)),
    ('rgb(255 0 0 / 50%)', (255, 0, 0, 128)),
    ('rgb(100% 0 0)', (255, 0, 0, 255)),
    ('rgb(none 128 none)', (0, 128, 0, 255)),
    ('rgb(255, 0, 0, 50%)', (255, 0, 0, 128)),
    ('rgb(1e2 0 0)', (100, 0, 0, 255)),
    ('rgb(2.5e1, 1, 1)', (25, 1, 1, 255)),
    ('rgb(255/**/0/**/0)', (255, 0, 0, 255)),
    ('rgb(-10 300 0)', (0, 255, 0, 255)),
    ('hsl(0.5turn 50% 50%)', (64, 191, 191, 255)),
    ('hsl(3.14159rad 100% 50%)', (0, 255, 255, 255)),
    ('hsl(200grad 50% 50%)', (64, 191, 191, 255)),
    ('hsl(120 50 50)', (64, 191, 64, 255)),
    ('hsl(none 50% 50%)', (191, 64, 64, 255)),
    ('hsla(120 50% 50%)', (64, 191, 64, 255)),
    ('hsl(120 -50% 50%)', (128, 128, 128, 255)),
    ('hsl(120 150% 50%)', (0, 255, 0, 255)),
    ('hwb(120 20% 30%)', (51, 179, 51, 255)),
    ('hwb(120deg 60% 60%)', (128, 128, 128, 255)),
    ('hwb(90 20 30)', (115, 179, 51, 255)),
    ('lab(50% 40 59.5)', (191, 87, 0, 255)),
    ('lab(54.29 80.82 69.91)', (255, 0, 0, 255)),
    ('lab(100 0 0)', (255, 255, 255, 255)),
    ('lab(50 40% -20%)', (183, 80, 163, 255)),
    ('lch(54.29 106.84 40.85)', (255, 0, 0, 255)),
    ('lch(50% 50% 120deg)', (70, 134, 0, 255)),
    ('lch(70 45 none)', (245, 137, 173, 255)),
    ('lch(70 -10 30)', (171, 171, 171, 255)),
    ('oklab(0.628 0.2249 0.1258)', (255, 0, 0, 255)),
    ('oklab(62.8% 56% 31%)', (254, 6, 8, 255)),
    ('oklab(1 0 0)', (255, 255, 255, 255)),
    ('oklch(0.628 0.2577 29.23)', (255, 0, 0, 255)),
    ('oklch(70% 0.1 200)', (64, 177, 183, 255)),
    ('oklch(0.7 40% 0.25turn)', (197, 152, 0, 255)),
    ('color(srgb 100% 50% 0%)', (255, 128, 0, 255)),
    ('color(srgb-linear 0.5 0.2 0.1)', (188, 124, 89, 255)),
    ('color(display-p3 0.9175 0.2003 0.1386)', (255, 0, 0, 255)),
    ('color(a98-rgb 0.6 0.5 0.4)', (163, 128, 101, 255)),
    ('color(prophoto-rgb 0.5 0.4 0.3)', (171, 115, 90, 255)),
    ('color(rec2020 0.5 0.4 0.3)', (154, 112, 87, 255)),
    ('color(xyz 0.3 0.3 0.3)', (162, 145, 143, 255)),
    ('color(xyz-d65 0.2 0.3 0.4)', (0, 167, 164, 255)),
    ('color(xyz-d50 0.2 0.3 0.25)', (38, 167, 148, 255)),
    ('color(srgb none 0.5 none)', (0, 128, 0, 255)),
    ('COLOR(SRGB 1 0 0)', (255, 0, 0, 255)),
    ('OKLCH(0.7 0.1 200)', (64, 177, 183, 255)),
    (' red ', (255, 0, 0, 255)),
    ('red /* unterminated', (255, 0, 0, 255)),
    ('rgb(255 0 0', (255, 0, 0, 255)),
    ('transparent', (0, 0, 0, 0)),
    # Escapes are decoded, as in CSS (D2). tinycss gives None for these.
    ('r\\65 d', (255, 0, 0, 255)),
    ('r\\65 d', (255, 0, 0, 255)),
    ('\\72\\65\\64', (255, 0, 0, 255)),
    ('#\\66 f0', (255, 255, 0, 255)),
    ('rg\\62(255 0 0)', (255, 0, 0, 255)),
    ('hsl(0.5tur\\6e  50% 50%)', (64, 191, 191, 255)),
    ('color(s\\rgb 1 0 0)', (255, 0, 0, 255)),
    ('rgb(no\\ne 128 none)', (0, 128, 0, 255)),
    # Non-ASCII text is only allowed in comments
    ('red /* é */', (255, 0, 0, 255)),
    ('rgb(255 /* ☃ */ 0 0', (255, 0, 0, 255)),
]

NOT_COLORS = [
    '',
    ' ',
    '/* black */',
    '#',
    '#12',
    '#12345',
    '#1234567',
    '#ff0000f',
    '#ggg',
    'canvas',
    'red blue',
    'rgb(255 0 0) x',
    'rgb()',
    'rgb(255 0)',
    'rgb(255 0 0 0)',
    'rgb(255 0 0, 1)',
    'rgb(255 0 0 /)',
    'rgb(255 0 0 / 1 / 1)',
    'rgb(255, 0, 0, none)',
    'rgb(255, 0%, 0)',
    'rgb(var(--x))',
    'rgb(calc(1) 0 0)',
    'rgb(from red r g b)',
    'rgb(1px 0 0)',
    'hsl(120, 50, 50)',
    'hsl(120% 50% 50%)',
    'hsl(120px 50% 50%)',
    'hwb(120, 20%, 30%)',
    'lab(50, 40, 59)',
    'lab(50 40)',
    'color(foo 1 0 0)',
    'color(srgb 1 0)',
    'color(srgb, 1, 0, 0)',
    'color(1 0 0)',
    'cmyk(0 0 0 0)',
    'réd',
    'none',
    'bla/*bad*/ck',
    'black-black',
    'black-',
    '-black',
    '-bla-ck',
    'current/*yes*/Color',
    'color-mix(in srgb, red, blue)',
    'light-dark(red, blue)',
    'device-cmyk(0 0 0 0)',
    'rgb(255 0 0)red',
    '"red"',
    'url(red)',
    'red;',
    'red !important',
    '1e999',
    'rgb(1e999 0 0)',
    'rgb(1e-999999 0 0 / 2e999)',
    # Escapes must decode to a name: these do not
    '\\31 23',
    'r\\e9 d',
    '#\\67 00',
    'rgb(\\31 0 0)',
    'hsl(0.5\\74urn 50% 50%)x',
]


def to_bytes(rgba: tuple[float, ...]) -> tuple[int, ...]:
    return tuple(round(x * 255) for x in rgba)


@pytest.mark.parametrize(('css', 'expected'), COLOR4)
def test_color4(css: str, expected: tuple[int, int, int, int]) -> None:
    ans = parse_rgba(css)
    assert isinstance(ans, RGBA), css
    for got, want in zip(to_bytes(ans), expected, strict=True):
        assert abs(got - want) <= 1, f'{css}: {to_bytes(ans)} != {expected}'


@pytest.mark.parametrize('css', NOT_COLORS)
def test_not_colors(css: str) -> None:
    assert parse_rgba(css) is None


def test_current_color() -> None:
    for css in ('currentColor', 'currentcolor', ' CURRENTCOLOR ', 'current\\43 olor'):
        assert parse_rgba(css) == 'currentColor'


def test_argument_type() -> None:
    with pytest.raises(TypeError):
        parse_rgba(b'red')  # type: ignore[arg-type]


def test_rgba_type() -> None:
    ans = parse_rgba('rgb(255 0 0 / 50%)')
    assert isinstance(ans, RGBA)
    assert isinstance(ans, tuple)
    assert ans == (1.0, 0.0, 0.0, 0.5)
    assert (ans.red, ans.green, ans.blue, ans.alpha) == ans
    assert ans[:3] == (1.0, 0.0, 0.0)
    match ans:
        case RGBA(red=r, alpha=a):
            assert (r, a) == (1.0, 0.5)
    assert pickle.loads(pickle.dumps(ans)) == ans
    assert repr(ans) == 'kgcss.RGBA(red=1.0, green=0.0, blue=0.0, alpha=0.5)'


def test_doctests() -> None:
    assert doctest.testmod(kgcss.colors).failed == 0


def rgb_function(text: str) -> tuple[float, ...]:
    """The value of rgb(r, g, b) or rgba(r, g, b, a) as serialized by the suite, in 0..1."""
    m = re.fullmatch(r'rgba?\((.+)\)', text)
    assert m is not None, text
    values = [float(x) for x in m.group(1).split(',')]
    return (*(v / 255 for v in values[:3]), values[3] if len(values) == 4 else 1.0)


@pytest.mark.parametrize('name', ['color_keywords_3.json', 'color_keywords_4.json', 'color_hexadecimal_3.json',
                                  'color_hexadecimal_4.json'])  # fmt: skip
def test_suite_keywords_and_hex(name: str) -> None:
    # The serializations of these are all rgb() and rgba(); the other files are for Color, in milestone 2
    for css, expected in load_cases(name):
        ans = parse_rgba(css)
        if expected is None:
            assert ans is None, css
        elif css.lower() == 'currentcolor':
            assert ans == 'currentColor'
        else:
            assert isinstance(ans, RGBA), css
            assert isinstance(expected, str)
            assert ans == pytest.approx(rgb_function(expected), abs=1e-6), css


# tinycss.color3 results, for colors that are valid CSS 3 (tinycss/tests/color4.py): Level 4 must agree.
COLOR3 = [
    ('#fff', (1.0, 1.0, 1.0, 1.0)),
    ('#CbA987', (0.796078431372549, 0.6627450980392157, 0.5294117647058824, 1.0)),
    ('rgb(203, 169, 135)', (0.796078431372549, 0.6627450980392157, 0.5294117647058824, 1.0)),
    ('rgb(0,51,255)', (0.0, 0.2, 1.0, 1.0)),
    ('rgb(/* R */0, /* G */51, /* B */255)', (0.0, 0.2, 1.0, 1.0)),
    ('rgb(10%, 20%, 30%)', (0.1, 0.2, 0.3, 1.0)),
    ('rgba(1, 2, 3, 0.5)', (0.00392156862745098, 0.00784313725490196, 0.011764705882352941, 0.5)),
    ('hsl(120, 50%, 50%)', (0.25, 0.75, 0.25, 1.0)),
    ('hsla(-300, 100%, 37.5%, 0.2)', (0.75, 0.7499999999999998, 0.0, 0.2)),
    (' /**/red/**/ ', (1.0, 0.0, 0.0, 1.0)),
]


@pytest.mark.parametrize(('css', 'expected'), COLOR3)
def test_superset_of_color3(css: str, expected: tuple[float, ...]) -> None:
    ans = parse_rgba(css)
    assert isinstance(ans, RGBA)
    assert ans == pytest.approx(expected, abs=1e-10)


# calibre's callers, with their tests (docx/writer/utils.py and mobi/utils.py)
def convert_color(value: str | None) -> str | None:
    if not value:
        return None
    val = parse_rgba(value)
    if val == 'currentColor':
        return 'auto'
    if not isinstance(val, tuple) or val.alpha < 0.01:
        return None
    return ''.join(f'{int(x * 255 + 0.5):02X}' for x in val[:3])


def convert_color_for_font_tag(val: object) -> str:
    rgba = parse_rgba(str(val or ''))
    if not isinstance(rgba, tuple):
        return str(val)
    return '#' + ''.join(f'{int(x * 255 + 0.5):02x}' for x in rgba[:3])


@pytest.mark.parametrize(
    ('fn', 'value', 'expected'),
    [
        (convert_color, None, None),
        (convert_color, 'transparent', None),
        (convert_color, 'none', None),
        (convert_color, '#12j456', None),
        (convert_color, 'currentColor', 'auto'),
        (convert_color, ' currentColor ', 'auto'),
        (convert_color, 'AliceBlue', 'F0F8FF'),
        (convert_color, 'black', '000000'),
        (convert_color, 'red', 'FF0000'),
        (convert_color, 'lime', '00FF00'),
        (convert_color, '#001', '000011'),
        (convert_color, '#12345d', '12345D'),
        (convert_color, 'rgb(255, 255, 255)', 'FFFFFF'),
        (convert_color, 'rgba(255, 0, 0, 23)', 'FF0000'),
        (convert_color, 'rgb(10%, 20%, 30%)', '1A334D'),
        (convert_color, 'rgb(-51, 306, 0)', '00FF00'),
        (convert_color, '#ff000080', 'FF0000'),
        (convert_color, '#ff000000', None),
        (convert_color, 'rgb(255 0 0 / 50%)', 'FF0000'),
        (convert_color, 'rebeccapurple', '663399'),
        (convert_color, 'hsl(120deg 50% 50%)', '40BF40'),
        (convert_color, 'hwb(120 20% 30%)', '33B333'),
        (convert_color, 'lab(100 0 0)', 'FFFFFF'),
        (convert_color_for_font_tag, 'currentColor', 'currentColor'),
        (convert_color_for_font_tag, 'inherit', 'inherit'),
        (convert_color_for_font_tag, 'red', '#ff0000'),
        (convert_color_for_font_tag, '#012', '#001122'),
        (convert_color_for_font_tag, 'rgb(10%, 20%, 30%)', '#1a334d'),
        (convert_color_for_font_tag, 'rgb(-51, 306, 0)', '#00ff00'),
        (convert_color_for_font_tag, '#ff000080', '#ff0000'),
        (convert_color_for_font_tag, 'rgb(255 0 0 / 50%)', '#ff0000'),
        (convert_color_for_font_tag, 'rebeccapurple', '#663399'),
        (convert_color_for_font_tag, 'lab(100 0 0)', '#ffffff'),
        (convert_color_for_font_tag, 'oklch(0.628 0.2577 29.23)', '#ff0000'),
        (convert_color_for_font_tag, 'rgb(var(--x))', 'rgb(var(--x))'),
    ],
)
def test_calibre_callers(fn: Callable[[str | None], str | None], value: str | None, expected: str | None) -> None:
    assert fn(value) == expected


# Random color-like text. Each piece is (text, is_name): names can be escaped without changing the meaning.
type Pieces = list[tuple[str, bool]]

FUNCTIONS = ('rgb', 'rgba', 'hsl', 'hsla', 'hwb', 'lab', 'lch', 'oklab', 'oklch', 'color', 'calc', 'foo')
SPACES = ('srgb', 'srgb-linear', 'display-p3', 'a98-rgb', 'prophoto-rgb', 'rec2020', 'xyz', 'xyz-d50', 'xyz-d65', 'x')
NAMES = ('red', 'transparent', 'currentcolor', 'rebeccapurple', 'aliceblue', 'canvas', 'none', 'grey')
UNITS = ('', '', '%', 'deg', 'grad', 'rad', 'turn', 'px', 'e')


def mixed_case(draw: st.DrawFn, text: str) -> str:
    flips = draw(st.lists(st.booleans(), min_size=len(text), max_size=len(text)))
    return ''.join(c.upper() if f else c for c, f in zip(text, flips, strict=True))


@st.composite
def numbers(draw: st.DrawFn) -> str:
    sign = draw(st.sampled_from(('', '', '-', '+')))
    whole = draw(st.sampled_from(('', '0', '1', '50', '100', '255', '360', '12345678901234567890')))
    frac = draw(st.sampled_from(('', '', '.5', '.25', '.')))
    exp = draw(st.sampled_from(('', '', 'e2', 'E-1', 'e+3', 'e999')))
    return sign + (whole or ('' if frac else '0')) + frac + exp


@st.composite
def separators(draw: st.DrawFn) -> str:
    return draw(st.sampled_from((' ', ' ', '', '  ', '\n', '/**/', ' /* x */ ')))


# The arguments of each function, for plausible colors: H a hue, P a percentage or number
SIGNATURES = {
    'rgb': 'PPP', 'rgba': 'PPP', 'hsl': 'HPP', 'hsla': 'HPP', 'hwb': 'HPP', 'lab': 'PPP', 'lch': 'PPH',
    'oklab': 'PPP', 'oklch': 'PPH', 'color': 'PPP',
}  # fmt: skip


@st.composite
def plausible_function(draw: st.DrawFn) -> Pieces:
    """A function that follows its grammar, mostly: about half are valid."""
    name = draw(st.sampled_from(sorted(SIGNATURES)))
    pieces: Pieces = [(mixed_case(draw, name), True), ('(', False)]
    if name == 'color':
        pieces += [(mixed_case(draw, draw(st.sampled_from(SPACES[:-1]))), True), (' ', False)]
    legacy = name in ('rgb', 'rgba', 'hsl', 'hsla') and draw(st.booleans())
    percent = draw(st.booleans())  # legacy rgb(): all percentages or all numbers
    for i, kind in enumerate(SIGNATURES[name]):
        if i:
            pieces.append((',' if legacy else '', False))
            pieces.append((draw(separators()) or ' ', False))
        if not legacy and draw(st.integers(0, 8)) == 0:
            pieces.append((mixed_case(draw, 'none'), True))
            continue
        value = draw(st.sampled_from(('0', '1', '0.5', '50', '120', '255', '-20', '300', '1e2', '.25')))
        pieces.append((value, False))
        if kind == 'H':
            unit = draw(st.sampled_from(('', 'deg', 'grad', 'rad', 'turn')))
            pieces.append((mixed_case(draw, unit), True) if unit else ('', False))
        elif (legacy and name.startswith('hsl')) or (legacy and percent) or (not legacy and draw(st.booleans())):
            pieces.append(('%', False))
    if draw(st.booleans()):
        alpha = draw(st.sampled_from(('0', '0.5', '1', '50%', '2', '-1', 'none')))
        pieces.append((', ' if legacy else ' / ', False))
        pieces.append((alpha, alpha == 'none'))
    if draw(st.integers(0, 6)):
        pieces.append((')', False))
    return pieces


@st.composite
def random_function(draw: st.DrawFn) -> Pieces:
    name = draw(st.sampled_from(FUNCTIONS))
    pieces: Pieces = [(mixed_case(draw, name), True), ('(', False)]
    if name == 'color' and draw(st.booleans()):
        pieces += [(draw(separators()), False), (mixed_case(draw, draw(st.sampled_from(SPACES))), True)]
    legacy = draw(st.booleans())
    for i in range(draw(st.sampled_from((1, 2, 3, 3, 3, 3, 4, 5)))):
        sep = (',' if legacy else '') + draw(separators())
        if i:
            pieces.append((sep, False))
        if i == 3 and not legacy:
            pieces.append(('/ ', False))
        if draw(st.integers(0, 5)) == 0:
            pieces.append((mixed_case(draw, 'none'), True))
        else:
            pieces.append((draw(numbers()), False))
            unit = draw(st.sampled_from(UNITS))
            if unit and unit != '%':
                pieces.append((mixed_case(draw, unit), True))
            else:
                pieces.append((unit, False))
    if draw(st.integers(0, 6)):
        pieces.append((draw(separators()) + ')', False))
    return pieces


@st.composite
def color_pieces(draw: st.DrawFn) -> Pieces:
    lead = draw(st.sampled_from(('', '', ' ', '/**/')))
    trail = draw(st.sampled_from(('', '', '', ' ', '/* x */', ' x', ' /*')))
    match draw(st.sampled_from(('plausible', 'plausible', 'plausible', 'random', 'hash', 'name'))):
        case 'hash':
            digits = draw(st.text('0123456789abcdefABCDEFg', min_size=0, max_size=9))
            body: Pieces = [('#', False), (digits, False)]
        case 'name':
            body = [(mixed_case(draw, draw(st.sampled_from(NAMES))), True)]
        case 'plausible':
            body = draw(plausible_function())
        case _:
            body = draw(random_function())
    return [(lead, False), *body, (trail, False)]


def render(pieces: Pieces) -> str:
    return ''.join(text for text, _ in pieces)


@given(color_pieces())
def test_no_exceptions(pieces: Pieces) -> None:
    css = render(pieces)
    ans = parse_rgba(css)
    assert ans is None or ans == 'currentColor' or (len(ans) == 4 and all(0 <= x <= 1 for x in ans)), css


@given(st.text())
def test_any_text(text: str) -> None:
    parse_rgba(text)


def escape(data: st.DataObject, name: str) -> str:
    out = []
    for c in name:
        match data.draw(st.integers(0, 3)) if c.isalpha() else 0:
            case 1:
                # The space ends the escape, and is part of it
                out.append(f'\\{ord(c):x} ')
            case 2 if c.lower() not in 'abcdef':
                out.append('\\' + c)
            case _:
                out.append(c)
    return ''.join(out)


@given(st.data(), color_pieces())
def test_escaped_names(data: st.DataObject, pieces: Pieces) -> None:
    css = render(pieces)
    escaped = ''.join(escape(data, text) if is_name else text for text, is_name in pieces)
    assert parse_rgba(escaped) == parse_rgba(css), (css, escaped)


calibre_parser: Callable[[str], object] | None = None
if (CALIBRE / 'calibre_extensions').exists():
    sys.path.insert(0, str(CALIBRE))
    try:
        calibre_parser = importlib.import_module('calibre_extensions.tokenizer').parse_css4_color_string
    except ImportError:
        pass
    finally:
        sys.path.remove(str(CALIBRE))


@pytest.mark.skipif(calibre_parser is None, reason='calibre_extensions is not in the benchmark cache')
@given(color_pieces())
def test_same_as_calibre(pieces: Pieces) -> None:
    # calibre's parser: ASCII without backslashes only, the input of the fast path
    assert calibre_parser is not None
    css = render(pieces)
    if '12345678901234567890' in css and 'lch' in css.lower():
        # kgcss normalizes hues with fmod(), exactly; calibre passes the huge angle to cos() and sin()
        return
    expected = calibre_parser(css)
    ans = parse_rgba(css)
    if isinstance(expected, tuple):
        assert isinstance(ans, RGBA), css
        assert ans == pytest.approx(expected, abs=1e-12), css
    else:
        assert ans == expected, css


# The suite's color files, through parse_color().to_css()

SUITE_FILES = (
    'color_keywords_3.json', 'color_keywords_4.json', 'color_hexadecimal_3.json', 'color_hexadecimal_4.json',
    'color_hsl_3.json', 'color_hsl_4.json', 'color_hwb_4.json', 'color_lab_4.json', 'color_lch_4.json',
    'color_oklab_4.json', 'color_oklch_4.json', 'color_function_4.json',
)  # fmt: skip
# The lightness range of each function and whether it has a chroma, for the clamping below
FUNCTION_RANGES = {'lab': (100, False), 'lch': (100, True), 'oklab': (1, False), 'oklch': (1, True)}
TOKEN = re.compile(r'none|[a-z][a-z0-9-]*|[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?|[,/]')


def css_parts(text: str) -> tuple[str, list[str | float]]:
    m = re.fullmatch(r'([a-z-]+)\((.*)\)', text)
    assert m is not None, text
    tokens: list[str | float] = []
    for t in TOKEN.findall(m.group(2)):
        try:
            tokens.append(float(t))
        except ValueError:
            tokens.append(t)
    return m.group(1), tokens


def spec_expected(text: str) -> tuple[str, list[str | float]]:
    """The suite's expected serialization, with the clamping that CSS Color 4 does at parsed-value time:
    lightness to its range and negative chroma to 0. The suite keeps them as written."""
    name, tokens = css_parts(text)
    if name in FUNCTION_RANGES:
        top, chroma = FUNCTION_RANGES[name]
        if isinstance(tokens[0], float):
            tokens[0] = min(top, max(0.0, tokens[0]))
        if chroma and isinstance(tokens[1], float):
            tokens[1] = max(0.0, tokens[1])
    return name, tokens


def same_parts(a: tuple[str, list[str | float]], b: tuple[str, list[str | float]]) -> bool:
    if a[0] != b[0] or len(a[1]) != len(b[1]):
        return False
    for x, y in zip(a[1], b[1], strict=True):
        if isinstance(x, float) and isinstance(y, float):
            if not math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-6):
                return False
        elif x != y:
            return False
    return True


@pytest.mark.parametrize('name', SUITE_FILES)
def test_suite(name: str) -> None:
    failures: list[tuple[str, object, object]] = []
    for css, expected in load_cases(name):
        ans = parse_color(css)
        if expected is None or ans is None:
            if ans != expected:
                failures.append((css, expected, ans))
        elif ans == 'currentColor':
            if expected != 'currentcolor':
                failures.append((css, expected, ans))
        else:
            assert isinstance(expected, str)
            got = ans.to_css()
            if not same_parts(css_parts(got), spec_expected(expected)):
                failures.append((css, expected, got))
            # parse_rgba() is parse_color().to_rgba()
            assert parse_rgba(css) == ans.to_rgba(), css
    assert not failures, f'{len(failures)} failures, the first: {failures[:5]}'


def test_suite_level_5() -> None:
    # CSS Color 5 is out of scope (§1): device-cmyk(), light-dark() and custom spaces are not colors
    for css, _ in load_cases('color_functions_5.json'):
        assert parse_color(css) is None, css


# The ranges of the channels of each space after parsing, for generated colors
def channel(low: float, high: float) -> st.SearchStrategy[float]:
    return st.floats(low, high, allow_nan=False, allow_infinity=False)


HUE = channel(0, 359.999)
SPACE_CHANNELS: dict[str, tuple[st.SearchStrategy[float], ...]] = {
    'rgb': (channel(0, 255),) * 3,
    'lab': (channel(0, 100), channel(-200, 200), channel(-200, 200)),
    'lch': (channel(0, 100), channel(0, 250), HUE),
    'oklab': (channel(0, 1), channel(-0.5, 0.5), channel(-0.5, 0.5)),
    'oklch': (channel(0, 1), channel(0, 0.5), HUE),
    **{space: (channel(-2, 3),) * 3 for space in ('srgb', 'srgb-linear', 'display-p3', 'a98-rgb', 'prophoto-rgb',
                                                  'rec2020', 'xyz-d50', 'xyz-d65')},
}  # fmt: skip


@st.composite
def colors(draw: st.DrawFn) -> Color:
    space = draw(st.sampled_from(sorted(SPACE_CHANNELS)))
    none = space != 'rgb'

    def value(s: st.SearchStrategy[float]) -> float | None:
        return None if none and draw(st.integers(0, 6)) == 0 else draw(s)

    c0, c1, c2 = (value(s) for s in SPACE_CHANNELS[space])
    return Color(space, (c0, c1, c2), value(st.sampled_from((1.0, 0.0)) | channel(0, 1)))  # type: ignore[arg-type]


def close(a: float | None, b: float | None) -> bool:
    return a is b or (a is not None and b is not None and math.isclose(a, b, abs_tol=1e-6))


@given(colors())
def test_to_css_round_trip(color: Color) -> None:
    css = color.to_css()
    again = parse_color(css)
    assert isinstance(again, Color), css
    assert again.to_css() == css
    assert again.space == color.space
    assert all(close(a, b) for a, b in zip((*again.channels, again.alpha), (*color.channels, color.alpha), strict=True))
    assert again.to_rgba() == pytest.approx(color.to_rgba(), abs=1e-5), css


@given(color_pieces())
def test_parse_rgba_is_parse_color_to_rgba(pieces: Pieces) -> None:
    css = render(pieces)
    ans = parse_color(css)
    assert parse_rgba(css) == (ans.to_rgba() if isinstance(ans, Color) else ans), css


@given(st.text())
def test_parse_color_any_text(text: str) -> None:
    parse_color(text)


def test_color_model() -> None:
    c = parse_color('lch(150% -10 -30 / none)')
    assert c == Color('lch', (100.0, 0.0, 330.0), None)
    assert c.to_css() == 'lch(100 0 330 / none)'
    assert parse_color('color(xyz 1 2 3)') == Color('xyz-d65', (1.0, 2.0, 3.0))
    assert parse_color('rgb(none 300 -5 / 2)') == Color('rgb', (0.0, 255.0, 0.0))
    assert parse_color('transparent') == Color('rgb', (0.0, 0.0, 0.0), 0.0)
    assert parse_color('oklab(0.5 none 0.1)').to_css() == 'oklab(0.5 none 0.1)'  # type: ignore[union-attr]
    assert parse_color('currentColor') == 'currentColor'
    assert parse_color('rgb(var(--x))') is None
    assert Color('rgb', (1, 2, 3), 0.9999999).to_css() == 'rgb(1, 2, 3)'
    assert Color('srgb', (1.5, -0.25, 1 / 3), 0.5).to_css() == 'color(srgb 1.5 -0.25 0.333333 / 0.5)'
    assert Color('srgb', (1.5, -0.25, 0)).to_rgba() == (1.0, 0.0, 0.0, 1.0)
    with pytest.raises(TypeError):
        parse_color(b'red')  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='Unknown color space'):
        Color('cmyk', (0, 0, 0))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='three channels'):
        Color('lab', (0, 0))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='finite'):
        Color('lab', (math.inf, 0, 0))
    with pytest.raises(ValueError, match='no none'):
        Color('rgb', (None, 0, 0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.alpha = 1  # type: ignore[misc]


def test_components() -> None:
    from kgcss import parse_value

    value = parse_value('1px solid rgb(1 2 3) #fff red', property_name='border')
    found = [parse_color(c) for c in value]
    assert found == [
        None,
        None,
        Color('rgb', (1.0, 2.0, 3.0)),
        Color('rgb', (255.0,) * 3),
        Color('rgb', (255.0, 0.0, 0.0)),
    ]
