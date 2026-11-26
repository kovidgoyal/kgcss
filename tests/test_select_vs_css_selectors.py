# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Select compared with calibre's css_selectors, on random trees and selectors.

css_selectors comes from the benchmark cache: run
`uv run --group bench python bench/bench_parse.py prepare` to get it. Without
it, these tests are skipped.

The known differences are kept out of the inputs rather than allowed for
in the comparison:

- css_selectors does not match :first-child, :nth-child() and the like on the
  root element, which has no parent. Trees are wrapped in an element that
  no selector here can match, and those pseudo-classes only appear in
  compounds with a type selector.
- :root and :scope ignore the rest of their compound in css_selectors.
- css_selectors ignores xml:lang and does not have the wildcards of :lang()
  ranges, so there is no :lang() here; the oracle tests cover it.
- css_selectors matches ids and classes case-insensitively: quirks mode.
- css_selectors counts comments and processing instructions as siblings
  for :first-child, :last-child, :only-child and :nth-child() without 'of',
  as it uses lxml's parent.index(). Only elements count, so the trees have
  no comments here.
- css_selectors raises ExpressionError for :hover and the like only when
  matching gets to them: div div:hover raises nothing if there is no
  div div. Select always raises, so those cases are not compared.
- css_selectors does not always return elements in document order, so its
  results are sorted.
- css_selectors strips namespaces from tags and attribute names only when
  the root element has one, so the wrapper is in the namespace of the tree,
  and trees without a namespace have no namespaced attributes.
"""

import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

pytest.importorskip('lxml')
from lxml import etree

from kgcss import ExpressionError, Select, SelectorSyntaxError

from .test_select import CLASSES, TAGS, VALUES, trees

CALIBRE = Path(__file__).parent.parent / 'bench' / '.cache' / 'calibre'
if not (CALIBRE / 'css_selectors' / 'select.py').exists():
    pytest.skip('css_selectors is not in the benchmark cache', allow_module_level=True)
sys.path.insert(0, str(CALIBRE))
import css_selectors  # type: ignore[import-not-found]  # noqa: E402

ANBS = ('1', '2', 'odd', 'even', '-n+2', 'n+2', '3n', 'n')
POSITIONS = (
    ':first-child', ':last-child', ':only-child', ':first-of-type', ':last-of-type', ':only-of-type',
    *(f':{name}({anb})' for name in ('nth-child', 'nth-last-child', 'nth-of-type', 'nth-last-of-type') for anb in ANBS),
)  # fmt: skip
TYPES = (*TAGS, '*|li', 'ns|div')


@st.composite
def compound(draw: st.DrawFn, depth: int) -> str:
    # A type selector first, so that a compound with a position pseudo-class cannot match the wrapper
    parts = [draw(st.sampled_from(TYPES))]
    for _ in range(draw(st.integers(0, 3))):
        choices = ['class', 'id', 'attribute', 'position', 'plain']
        if depth < 2:
            choices += ['is', 'has', 'nth-of']
        match draw(st.sampled_from(choices)):
            case 'class':
                parts.append('.' + draw(st.sampled_from(CLASSES)))
            case 'id':
                parts.append('#' + draw(st.sampled_from(('a', 'A', 'b', 'é'))))
            case 'attribute':
                name = draw(st.sampled_from(('href', 'lang', 'data-x', 'class')))
                op = draw(st.sampled_from(('', '=', '~=', '|=', '^=', '$=', '*=')))
                flag = draw(st.sampled_from(('', ' i', ' s')))
                parts.append(f'[{name}{op}"{draw(st.sampled_from(VALUES))}"{flag}]' if op else f'[{name}]')
            case 'position':
                parts.append(draw(st.sampled_from(POSITIONS)))
            case 'plain':
                parts.append(draw(st.sampled_from((':empty', ':any-link', ':hover'))))
            case 'is':
                name = draw(st.sampled_from(('is', 'where', 'not')))
                parts.append(f':{name}({draw(selector_list(depth + 1))})')
            case 'has':
                parts.append(f':has({draw(selector_list(depth + 1, relative=True))})')
            case 'nth-of':
                name = draw(st.sampled_from(('nth-child', 'nth-last-child')))
                parts.append(f':{name}({draw(st.sampled_from(ANBS))} of {draw(selector_list(depth + 1))})')
    return ''.join(parts)


@st.composite
def complex_selector(draw: st.DrawFn, depth: int, relative: bool) -> str:
    text = draw(st.sampled_from(('', '> ', '+ ', '~ '))) if relative else ''
    text += draw(compound(depth))
    for _ in range(draw(st.integers(0, 3))):
        text += draw(st.sampled_from((' ', ' > ', ' + ', ' ~ '))) + draw(compound(depth))
    return text


@st.composite
def selector_list(draw: st.DrawFn, depth: int = 0, relative: bool = False) -> str:
    return ', '.join(draw(complex_selector(depth, relative)) for _ in range(draw(st.integers(1, 3))))


def wrapped(tree: etree._Element) -> etree._Element:
    # In the namespace of the tree: css_selectors strips namespaces from tags only when the root has one
    namespace = tree.tag.rpartition('}')[0] + '}' if tree.tag.startswith('{') else ''
    # css_selectors counts comments as siblings for :first-child and :nth-child(), which Selectors does
    # not. Removing them keeps their tails as text, so :empty is the same.
    etree.strip_tags(tree, etree.Comment)
    if not namespace:
        # In a tree without a namespace, css_selectors does not strip namespaces from attribute names
        for e in tree.iter():
            for name in [k for k in e.attrib if str(k).startswith('{')]:
                del e.attrib[name]
    wrapper = etree.Element(namespace + 'wrapper')
    wrapper.append(tree)
    return wrapper


def ours(document: etree._Element, css: str, ignore: bool) -> list[etree._Element] | None:
    try:
        return list(Select(document, ignore, quirks_mode=True)(css))
    except ExpressionError:
        return None


def theirs(document: etree._Element, css: str, ignore: bool) -> list[etree._Element] | None:
    try:
        found = list(css_selectors.Select(document, ignore_inappropriate_pseudo_classes=ignore)(css))
    except css_selectors.ExpressionError:
        return None
    # css_selectors does not always give document order, as for div > div in nested divs
    order = {e: i for i, e in enumerate(document.iter())}
    return sorted(found, key=order.__getitem__)


@given(trees(), selector_list(), st.booleans())
def test_same_as_css_selectors(tree: etree._Element, css: str, ignore: bool) -> None:
    document = wrapped(tree)
    try:
        ans = ours(document, css, ignore)
    except SelectorSyntaxError:
        return
    if ans is None:
        # Not comparable: css_selectors only raises when it gets to the part it cannot match
        return
    assert ans == theirs(document, css, ignore), css


@pytest.mark.parametrize(
    'css',
    ['li:first-child', 'div:nth-child(2n of .x)', 'p:has(> span + a)', 'li:nth-last-of-type(-n+2)', 'a:not(.x, div p)'],
)
def test_examples(css: str) -> None:
    document = wrapped(
        etree.fromstring('<div><p class="x"><span/><a/></p><li/><li class="X"/><li/><a class="x"/></div>')
    )
    assert ours(document, css, False) == theirs(document, css, False)
