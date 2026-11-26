# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Matching selectors with Select.

test_select, test_select_nth_has_lang, test_select_level4 and
test_select_shakespeare are ported from calibre's css_selectors
(src/css_selectors/tests.py). The random tests compare Select with the naive
matcher in selector_oracle.py, and with css_selectors itself.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import assert_type

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

DATA = Path(__file__).parent / 'data'
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
    def __init__(self, select: Select[etree._Element]) -> None:
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


def test_select_nth_has_lang() -> None:
    # The rest of css_selectors' test_select
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


HTML_LEVEL4 = """
<html id="html"><body id="body">
<div id="book">
  <h1 id="chapter1" class="ChapterS">One</h1>
  <h2 id="sub1" class="SubChapter">One A</h2>
  <p id="p1" class="para">Text</p>
  <p id="p2" class="para note">Text</p>
  <h1 id="chapter2" class="ChapterS">Two</h1>
  <p id="p3" class="para">Text</p>
  <h1 id="chapter3" class="ChapterS">Three</h1>
  <!-- comment -->
  <h2 id="sub3" class="SubChapter">Three A</h2>
  <section id="sec1">
    <div id="sec1-div"><span id="sec1-span" class="hl">x</span></div>
    <p id="sec1-p" class="para">y</p>
  </section>
  <section id="sec2">
    <p id="sec2-p1" class="para">z</p>
    <p id="sec2-p2" class="para note">w</p>
    <p id="sec2-p3" class="para">v</p>
    <p id="sec2-p4" class="para note">u</p>
  </section>
  <ul id="list">
    <li id="li1" data-x="Alpha">a</li>
    <li id="li2" data-x="beta gamma">b</li>
    <li id="li3" data-x="ALPHA-one">c</li>
  </ul>
</div>
</body></html>
"""


def test_select_level4() -> None:
    parser = etree.XMLParser(recover=True, no_network=True, resolve_entities=False)
    document = etree.fromstring(HTML_LEVEL4, parser=parser)
    select = Select(document)
    pcss = Checker(select)

    # Look-behind using :has(), the comment between chapter3 and sub3 must be ignored
    assert pcss('.ChapterS:has(+ .SubChapter)', '.ChapterS:has(+.SubChapter)', 'h1:has(+ h2)') == [
        'chapter1', 'chapter3',
    ]  # fmt: skip
    assert pcss('.ChapterS:not(:has(+ .SubChapter))', 'h1:has(+ p)') == ['chapter2']
    assert pcss('h1:has(~ section)', 'body > div:has(section) > h1') == ['chapter1', 'chapter2', 'chapter3']
    assert pcss('h1:has(+ h2 + p)', 'h1:has(+ h2 + p.para ~ p.note)') == ['chapter1']
    assert pcss('h1:has(+ h2 ~ section)') == ['chapter1', 'chapter3']
    assert pcss('h1:has(+ h2 + section)') == ['chapter3']
    assert pcss(
        'section:has(span)', 'section:has(> div > span)', 'section:has(div span.hl)', 'section:has(> div span)',
        'section:has(p):has(span)', 'section:has(> div + p)',
    ) == ['sec1']  # fmt: skip
    assert pcss(
        'section:has(> span)', 'section:has(> p.note + p.note)', 'section:has(> p + div)',
        'section:has(+ section + section)',
    ) == []  # fmt: skip
    assert pcss(
        'section:has(> p.note)', 'section:has(> p + p.note + p)', 'section:has(> .note ~ .note)', 'section:has(+ ul)'
    ) == ['sec2']
    assert pcss('section:has(.note, .hl)', 'section:has(:is(.note, .hl))') == ['sec1', 'sec2']
    assert pcss(':has(> .hl)') == ['sec1-div']
    assert pcss('div:has(.hl)') == ['book', 'sec1-div']
    assert pcss(':has(.hl)', ':has(span)') == ['html', 'body', 'book', 'sec1', 'sec1-div']
    assert pcss('p:has(~ p.note)') == ['p1', 'sec2-p1', 'sec2-p2', 'sec2-p3']
    assert pcss(':has(+ section)') == ['sub3', 'sec1']
    assert pcss('ul:has(> li:nth-child(3))', 'ul:has(> li:nth-child(3 of [data-x]))') == ['list']
    assert pcss(':has(> section) > h2') == ['sub1', 'sub3']
    assert pcss('section:has(> p) p:has(+ p.note)') == ['sec2-p1', 'sec2-p3']

    assert pcss('p:is(.note, #p3)') == ['p2', 'p3', 'sec2-p2', 'sec2-p4']
    assert pcss(':is(section, ul) > :first-child') == ['sec1-div', 'sec2-p1', 'li1']
    assert pcss(':where(section) p', 'section :is(p)', ':is(section p)') == [
        'sec1-p', 'sec2-p1', 'sec2-p2', 'sec2-p3', 'sec2-p4',
    ]  # fmt: skip
    assert pcss('p:not(.note, section p)') == ['p1', 'p3']
    assert pcss('p:not(:is(.note))', 'p:not(.note)') == ['p1', 'p3', 'sec1-p', 'sec2-p1', 'sec2-p3']

    assert pcss('p:nth-child(2 of .note)') == ['sec2-p4']
    assert pcss(':nth-child(1 of .note)') == ['p2', 'sec2-p2']
    assert pcss(':nth-last-child(2 of .note)') == ['sec2-p2']
    assert pcss(':nth-last-child(1 of .note)') == ['p2', 'sec2-p4']
    assert pcss('p:nth-last-child(1 of .para)') == ['p3', 'sec1-p', 'sec2-p4']
    assert pcss(':nth-child(odd of .para)', ':nth-child(2n+1 of p)') == ['p1', 'p3', 'sec1-p', 'sec2-p1', 'sec2-p3']
    assert pcss('h1:nth-child(2 of h1)', '.ChapterS:nth-last-child(2 of h1)') == ['chapter2']
    assert pcss(':nth-child(2 of h1, h2)') == ['sub1']
    assert pcss(':nth-child(-n+2 of .ChapterS)') == ['chapter1', 'chapter2']
    assert pcss('h1:nth-child(2 of .SubChapter)', 'li:nth-child(4 of li)') == []

    assert pcss('li[data-x="alpha" i]', 'li[data-x="Alpha" s]', 'li[data-x="Alpha"]') == ['li1']
    assert pcss('li[data-x="alpha"]', 'li[data-x="alpha" s]') == []
    assert pcss('li[data-x|="alpha" i]', 'li[data-x^="ALP" i]', 'li[data-x*="PH" i]') == ['li1', 'li3']
    assert pcss('li[data-x$="ONE" i]', 'li[data-x|="ALPHA" s]') == ['li3']
    assert pcss('li[data-x~="GAMMA" i]', '[DATA-X="beta GAMMA" i]') == ['li2']
    assert pcss('li[data-x~="" i]', 'li[data-x^="" i]', 'li[data-x~="beta gamma" i]') == []
    assert pcss('[id="P1" i]') == ['p1']
    assert pcss('[class~="NOTE" i]') == ['p2', 'sec2-p2', 'sec2-p4']

    # Restricting matches to a subtree
    sec2 = next(select('#sec2'))
    assert [e.get('id') for e in select('p:has(+ .note)', root=sec2)] == ['sec2-p1', 'sec2-p3']


def test_select_shakespeare() -> None:
    document = html.document_fromstring((DATA / 'css_selectors_shakespeare.html').read_text(encoding='utf-8'))
    # css_selectors matched classes case-insensitively
    select = Select(document, quirks_mode=True)

    def count(s: str) -> int:
        return sum(1 for _ in select(s))

    # Data borrowed from http://mootools.net/slickspeed/
    assert count('*') == 249
    assert count('div:only-child') == 22
    assert count('div:nth-child(even)') == 106
    assert count('div:nth-child(2n)') == 106
    assert count('div:nth-child(odd)') == 137
    assert count('div:nth-child(2n+1)') == 137
    assert count('div:nth-child(n)') == 243
    assert count('div:last-child') == 53
    assert count('div:first-child') == 51
    assert count('div > div') == 242
    assert count('div + div') == 190
    assert count('div ~ div') == 190
    assert count('body') == 1
    assert count('body div') == 243
    assert count('div') == 243
    assert count('div div') == 242
    assert count('div div div') == 241
    assert count('div, div, div') == 243
    assert count('div, a, span') == 243
    assert count('.dialog') == 51
    assert count('div.dialog') == 51
    assert count('div .dialog') == 51
    assert count('div.character, div.dialog') == 99
    assert count('div.direction.dialog') == 0
    assert count('div.dialog.direction') == 0
    assert count('div.dialog.scene') == 1
    assert count('div.scene.scene') == 1
    assert count('div.scene .scene') == 0
    assert count('div.direction .dialog ') == 0
    assert count('div .dialog .direction') == 4
    assert count('div.dialog .dialog .direction') == 4
    assert count('#speech5') == 1
    assert count('div#speech5') == 1
    assert count('div #speech5') == 1
    assert count('div.scene div.dialog') == 49
    assert count('div#scene1 div.dialog div') == 142
    assert count('#scene1 #speech1') == 1
    assert count('div[class]') == 103
    assert count('div[class=dialog]') == 50
    assert count('div[class^=dia]') == 51
    assert count('div[class$=log]') == 50
    assert count('div[class*=sce]') == 1
    assert count('div[class|=dialog]') == 50
    assert count('div[class~=dialog]') == 51

    # Level 4 selectors
    assert count(':is(div.character, div.dialog)') == 99
    assert count('div:where(.character, .dialog)') == 99
    assert count('div:not(.character, .dialog)') == 243 - 99
    assert count('div:nth-child(n of .dialog)') == 51
    assert count('div:nth-child(n of div)') == count('div')
    assert count('div[CLASS=DIALOG i]') == 50

    # Check :has() against a brute force implementation
    def brute_has(anchor_selector: str, relation: str, target_selector: str) -> int:
        targets = frozenset(select(target_selector))
        ans = 0
        for anchor in select(anchor_selector):
            if relation == ' ':
                candidates = list(anchor.iterdescendants('*'))
            elif relation == '>':
                candidates = list(anchor.iterchildren('*'))
            elif relation == '+':
                candidates = list(anchor.itersiblings('*'))[:1]
            else:
                candidates = list(anchor.itersiblings('*'))
            if any(c in targets for c in candidates):
                ans += 1
        return ans

    for anchor, target in (
        ('div', '.dialog'), ('div', '.direction'), ('div.dialog', 'div.dialog'), ('*', 'h3'), ('div', '#speech5'),
    ):  # fmt: skip
        for relation in ' >+~':
            expected = brute_has(anchor, relation, target)
            rel = '' if relation == ' ' else relation
            assert count(f'{anchor}:has({rel} {target})') == expected, (anchor, relation, target)
    inner = frozenset(select('div.dialog:has(> div.direction)'))
    expected = sum(1 for d in select('div') if any(c in inner for c in d.iterchildren('*')))
    assert count('div:has(> div.dialog > div.direction)') == expected


def test_lang() -> None:
    xhtml = (
        '<html xmlns="http://www.w3.org/1999/xhtml" lang="en-US"><body>'
        '<p id="a">a</p>'
        '<p id="b" xml:lang="fr-CH" lang="de">b<span id="c">c</span></p>'
        '<p id="d" lang="">d</p>'
        '<p id="e" xmlns:x="urn:x" x:lang="fr">e</p>'
        '<p id="f" lang="de-Latn-CH-1996">f</p>'
        '<p id="g" lang="de-x-CH">g</p>'
        '</body></html>'
    )
    select = Select(etree.fromstring(xhtml))
    pcss = Checker(select)
    # html and body have no id
    assert pcss(':lang(en)', ':lang("en-us")') == [None, None, 'a', 'e']
    assert pcss('p:lang(EN)') == ['a', 'e']
    # xml:lang wins over lang on the same element (css_selectors ignored xml:lang), and is inherited
    assert pcss(':lang(fr)', ':lang(fr-ch)') == ['b', 'c']
    assert pcss('p:lang(fr)') == ['b']
    assert pcss('span:lang(fr)') == ['c']
    assert pcss(':lang(de)') == ['f', 'g']
    # lang="" is an unknown language. Only the lang of no namespace, or of the XML namespace, counts.
    assert pcss('#d:lang(en)', '#d:lang("")', '#e:lang(fr)') == []
    # Extended filtering (RFC 4647): ranges can skip subtags, but not past a singleton such as x
    assert pcss(':lang("*-CH")') == ['b', 'c', 'f']
    assert pcss(':lang(de-CH)', ':lang("de-*-CH")') == ['f']
    assert pcss(':lang(en, de)') == [None, None, 'a', 'e', 'f', 'g']
    # lxml.html keeps xml:lang as the attribute name
    document = html.fromstring('<div lang="de"><p xml:lang="fr">x</p></div>')
    assert [e.tag for e in Select(document)(':lang(fr)')] == ['p']


def test_relative_and_nth_edges() -> None:
    document = ids_document()
    select = Select(next(Select(document)('#first-ol')))
    # A selector that starts with a combinator, not from a nested rule, is relative to :scope
    relative = parse_selectors('> .c, ~ ol', nested=True)
    assert [e.get('id') for e in select(relative)] == ['third-li', 'fourth-li', 'second-ol']
    pcss = Checker(Select(document))
    # The root element has no siblings, and is the first and only of its kind (Selectors Level 4)
    assert pcss('html:first-child', 'html:only-child', 'html:nth-child(1)', 'html:nth-last-of-type(1)') == ['html']
    assert pcss('li:nth-child(-n+0)', 'li:nth-child(-2n)', 'li:nth-child(0n+0)', 'li:nth-child(99999999999)') == []
    assert pcss('li:nth-child(-n+2)', 'li:nth-last-child(n+6)') == ['first-li', 'second-li']
    assert pcss('li:nth-child(-2n+3)') == ['first-li', 'third-li']


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
LANGUAGES = ('en', 'en-US', 'EN-gb', 'fr-CH', 'de-Latn-CH', 'de-x-CH', '')
XML_LANG = '{http://www.w3.org/XML/1998/namespace}lang'


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
            name = draw(st.sampled_from(('href', 'data-x', 'HREF', '{urn:x}lang')))
            e.set(name, draw(st.sampled_from(VALUES)))
        for name in ('lang', XML_LANG):
            if draw(st.integers(0, 4)) == 0:
                e.set(name, draw(st.sampled_from(LANGUAGES)))
        if draw(st.integers(0, 3)) == 0:
            e.text = draw(st.sampled_from(('', ' ', 't')))
        if depth < 4:
            for _ in range(draw(st.integers(0, 4))):
                if draw(st.integers(0, 5)) == 0:
                    c = etree.Comment('c')
                    e.append(c)
                    c.tail = draw(st.sampled_from((None, '', 'x')))
                else:
                    element(depth + 1, e)
        return e

    return element(0, None)


ANBS = ('1', '2', 'odd', 'even', '-n+2', 'n+2', '3n', '0', 'n')
PLAIN_PSEUDO = (
    ':root', ':scope', ':any-link', ':empty', ':first-child', ':last-child', ':only-child', ':first-of-type',
    ':last-of-type', ':only-of-type', ':hover', '&',
)  # fmt: skip


LANG_RANGES = ('en', 'EN-US', 'fr', '"*-CH"', '"de-*-CH"', 'x', 'en, fr')
PSEUDO_ELEMENTS = ('::before', ':first-line', '::marker')


@st.composite
def subclass_selector(draw: st.DrawFn, depth: int, in_has: bool) -> str:
    kinds = ['class', 'id', 'attribute', 'pseudo', 'nth', 'lang']
    if depth < 2:
        kinds += ['is', 'nth-of']
        if not in_has:  # :has() cannot be nested
            kinds.append('has')
    match draw(st.sampled_from(kinds)):
        case 'class':
            return '.' + draw(st.sampled_from(CLASSES))
        case 'id':
            return '#' + draw(st.sampled_from(('a', 'A', 'b', 'é')))
        case 'attribute':
            name = draw(st.sampled_from(('href', 'lang', 'data-x', 'class', 'ID', 'xml|lang')))
            op = draw(st.sampled_from(('', '=', '~=', '|=', '^=', '$=', '*=')))
            if not op:
                return f'[{name}]'
            return f'[{name}{op}"{draw(st.sampled_from(VALUES))}"{draw(st.sampled_from(("", " i", " s")))}]'
        case 'pseudo':
            return draw(st.sampled_from(PLAIN_PSEUDO))
        case 'nth':
            name = draw(st.sampled_from(('nth-child', 'nth-last-child', 'nth-of-type', 'nth-last-of-type')))
            return f':{name}({draw(st.sampled_from(ANBS))})'
        case 'nth-of':
            name = draw(st.sampled_from(('nth-child', 'nth-last-child')))
            return f':{name}({draw(st.sampled_from(ANBS))} of {draw(selector_list(depth + 1, False, in_has=in_has))})'
        case 'lang':
            return f':lang({draw(st.sampled_from(LANG_RANGES))})'
        case 'is':
            name = draw(st.sampled_from(('is', 'where', 'not')))
            return f':{name}({draw(selector_list(depth + 1, False, in_has=in_has))})'
    return f':has({draw(selector_list(depth + 1, False, relative=True, in_has=True))})'


@st.composite
def compound(draw: st.DrawFn, depth: int, in_has: bool) -> str:
    # A type selector can only come first
    text = draw(st.sampled_from(('', '', '*', 'div', 'P', 'span', 'a', 'li', '*|li', 'ns|div', 'area')))
    for _ in range(draw(st.integers(0 if text else 1, 3))):
        text += draw(subclass_selector(depth, in_has))
    return text


@st.composite
def complex_selector(draw: st.DrawFn, depth: int, top: bool, relative: bool, in_has: bool) -> str:
    text = draw(st.sampled_from(('', '> ', '+ ', '~ '))) if relative else ''
    text += draw(compound(depth, in_has))
    for _ in range(draw(st.integers(0, 3))):
        text += draw(st.sampled_from((' ', ' > ', ' + ', ' ~ '))) + draw(compound(depth, in_has))
    # Pseudo-elements are only valid at the end of a selector at the top level
    if top and draw(st.integers(0, 5)) == 0:
        text += draw(st.sampled_from(PSEUDO_ELEMENTS))
    return text


@st.composite
def selector_list(
    draw: st.DrawFn, depth: int = 0, top: bool = True, relative: bool = False, in_has: bool = False
) -> str:
    return ', '.join(draw(complex_selector(depth, top, relative, in_has)) for _ in range(draw(st.integers(1, 3))))


selectors = selector_list()


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


@pytest.mark.parametrize(
    'css',
    [
        '.x > p', '.x p', '.x + p', '.x ~ p', 'div.x p span', '.x .x p', '.x ~ .x ~ p', '#a > div > p',
        '.x > :nth-child(2n)', '.x ~ p:has(span)', '.x p, .y + p', 'section .x > p',
    ],
)  # fmt: skip
def test_matching_from_the_left(css: str) -> None:
    # A rare compound before the last combinator and a common last one: matched from the left
    parts = ['<root><section>']
    for i in range(30):
        cls = ' class="x"' if i % 9 == 0 else (' class="y"' if i % 7 == 0 else '')
        ident = ' id="a"' if i == 10 else ''
        parts.append(f'<div{cls}{ident}><p/><p><span/></p><div class="x"><p/></div></div><p/>')
    parts.append('</section><p class="x"/><p/></root>')
    document = etree.fromstring(''.join(parts))
    expected = Oracle(document).select(build_tree(c) for c in compile_selectors(css))
    select = Select(document)
    assert list(select(css)) == expected
    # Within a subtree too
    section = document[0]
    in_section = [e for e in expected if e is section or section in e.iterancestors()]
    assert list(select(css, root=section)) == in_section


def test_select_is_generic_over_the_element_type() -> None:
    # assert_type is checked by ty: Select keeps the type of the elements of the tree it was made from
    import xml.etree.ElementTree as ET

    lxml_root = etree.fromstring('<a><b/></a>')
    for select in (Select(lxml_root), Select(lxml_root.getroottree())):
        assert_type(select, Select[etree._Element])
        assert_type(select.root, etree._Element)
        found = list(select('b'))
        assert_type(found, list[etree._Element])
        assert [e.tag for e in found] == ['b']
    et_root = ET.fromstring('<a><b/></a>')
    for et_select in (Select(et_root), Select(ET.ElementTree(et_root))):
        assert_type(et_select, Select[ET.Element])
        assert_type(list(et_select('b', root=et_root)), list[ET.Element])
        assert et_select.root is et_root
        assert et_select.matches(et_root[0], 'a > b')
