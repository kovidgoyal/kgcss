# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""CSSOM milestone 5: modifying the object model."""

import copy

import pytest

from kgcss import (
    CharsetRule,
    Comment,
    CSSSyntaxError,
    ImportRule,
    LayerStatementRule,
    MarginRule,
    MediaRule,
    NamespaceRule,
    NestedDeclarationsRule,
    PageRule,
    Property,
    Rule,
    RuleType,
    Selector,
    SelectorSyntaxError,
    SerializeOptions,
    StyleRule,
    Stylesheet,
    UnknownAtRule,
    parse_rule,
    parse_style,
    parse_stylesheet,
    serialize,
)

PRESERVE = SerializeOptions(style='preserve')


def types(rules: object) -> list[str]:
    assert hasattr(rules, '__iter__')
    return [r.type.name for r in rules]


def sheet_of(rule: Rule) -> Stylesheet | None:
    # Function calls, so that mypy does not keep a narrowed type across mutations
    return rule.parent_style_sheet


def parent_of(rule: Rule) -> Rule | None:
    return rule.parent_rule


def style(sheet: Stylesheet, n: int = 0) -> StyleRule:
    rule = sheet.rules.of_type(RuleType.STYLE)[n]
    assert isinstance(rule, StyleRule)
    return rule


def test_rule_list_operations() -> None:
    sheet = parse_stylesheet('a {} b {} c {}')
    rules = sheet.rules
    a, b, c = rules
    del rules[1]
    assert b.parent_style_sheet is None
    assert types(rules) == ['STYLE', 'STYLE']
    assert sheet._dirty
    rules.insert(0, b)
    assert sheet_of(b) is sheet
    assert rules.index(b) == 0
    rules.sort(key=lambda r: getattr(r, 'selector_text', ''))
    assert [r.selector_text for r in rules if isinstance(r, StyleRule)] == ['a', 'b', 'c']
    assert rules.pop() is c
    assert sheet_of(c) is None
    rules.remove(a)
    assert len(rules) == 1
    rules[0] = c
    assert sheet_of(b) is None
    assert sheet_of(c) is sheet
    rules[:] = [a, b]
    assert sheet_of(c) is None
    assert all(r.parent_style_sheet is sheet for r in (a, b))
    # A rule that belongs to another sheet cannot be added with list operations
    other = parse_stylesheet('d {}').rules[0]
    with pytest.raises(ValueError, match='belongs to another'):
        rules.append(other)
    with pytest.raises(ValueError, match='not in the list'):
        rules.index(other)


def test_nested_rule_lists() -> None:
    sheet = parse_stylesheet('@media x { a { z: 1 } } b { c { z: 2 } }')
    media, b = sheet.rules
    assert isinstance(media, MediaRule)
    assert isinstance(b, StyleRule)
    moved = media.rules.pop()
    assert moved.parent_rule is None
    assert moved.parent_style_sheet is None
    b.rules.append(moved)
    assert parent_of(moved) is b
    assert sheet_of(moved) is sheet
    # The emptied @media rule is dropped
    assert sheet.css_text == 'b {\n  c {\n    z: 2;\n  }\n  a {\n    z: 1;\n  }\n}'


def test_insert_rule() -> None:
    sheet = parse_stylesheet('@charset "x"; @import "a"; @namespace "n"; p {}')
    q = sheet.insert_rule('q { r: s }')
    assert isinstance(q, StyleRule)
    assert q.parent_style_sheet is sheet
    assert sheet.rules[-1] is q
    imp = sheet.insert_rule('@import "b"', 2)
    assert isinstance(imp, ImportRule)
    assert types(sheet) == ['CHARSET', 'IMPORT', 'IMPORT', 'NAMESPACE', 'STYLE', 'STYLE']
    for text, index, message in [
        ('@import "c"', 5, 'must come before'),
        ('@namespace "m"', 5, 'must come before'),
        ('@charset "y"', 0, 'only be one'),
        ('a {}', 0, 'cannot come before'),
        ('@import "c"', 0, 'cannot come before'),
    ]:
        with pytest.raises(ValueError, match=message):
            sheet.insert_rule(text, index)
    with pytest.raises(IndexError):
        sheet.insert_rule('a {}', 99)
    with pytest.raises(CSSSyntaxError):
        sheet.insert_rule('not a rule')
    with pytest.raises(CSSSyntaxError):
        sheet.insert_rule('a {} b {}')
    # Comments and @layer statements can go anywhere after @charset
    sheet.insert_rule('/* c */', 1)
    sheet.insert_rule('@layer x;', 1)
    with pytest.raises(ValueError, match='before @charset'):
        sheet.insert_rule('@layer y;', 0)
    assert types(sheet)[:3] == ['CHARSET', 'LAYER_STATEMENT', 'COMMENT']


def test_insert_rule_copies_rules_from_elsewhere() -> None:
    # requirements 3.1: Kobo inserts the same rules into many sheets
    extra = parse_stylesheet('a { b: c }')
    rule = extra.rules[0]
    one, two = parse_stylesheet(''), parse_stylesheet('')
    r1, r2 = one.insert_rule(rule), two.insert_rule(rule)
    assert r1 is not rule
    assert r2 is not r1
    assert rule.parent_style_sheet is extra
    assert one.css_text == two.css_text == extra.css_text
    # A free rule is inserted as is
    free = parse_rule('d { e: f }')
    assert one.insert_rule(free) is free


def test_insert_rule_contexts() -> None:
    sheet = parse_stylesheet('@media x {} a {} @page {}')
    media, a, page = sheet.rules
    assert isinstance(media, MediaRule)
    assert isinstance(a, StyleRule)
    assert isinstance(page, PageRule)
    media.insert_rule('b { c: d }')
    with pytest.raises(ValueError, match='only allowed at the top level'):
        media.insert_rule('@import "x"')
    nested = a.insert_rule('&:hover { e: f }')
    assert nested.parent_rule is a
    a.insert_rule('@media print { g: h }')
    margin = page.insert_rule('@top-left { content: "x" }')
    assert isinstance(margin, MarginRule)
    with pytest.raises(CSSSyntaxError, match='Only margin rules'):
        page.insert_rule('p {}')
    with pytest.raises(ValueError, match='not allowed'):
        sheet.insert_rule(margin)
    assert sheet.css_text == (
        '@media x {\n  b {\n    c: d;\n  }\n}\na {\n  &:hover {\n    e: f;\n  }\n  @media print {\n    g: h;\n  }\n}\n'
        '@page {\n  @top-left {\n    content: "x";\n  }\n}'
    )
    media.delete_rule(0)
    a.delete_rule(nested)
    assert len(media.rules) == 0
    assert len(a.rules) == 1


def test_delete_rule() -> None:
    sheet = parse_stylesheet('a {} b {} c {}')
    b = sheet.rules[1]
    sheet.delete_rule(b)
    sheet.delete_rule(0)
    assert [r.selector_text for r in sheet.rules if isinstance(r, StyleRule)] == ['c']
    with pytest.raises(ValueError, match='not in the list'):
        sheet.delete_rule(b)


def test_add() -> None:
    # requirements 3.1: add() puts @charset, @import and @namespace in a valid position
    sheet = parse_stylesheet('/* c */ @import "a"; @namespace "n"; p {}')
    sheet.add('@import "b"')
    sheet.add('@namespace x "m"')
    sheet.add('q {}')
    sheet.add('@charset "utf-8"')
    assert types(sheet) == ['CHARSET', 'COMMENT', 'IMPORT', 'IMPORT', 'NAMESPACE', 'NAMESPACE', 'STYLE', 'STYLE']
    sheet.add('@charset "iso-8859-1"')
    charsets = sheet.rules.of_type(RuleType.CHARSET)
    assert len(charsets) == 1
    assert isinstance(charsets[0], CharsetRule)
    assert charsets[0].encoding == 'iso-8859-1'
    empty = Stylesheet()
    empty.add('@namespace "x"')
    empty.add('@import "y"')
    assert types(empty) == ['IMPORT', 'NAMESPACE']


def test_assigning_rule_css_text() -> None:
    sheet = parse_stylesheet('a { b: c } @media x { d { e: f } }')
    a, media = sheet.rules
    a.css_text = 'p, q { r: s }'
    assert sheet.rules[0] is a
    assert isinstance(a, StyleRule)
    assert a.selector_text == 'p, q'
    assert a.style['r'] == 's'
    assert a.parent_style_sheet is sheet
    media.css_text = '@media print { t { u: v } w {} }'
    assert isinstance(media, MediaRule)
    assert media.media.media_text == 'print'
    assert all(r.parent_rule is media and r.parent_style_sheet is sheet for r in media.rules)
    assert sheet.css_text == 'p, q {\n  r: s;\n}\n@media print {\n  t {\n    u: v;\n  }\n}'
    with pytest.raises(CSSSyntaxError, match='Expected the text of a StyleRule'):
        a.css_text = '@media x {}'
    with pytest.raises(CSSSyntaxError):
        a.css_text = 'garbage'
    # The assigned text is written as is in preserve mode. It is a different text from the
    # sheet's, so the original whitespace between the rules is not reused.
    assert serialize(sheet, PRESERVE) == 'p, q { r: s }\n@media print { t { u: v } w {} }'


def test_assigning_other_css_text() -> None:
    sheet = parse_stylesheet('/* a */ @page { @top-left { b: c } d { e: f; g {} h: i } }')
    comment = sheet.rules[0]
    comment.css_text = '/* z */'
    assert isinstance(comment, Comment)
    assert comment.text == ' z '
    page = sheet.rules[1]
    assert isinstance(page, PageRule)
    margin = page.rules[0]
    margin.css_text = '@bottom-right { j: k }'
    assert isinstance(margin, MarginRule)
    assert margin.name == 'bottom-right'
    prop = Property('a', 'b')
    prop.css_text = 'C: D !IMPORTANT;'
    assert (prop.name, prop.value, prop.priority) == ('c', 'D', 'important')
    for bad in ('a', 'a: b; c: d', 'a: f('):
        with pytest.raises((CSSSyntaxError, ValueError)):
            prop.css_text = bad
    nested = parse_stylesheet('a { b {} c: d }')
    rule = style(nested)
    decls = rule.rules[1]
    assert isinstance(decls, NestedDeclarationsRule)
    decls.css_text = 'e: f; g: h'
    # The empty nested rule b {} is dropped
    assert rule.css_text == 'a {\n  e: f;\n  g: h;\n}'


def test_assigning_sheet_css_text() -> None:
    sheet = parse_stylesheet('a {}', href='x.css')
    old = sheet.rules[0]
    sheet.css_text = '@import "y"; b { c: d }'
    assert old.parent_style_sheet is None
    assert types(sheet) == ['IMPORT', 'STYLE']
    assert all(r.parent_style_sheet is sheet for r in sheet.rules)
    assert sheet.href == 'x.css'
    assert serialize(sheet, PRESERVE) == '@import "y"; b { c: d }'


def test_attribute_changes_mark_rules_modified() -> None:
    css = '@import "a" print; @namespace x "u"; @layer l; @page :first { margin: 0 } @foo bar; @media s { a {} }'
    sheet = parse_stylesheet(css)
    imp, ns, layer, page, foo, media = sheet.rules
    assert serialize(sheet, PRESERVE) == css
    assert isinstance(imp, ImportRule)
    imp.href = 'b.css'
    imp.media.media_text = 'screen'
    assert isinstance(ns, NamespaceRule)
    ns.prefix = 'y'
    assert isinstance(layer, LayerStatementRule)
    layer.names = ('l', 'm')
    assert isinstance(page, PageRule)
    page.selector_text = ':left'
    assert isinstance(foo, UnknownAtRule)
    foo.prelude_text = 'baz'
    assert isinstance(media, MediaRule)
    media.prelude_text = 'print'
    assert serialize(sheet, PRESERVE) == (
        '@import "b.css" screen; @namespace y url(u); @layer l, m; @page :left {\n  margin: 0;\n} @foo baz; '
        '@media print {\n  a {}\n}'
    )


def test_selectors() -> None:
    sheet = parse_stylesheet('a, b.c {} d {}')
    rule, other = style(sheet), style(sheet, 1)
    selectors = rule.selectors
    assert selectors is rule.selectors
    assert [s.text for s in selectors] == ['a', 'b.c']
    assert selectors.text == 'a, b.c'
    # Callers can attach attributes to selectors (calibre's selector.calibre_used)
    selectors[1].calibre_used = True  # type: ignore[attr-defined]
    del selectors[0]
    assert rule.selector_text == 'b.c'
    # Appending a selector from another rule appends a copy, with its attributes
    other.selectors.append(selectors[0])
    assert other.selector_text == 'd, b.c'
    copied = other.selectors[1]
    assert copied is not selectors[0]
    assert copied.calibre_used  # type: ignore[attr-defined]
    selectors.append(Selector('e > f'))
    selectors.sort(reverse=True)
    assert rule.selector_text == 'e > f, b.c'
    selectors[0].text = 'g'
    assert rule.selector_text == 'g, b.c'
    rule.selector_text = 'h,i'
    assert [s.text for s in selectors] == ['h', 'i']
    assert serialize(sheet, SerializeOptions(drop_empty_rules=False)) == 'h, i {\n}\nd, b.c {\n}'
    for bad in ('', 'a,', ',a', 'a {', 'a;b', 'a)', '[a', '"a'):
        with pytest.raises(CSSSyntaxError):
            rule.selector_text = bad
    with pytest.raises(CSSSyntaxError):
        Selector('a, b')
    assert selectors[0].specificity == (0, 0, 1)
    # Selectors are checked in full: these are balanced, but not selectors
    for bad in ('a b!', '[a!=b]', 'a::before b', '#1'):
        with pytest.raises(SelectorSyntaxError):
            rule.selector_text = bad
    assert rule.selector_text == 'h, i'
    assert repr(selectors) == "<SelectorList 'h, i'>"


def test_media_list() -> None:
    sheet = parse_stylesheet('@media screen, print { a { b: c } }')
    media = sheet.rules[0]
    assert isinstance(media, MediaRule)
    ml = media.media
    ml.append('(min-width: 10em)')
    del ml[0]
    ml[0] = 'tv'
    assert ml.media_text == 'tv, (min-width: 10em)'
    assert media.prelude_text == ml.media_text
    assert sheet.css_text.startswith('@media tv, (min-width: 10em) {')


def test_namespaces() -> None:
    # requirements P9: fb2_input sets namespaces['h'] and relies on it being written
    sheet = parse_stylesheet('@import "a"; @namespace "d"; p {}')
    ns = sheet.namespaces
    assert dict(ns) == {'': 'd'}
    ns['h'] = 'http://www.w3.org/1999/xhtml'
    assert types(sheet) == ['IMPORT', 'NAMESPACE', 'NAMESPACE', 'STYLE']
    assert ns['h'] == 'http://www.w3.org/1999/xhtml'
    ns[''] = 'e'
    assert sheet.namespaces[''] == 'e'
    assert len(ns) == 2
    assert list(ns) == ['', 'h']
    del ns['']
    assert dict(ns) == {'h': 'http://www.w3.org/1999/xhtml'}
    with pytest.raises(KeyError):
        del ns['nope']
    with pytest.raises(KeyError):
        ns['nope']
    assert '@namespace h url(http://www.w3.org/1999/xhtml);' in sheet.css_text


def test_copy() -> None:
    css = '@media x { a { b: c } } @page { @top-left { d: e } } f { h: i; g { k: l } } /* j */'
    sheet = parse_stylesheet(css, href='s.css')
    dup = sheet.copy()
    assert dup.css_text == sheet.css_text
    assert dup.href == 's.css'
    assert serialize(dup, PRESERVE) == css
    style(dup).style.set('h', 'z')
    assert style(sheet).style['h'] == 'i'
    assert copy.deepcopy(sheet).css_text == sheet.css_text
    # A modified sheet copies its current state
    style(sheet).style.set('h', 'y')
    assert style(sheet.copy()).style['h'] == 'y'
    for rule in sheet.iter_rules():
        c = rule.copy()
        assert type(c) is type(rule)
        assert c.parent_rule is None
        assert c.parent_style_sheet is None
        assert serialize(c) == serialize(rule)
    # Constructed rules, including ones that only exist in a context
    for rule in (MarginRule('top-center'), Comment(' x '), NestedDeclarationsRule(), StyleRule('a > b')):
        c = rule.copy()
        assert type(c) is type(rule)
        assert serialize(c, SerializeOptions(drop_empty_rules=False)) == serialize(
            rule, SerializeOptions(drop_empty_rules=False)
        )
    assert copy.deepcopy(parse_rule('a { b: c }')).css_text == 'a {\n  b: c;\n}'


def test_kepubify_page_rules_round_trip() -> None:
    # polish/kepubify.py turns @page rules into comments and back, byte for byte
    cookie = 'kobo-cookie'
    css = '@page {\n  margin: 13px;\n}\ndiv {\n  widows: 12;\n  color: red;\n}'
    sheet = parse_stylesheet(css)
    for i, rule in enumerate(list(sheet.rules)):
        if isinstance(rule, PageRule):
            comment = parse_rule(f'/* {cookie}: {rule.css_text} */')
            sheet.delete_rule(i)
            sheet.insert_rule(comment, i)
    kepub = sheet.css_text
    assert kepub == f'/* {cookie}: @page {{\n  margin: 13px;\n}} */\ndiv {{\n  widows: 12;\n  color: red;\n}}'
    back = parse_stylesheet(kepub)
    for i, rule in enumerate(list(back.rules)):
        if isinstance(rule, Comment) and rule.text.startswith(f' {cookie}: '):
            back.delete_rule(i)
            back.insert_rule(rule.text[len(cookie) + 3 : -1], i)
    assert back.css_text == css


def test_incomplete_sources_are_closed_in_preserve_mode() -> None:
    sheet = parse_stylesheet('a { b: c } d { e: f(')
    sheet.add('@import url(c.css) print')
    sheet.insert_rule('x { y: "z')
    assert serialize(sheet, PRESERVE) == '@import url(c.css) print;\na { b: c } d {\n  e: f();\n}\nx {\n  y: "z";\n}'


def test_free_rules() -> None:
    rule: Rule = parse_rule('a { b: c }')
    assert rule.parent_style_sheet is None
    assert parse_rule('@media x { y {} }').type is RuleType.MEDIA
    with pytest.raises(CSSSyntaxError):
        parse_rule('/* unclosed')
    with pytest.raises(CSSSyntaxError):
        parse_rule('')
    assert parse_style('a: b').css_text == 'a: b;'
