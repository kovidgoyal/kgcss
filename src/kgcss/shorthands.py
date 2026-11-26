# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Expanding shorthands into longhands, and condensing them back.

The shorthands are margin, padding, border, border-top (and the other edges),
border-style, border-width, border-color, list-style and font.

>>> expand_shorthand('margin', '1em 2em')
{'margin-top': '1em', 'margin-right': '2em', 'margin-bottom': '1em', 'margin-left': '2em'}
>>> expand_shorthand('border-top', 'red 2px')
{'border-top-width': '2px', 'border-top-style': 'none', 'border-top-color': 'red'}
>>> expand_shorthand('border-top', 'solid dotted') is None
True
>>> from kgcss import parse_style
>>> block = parse_style('margin-top: 1px; margin-right: 2px; margin-bottom: 1px; margin-left: 2px; color: red')
>>> condense_shorthands(block)
>>> block.to_css(single_line=True)
'margin: 1px 2px; color: red'
"""

from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Final, cast

from kgcss.cssom.declarations import DeclarationBlock, Property
from kgcss.cssom.values import Component, Function, Ident, Number, Separator, parse_value
from kgcss.fonts import parse_font, serialize_font_family
from kgcss.grammar import CSS_WIDE_KEYWORDS, matches

__all__ = ['INITIAL_VALUES', 'condense_shorthands', 'expand_shorthand', 'longhands']

EDGES: Final = ('top', 'right', 'bottom', 'left')
BORDER_PARTS: Final = ('width', 'style', 'color')  # the order of the border grammar
FONT_LONGHANDS: Final = (
    'font-style', 'font-variant', 'font-weight', 'font-stretch', 'font-size', 'line-height', 'font-family',
)  # fmt: skip
LEGACY_FONTS: Final = frozenset(('caption', 'icon', 'menu', 'message-box', 'small-caption', 'status-bar'))

# The initial values of CSS, except font-family, which depends on the user agent: serif, as calibre has it
INITIAL_VALUES: Final[Mapping[str, str]] = {
    **{f'margin-{e}': '0' for e in EDGES},
    **{f'padding-{e}': '0' for e in EDGES},
    **{f'border-{e}-width': 'medium' for e in EDGES},
    **{f'border-{e}-style': 'none' for e in EDGES},
    **{f'border-{e}-color': 'currentcolor' for e in EDGES},
    'list-style-type': 'disc',
    'list-style-position': 'outside',
    'list-style-image': 'none',
    'font-style': 'normal',
    'font-variant': 'normal',
    'font-weight': 'normal',
    'font-stretch': 'normal',
    'font-size': 'medium',
    'line-height': 'normal',
    'font-family': 'serif',
}

LONGHANDS: Final[dict[str, tuple[str, ...]]] = {
    'margin': tuple(f'margin-{e}' for e in EDGES),
    'padding': tuple(f'padding-{e}' for e in EDGES),
    **{f'border-{p}': tuple(f'border-{e}-{p}' for e in EDGES) for p in BORDER_PARTS},
    **{f'border-{e}': tuple(f'border-{e}-{p}' for p in BORDER_PARTS) for e in EDGES},
    'border': tuple(f'border-{e}-{p}' for e in EDGES for p in BORDER_PARTS),
    'list-style': ('list-style-type', 'list-style-position', 'list-style-image'),
    'font': FONT_LONGHANDS,
}


def longhands(name: str) -> tuple[str, ...]:
    """The longhands that the shorthand name sets, or () if it is not one of the shorthands here."""
    return LONGHANDS.get(name.lower(), ())


def keyword(c: Component) -> str | None:
    return c.value.lower() if isinstance(c, Ident) and c.value.isascii() else None


def matches_longhand(name: str, c: Component, quirks_mode: bool) -> bool:
    if matches(name, c):
        return True
    # The unitless length quirk of HTML: a number is a length in px, for these properties
    return quirks_mode and isinstance(c, Number) and not name.startswith(('list-style', 'border-', 'font-'))


def matches_width(name: str, c: Component, quirks_mode: bool) -> bool:
    if matches(name, c):
        return True
    return quirks_mode and isinstance(c, Number) and name.endswith('-width') and c.value >= 0


def expand_edges(name: str, items: Sequence[Component], quirks_mode: bool) -> dict[str, str] | None:
    """margin, padding, border-style, border-width and border-color: one to four values for the edges"""
    if not 1 <= len(items) <= 4:
        return None
    names = LONGHANDS[name]
    check = matches_width if name == 'border-width' else matches_longhand
    if not all(check(names[0], c, quirks_mode) for c in items):
        return None
    t = [c.css_text for c in items]
    while len(t) < 4:
        # right from top, bottom from top, left from right
        t.append(t[{1: 0, 2: 0, 3: 1}[len(t)]])
    values = t
    return dict(zip(names, values, strict=True))


def assign(names: Sequence[str], items: Sequence[Component], check: Callable[[str, Component], bool]
           ) -> dict[str, str] | None:  # fmt: skip
    """For a || grammar: each component goes to the first longhand it matches that is not set yet."""
    ans: dict[str, str] = {}
    for c in items:
        for name in names:
            if name not in ans and check(name, c):
                ans[name] = c.css_text
                break
        else:
            return None
    return ans


def expand_border_edge(edges: Sequence[str], items: Sequence[Component], initial: Mapping[str, str],
                       quirks_mode: bool) -> dict[str, str] | None:  # fmt: skip
    """border and border-<edge>: <line-width> || <line-style> || <color>"""
    if not 1 <= len(items) <= 3:
        return None
    parts = assign(BORDER_PARTS, items, lambda p, c: matches_width(f'border-top-{p}', c, quirks_mode))
    if parts is None:
        return None
    return {f'border-{e}-{p}': parts.get(p, initial[f'border-{e}-{p}']) for e in edges for p in BORDER_PARTS}


def expand_list_style(items: Sequence[Component], initial: Mapping[str, str]) -> dict[str, str] | None:
    """<'list-style-position'> || <'list-style-image'> || <'list-style-type'>, where none goes to the type
    or the image, whichever the other components do not set (CSS Lists 3)"""
    names = ('list-style-position', 'list-style-image', 'list-style-type')
    nones = sum(1 for c in items if keyword(c) == 'none')
    ans = assign(names, [c for c in items if keyword(c) != 'none'], lambda n, c: matches(n, c) is True)
    if ans is None or len(items) > 3:
        return None
    free = [n for n in ('list-style-image', 'list-style-type') if n not in ans]
    if nones > len(free):
        return None
    if nones:
        # One none sets both type and image when neither is given; otherwise each none sets one
        for n in free[: max(nones, len(free))]:
            ans[n] = 'none'
    return {n: ans.get(n, initial[n]) for n in LONGHANDS['list-style']}


def expand_font(text: str, items: Sequence[Component], initial: Mapping[str, str]) -> dict[str, str] | None:
    """font, with parse_font(), as calibre does. A legacy system font gives the initial values."""
    if len(items) == 1 and keyword(items[0]) in LEGACY_FONTS:
        return {n: initial[n] for n in FONT_LONGHANDS}
    font = parse_font(text)
    if not font:
        return None
    ans = {n: initial[n] for n in FONT_LONGHANDS}
    for k in ('font-style', 'font-variant', 'font-weight', 'font-stretch', 'font-size', 'line-height'):
        if (v := cast('str | None', font.get(k))) is not None:
            ans[k] = v
    if (families := font.get('font-family')) is not None:
        ans['font-family'] = serialize_font_family(families)
    return ans


def expand_shorthand(
    name: str, value: str, *, initial: Mapping[str, str] = INITIAL_VALUES, quirks_mode: bool = False
) -> dict[str, str] | None:
    """The longhands of the shorthand name for value: those the value gives, and the initial values for
    the rest, from initial. None if name is not a shorthand here, or value does not match its grammar, or
    uses var() and the like, whose longhands are not known before computing. A CSS-wide keyword, such
    as inherit, is given to every longhand. With quirks_mode, numbers without units are lengths, as in
    the HTML quirks mode: calibre's normalize_css accepted them."""
    name = name.lower()
    names = LONGHANDS.get(name)
    if names is None:
        return None
    parsed = parse_value(value)
    if any(isinstance(c, Separator) for c in parsed.items) and name != 'font':
        return None
    items = list(parsed)
    if not items:
        return None
    if any(isinstance(c, Function) and c.name.lower() in ('var', 'env', 'attr') for c in items):
        return None
    if len(items) == 1 and (k := keyword(items[0])) in CSS_WIDE_KEYWORDS:
        return dict.fromkeys(names, k)
    if any(keyword(c) in CSS_WIDE_KEYWORDS for c in items):
        return None
    match name:
        case 'margin' | 'padding' | 'border-style' | 'border-width' | 'border-color':
            return expand_edges(name, items, quirks_mode)
        case 'border':
            return expand_border_edge(EDGES, items, initial, quirks_mode)
        case 'list-style':
            return expand_list_style(items, initial)
        case 'font':
            return expand_font(value, items, initial)
    return expand_border_edge((name.removeprefix('border-'),), items, initial, quirks_mode)


# Condensation


def edge_shorthand(values: Sequence[str]) -> str:
    """The shortest value for the four edges, in the order top, right, bottom, left"""
    t, r, b, l = values  # noqa: E741
    if l == r:
        if t == b:
            return t if t == r else f'{t} {r}'
        return f'{t} {r} {b}'
    return f'{t} {r} {b} {l}'


def is_css_wide(value: str) -> bool:
    return value.strip().lower() in CSS_WIDE_KEYWORDS


def combinable(values: Sequence[str]) -> bool:
    """A CSS-wide keyword can only be the whole value of a shorthand"""
    return not any(is_css_wide(v) for v in values) or (len(set(values)) == 1)


def replace_group(
    block: DeclarationBlock, props: Sequence[Property], values: Sequence[str], name: str, value: str
) -> bool:
    """Put the shorthand where the first of the longhands was, and remove them all. Only if the shorthand
    means exactly the longhands, whose normalized values are values: an invalid longhand would make the
    whole shorthand invalid, and lose the others with it."""
    expected = {p.name: v for p, v in zip(props, values, strict=True)}
    expanded = expanded_with_defaults(name, value)
    if name == 'border':
        if expanded is None or any(
            expanded_with_defaults(p.name, v) != {k: x for k, x in expanded.items() if k.startswith(p.name + '-')}
            for p, v in zip(props, values, strict=True)
        ):
            return False
    elif expanded != expected:
        return False
    # One rebuild of the list: removing names one by one rebuilds it each time
    names = {p.name for p in props}
    items = list(block.items)
    i = min(items.index(p) for p in props)
    keep = [x for x in items if not (isinstance(x, Property) and x.name in names)]
    keep.insert(sum(1 for x in items[:i] if not (isinstance(x, Property) and x.name in names)), Property(name, value))
    block.items[:] = keep
    return True


@lru_cache(maxsize=1024)
def expanded_with_defaults(name: str, value: str) -> dict[str, str] | None:
    # Cached for condensation, which only compares the result: it must not be changed
    return expand_shorthand(name, value)


def condensable(
    view: Mapping[str, Property], names: Sequence[str], overlapping: Sequence[str]
) -> tuple[list[Property], list[str]] | None:
    """The effective declarations of names and their normalized values, if they can be merged. view is the
    block's effective declarations. Values are compared normalized, as they are written: 0 and 0px are the
    same."""
    props = [view.get(n) for n in names]
    if any(p is None or p.important for p in props):
        return None
    if any(n in view for n in overlapping):
        # Another shorthand also sets these: merging could change which declaration wins
        return None
    ans = [p for p in props if p is not None]
    values = [p.value_to_css() for p in ans]
    return (ans, values) if combinable(values) else None


def condense_shorthands(block: DeclarationBlock) -> None:
    """Replace longhands with shorthands where all of them are set, as calibre's normalize_css does: the
    four edges of margin and padding with the shortest shorthand; the width, style and color of a border
    edge with border-<edge>; four equal border edges with border. Declarations with !important are left
    alone, as are groups that another shorthand in the block overlaps, and groups with a value that is
    invalid for its longhand, which would make the whole shorthand invalid."""
    view = {p.name: p for p in block}
    if not any(n.startswith(('margin-', 'padding-', 'border-')) for n in view):
        return

    def merge(group: tuple[list[Property], list[str]], name: str, value: str) -> None:
        nonlocal view
        if replace_group(block, *group, name, value):
            view = {p.name: p for p in block}

    for prefix in ('margin', 'padding'):
        if (group := condensable(view, LONGHANDS[prefix], (prefix,))) is not None:
            merge(group, prefix, edge_shorthand(group[1]))
    border_shorthands = ('border', 'border-width', 'border-style', 'border-color')
    for e in EDGES:
        names = LONGHANDS[f'border-{e}']
        if (group := condensable(view, names, (*border_shorthands, f'border-{e}'))) is not None:
            values = group[1]
            # calibre writes color, style, width
            merge(group, f'border-{e}', values[0] if is_css_wide(values[0]) else ' '.join(reversed(values)))
    edges = [f'border-{e}' for e in EDGES]
    overlapping = (*border_shorthands, *LONGHANDS['border'])
    if (group := condensable(view, edges, overlapping)) is not None and len(set(group[1])) == 1:
        merge(group, 'border', group[1][0])
