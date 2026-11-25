# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Matching selectors with Select (plan 03, milestone 3).

test_select is ported from calibre's css_selectors (src/css_selectors/tests.py),
the parts that need milestone 4 (nth, of-type, :has(), :lang()) are in
test_select_milestone4. The random tests compare Select with the naive
matcher in selector_oracle.py.
"""

from collections.abc import Iterator

import pytest
from hypothesis import given
from hypothesis import strategies as st

# lxml enables the GIL on free-threaded builds, so those tests run without it (see test_threads.py)
pytest.importorskip('lxml')
from lxml import etree, html

from kgcss import (
    ExpressionError,
    Select,
    SelectorSyntaxError,
    StyleRule,
    parse_selectors,
    parse_stylesheet,
)
from kgcss.selectors import INAPPROPRIATE_PSEUDO_CLASSES, build_tree, compile_selectors

from .selector_oracle import Oracle, Unsupported

HTML_IDS = """
<html id="html"><head>
  <link id="link-href" href="foo" />
  <link id="link-nohref" />
</head><body>
<div id="outer-div">
 <a id="name-anchor" name="foo"></a>
 <a id="tag-anchor" rel="tag" href="http://localhost/foo">link</a>
 <a id="nofollow-anchor" rel="nofollow" href="https://example.org">
    link</a>
 <ol id="first-ol" class="a b c">
   <li id="first-li">content</li>
   <li id="second-li" lang="En-us">
     <div id="li-div">
     </div>
   </li>
   <li id="third-li" class="ab c"></li>
   <li id="fourth-li" class="ab
c"></li>
   <li id="fifth-li"></li>
   <li id="sixth-li"></li>
   <li id="seventh-li">  </li>
 </ol>
 <p id="paragraph">
   <b id="p-b">hi</b> <em id="p-em">there</em>
   <b id="p-b2">guy</b>
   <input type="checkbox" id="checkbox-unchecked" />
   <input type="checkbox" id="checkbox-disabled" disabled="" />
   <input type="text" id="text-checked" checked="checked" />
   <input type="hidden" />
   <input type="hidden" disabled="disabled" />
   <input type="checkbox" id="checkbox-checked" checked="checked" />
   <input type="checkbox" id="checkbox-disabled-checked"
          disabled="disabled" checked="checked" />
   <fieldset id="fieldset" disabled="disabled">
     <input type="checkbox" id="checkbox-fieldset-disabled" />
     <input type="hidden" />
   </fieldset>
 </p>
 <ol id="second-ol">
 </ol>
 <map name="dummymap">
   <area shape="circle" coords="200,250,25" href="foo.html" id="area-href" />
   <area shape="default" id="area-nohref" />
 </map>
</div>
<div id="foobar-div" foobar="ab bc
cde"><span id="foobar-span"></span></div>
</body></html>
"""


def ids_document() -> etree._Element:
    parser = etree.XMLParser(recover=True, no_network=True, resolve_entities=False)
    return etree.fromstring(HTML_IDS, parser=parser)


class Checker:
    def __init__(self, select: Select) -> None:
        self.select = select

    def ids(self, selector: str) -> list[str | None]:
        return [e.get('id') for e in self.select(selector)]

    def __call__(self, main: str, *others: str) -> list[str | None]:
        result = self.ids(main)
        for other in others:
            assert self.ids(other) == result, other
        return result


def test_select() -> None:
    document = ids_document()
    select = Select(document)
    pcss = Checker(select)
    all_ids = pcss('*')
    assert all_ids[:6] == ['html', None, 'link-href', 'link-nohref', None, 'outer-div']
    assert all_ids[-1:] == ['foobar-span']
    assert pcss('div') == ['outer-div', 'li-div', 'foobar-div']
    assert pcss('DIV') == ['outer-div', 'li-div', 'foobar-div']  # case-insensitive in HTML
    assert pcss('div div') == ['li-div']
    assert pcss('div, div div') == ['outer-div', 'li-div', 'foobar-div']
    assert pcss('a[name]') == ['name-anchor']
    assert pcss('a[NAme]') == ['name-anchor']  # case-insensitive in HTML:
    assert pcss('a[rel]') == ['tag-anchor', 'nofollow-anchor']
    assert pcss('a[rel="tag"]') == ['tag-anchor']
    assert pcss('a[href*="localhost"]') == ['tag-anchor']
    assert pcss('a[href*=""]') == []
    assert pcss('a[href^="http"]') == ['tag-anchor', 'nofollow-anchor']
    assert pcss('a[href^="http:"]') == ['tag-anchor']
    assert pcss('a[href^=""]') == []
    assert pcss('a[href$="org"]') == ['nofollow-anchor']
    assert pcss('a[href$=""]') == []
    assert pcss('div[foobar~="bc"]', 'div[foobar~="cde"]') == ['foobar-div']
    assert pcss('[foobar~="ab bc"]', '[foobar~=""]', '[foobar~=" \t"]') == []
    assert pcss('div[foobar~="cd"]') == []
    assert pcss('*[lang|="En"]', '[lang|="En-us"]') == ['second-li']
    # Attribute values are case sensitive
    assert pcss('*[lang|="en"]', '[lang|="en-US"]') == []
    assert pcss('*[lang|="e"]') == []
    assert pcss('li:first-child') == ['first-li']
    assert pcss('li:last-child') == ['seventh-li']
    assert pcss('span:only-child') == ['foobar-span']
    assert pcss('li div:only-child') == ['li-div']
    assert pcss('div *:only-child') == ['li-div', 'foobar-span']
    assert pcss('a:empty', 'a:EMpty') == ['name-anchor']
    assert pcss('li:empty') == ['third-li', 'fourth-li', 'fifth-li', 'sixth-li']
    assert pcss(':root', 'html:root') == ['html']
    # css_selectors gives html for li:root too, as it ignores the rest of a compound with :root
    assert pcss('li:root') == []
    assert pcss('* :root', 'p *:root') == []
    assert pcss('.a', '.b', '*.a', 'ol.a') == ['first-ol']
    assert pcss('.c', '*.c') == ['first-ol', 'third-li', 'fourth-li']
    assert pcss('ol *.c', 'ol li.c', 'li ~ li.c', 'ol > li.c') == ['third-li', 'fourth-li']
    assert pcss('#first-li', 'li#first-li', '*#first-li') == ['first-li']
    assert pcss('li div', 'li > div', 'div div') == ['li-div']
    assert pcss('div > div') == []
    assert pcss('div>.c', 'div > .c') == ['first-ol']
    assert pcss('div + div') == ['foobar-div']
    assert pcss('a ~ a') == ['tag-anchor', 'nofollow-anchor']
    assert pcss('a[rel="tag"] ~ a') == ['nofollow-anchor']
    assert pcss('ol#first-ol li:last-child') == ['seventh-li']
    assert pcss('ol#first-ol *:last-child') == ['li-div', 'seventh-li']
    assert pcss('#outer-div:first-child') == ['outer-div']
    assert pcss('#outer-div :first-child') == [
        'name-anchor', 'first-li', 'li-div', 'p-b', 'checkbox-fieldset-disabled', 'area-href',
    ]  # fmt: skip
    assert pcss('a[href]') == ['tag-anchor', 'nofollow-anchor']
    assert pcss(':not(*)') == []
    assert pcss('a:not([href])') == ['name-anchor']
    assert pcss('ol :Not(li[class])') == ['first-li', 'second-li', 'li-div', 'fifth-li', 'sixth-li', 'seventh-li']
    assert pcss(r'di\a0 v', r'div\[') == []
    assert pcss(r'[h\a0 ref]', r'[h\]ref]') == []

    with pytest.raises(ExpressionError):
        tuple(select('body:nth-child'))

    # Level 4 selectors
    assert pcss('li:is(#first-li, .c)', 'li:where(#first-li, .c)', 'li:matches(#first-li, .c)') == [
        'first-li', 'third-li', 'fourth-li',
    ]  # fmt: skip
    assert pcss(':is(ol, map) > :first-child') == ['first-li', 'area-href']
    assert pcss(':is(div p) > b', ':where(p) b') == ['p-b', 'p-b2']
    assert pcss('li:is()', 'li:where()', 'li:is(3x)') == []
    assert pcss('#outer-div > :not(a, p, map)') == ['first-ol', 'second-ol']
    assert pcss('ol :not(:not(.c))', 'li:not(:not(.c))') == ['third-li', 'fourth-li']
    assert pcss('div:not(div div)') == ['outer-div', 'foobar-div']
    assert pcss(':any-link', ':is(a, area, link):any-link', ':any-link[href]') == [
        'link-href', 'tag-anchor', 'nofollow-anchor', 'area-href',
    ]  # fmt: skip
    assert pcss(':scope') == ['html']
    assert pcss('a[rel="TAG" i]', 'a[rel="tag" s]', 'a[rel=tag i]', "a[REL='TaG'I]") == ['tag-anchor']
    assert pcss('a[rel="TAG"]', 'a[rel="TAG" s]') == []
    assert pcss('[lang|="EN" i]', '[lang|="en-US" i]', '[lang|="en" i]') == ['second-li']
    assert pcss('[lang|="e" i]', '[lang|="" i]') == []
    assert pcss('a[href^="HTTP" i]') == ['tag-anchor', 'nofollow-anchor']
    assert pcss('a[href$="ORG" i]') == ['nofollow-anchor']
    assert pcss('a[href*="LOCALHOST" i]') == ['tag-anchor']
    assert pcss('div[foobar~="BC" i]', 'div[foobar~="cDe" i]') == ['foobar-div']
    assert pcss('[foobar~="" i]', '[foobar~="ab bc" i]', 'a[href*="" i]', 'a[href^="" i]', 'a[href$="" i]') == []
    # The XML parser normalizes the newline in the class of fourth-li to a space
    assert pcss('li[class="AB C" i]', 'li[class="ab c"]') == ['third-li', 'fourth-li']
    assert pcss('[id=FIRST-LI i]', '[id="first-li" s]') == ['first-li']

    # Unsupported selectors are ignored in the forgiving selector lists of
    # :is() and :where(), but not elsewhere
    assert pcss('li:is(:unknown, .c)', 'li:where(:unknown(1), .c)', 'li:is(::before, .c)') == ['third-li', 'fourth-li']
    with pytest.raises(ExpressionError):
        tuple(select('li:not(:unknown)'))

    select = Select(document, ignore_inappropriate_pseudo_classes=True)
    assert len(tuple(select('p:hover'))) > 0
    assert len(tuple(select('p:focus-within'))) > 0
    # Ignored pseudo-classes match everything, as for li:hover
    assert tuple(select('li:is(:hover, .c)')) == tuple(select('li'))
    assert tuple(select('li:not(:hover)')) == ()


@pytest.mark.xfail(raises=ExpressionError, strict=True, reason='Needs milestone 4: nth, of-type, :has(), :lang()')
def test_select_milestone4() -> None:
    document = ids_document()
    select = Select(document)
    pcss = Checker(select)
    assert pcss(':lang("EN")', '*:lang(en-US)') == ['second-li', 'li-div']
    assert pcss(':lang("e")') == []
    assert pcss('li:nth-child(1)', 'li:first-child') == ['first-li']
    assert pcss('li:nth-child(3)', '#first-li ~ :nth-child(3)') == ['third-li']
    assert pcss('li:nth-child(10)') == []
    assert pcss('li:nth-child(2n)', 'li:nth-child(even)', 'li:nth-child(2n+0)') == [
        'second-li',
        'fourth-li',
        'sixth-li',
    ]
    assert pcss('li:nth-child(+2n+1)', 'li:nth-child(odd)') == ['first-li', 'third-li', 'fifth-li', 'seventh-li']
    assert pcss('li:nth-child(2n+4)') == ['fourth-li', 'sixth-li']
    assert pcss('li:nth-child(3n+1)') == ['first-li', 'fourth-li', 'seventh-li']
    assert pcss('li:nth-last-child(0)') == []
    assert pcss('li:nth-last-child(1)', 'li:last-child') == ['seventh-li']
    assert pcss('li:nth-last-child(2n)', 'li:nth-last-child(even)') == ['second-li', 'fourth-li', 'sixth-li']
    assert pcss('li:nth-last-child(2n+2)') == ['second-li', 'fourth-li', 'sixth-li']
    assert pcss('ol:first-of-type') == ['first-ol']
    assert pcss('ol:nth-child(1)') == []
    assert pcss('ol:nth-of-type(2)') == ['second-ol']
    assert pcss('ol:nth-last-of-type(1)') == ['second-ol']
    assert pcss('p *:only-of-type') == ['p-em', 'fieldset']
    assert pcss('p:only-of-type') == ['paragraph']
    assert pcss('ol:has(> li)', 'ol:has(li)', 'ol:has(li div)', 'ol:has(> li > div)', 'ol:has(div, span)') == [
        'first-ol'
    ]
    assert pcss('ol:has(> div)', 'ol:has(+ div)', 'map:has(li)') == []
    assert pcss(':has(> #li-div)', 'li:has(*)') == ['second-li']
    assert pcss('div:has(div)', ':has(+ #foobar-div)', '#outer-div:has(#fieldset input)') == ['outer-div']
    assert pcss('div:has(span)') == ['foobar-div']
    assert pcss('a:has(~ map)', 'a:has(~ ol ~ p)') == ['name-anchor', 'tag-anchor', 'nofollow-anchor']
    assert pcss('li:has(+ li.c)') == ['second-li', 'third-li']
    assert pcss('li:has(~ .c)') == ['first-li', 'second-li', 'third-li']
    assert pcss('p:has(b + em)', 'p:has(> em + b)', 'p:has(> b ~ fieldset > input)') == ['paragraph']
    assert pcss('p:has(> em + em)', 'p:has(> fieldset + b)') == []
    assert pcss(':has(> b, > area)') == ['paragraph', None]
    assert pcss(':has(#p-b)') == ['html', None, 'outer-div', 'paragraph']
    assert pcss('li:nth-child(2 of .c)', 'li:nth-last-child(1 of .c)') == ['fourth-li']
    assert pcss('li:nth-child(odd of :not(.c))') == ['first-li', 'fifth-li', 'seventh-li']
    assert pcss('li:nth-last-child(even of :not(.c))') == ['second-li', 'sixth-li']
    assert pcss(':nth-child(1 of li)', 'li:nth-child(1 of li, div)') == ['first-li']
    assert pcss(':nth-child(2 of div)') == ['foobar-div']
    assert pcss('li:nth-child(n of .nonexistent)', 'li:nth-child(1 of div)') == []
    assert pcss(':scope:has(body)') == ['html']
    with pytest.raises(ExpressionError):
        tuple(select('li:has(:unknown)'))
    with pytest.raises(ExpressionError):
        tuple(select('li:nth-child(1 of :unknown)'))


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def test_api() -> None:
    document = ids_document()
    select = Select(document)
    # A generator: errors are raised during iteration, not when select() is called
    bad = select('a!')
    with pytest.raises(SelectorSyntaxError):
        next(bad)
    unsupported = select('a:hover')
    with pytest.raises(ExpressionError):
        next(unsupported)
    with pytest.raises(StopIteration):
        next(select('nothing'))
    ol = next(select('#first-ol'))
    assert ol in select('ol')
    # root limits the results to its subtree, matching still uses the whole document
    assert [e.get('id') for e in select('div li', root=ol)] == [
        'first-li', 'second-li', 'third-li', 'fourth-li', 'fifth-li', 'sixth-li', 'seventh-li',
    ]  # fmt: skip
    assert [e.get('id') for e in select('div > li', root=ol)] == []
    assert [e.get('id') for e in select('#outer-div li.c', root=ol)] == ['third-li', 'fourth-li']
    assert [e.get('id') for e in select('ol', root=ol)] == ['first-ol']
    assert select.has_matches('li.c')
    assert not select.has_matches('li.c', root=next(select('#second-ol')))
    assert not select.has_matches('nothing')
    li = next(select('#third-li'))
    assert select.matches(li, 'ol > .c')
    assert not select.matches(li, ':first-child')
    other = etree.fromstring('<a/>')
    with pytest.raises(ValueError, match='not an element of the tree'):
        list(select('a', root=other))
    # Select takes an element anywhere in the tree, or an ElementTree. :scope is that element.
    assert Select(etree.ElementTree(document))._snapshot.count == select._snapshot.count
    scoped = Select(ol)
    assert [e.get('id') for e in scoped(':scope')] == ['first-ol']
    assert [e.get('id') for e in scoped(':root')] == ['html']
    # Selectors, lists of them, and selectors of nested rules
    sheet = parse_stylesheet('ol { & > .c { x: y } } #second-li, #fifth-li { x: y }')
    outer, second = sheet.rules
    assert isinstance(outer, StyleRule)
    assert isinstance(second, StyleRule)
    nested = outer.rules[0]
    assert isinstance(nested, StyleRule)
    assert [e.get('id') for e in select(nested.selectors[0])] == ['third-li', 'fourth-li']
    assert [e.get('id') for e in select(second.selectors)] == ['second-li', 'fifth-li']
    assert [e.get('id') for e in select(parse_selectors('#seventh-li, li:empty.c'))] == [
        'third-li', 'fourth-li', 'seventh-li',
    ]  # fmt: skip
    assert repr(select).startswith('<Select ')


def test_quirks_mode() -> None:
    document = html.fromstring('<div id="Main"><p class="Note x">a</p><p class="note">b</p></div>')
    standards = Select(document)
    quirks = Select(document, quirks_mode=True)
    assert not standards.quirks_mode
    assert quirks.quirks_mode
    assert [e.text for e in standards('.note')] == ['b']
    assert [e.text for e in standards('.Note')] == ['a']
    assert [e.text for e in quirks('.NOTE')] == ['a', 'b']
    assert not standards.has_matches('#main')
    assert quirks.has_matches('#main')
    assert standards.has_matches('#Main')
    # Tag and attribute names are always ASCII case-insensitive, attribute values only with i
    assert standards.has_matches('DIV[ID=Main]')
    assert not standards.has_matches('div[id=main]')
    assert standards.has_matches('div[id=main i]')


def test_namespaces_and_non_elements() -> None:
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:svg="http://www.w3.org/2000/svg" '
        'xmlns:x="urn:x"><body><!-- c --><?pi x?><p x:lang="fr" lang="en">t<!-- c -->t</p>'
        '<p><!-- only a comment --></p><p><!-- c -->tail</p><svg:svg><svg:rect/></svg:svg></body></html>'
    )
    document = etree.fromstring(xhtml)
    select = Select(document)
    # Namespaces are ignored: in tags, attributes, and in selectors
    assert [e.tag.rpartition('}')[2] for e in select('svg|rect, *|rect')] == ['rect']
    assert len(list(select('p[lang]'))) == 1
    assert len(list(select('p[xml|lang="fr"]'))) == 1
    # Comments and processing instructions are not elements, but their tails are text
    assert len(list(select('body > *'))) == 4
    assert len(list(select('p:empty'))) == 1
    assert len(list(select('body > :first-child'))) == 1
    # The standard library's ElementTree works too
    import xml.etree.ElementTree as ET

    tree = ET.fromstring('<root><a class="x"/><b><a/></b></root>')
    assert [e.tag for e in Select(tree)('b > a, .x')] == ['a', 'a']


def test_deep_and_long() -> None:
    # Long descendant chains are linear thanks to the failure memo, and long selectors do not
    # overflow the stack
    depth = 2000
    text = '<div>' * depth + '<p/>' + '</div>' * depth
    document = etree.fromstring(text, parser=etree.XMLParser(huge_tree=True))
    select = Select(document)
    assert len(list(select('div ' * 50 + 'p'))) == 1
    assert len(list(select('div ' * 50 + 'span p'))) == 0
    assert len(list(select(' '.join(['div'] * 1500) + ' > p'))) == 1
    assert len(list(select('div > ' * (depth - 1) + 'div > p'))) == 1


def test_inappropriate_set_matches_c() -> None:
    document = etree.fromstring('<p/>')
    select = Select(document, ignore_inappropriate_pseudo_classes=True)
    for name in INAPPROPRIATE_PSEUDO_CLASSES:
        assert select.has_matches(f'p:{name}'), name
    assert not Select(document).has_matches('p:is(:hover)')


# ---------------------------------------------------------------------------
# Against the oracle
# ---------------------------------------------------------------------------

TAGS = ('div', 'p', 'span', 'a', 'li', 'area', 'link', 'DIV', 'P')
CLASSES = ('x', 'X', 'y', 'x-y', 'é')
VALUES = ('a', 'A', 'a-b', 'a b', '', 'ab', 'b a', 'x-y')


@st.composite
def trees(draw: st.DrawFn) -> etree._Element:
    namespace = draw(st.sampled_from(('', '{http://www.w3.org/1999/xhtml}')))

    def element(depth: int, parent: etree._Element | None) -> etree._Element:
        tag = namespace + draw(st.sampled_from(TAGS))
        e = etree.Element(tag) if parent is None else etree.SubElement(parent, tag)
        if draw(st.booleans()):
            e.set('id', draw(st.sampled_from(('a', 'A', 'b', 'é'))))
        if draw(st.booleans()):
            e.set('class', ' '.join(draw(st.lists(st.sampled_from(CLASSES), max_size=3))))
        if draw(st.booleans()):
            e.set(
                draw(st.sampled_from(('href', 'lang', 'data-x', 'HREF', '{urn:x}lang'))), draw(st.sampled_from(VALUES))
            )
        if draw(st.integers(0, 3)) == 0:
            e.text = draw(st.sampled_from(('', ' ', 't')))
        if depth < 4:
            for _ in range(draw(st.integers(0, 3))):
                if draw(st.integers(0, 5)) == 0:
                    c = etree.Comment('c')
                    e.append(c)
                    c.tail = draw(st.sampled_from((None, '', 'x')))
                else:
                    element(depth + 1, e)
        return e

    return element(0, None)


simple = st.one_of(
    st.sampled_from(('*', 'div', 'P', 'span', 'a', 'li', '*|li', 'ns|div', 'area')),
    st.sampled_from(CLASSES).map(lambda c: '.' + c),
    st.sampled_from(('a', 'A', 'b', 'é')).map(lambda i: '#' + i),
    st.builds(
        lambda n, op, v, f: f'[{n}{op}"{v}"{f}]' if op else f'[{n}]',
        st.sampled_from(('href', 'lang', 'data-x', 'class', 'ID', 'xml|lang')),
        st.sampled_from(('', '=', '~=', '|=', '^=', '$=', '*=')),
        st.sampled_from(VALUES),
        st.sampled_from(('', ' i', ' s')),
    ),
    st.sampled_from(
        (
            ':root',
            ':scope',
            ':any-link',
            ':empty',
            ':first-child',
            ':last-child',
            ':only-child',
            ':hover',
            '::before',
            ':first-line',
            '&',
        )
    ),
)


def compound(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    pseudo = st.builds(lambda n, s: f':{n}({s})', st.sampled_from(('is', 'where', 'not')), inner)
    return st.lists(st.one_of(simple, pseudo), min_size=1, max_size=3).map(''.join)


def selector_lists(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    complex_selector = st.builds(
        lambda first, rest: first + ''.join(c + s for c, s in rest),
        compound(inner),
        st.lists(st.tuples(st.sampled_from((' ', ' > ', ' + ', ' ~ ')), compound(inner)), max_size=3),
    )
    return st.lists(complex_selector, min_size=1, max_size=3).map(', '.join)


def _lists() -> st.SearchStrategy[str]:
    return selector_lists(selector_lists(selector_lists(st.just('a'))))


selectors = st.deferred(_lists)


def oracle_result(document: etree._Element, css: str, ignore: bool, quirks: bool) -> list[etree._Element] | None:
    trees_ = [build_tree(c) for c in compile_selectors(css)]
    try:
        return Oracle(document, ignore, quirks).select(trees_)
    except Unsupported:
        return None


def select_result(document: etree._Element, css: str, ignore: bool, quirks: bool) -> list[etree._Element] | None:
    try:
        return list(Select(document, ignore, quirks_mode=quirks)(css))
    except ExpressionError:
        return None


@given(trees(), selectors, st.booleans(), st.booleans())
def test_against_oracle(document: etree._Element, css: str, ignore: bool, quirks: bool) -> None:
    try:
        compile_selectors(css)
    except SelectorSyntaxError:
        return
    assert select_result(document, css, ignore, quirks) == oracle_result(document, css, ignore, quirks)


def iter_ids(elements: Iterator[etree._Element]) -> list[str | None]:
    return [e.get('id') for e in elements]
