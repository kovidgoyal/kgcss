# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Shorthand expansion and condensation.

The tests of calibre's ebooks/oeb/normalize_css.py are ported here, with
calibre's DEFAULTS as the initial values. The differences are marked.
"""

import doctest
from itertools import product

import pytest
from hypothesis import given
from hypothesis import strategies as st

import kgcss.shorthands
from kgcss import INITIAL_VALUES, condense_shorthands, expand_shorthand, longhands, parse_style
from kgcss.cssom.declarations import DeclarationBlock

EDGES = ('top', 'right', 'bottom', 'left')
BORDER_PROPS = ('color', 'style', 'width')
# The part of calibre's DEFAULTS that these shorthands use
DEFAULTS = {
    **{f'border-{e}-color': 'currentColor' for e in EDGES},
    **{f'border-{e}-style': 'none' for e in EDGES},
    **{f'border-{e}-width': 'medium' for e in EDGES},
    **{f'margin-{e}': '0' for e in EDGES},
    **{f'padding-{e}': '0' for e in EDGES},
    'font-family': 'serif',
    'font-size': 'medium',
    'font-stretch': 'normal',
    'font-style': 'normal',
    'font-variant': 'normal',
    'font-weight': 'normal',
    'line-height': 'normal',
    'list-style-image': 'none',
    'list-style-position': 'outside',
    'list-style-type': 'disc',
}
# calibre's font composition has no font-stretch, which the font shorthand also sets
FONT_COMPOSITION = ('font-style', 'font-variant', 'font-weight', 'font-stretch', 'font-size', 'line-height',
                    'font-family')  # fmt: skip


def expand(name: str, value: str) -> dict[str, str] | None:
    return expand_shorthand(name, value, initial=DEFAULTS, quirks_mode=True)


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('some_font', {'font-family': 'some_font'}),
        ('inherit', dict.fromkeys(FONT_COMPOSITION, 'inherit')),
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
                'font-family': 'serif', 'font-weight': 'bold', 'font-style': 'italic', 'font-size': 'larger',
                'line-height': 'normal', 'font-variant': 'small-caps',
            },
        ),
        ('2em A B', {'font-family': '"A B"', 'font-size': '2em'}),
        # Beyond calibre's tests
        ('caption', {}),  # a legacy system font: the initial values, as calibre's normalize_font gives
        ('condensed 12pt a', {'font-stretch': 'condensed', 'font-size': '12pt', 'font-family': 'a'}),
    ],
)  # fmt: skip
def test_font_normalization(raw: str, expected: dict[str, str]) -> None:
    base = {} if raw == 'inherit' else {k: DEFAULTS[k] for k in FONT_COMPOSITION}
    assert expand('font', raw) == {**base, **expected}


def border_edge_dict(expected: dict[str, str], edge: str = 'right') -> dict[str, str]:
    ans = {f'border-{edge}-{x}': DEFAULTS[f'border-{edge}-{x}'] for x in ('style', 'width', 'color')}
    for x, v in expected.items():
        ans[f'border-{edge}-{x}'] = v
    return ans


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('solid 1px red', {'color': 'red', 'width': '1px', 'style': 'solid'}),
        ('1px', {'width': '1px'}),
        ('#aaa', {'color': '#aaa'}),
        ('2em groove', {'width': '2em', 'style': 'groove'}),
    ],
)
def test_border_edge_normalization(raw: str, expected: dict[str, str]) -> None:
    for edge in EDGES:
        assert expand(f'border-{edge}', raw) == border_edge_dict(expected, edge)


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('solid 1px red', {'color': 'red', 'width': '1px', 'style': 'solid'}),
        ('1px', {'width': '1px'}),
        ('#aaa', {'color': '#aaa'}),
        ('thin groove', {'width': 'thin', 'style': 'groove'}),
    ],
)
def test_border_normalization(raw: str, expected: dict[str, str]) -> None:
    ans: dict[str, str] = {}
    for edge in EDGES:
        ans.update(border_edge_dict(expected, edge))
    assert expand('border', raw) == ans


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        # calibre also expands border-width: 10%, but a percentage is not a border width
        ('width', '10%'),
        ('color', 'rgb(0, 1, 1)'),
        ('style', 'double'),
    ],
)
def test_border_part_normalization(name: str, value: str) -> None:
    expected = None if value == '10%' else {f'border-{e}-{name}': value for e in EDGES}
    assert expand(f'border-{name}', value) == expected


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('2px', ('2px', '2px', '2px', '2px')),
        ('1em 2em', ('1em', '2em', '1em', '2em')),
        ('1em 2em 3em', ('1em', '2em', '3em', '2em')),
        ('1 2 3 4', ('1', '2', '3', '4')),  # unitless: only with quirks_mode
    ],
)
def test_edge_normalization(raw: str, expected: tuple[str, ...]) -> None:
    for prefix in ('margin', 'padding'):
        assert expand(prefix, raw) == {f'{prefix}-{e}': x for e, x in zip(EDGES, expected, strict=True)}
    assert (expand_shorthand('margin', raw) is None) is (raw == '1 2 3 4')


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('url(http://www.example.com/images/list.png)', {'image': 'url(http://www.example.com/images/list.png)'}),
        ('inside square', {'position': 'inside', 'type': 'square'}),
        ('upper-roman url(img) outside', {'position': 'outside', 'type': 'upper-roman', 'image': 'url(img)'}),
        # CSS Lists 3 for none
        ('none', {'type': 'none', 'image': 'none'}),
        ('none square', {'type': 'square', 'image': 'none'}),
        ('none url(x)', {'type': 'none', 'image': 'url(x)'}),
        ('inside none none', {'position': 'inside', 'type': 'none', 'image': 'none'}),
        ('hebrew', {'type': 'hebrew'}),
    ],
)
def test_list_style_normalization(raw: str, expected: dict[str, str]) -> None:
    ans = {f'list-style-{x}': DEFAULTS[f'list-style-{x}'] for x in ('type', 'position', 'image')}
    ans.update({f'list-style-{k}': v for k, v in expected.items()})
    assert expand('list-style', raw) == ans


def test_filter_css_normalization() -> None:
    # calibre's normalize_filter_css(names) is names and the longhands of each
    def normalize_filter_css(props: set[str]) -> set[str]:
        return props.union(*(longhands(p) for p in props))

    assert normalize_filter_css({'font'}) == {'font', *FONT_COMPOSITION}  # with font-stretch, unlike calibre
    for p in ('margin', 'padding'):
        assert normalize_filter_css({p}) == {p} | {f'{p}-{x}' for x in EDGES}
    bvals = {f'border-{e}-{x}' for e in EDGES for x in BORDER_PROPS}
    assert normalize_filter_css({'border'}) == bvals | {'border'}
    for x in BORDER_PROPS:
        assert normalize_filter_css({f'border-{x}'}) == {f'border-{e}-{x}' for e in EDGES} | {f'border-{x}'}
    for e in EDGES:
        assert normalize_filter_css({f'border-{e}'}) == {f'border-{e}-{x}' for x in BORDER_PROPS} | {f'border-{e}'}
    assert normalize_filter_css({'list-style'}) == {
        'list-style',
        'list-style-image',
        'list-style-type',
        'list-style-position',
    }
    assert longhands('color') == ()
    assert longhands('MARGIN') == longhands('margin')


@pytest.mark.parametrize(
    ('values', 'expected'),
    [
        ((1, 1, 3), None),
        ((1, 2, 3, 4), '2pt 3pt 4pt 1pt'),
        ((1, 2, 3, 2), '2pt 3pt 2pt 1pt'),
        ((1, 2, 1, 3), '2pt 1pt 3pt'),
        ((1, 2, 1, 2), '2pt 1pt'),
        ((1, 1, 1, 1), '1pt'),
        (('2%', '2%', '2%', '2%'), '2%'),
        (('0', '0', '0', '0'), '0'),
    ],
)
def test_edge_condensation(values: tuple[int | str, ...], expected: str | None) -> None:
    for prefix in ('margin', 'padding'):
        css = {f'{prefix}-{x}': f'{y}pt' if isinstance(y, int) else y
               for x, y in zip(('left', 'top', 'right', 'bottom'), values, strict=False)}  # fmt: skip
        style = parse_style('; '.join(f'{k}:{v}' for k, v in css.items()))
        condense_shorthands(style)
        assert style.get_value(prefix) == (expected or '')
        if expected is not None:
            for edge in EDGES:
                assert f'{prefix}-{edge}' not in style


def test_border_condensation() -> None:
    vals = 'red solid 5px'
    css = '; '.join(f'border-{e}-{p}: {v}' for e in EDGES for p, v in zip(BORDER_PROPS, vals.split(), strict=True))
    style = parse_style(css)
    condense_shorthands(style)
    for e, p in product(EDGES, BORDER_PROPS):
        assert f'border-{e}-{p}' not in style
        assert f'border-{e}' not in style
        assert f'border-{p}' not in style
    assert style['border'] == vals
    css = '; '.join(f'border-top-{p}: {v}' for p, v in zip(BORDER_PROPS, vals.split(), strict=True))
    style = parse_style(css)
    condense_shorthands(style)
    assert style.to_css(single_line=True) == f'border-top: {vals}'
    css += ';' + '; '.join(
        f'border-{e}-{p}: {v}'
        for e in ('right', 'left', 'bottom')
        for p, v in zip(BORDER_PROPS, vals.replace('red', 'green').split(), strict=True)
    )
    style = parse_style(css)
    condense_shorthands(style)
    assert len(style) == 4
    assert style['border-top'] == vals
    assert style['border-left'] == vals.replace('red', 'green')


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('margin', '1px 2px 3px 4px 5px'),
        ('margin', 'var(--x)'),
        ('margin', '1px var(--x)'),
        ('margin', 'inherit 1px'),
        ('margin', '1px, 2px'),
        ('padding', '-1px'),
        ('border', 'solid dotted'),  # calibre kept the first
        ('border', '1px solid red blue'),
        ('border-top', 'foo'),
        ('border-color', 'red 1px'),
        ('list-style', 'none none none'),
        ('list-style', 'disc square'),
        ('list-style', 'none disc url(x)'),
        ('font', ''),
        ('font', 'var(--f)'),
        ('color', 'red'),
        ('margin', ''),
    ],
)
def test_not_expanded(name: str, value: str) -> None:
    assert expand_shorthand(name, value, quirks_mode=True) is None


def test_expansion_details() -> None:
    assert expand_shorthand('MARGIN', 'INHERIT') == dict.fromkeys(longhands('margin'), 'inherit')
    assert expand_shorthand('border', 'unset') == dict.fromkeys(longhands('border'), 'unset')
    assert expand_shorthand('border', '1px') == {
        **{f'border-{e}-width': '1px' for e in EDGES},
        **{f'border-{e}-style': 'none' for e in EDGES},
        **{f'border-{e}-color': 'currentcolor' for e in EDGES},
    }
    assert expand_shorthand('border-top', 'canvas 1px')['border-top-color'] == 'canvas'  # type: ignore[index]
    assert expand_shorthand('border-width', '1 2') is None
    assert expand_shorthand('border-width', '1 2', quirks_mode=True) == {
        'border-top-width': '1',
        'border-right-width': '2',
        'border-bottom-width': '1',
        'border-left-width': '2',
    }
    assert expand_shorthand('border-top', 'solid 1', quirks_mode=True) == {
        'border-top-width': '1',
        'border-top-style': 'solid',
        'border-top-color': 'currentcolor',
    }
    assert expand_shorthand('font', 'caption') == {k: INITIAL_VALUES[k] for k in FONT_COMPOSITION}
    # Every result has every longhand
    for name in ('margin', 'padding', 'border', 'border-left', 'border-style', 'list-style', 'font'):
        for value in ('inherit', 'solid', '1px', 'red', 'disc', '12pt serif'):
            ans = expand_shorthand(name, value)
            assert ans is None or tuple(ans) == longhands(name)


def test_condensation_details() -> None:
    def condensed(css: str) -> str:
        block = parse_style(css)
        condense_shorthands(block)
        return block.to_css(single_line=True)

    edges = 'margin-top: 1px; margin-right: 1px; margin-bottom: 1px; margin-left: 1px'
    # Placed where the first longhand was
    assert condensed(f'color: red; {edges}; width: 0') == 'color: red; margin: 1px; width: 0'
    # Not with !important, nor when a shorthand overlaps
    assert condensed(edges + ' !important') == edges + ' !important'
    assert condensed(f'margin: 0; {edges}') == f'margin: 0; {edges}'
    # CSS-wide keywords only when they are the whole value
    inherit = edges.replace('1px', 'inherit')
    assert condensed(inherit) == 'margin: inherit'
    assert condensed(inherit.replace('inherit', '1px', 1)) == inherit.replace('inherit', '1px', 1)
    # A longhand that comes between the edges would be overridden by the border: no border
    css = ('border-right: 1px solid red; border-top-color: blue; border-top: 1px solid red; '
           'border-bottom: 1px solid red; border-left: 1px solid red')  # fmt: skip
    assert condensed(css) == css
    # Not when one longhand is invalid: the whole shorthand would be, losing the valid ones
    css = 'padding-top: 0; padding-right: 0; padding-bottom: 0; padding-left: auto'
    assert condensed(css) == css
    # Repeated declarations: the effective one counts, and all go
    assert condensed(f'margin-top: 5px; {edges}') == 'margin: 1px'


def test_doctests() -> None:
    assert doctest.testmod(kgcss.shorthands).failed == 0


# Condensing never changes what each longhand is

VALUES = {
    'margin': ('0', '1px', '2px', 'auto', 'inherit', '-1em'),
    'border-width': ('1px', 'thin', 'inherit', '0'),
    'border-style': ('solid', 'none', 'dotted', 'inherit'),
    'border-color': ('red', '#abc', 'currentcolor', 'inherit'),
}
LONGHAND_NAMES = (
    *longhands('margin'), *longhands('padding'), *longhands('border'),
)  # fmt: skip


def value_kind(name: str) -> str:
    if name.startswith(('margin', 'padding')):
        return 'margin'
    return 'border-' + name.rpartition('-')[2]


def effective(block: DeclarationBlock) -> dict[str, tuple[str, bool]]:
    """What each longhand ends up as: (value, important), with shorthands expanded, in cascade order, and
    invalid declarations ignored."""
    ans: dict[str, tuple[str, bool]] = {}
    for item in block.items:
        if not isinstance(item, kgcss.Property):
            continue
        name, important = item.name, item.important
        if longhands(name):
            expanded = expand_shorthand(name, item.value)
        else:
            expanded = {name: item.value} if kgcss.matches(name, item.value) is not False else None
        if expanded is None:
            continue  # invalid: ignored, as browsers do
        for k, v in expanded.items():
            if k in ans and ans[k][1] and not important:
                continue
            ans[k] = (v, important)
    return ans


@st.composite
def blocks(draw: st.DrawFn) -> str:
    groups = draw(st.lists(st.sampled_from(('margin', 'padding', 'border')), min_size=1, max_size=3))
    decls: list[str] = []
    for g in groups:
        for name in longhands(g):
            if draw(st.integers(0, 9)):
                decls.append(f'{name}: {draw(st.sampled_from(VALUES[value_kind(name)]))}')
    for _ in range(draw(st.integers(0, 3))):
        match draw(st.sampled_from(('other', 'shorthand', 'important', 'repeat'))):
            case 'other':
                extra = 'color: red'
            case 'shorthand':
                extra = draw(
                    st.sampled_from(('margin: 3px', 'border-top: 1px solid', 'border: 0', 'border-color: red'))
                )
            case 'important':
                name = draw(st.sampled_from(LONGHAND_NAMES))
                extra = f'{name}: {draw(st.sampled_from(VALUES[value_kind(name)][:3]))} !important'
            case _:
                name = draw(st.sampled_from(LONGHAND_NAMES))
                extra = f'{name}: {draw(st.sampled_from(VALUES[value_kind(name)][:3]))}'
        decls.insert(draw(st.integers(0, len(decls))), extra)
    return '; '.join(decls)


@given(blocks())
def test_condensing_keeps_meaning(css: str) -> None:
    block = parse_style(css)
    before = effective(block)
    condense_shorthands(block)
    assert effective(block) == before, (css, block.to_css(single_line=True))
    # And it is idempotent
    text = block.to_css(single_line=True)
    condense_shorthands(block)
    assert block.to_css(single_line=True) == text


@given(st.sampled_from(sorted(kgcss.shorthands.LONGHANDS)), st.text())
def test_any_text(name: str, text: str) -> None:
    ans = expand_shorthand(name, text)
    assert ans is None or tuple(ans) == longhands(name)
