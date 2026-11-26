# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The css_parser-style facade, kgcss.compat.

The same script of css_parser operations runs on css_parser itself (when it is
installed) and on the facade, and what they see is compared. The editor demo
plugin of calibre's manual runs on the facade unchanged.
"""

import doctest
import importlib
import re
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

import kgcss
import kgcss.compat
from kgcss.compat import CSSRule, CSSStyleSheet, parseString, parseStyle, unwrap, wrap


def test_doctests() -> None:
    assert doctest.testmod(kgcss.compat).failed == 0


# calibre's manual/plugin_examples/editor_demo/main.py, unchanged but for the font size number format
def magnify_stylesheet(sheet: Any, factor: float) -> None:  # noqa: ANN401
    for rule in sheet.cssRules.rulesOfType(CSSRule.STYLE_RULE):
        magnify_declaration(rule.style, factor)


def magnify_declaration(style: Any, factor: float) -> None:  # noqa: ANN401
    val = style.getPropertyValue('font-size')
    if not val:
        return
    num = re.search(r'[0-9.]+', val)
    if num is not None:
        n = num.group()
        val = val.replace(n, f'{float(n) * factor:g}')
        style.setProperty('font-size', val)


def test_editor_demo_plugin() -> None:
    sheet = parseString('p { font-size: 12pt } @media print { h1 { font-size: 2em } } a { color: red }')
    magnify_stylesheet(sheet, 2)
    # Only the top level style rules, as css_parser's rulesOfType gives them
    assert unwrap(sheet).rules[0].style['font-size'] == '24pt'  # type: ignore[attr-defined]
    assert unwrap(sheet).rules[1].rules[0].style['font-size'] == '2em'  # type: ignore[attr-defined]
    assert kgcss.compat.serialize(sheet, 'text/css', pretty_print=True) == unwrap(sheet).encode()
    block = parseStyle('font-size: 10px; color: red')
    magnify_declaration(block, 1.5)
    assert block.getCssText(separator=' ') == 'font-size: 15px; color: red'


def observe(css: Any, CSSRule: Any) -> list[object]:  # noqa: ANN401
    """What a program written for css_parser sees, as a list of observations. css is a module with
    parseString and parseStyle."""
    seen: list[object] = []
    sheet = css.parseString(
        '@charset "utf-8"; @import url(i.css) print; @namespace svg url(http://www.w3.org/2000/svg); '
        'a.x, #y > b { color: red !important; margin: 0 1px; background: url("p q.png") } '
        '/* c */ @media screen and (min-width: 1px) { p { font: italic 12px/1.2 "A B", serif } } '
        '@font-face { font-family: F; src: url(f.ttf) } @page :first { margin: 1in }'
    )
    rules = sheet.cssRules
    seen.append([(r.type, r.typeString) for r in rules])
    seen.append(len(rules))
    seen.append([r.selectorText for r in rules.rulesOfType(CSSRule.STYLE_RULE)])
    imp = rules[1]
    seen.append((imp.href, imp.media.mediaText))
    style_rule = rules[3]
    seen.append(style_rule.selectorText)
    seen.append([s.selectorText for s in style_rule.selectorList])
    seen.append([s.specificity for s in style_rule.selectorList])
    style = style_rule.style
    seen.append((style.getPropertyValue('color'), style.getPropertyPriority('color'), style.getPropertyValue('nope')))
    seen.append((style.length, style.item(0), style.item(99), 'margin' in style, style['margin']))
    prop = style.getProperty('color')
    seen.append((prop.name, prop.value, prop.priority, style.getProperty('nope')))
    value = style.getPropertyCSSValue('background')
    seen.append([(v.type, v.value) for v in value])
    seen.append(style.removeProperty('margin'))
    seen.append(style.removeProperty('margin'))
    style.setProperty('color', 'blue')
    seen.append((style.getPropertyValue('color'), style.getPropertyPriority('color')))
    style.setProperty('padding', '2px', 'important')
    seen.append([p.name for p in style.getProperties()])
    seen.append(style.getCssText(separator=' ').replace(';', ''))
    seen.append(rules[4].type)
    media = rules[5]
    seen.append((media.media.mediaText, media.cssRules[0].selectorText, media.cssRules[0].parentRule.type))
    font = media.cssRules[0].style.getPropertyCSSValue('font')
    if font is not None:
        seen.append([v.cssText for v in font])
    seen.append(rules[6].style.getPropertyValue('font-family'))
    seen.append((rules[7].selectorText, rules[7].style.getPropertyValue('margin')))
    seen.append(style_rule.parentStyleSheet is not None)
    sheet.deleteRule(4)
    seen.append(len(sheet.cssRules))
    seen.append(sheet.cssRules.index(sheet.cssRules[4]))
    i = sheet.insertRule('z { top: 0 }', len(sheet.cssRules))
    seen.append((i, sheet.cssRules[i].selectorText))
    sheet.deleteRule(sheet.cssRules[i])
    seen.append(len(sheet.cssRules))
    decl = css.parseStyle('a: b; a: c; d: e')
    seen.append((decl.getPropertyValue('a'), [p.value for p in decl.getProperties(all=True)], decl.keys()))
    return seen


# What differs, by observation index, and why: the facade follows kgcss
DIFFERENCES: dict[int, str] = {
    15: 'single-line text quotes strings with single quotes, so that it is safe in style="" (requirements §18)',
}


def test_same_as_css_parser() -> None:
    import logging

    css_parser: ModuleType = pytest.importorskip('css_parser')
    # Imported dynamically: whether css_parser is installed (it has no type hints) differs between environments
    real_css_rule: type = importlib.import_module('css_parser.css').CSSRule

    css_parser.log.setLevel(logging.CRITICAL)
    real = observe(css_parser, real_css_rule)
    ours = observe(kgcss.compat, CSSRule)
    mismatches = [(i, a, b) for i, (a, b) in enumerate(zip(real, ours, strict=True)) if a != b]
    assert [i for i, _, _ in mismatches] == list(DIFFERENCES), mismatches


def test_values_like_css_parser() -> None:
    style = parseStyle('font: italic 12px/1.2 "A B", serif; background: url(x.png) #fff; width: calc(1px + 2px)')
    font = style.getPropertyCSSValue('font')
    assert font is not None
    assert [(v.type, v.cssText, v.value) for v in font] == [
        ('IDENT', 'italic', 'italic'),
        ('DIMENSION', '12px', 12),
        ('NUMBER', '1.2', 1.2),
        ('STRING', '"A B"', 'A B'),
        ('IDENT', 'serif', 'serif'),
    ]
    assert font[1].dimension == 'px'
    bg = style.getPropertyCSSValue('background')
    assert bg is not None
    assert (bg[0].type, bg[0].uri, bg.length, bg.item(5)) == ('URI', 'x.png', 2, None)
    width = style.getPropertyCSSValue('width')
    assert width is not None
    assert (width[0].type, width[0].name) == ('FUNCTION', 'calc')
    width.cssText = '3px'
    assert style.getPropertyValue('width') == '3px'


def test_views_are_live() -> None:
    sheet = kgcss.parse_stylesheet('a { color: red } @supports (display: grid) { b { c: d } }')
    view = wrap(sheet)
    assert isinstance(view, CSSStyleSheet)
    assert unwrap(view) is sheet
    # Changes through the view are in the kgcss objects, and the other way round
    rule = view.cssRules[0]
    rule.style.setProperty('margin', '0')  # type: ignore[attr-defined]
    assert sheet.rules[0].style['margin'] == '0'  # type: ignore[attr-defined]
    sheet.rules[0].selector_text = 'p'  # type: ignore[attr-defined]
    assert rule.selectorText == 'p'  # type: ignore[attr-defined]
    # Views of the same object are equal and hash alike
    assert view.cssRules[0] == rule
    assert hash(view.cssRules[0]) == hash(rule)
    assert view.cssRules[0] != view.cssRules[1]
    assert rule in view.cssRules
    # @supports is an unknown rule to css_parser, but its rules are there
    supports = view.cssRules[1]
    assert (supports.type, supports.typeString) == (CSSRule.UNKNOWN_RULE, 'UNKNOWN_RULE')
    assert supports.atkeyword == '@supports'  # type: ignore[attr-defined]
    assert supports.cssRules[0].selectorText == 'b'  # type: ignore[attr-defined]
    supports.insertRule('e { f: g }')  # type: ignore[attr-defined]
    assert [r.selector_text for r in sheet.iter_rules(kgcss.RuleType.STYLE)] == ['p', 'b', 'e']  # type: ignore[attr-defined]
    # wrap() works on rules, blocks and properties too
    block = sheet.rules[0].style  # type: ignore[attr-defined]
    assert unwrap(wrap(block)) is block
    assert unwrap(wrap(sheet.rules[0])) is sheet.rules[0]
    prop = block.get_property('color')
    assert prop is not None
    assert wrap(prop).value == 'red'
    with pytest.raises(TypeError):
        wrap('a')  # type: ignore[call-overload]


def test_sheet_api() -> None:
    sheet = parseString(b'@charset "latin1"; a { content: "\xe9" }')
    assert sheet.encoding == 'windows-1252'  # the WHATWG encoding for the label latin1
    assert sheet.cssText == '@charset "latin1";\na {\n  content: "\xe9";\n}'.encode('latin1')
    assert sheet.cssRules[0].encoding == 'latin1'  # type: ignore[attr-defined]
    sheet.cssText = 'b { c: d }'
    assert [r.selectorText for r in sheet] == ['b']  # type: ignore[attr-defined]
    # add() puts @import where it is valid; insertRule() and add() copy rules from other sheets
    other = parseString('x { y: z }')
    assert sheet.add('@import "i.css";') == 0
    assert sheet.add(other.cssRules[0]) == 2
    assert unwrap(sheet.cssRules[2]) is not unwrap(other.cssRules[0])
    sheet.cssRules.append('w { v: u }')
    del sheet.cssRules[0]
    sheet.cssRules.remove(sheet.cssRules[0])
    assert [r.cssText for r in sheet.cssRules] == ['x {\n  y: z;\n}', 'w {\n  v: u;\n}']
    assert sheet.cssRules.item(5) is None
    assert sheet.cssRules[0:1][0].cssText.startswith('x')
    # URLs
    sheet = parseString('@import "a.css"; b { background: url(c.png) }')
    assert list(kgcss.compat.getUrls(sheet)) == ['a.css', 'c.png']
    kgcss.compat.replaceUrls(sheet, str.upper, ignoreImportRules=True)
    assert list(kgcss.compat.getUrls(sheet)) == ['a.css', 'C.PNG']
    # Namespaces, media, comments, rules from text
    sheet = parseString('@media print { a {} } /* c */')
    media = sheet.cssRules[0]
    media.media.appendMedium('screen')  # type: ignore[attr-defined]
    assert media.media.mediaText == 'print, screen'  # type: ignore[attr-defined]
    media.media.deleteMedium('print')  # type: ignore[attr-defined]
    assert (media.media.length, media.media.item(0)) == (1, 'screen')  # type: ignore[attr-defined]
    assert sheet.cssRules[1].type == CSSRule.COMMENT
    sheet.namespaces['h'] = 'http://www.w3.org/1999/xhtml'
    assert b'@namespace h' in sheet.cssText
    assert kgcss.compat.parseRule('@page { margin: 0 }').type == CSSRule.PAGE_RULE


def test_property_api() -> None:
    block = parseStyle('widows: 2 !IMPORTANT')
    prop = block.getProperty('widows')
    assert prop is not None
    assert (prop.priority, prop.literalpriority, prop.cssText) == ('important', '!IMPORTANT', 'widows: 2 !important')
    prop.name = '-calibre-removed-widows'
    assert block.keys() == ['-calibre-removed-widows']
    new = kgcss.compat.Property('color', 'red')
    block.setProperty(new)
    block['margin'] = '0'
    del block['-calibre-removed-widows']
    assert block.getCssText(separator=' ') == 'color: red; margin: 0'
    assert block.getCssText() == 'color: red;\nmargin: 0;'
    with pytest.raises(ValueError, match='value'):
        block.setProperty('top')
    block.cssText = 'a: b'
    assert len(block) == 1


@pytest.mark.parametrize(
    ('name', 'fn'),
    [
        ('parseString', lambda: kgcss.compat.parseString('a{}')),
        ('parseStyle', lambda: kgcss.compat.parseStyle('a: b')),
    ],
)
def test_entry_points(name: str, fn: Callable[[], object]) -> None:
    assert fn() is not None
    assert name in kgcss.compat.__all__
