# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The object model: URLs (requirements §7)."""

import pytest

from kgcss import (
    ImportRule,
    SerializeOptions,
    StyleRule,
    iter_urls,
    parse_style,
    parse_stylesheet,
    remove_url_values,
    replace_urls,
    replace_urls_in_text,
    serialize,
)

PRESERVE = SerializeOptions(style='preserve')

SHEET = """@import "a.css"; @import url(b.css) print; @namespace svg url(http://svg);
p { background: url(c.png), image-set("d.png" 1x, url(e.png) 2x); content: "url(no)" /* url(no) */ }
@font-face { src: url(f.otf) format("opentype"), local(X) }
@page { background: url(g.png); @top-left { content: url(h.png) } }
@supports (background: url(i.png)) { @media print { q { r: cross-fade(url(j.png), url("k l.png")) } } }
@keyframes k { from { background: url(m.png) } } s { &:hover { t: u\\72l(n.png) } }
v { w: url(data:image/png;base64,xx); x: url( "o.png" ) }"""
ALL = ['a.css', 'b.css', 'c.png', 'd.png', 'e.png', 'f.otf', 'g.png', 'h.png', 'i.png', 'j.png', 'k l.png', 'm.png']
ALL += ['n.png', 'data:image/png;base64,xx', 'o.png']


def test_iter_urls_coverage() -> None:
    # requirements U1 and U3: @page, margin rules, @supports, unknown at-rules, nested rules and
    # functions such as image-set() and cross-fade() are all covered
    sheet = parse_stylesheet(SHEET)
    assert list(iter_urls(sheet)) == ALL
    assert list(iter_urls(sheet, ignore_imports=True)) == ALL[2:]
    rule = sheet.rules.of_type(*[r.type for r in sheet.rules if isinstance(r, StyleRule)][:1])[0]
    assert list(iter_urls(rule)) == ['c.png', 'd.png', 'e.png']
    assert isinstance(rule, StyleRule)
    assert list(iter_urls(rule.style)) == ['c.png', 'd.png', 'e.png']
    assert list(iter_urls(parse_style('a: url(x) url(y)'))) == ['x', 'y']
    # Not URLs: @namespace, strings and comments that look like url(), bad urls
    assert list(iter_urls(parse_stylesheet('@namespace url(n); a { b: "url(s)" /* url(c) */ url(a b) }'))) == []


def test_replace_urls() -> None:
    # requirements U2
    sheet = parse_stylesheet(SHEET)
    seen: list[str] = []

    def fn(url: str) -> str:
        seen.append(url)
        return url if url.startswith('data:') else 'images/' + url

    assert replace_urls(sheet, fn)
    assert seen == ALL
    assert list(iter_urls(sheet)) == [u if u.startswith('data:') else 'images/' + u for u in ALL]
    assert not replace_urls(sheet, lambda u: u)
    imp = sheet.rules[0]
    assert isinstance(imp, ImportRule)
    assert imp.href == 'images/a.css'
    text = sheet.css_text
    # Quotes are added only when needed, an @import string stays a string
    assert '@import "images/a.css";' in text
    assert 'url("images/k l.png")' in text
    assert 'url(images/c.png)' in text
    assert 'image-set("images/d.png" 1x' in text
    sheet2 = parse_stylesheet(SHEET)
    replace_urls(sheet2, lambda u: 'z', ignore_imports=True)
    assert [r.href for r in sheet2.rules if isinstance(r, ImportRule)] == ['a.css', 'b.css']


def test_replace_urls_changes_only_affected_declarations() -> None:
    css = 'a { b: c;  d : url(x.png) ; e:f } g { h: i }'
    sheet = parse_stylesheet(css)
    assert replace_urls(sheet, lambda u: 'y.png')
    # Only the rule with the URL is rewritten, and in it only the changed declaration
    assert serialize(sheet, PRESERVE) == 'a {\n  b: c;\n  d: url(y.png);\n  e:f;\n} g { h: i }'
    block = parse_style('a: url(x); b: c')
    assert replace_urls(block, str.upper)
    assert block.css_text == 'a: url(X);\nb: c;'


def test_replace_urls_in_text() -> None:
    # requirements U4: only the URLs change, everything else is byte identical
    out = replace_urls_in_text(SHEET, str.upper)
    expected = SHEET
    for url in ALL:
        if url == 'k l.png':
            expected = expected.replace('url("k l.png")', 'url("K L.PNG")')
        elif url == 'o.png':
            expected = expected.replace('url( "o.png" )', 'url(O.PNG)')
        elif url == 'n.png':
            expected = expected.replace('u\\72l(n.png)', 'url(N.PNG)')
        else:
            expected = expected.replace(url, url.upper(), 1)
    assert out == expected
    assert replace_urls_in_text(SHEET, lambda u: u) is SHEET
    plain = 'a { b: c }'
    assert replace_urls_in_text(plain, str.upper) is plain
    # A style attribute has no @import
    assert replace_urls_in_text('a: url(x); b: "y"', str.upper, style_attribute=True) == 'a: url(X); b: "y"'
    assert replace_urls_in_text('@import "x";', str.upper, style_attribute=True) == '@import "x";'
    assert replace_urls_in_text('@import "x";', str.upper) == '@import "X";'
    # New URLs are quoted when needed
    assert replace_urls_in_text('a { b: url(x) }', lambda u: 'a b"c') == 'a { b: url("a b\\"c") }'


def test_url_mapper_returning_none_keeps_the_url() -> None:
    def fn(url: str) -> str | None:
        return None if url.endswith('.png') else 'new-' + url

    sheet = parse_stylesheet(SHEET)
    assert replace_urls(sheet, fn)
    assert list(iter_urls(sheet)) == [u if u.endswith('.png') else 'new-' + u for u in ALL]
    sheet = parse_stylesheet(SHEET)
    assert not replace_urls(sheet, lambda u: None)
    assert serialize(sheet, PRESERVE) == SHEET
    assert replace_urls_in_text(SHEET, lambda u: None) is SHEET
    assert replace_urls_in_text('a { b: url(x.png) url(y.otf) }', fn) == 'a { b: url(x.png) url(new-y.otf) }'


@pytest.mark.parametrize(
    ('css', 'expected'),
    [
        # requirements U5 and polish/tests/cascade.py test_remove_property_value
        ('background-image: url(b.png); background: black url(a.png) fixed', 'background: black fixed;'),
        ('src: url(a.png), url(b.otf); c: d', 'src: url(b.otf);\nc: d;'),
        ('src: url(a.png), url(b.png)', ''),
        ('a: image-set(url(a.png) 1x) red', 'a: red;'),
        ('a: url(x.otf)', 'a: url(x.otf);'),
    ],
)
def test_remove_url_values(css: str, expected: str) -> None:
    block = parse_style(css)
    before = block.css_text
    assert remove_url_values(block, lambda u: u.endswith('.png')) == (expected != before)
    assert block.css_text == expected


def test_replace_urls_in_values_left_open_by_eof() -> None:
    # The value is structurally incomplete, replacing a URL in it must still work
    sheet = parse_stylesheet('a {a:f(;@import url(a.css) {}}')
    assert replace_urls(sheet, lambda u: 'X' + u)
    assert list(iter_urls(sheet)) == ['Xa.css']


def test_rules_without_urls_are_not_parsed() -> None:
    sheet = parse_stylesheet('a { b: c } d { e: url(f) } g { h: i; j { k: l } }')
    a, _, g = sheet.rules
    assert list(iter_urls(sheet)) == ['f']
    # Blocks whose text has nothing like a URL are skipped without being parsed
    assert isinstance(a, StyleRule)
    assert isinstance(g, StyleRule)
    assert a._rules is None
    assert g._rules is None
    # Changed rules are always looked at, as their source text is stale
    a.style.set('b', 'url(new)')
    nested = g.rules[0]
    assert isinstance(nested, StyleRule)
    nested.style.set('k', 'image-set("x" 1x)')
    assert list(iter_urls(sheet)) == ['new', 'f', 'x']
    assert replace_urls(sheet, lambda u: u.upper())
    assert serialize(sheet) == (
        'a {\n  b: url(NEW);\n}\nd {\n  e: url(F);\n}\ng {\n  h: i;\n  j {\n    k: image-set("X" 1x);\n  }\n}'
    )
