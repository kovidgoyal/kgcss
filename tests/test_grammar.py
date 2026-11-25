# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The value grammar of the longhands (docs/plan-05-fonts-grammar.md, §2.2)."""

import doctest
import timeit
from functools import partial

import pytest
from hypothesis import given
from hypothesis import strategies as st

import kgcss.grammar
from kgcss import is_color, matches, parse_value
from kgcss.grammar import PROPERTIES

VALID = {
    'border-top-style': ['solid', 'none', 'hidden', 'SOLID', 'double', 'outset'],
    'border-right-width': ['thin', 'MEDIUM', 'thick', '1px', '0', '0.5em', 'calc(1px + 2px)', '1Q'],
    'border-bottom-color': ['red', '#abc', '#abcd', 'rgb(1,2,3)', 'rgba(1,2,3,.5)', 'transparent', 'currentColor',
                            'canvas', 'ActiveBorder', 'lab(1 2 3)', 'r\\65 d'],
    'list-style-type': ['disc', 'upper-roman', 'none', 'foo', '"x"', 'symbols(cyclic "*")', 'hebrew', 'é'],
    'list-style-position': ['inside', 'OUTSIDE'],
    'list-style-image': ['none', 'url(x.png)', 'url("x")', 'linear-gradient(red, blue)', 'image-set("a.png" 1x)'],
    'margin-left': ['1px', 'auto', '-1em', '10%', '-10%', '0', 'calc(1px)'],
    'padding-bottom': ['1px', '0', '10%', 'min(1px, 2em)'],
    'font-style': ['italic', 'oblique', 'normal', 'oblique 10deg', 'oblique -1rad'],
    'font-variant': ['small-caps', 'normal', 'all-small-caps'],
    'font-weight': ['bold', 'lighter', '700', '750', '1', '1000', '1.5'],
    'font-stretch': ['condensed', 'ultra-expanded', '50%'],
    'font-size': ['12pt', 'large', 'larger', '80%', 'math', '0'],
    'line-height': ['1.4', 'normal', '12pt', '150%', '0'],
    'font-family': ['"A B", serif', 'A B, serif', 'serif', '"a"', 'a, "b", c d e'],
}  # fmt: skip
INVALID = {
    'border-top-style': ['1px', 'solid dotted', 'red', '', 'solid,'],
    'border-right-width': ['-1px', '10%', '1', 'solid', '1deg', '1px 2px'],
    'border-bottom-color': ['1px', 'rgb(var(--x))', 'foo', 'inherit inherit', '"red"'],
    'list-style-type': ['inside', 'outside', 'default', '--x', '1', 'disc square'],
    'list-style-position': ['disc', 'none'],
    'list-style-image': ['disc', 'red', 'url(x) url(y)'],
    'margin-left': ['1', 'normal', '1px 2px', '1deg'],
    'padding-bottom': ['-1px', 'auto', '-10%'],
    'font-style': ['bold', 'italic 10deg', 'oblique 10px'],
    'font-variant': ['bold'],
    'font-weight': ['0', '1001', '-700', 'heavy'],
    'font-stretch': ['bold', '-50%'],
    'font-size': ['-1px', 'normal', '1'],
    'line-height': ['-1', 'auto'],
    'font-family': ['serif,', ', serif', '"a" b', 'inherit, serif', 'default', '1', 'a,,b'],
}  # fmt: skip


@pytest.mark.parametrize(('name', 'value'), [(n, v) for n, vs in VALID.items() for v in vs])
def test_valid(name: str, value: str) -> None:
    assert matches(name, value) is True


@pytest.mark.parametrize(('name', 'value'), [(n, v) for n, vs in INVALID.items() for v in vs])
def test_invalid(name: str, value: str) -> None:
    assert matches(name, value) is False


def test_general() -> None:
    # CSS-wide keywords are valid for every longhand; var() and the like for none
    for name in PROPERTIES:
        for kw in ('inherit', 'INITIAL', 'unset', 'revert', 'revert-layer'):
            assert matches(name, kw) is True, (name, kw)
        assert matches(name, 'var(--x)') is False
        assert matches(name, 'env(x)') is False
    assert len(PROPERTIES) == 4 * 5 + 3 + 7
    # Unknown properties, and shorthands, are not known
    for name in ('color', 'margin', 'border', 'list-style', 'font', '--x', 'background'):
        assert matches(name, 'red') is None
    # Property names are case-insensitive; components are accepted
    assert matches('Border-Top-Style', 'solid') is True
    for c in parse_value('1px solid red'):
        assert matches('border-top-width', c) is (c.css_text == '1px')
        assert matches('border-top-style', c) is (c.css_text == 'solid')
        assert matches('border-top-color', c) is (c.css_text == 'red')


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('red', True),
        ('#abc', True),
        ('#abcd', True),
        ('rgba(1, 2, 3, 0.5)', True),
        ('transparent', True),
        ('currentColor', True),
        ('lab(50 1 2)', True),
        ('hsl(1, 2%, 3%)', True),
        # D5: not inherit, which css_parser's CSS 2 profile accepts
        ('inherit', False),
        ('canvas', False),  # a system color, but it has no RGB: matches() takes it as a <color>
        ('1px', False),
        ('', False),
    ],
)
def test_is_color(value: str, expected: bool) -> None:
    assert is_color(value) is expected
    (c,) = parse_value(value) or [None]
    if c is not None:
        assert is_color(c) is expected


# Where kgcss and css_parser disagree: css_parser follows CSS 2.1 and is lax about negative values.
DIFFERENCES = [
    ('border-top-width', '-1px', False),  # negative widths are invalid
    ('border-top-width', 'calc(1px)', True),  # css_parser has no calc()
    ('border-top-color', '#abcd', True),  # CSS 4 colors
    ('border-top-color', 'lab(1 2 3)', True),
    ('border-top-color', 'canvas', True),  # system colors
    ('border-top-color', 'ActiveBorder', True),
    ('list-style-type', 'foo', True),  # D4: any counter style name, CSS Lists 3
    ('list-style-type', 'hebrew', True),
    ('list-style-type', '"x"', True),
    ('list-style-type', 'symbols(cyclic "*")', True),
    ('list-style-image', 'linear-gradient(red, blue)', True),  # gradients
    ('margin-top', 'calc(1px)', True),
    ('padding-top', '-1px', False),  # negative padding is invalid
    ('font-weight', '750', True),  # CSS Fonts 4: any number from 1 to 1000
    ('font-weight', '1000', True),
    ('font-size', 'math', True),
    ('line-height', '-1', False),  # negative line heights are invalid
    ('font-family', 'default', False),  # reserved
    ('font-style', 'oblique 10deg', True),  # CSS Fonts 4
    ('font-stretch', '50%', True),
]


def test_compared_with_css_parser() -> None:
    profile = pytest.importorskip('css_parser').profile
    for name, value, ours in DIFFERENCES:
        assert matches(name, value) is ours
        assert bool(profile.validate(name, value)) is not ours, (name, value)
    # Everything else in the tables agrees, where css_parser knows the value at all (no CSS 3 or 4 syntax)
    agree = [
        ('border-top-style', 'solid'), ('border-top-style', '1px'), ('border-top-width', 'thin'),
        ('border-top-width', '10%'), ('border-top-color', 'red'), ('border-top-color', '1px'),
        ('list-style-type', 'disc'), ('list-style-type', 'inside'), ('list-style-position', 'inside'),
        ('list-style-image', 'url(x.png)'), ('list-style-image', 'disc'), ('margin-top', 'auto'),
        ('margin-top', '1'), ('padding-top', 'auto'), ('font-weight', 'bold'), ('font-weight', '0'),
        ('font-size', 'large'), ('font-size', '-1px'), ('line-height', 'normal'), ('font-family', 'A B, serif'),
        ('font-style', 'italic'), ('font-variant', 'small-caps'), ('font-stretch', 'condensed'),
    ]  # fmt: skip
    for name, value in agree:
        assert matches(name, value) is bool(profile.validate(name, value)), (name, value)


def test_doctests() -> None:
    assert doctest.testmod(kgcss.grammar).failed == 0


def test_speed() -> None:
    # §6: under 2 µs for a keyword, 5 µs for a color, with the cache warm (generous bounds, for slow machines)
    for value, bound in (('solid', 2e-6), ('rgb(1 2 3)', 5e-6)):
        matches('border-top-style', value)
        t = min(timeit.repeat(partial(matches, 'border-top-style', value), number=1000, repeat=5)) / 1000
        assert t < bound * 5


@given(st.sampled_from(sorted(PROPERTIES)), st.text())
def test_any_text(name: str, text: str) -> None:
    assert matches(name, text) in (True, False)
    is_color(text)
