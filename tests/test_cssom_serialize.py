# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""CSSOM milestone 4: serialization."""

import pytest

from kgcss import (
    Comment,
    CSSSyntaxError,
    DeclarationBlock,
    ImportRule,
    MediaRule,
    Property,
    RuleType,
    SerializeOptions,
    StyleRule,
    Stylesheet,
    UnknownAtRule,
    parse_style,
    parse_stylesheet,
    serialize,
)

PRESERVE = SerializeOptions(style='preserve')


def pretty(css: str, options: SerializeOptions | None = None) -> str:
    return serialize(parse_stylesheet(css), options or SerializeOptions())


def decls(css: str) -> str:
    return parse_style(css).css_text


# Expectations pinned by calibre's own tests (requirements §16)


def test_kepubify_round_trip() -> None:
    # polish/tests/kepubify.py: the CSS written back must be byte identical
    css = '@page {\n  margin: 13px;\n}\ndiv {\n  widows: 12;\n  color: red;\n}'
    assert pretty(css) == css
    comment = '/* kobo: @page {\n  margin: 13px;\n} */\ndiv {\n  -kobo-widows: 12;\n  color: red;\n}'
    assert pretty(comment) == comment


def test_declaration_block_text() -> None:
    # css_transform_rules.py tests compare str(declaration_block).rstrip(';')
    block = parse_style('margin-bottom: 0; margin-left: 0;margin-right:0')
    assert block.css_text.rstrip(';') == 'margin-bottom: 0;\nmargin-left: 0;\nmargin-right: 0'
    assert decls('color: red').rstrip(';') == 'color: red'
    assert decls('font-family: "c c", d') == 'font-family: "c c", d;'
    assert decls('line-height: 3') == 'line-height: 3;'


def test_remove_property_value() -> None:
    # polish/tests/cascade.py test_remove_property_value
    block = parse_style('background-image: url(b.png); background: black url(a.png) fixed')
    block.remove('background-image')
    prop = block.get_property('background')
    assert prop is not None
    prop.remove_component(1)
    assert block.css_text.rstrip(';') == 'background: black fixed'


def test_selector_text() -> None:
    # polish/tests/structure.py: selectors as set by calibre code
    sheet = parse_stylesheet('#a {} @media x { p#b, [id="b"] {} } #c {}')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    rule.selector_text = 'p#id_x-2 , [id="id_x-2"]'
    assert rule.selector_text == 'p#id_x-2, [id="id_x-2"]'


@pytest.mark.parametrize(
    ('selector', 'expected'),
    [
        ('a , b', 'a, b'),
        ('a>b', 'a > b'),
        ('a   b', 'a b'),
        ('a+b~c', 'a + b ~ c'),
        ('a\n>\tb', 'a > b'),
        ('[ href = "x" i ]', '[href="x" i]'),
        ("[href='x']", '[href="x"]'),
        ('#\\31 23', '#\\31 23'),
        ('.\\31 23', '.\\31 23'),
        ('svg|svg', 'svg|svg'),
        ('A.Class', 'A.Class'),
        ('a /* c */ b', 'a /* c */ b'),
        (':not( .a , .b )', ':not(.a, .b)'),
        (':nth-child( 2n + 1 )', ':nth-child(2n + 1)'),
        (':nth-child(2n+1)', ':nth-child(2n+1)'),
        ('> a', '> a'),
        ('&:hover', '&:hover'),
    ],
)
def test_selector_normalization(selector: str, expected: str) -> None:
    # requirements 3.3: whitespace normalized, escapes (bug fix), case and comments kept
    assert StyleRule(selector).selector_text == expected


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        # requirements S3
        ('.5', '0.5'),
        ('1.0', '1'),
        ('0px', '0'),
        ('-0.0px', '0'),
        ('010', '10'),
        ('+1px', '+1px'),
        ('+.5em', '+0.5em'),
        ('1e3', '1000'),
        ('1.0E1px', '10px'),
        ('0%', '0%'),
        ('0s', '0s'),
        ('0deg', '0deg'),
        ('0PX', '0'),
        ('1.50em', '1.5em'),
        ('1e999', '1e999'),
        ('#ff0000', '#f00'),
        ('#FF0000', '#F00'),
        ('#ff0001', '#ff0001'),
        ('#aabbccdd', '#abcd'),
        ('RGB( 1 ,2,3 )', 'rgb(1, 2, 3)'),
        ('rgba(0,0,0,.5)', 'rgba(0, 0, 0, 0.5)'),
        ("'xxx'", '"xxx"'),
        ('"a\\"b"', '"a\\"b"'),
        ("'it\\'s'", '"it\'s"'),
        ('url( "a.png" )', 'url(a.png)'),
        ("url('a b.png')", 'url("a b.png")'),
        ('url(a\\ b.png)', 'url("a b.png")'),
        ('url("")', 'url("")'),
        ('calc(0px + 1.0em)', 'calc(0px + 1em)'),
        ('rgb(0 0 0 / 0%)', 'rgb(0 0 0 / 0%)'),
        ('a  ,  b', 'a, b'),
        ('italic 12px/1.2 "My Font",serif', 'italic 12px/1.2 "My Font", serif'),
        ('Red', 'Red'),
        ('\\31 x', '\\31 x'),
        ('a /* c */ b', 'a /* c */ b'),
        ('var(--x)', 'var(--x)'),
        ('My\\ Font', 'My\\ Font'),
    ],
)
def test_value_normalization(value: str, expected: str) -> None:
    assert Property('x', value).css_text == f'x: {expected}'


def test_zero_units_only_at_top_level() -> None:
    assert decls('margin: 0px 0em') == 'margin: 0 0;'
    assert decls('width: calc(100% - 0px)') == 'width: calc(100% - 0px);'
    assert decls('transform: translate(0px)') == 'transform: translate(0px);'


def test_declarations() -> None:
    assert decls('COLOR: red !IMPORTANT') == 'color: red !important;'
    assert decls('a: b; a: c') == 'a: b;\na: c;'
    assert decls('a: b; /* c */ d: e') == 'a: b;\n/* c */\nd: e;'
    assert decls('a:') == 'a:;'
    # Custom properties are written exactly
    assert decls('--X:  { a  b }  ') == '--X: { a  b };'
    assert decls('--x: 0px') == '--x: 0px;'
    assert decls('--x: a,b') == '--x: a,b;'
    assert Property('color', 'red').css_text == 'color: red'
    assert Property('color', 'red', 'important').css_text == 'color: red !important'


def test_single_line() -> None:
    # requirements 4.1: safe inside a double-quoted HTML attribute
    block = parse_style('a: b; /* c */ content: "x \\"y\\" \'z\'"; d: url("a b") !important; e: \\"f')
    text = block.to_css(single_line=True)
    assert text == "a: b; content: 'x \\22 y\\22  \\'z\\''; d: url('a b') !important; e: \\22 f"
    assert '"' not in text
    assert '\n' not in text
    # Values that cannot be written on one line are left out
    assert parse_style('a: "x\n; b: c').to_css(single_line=True) == 'b: c'
    assert parse_style('a: url(x"y)').to_css(single_line=True) == ''
    assert parse_style('').to_css(single_line=True) == ''


def test_rules() -> None:
    css = """@charset "utf-8"; @import "a.css" screen; @import url("b.css") layer(x) supports(display: grid);
    @namespace svg "http://x"; @namespace url(http://y); @layer a, b;
    @media screen { a { b: c } } @supports (x: y) { @media print { d { e: f } } }
    @font-face { font-family: X; src: url(x.otf) } @page :first { margin: 0; @top-left { content: "x" } }
    @-moz-document url-prefix() { g { h: i } } @keyframes k { from { a: b } } /* c */
    x { y: z; &:hover { w: v } u: t }"""
    expected = """@charset "utf-8";
@import "a.css" screen;
@import url(b.css) layer(x) supports(display: grid);
@namespace svg url(http://x);
@namespace url(http://y);
@layer a, b;
@media screen {
  a {
    b: c;
  }
}
@supports (x: y) {
  @media print {
    d {
      e: f;
    }
  }
}
@font-face {
  font-family: X;
  src: url(x.otf);
}
@page :first {
  margin: 0;
  @top-left {
    content: "x";
  }
}
@-moz-document url-prefix() {
  g {
    h: i;
  }
}
@keyframes k { from { a: b } }
/* c */
x {
  y: z;
  &:hover {
    w: v;
  }
  u: t;
}"""
    assert pretty(css) == expected
    assert pretty(expected) == expected


def test_options() -> None:
    css = 'a { b: c } d {} /* e */ @media x { f {} }'
    assert pretty(css, SerializeOptions(indent='\t')) == 'a {\n\tb: c;\n}\n/* e */'
    # Kobo relies on empty rules being dropped, which can be turned off
    assert pretty(css, SerializeOptions(drop_empty_rules=False)) == (
        'a {\n  b: c;\n}\nd {\n}\n/* e */\n@media x {\n  f {\n  }\n}'
    )
    assert pretty(css, SerializeOptions(keep_comments=False)) == 'a {\n  b: c;\n}'
    assert pretty('a { b: .5 }', SerializeOptions(normalize=False)) == 'a {\n  b: .5;\n}'
    # Kobo empties a rule to remove it from the output
    sheet = parse_stylesheet('@page { margin: 0 } a { b: c }')
    page = sheet.rules[0]
    page.style = ''  # type: ignore[attr-defined]
    assert sheet.css_text == 'a {\n  b: c;\n}'


def test_unknown_at_rules_are_closed() -> None:
    assert pretty('@foo bar') == '@foo bar;'
    assert pretty('@foo bar { "x') == '@foo bar { "x"}'
    assert pretty('@foo bar; a { b: c }') == '@foo bar;\na {\n  b: c;\n}'
    assert serialize(UnknownAtRule('x', 'y  z', ' a { b } ')) == '@x y  z { a { b } }'
    assert serialize(UnknownAtRule('x')) == '@x;'


def test_unclosed_values_are_closed() -> None:
    # What EOF left open is closed, so the text that follows cannot be swallowed
    assert pretty('a { b: f(x') == 'a {\n  b: f(x);\n}'
    assert pretty('a { b: "x') == 'a {\n  b: "x";\n}'
    assert pretty('a { b: url(x') == 'a {\n  b: url(x);\n}'
    # A comment after the value is not part of it
    assert pretty('a { b: x /* y') == 'a {\n  b: x;\n}'
    assert pretty('a { b: x /* y */ z') == 'a {\n  b: x /* y */ z;\n}'
    # A bad string is ended by a newline, which is kept
    assert pretty('a { b: "x\n; c: d }') == 'a {\n  b: "x\n;\n  c: d;\n}'


def test_constructed_objects() -> None:
    sheet = Stylesheet()
    assert sheet.css_text == ''
    assert serialize(StyleRule('a')) == ''
    assert serialize(StyleRule('a'), SerializeOptions(drop_empty_rules=False)) == 'a {\n}'
    assert serialize(MediaRule('print'), SerializeOptions(drop_empty_rules=False)) == '@media print {\n}'
    assert serialize(ImportRule('a b.css', 'print')) == '@import url("a b.css") print;'
    assert serialize(Comment(' x ')) == '/* x */'
    with pytest.raises(ValueError, match='cannot contain'):
        Comment('*/')
    rule = StyleRule('p')
    rule.style.set('color', 'red')
    assert rule.css_text == 'p {\n  color: red;\n}'
    assert DeclarationBlock().css_text == ''


def test_preserve_style() -> None:
    css = 'a{b:c}\n\n/* d */  e { f : g ; h:i }\n@media x{j{k:l}}'
    sheet = parse_stylesheet(css)
    assert serialize(sheet, PRESERVE) == css
    rule = sheet.rules[2]
    assert isinstance(rule, StyleRule)
    prop = rule.style.get_property('h')
    assert prop is not None
    prop.value = 'z'
    # Unchanged rules keep their text and the whitespace between them, unchanged declarations too
    assert serialize(sheet, PRESERVE) == 'a{b:c}\n\n/* d */  e {\n  f : g;\n  h: z;\n}\n@media x{j{k:l}}'


def test_comment_edits() -> None:
    sheet = parse_stylesheet('/* a */ b {}')
    comment = sheet.rules[0]
    assert isinstance(comment, Comment)
    comment.text = ' z '
    assert serialize(sheet, PRESERVE) == '/* z */ b {}'


def test_declaration_block_css_text_setter() -> None:
    sheet = parse_stylesheet('a { b: c }')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    rule.style.css_text = 'd: e; f: g'
    assert rule.css_text == 'a {\n  d: e;\n  f: g;\n}'
    assert all(p.parent is rule.style for p in rule.style)


def test_encode() -> None:
    # requirements S4: str by default, bytes in the @charset encoding on request
    sheet = parse_stylesheet('@charset "iso-8859-1"; a { content: "é—x" }')
    assert sheet.encode() == b'@charset "iso-8859-1";\na {\n  content: "\xe9\x97x";\n}'
    sheet = parse_stylesheet('@charset "iso-8859-2"; a { content: "é—" }')
    # Characters the encoding cannot represent become CSS escapes
    assert sheet.encode() == b'@charset "iso-8859-2";\na {\n  content: "\xe9\\2014 ";\n}'
    assert parse_stylesheet('a { b: "é" }').encode() == 'a {\n  b: "é";\n}'.encode()
    assert parse_stylesheet('@charset "bogus"; a{b:c}').encode().endswith(b'b: c;\n}')


def test_css_syntax_error_is_exported() -> None:
    assert issubclass(CSSSyntaxError, ValueError)


@pytest.mark.parametrize(
    'css',
    [
        # Inputs found by property tests, where serializing was not idempotent
        '\\31 x: y',  # names are stored unescaped and must be escaped again
        'a {a {}/* x */',  # comments do not make a rule non-empty, wherever they are
        '--x:url(url(',  # a bad url ended by EOF must be closed
        'a {a:f("a\r\n',  # a bad string's newline is not doubled
        'a:\\',  # a backslash at EOF is an escape for U+FFFD
        '--a:red /* c */ red',
        '--x:,\\\n',  # a lone backslash keeps its newline
        'a:\\ ',  # the escaped space is part of the token, not trimmed
        '\\0{',  # a final hex escape gets its terminating space
        'a {a:{b} red}',  # a nested rule that would read back as a declaration
        'a:a:\\ -.5em',  # rewriting a number must not merge it with its neighbours
        'a:url(\n,/* x */',
        'a\\0>a {}',  # a hex escape absorbs the space before a combinator
        'a:!IMPORTANT/* x */(',  # dropping a comment must not merge tokens
        'a:a/* x */1e3',  # a dropped comment in a value acts as whitespace
        'k { &:hover { l: m } /* c */ }',
        'a:b\\0\n(',  # a newline ending a hex escape becomes a space on a single line
    ],
)
def test_found_by_property_tests(css: str) -> None:
    for options in (SerializeOptions(), SerializeOptions(normalize=False), SerializeOptions(drop_empty_rules=False)):
        once = serialize(parse_stylesheet(css), options)
        assert serialize(parse_stylesheet(once), options) == once
        text = parse_style(css).to_css(options)
        assert parse_style(text).to_css(options) == text
    single = parse_style(css).to_css(single_line=True)
    assert '"' not in single
    assert '\n' not in single
    assert parse_style(single).to_css(single_line=True) == single


def test_dropped_comments_in_selectors() -> None:
    from kgcss.cssom.serializer import normalize_selector

    # Comments in selectors are part of the selector text (requirements 3.3), keep_comments=False
    # applies to comment rules and to comments in declarations
    no_comments = SerializeOptions(keep_comments=False)
    sheet = parse_stylesheet('/* x */ a/**/.b {c: d /* y */}')
    assert serialize(sheet, no_comments) == 'a/**/.b {\n  c: d;\n}'
    # Dropping comments from a selector never merges tokens and never adds a combinator
    assert normalize_selector('a/**/.b', no_comments) == 'a.b'
    assert normalize_selector('e/**/f', no_comments) == 'e/**/f'
    assert normalize_selector('i /**/ j', no_comments) == 'i j'


def test_preserve_at_rule_left_open_by_eof() -> None:
    # The ; of @url(...; is inside a bracket that the end of the input left open. Written verbatim
    # before other rules, it would swallow them, as found when inlining an imported sheet.
    sheet = parse_stylesheet('@url(@charset "x";')
    rule = sheet.rules[0]
    sheet.add('a { b: c }')
    text = serialize(sheet, SerializeOptions(style='preserve'))
    assert [r.type for r in parse_stylesheet(text).rules] == [rule.type, RuleType.STYLE]


def test_preserve_declaration_with_comment_left_open_by_eof() -> None:
    # Found by the property tests: the comment after !important, left open by the end of the
    # input, would swallow what follows when written verbatim
    sheet = parse_stylesheet('a:a {a: !important/*Z')
    sheet.add('a { b: c }')
    text = serialize(sheet, SerializeOptions(style='preserve'))
    assert serialize(parse_stylesheet(text)) == serialize(sheet)


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('1e1e3', '1e1e3'), ('1.0e1E+2', '1.0e1E+2'), ('0.0e1e-5', '0.0e1e-5'), ('2e1em', '20em'), ('1e1e', '10e'),
     ('1e1e-x', '10e-x'), ('1e1e\\33', '10e\\33 '), ('1e1e\u00b2', '10e\u00b2')],
)  # fmt: skip
def test_units_like_exponents(value: str, expected: str) -> None:
    # Found by the property tests: 1e1e3 is the number 1e1 with the unit e3, rewriting the number as
    # 10 gave 10e3, which is a number
    text = parse_stylesheet(f'a {{ b: {value} }}').css_text
    assert text == f'a {{\n  b: {expected};\n}}'
    assert parse_stylesheet(text).css_text == text
