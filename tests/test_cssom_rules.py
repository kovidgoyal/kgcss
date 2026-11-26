# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The object model: building the rule tree."""

import codecs
import logging
from collections.abc import Callable, Iterator
from typing import assert_type, get_args, get_overloads, get_type_hints

import pytest

import kgcss
from kgcss import (
    CharsetRule,
    Comment,
    ContainerRule,
    DocumentRule,
    FontFaceRule,
    GroupingRule,
    ImportRule,
    LayerBlockRule,
    LayerStatementRule,
    MarginRule,
    MediaList,
    MediaRule,
    NamespaceRule,
    NestedDeclarationsRule,
    PageRule,
    ParseError,
    Rule,
    RuleList,
    RuleType,
    ScopeRule,
    SerializeOptions,
    StartingStyleRule,
    StyleRule,
    Stylesheet,
    SupportsRule,
    UnknownAtRule,
    parse_stylesheet,
    serialize,
)

PRESERVE = SerializeOptions(style='preserve')
# A sheet with a rule of every type
ALL_RULE_TYPES = """@charset "utf-8"; @layer x, y; @import "a.css"; @namespace svg "u";
/* comment */ a { b { c: d } e: f } @media print { g {} } @font-face { font-family: x }
@page { @top-left { content: "x" } } @supports (display: grid) { h {} } @container (width > 1px) { i {} }
@layer z { j {} } @scope (k) { l {} } @starting-style { m {} } @-moz-document url-prefix() { n {} }
@keyframes o { from { color: red } }"""


def types(rules: RuleList | Stylesheet) -> list[str]:
    return [r.type.name for r in rules]


def kinds(sheet: Stylesheet) -> list[str]:
    return [e.kind for e in sheet.errors]


def test_top_level_rules() -> None:
    css = (
        '@charset "utf-8";\n/* c */\n@import url(a.css);\n@namespace svg url(http://www.w3.org/2000/svg);\n'
        'a > b { color: red }\n@media screen { p { x: y } }\n@font-face { font-family: X }\n'
        '@page :first { margin: 0 }\n@layer a, b.c;\n@keyframes k { from { a: b } }\n'
    )
    sheet = parse_stylesheet(css)
    assert types(sheet) == [
        'CHARSET', 'COMMENT', 'IMPORT', 'NAMESPACE', 'STYLE',
        'MEDIA', 'FONT_FACE', 'PAGE', 'LAYER_STATEMENT', 'UNKNOWN',
    ]  # fmt: skip
    assert sheet.errors == []
    assert len(sheet) == 10
    assert list(sheet) == list(sheet.rules)
    charset, comment, imp, ns, style, media, font_face, page, layer, unknown = sheet.rules
    assert isinstance(charset, CharsetRule)
    assert charset.encoding == 'utf-8'
    assert isinstance(comment, Comment)
    assert comment.text == ' c '
    assert isinstance(imp, ImportRule)
    assert imp.href == 'a.css'
    assert isinstance(ns, NamespaceRule)
    assert (ns.prefix, ns.namespace_uri) == ('svg', 'http://www.w3.org/2000/svg')
    assert isinstance(style, StyleRule)
    assert style.selector_text == 'a > b'
    assert isinstance(media, MediaRule)
    assert media.media.media_text == 'screen'
    assert isinstance(font_face, FontFaceRule)
    assert isinstance(page, PageRule)
    assert page.selector_text == ':first'
    assert isinstance(layer, LayerStatementRule)
    assert layer.names == ('a', 'b.c')
    assert isinstance(unknown, UnknownAtRule)
    for rule in sheet.rules:
        assert rule.parent_style_sheet is sheet
        assert rule.parent_rule is None
    assert (style.line, style.column) == (5, 1)
    assert repr(style) == "<StyleRule 'a > b' at 5:1>"
    assert repr(sheet) == '<Stylesheet with 10 rules>'


def test_rule_type_lookup_by_name() -> None:
    # calibre's cascade.iterrules looks types up by string name
    assert RuleType['STYLE'] is RuleType.STYLE
    assert RuleType['FONT_FACE'] is RuleType.FONT_FACE
    assert StyleRule.type is RuleType.STYLE


@pytest.mark.parametrize(
    ('css', 'expected_types', 'expected_errors'),
    [
        ('/* x */ @charset "a"; a{}', ['COMMENT', 'CHARSET', 'STYLE'], []),
        ('a{} @charset "a";', ['STYLE'], ['misplaced-rule']),
        ('@import "a"; @import "b"; a{}', ['IMPORT', 'IMPORT', 'STYLE'], []),
        ('@layer x; @import "a";', ['LAYER_STATEMENT', 'IMPORT'], []),
        ('a{} @import "a";', ['STYLE'], ['misplaced-rule']),
        ('@namespace x "u"; @import "a";', ['NAMESPACE'], ['misplaced-rule']),
        ('@import "a"; @namespace x "u"; @namespace "v";', ['IMPORT', 'NAMESPACE', 'NAMESPACE'], []),
        ('a{} @namespace "u";', ['STYLE'], ['misplaced-rule']),
        ('@media x { @import "a"; @charset "b"; @namespace "c"; }', ['MEDIA'], ['invalid-rule'] * 3),
        ('@charset "a" "b"; @charset x; @charset "a" {}', [], ['invalid-rule'] * 3),
    ],
)
def test_placement_recovery(css: str, expected_types: list[str], expected_errors: list[str]) -> None:
    # requirements P5: misplaced @charset, @import and @namespace are dropped and logged
    sheet = parse_stylesheet(css)
    assert types(sheet) == expected_types
    assert kinds(sheet) == expected_errors


@pytest.mark.parametrize(
    ('css', 'href', 'media', 'layer', 'supports'),
    [
        ('@import url(a.css);', 'a.css', '', None, None),
        ('@import "a.css" screen, print;', 'a.css', 'screen, print', None, None),
        ('@import url("a b.css") print and (color);', 'a b.css', 'print and (color)', None, None),
        ('@import url(a.css) layer;', 'a.css', '', '', None),
        ('@import url(a.css) layer(x.y) supports(display: grid) screen;', 'a.css', 'screen', 'x.y', 'display: grid'),
        ('@IMPORT url(a.css);', 'a.css', '', None, None),
    ],
)
def test_import_rule(css: str, href: str, media: str, layer: str | None, supports: str | None) -> None:
    (rule,) = parse_stylesheet(css)
    assert isinstance(rule, ImportRule)
    assert (rule.href, rule.media.media_text, rule.layer, rule.supports) == (href, media, layer, supports)


def test_invalid_import() -> None:
    sheet = parse_stylesheet('@import; @import foo; @import "a" {}')
    assert types(sheet) == []
    assert kinds(sheet) == ['invalid-rule'] * 3


def test_namespaces() -> None:
    sheet = parse_stylesheet('@namespace url(http://x); @namespace svg "http://svg"; @namespace a b c; @namespace;')
    assert sheet.namespaces == {'': 'http://x', 'svg': 'http://svg'}
    assert kinds(sheet) == ['invalid-rule', 'invalid-rule']
    default = sheet.rules[0]
    assert isinstance(default, NamespaceRule)
    assert default.prefix is None


@pytest.mark.parametrize(
    ('name', 'cls'),
    [
        ('media', MediaRule),
        ('supports', SupportsRule),
        ('container', ContainerRule),
        ('layer', LayerBlockRule),
        ('scope', ScopeRule),
        ('starting-style', StartingStyleRule),
        ('document', DocumentRule),
        ('-moz-document', DocumentRule),
        ('MEDIA', MediaRule),
    ],
)
def test_grouping_rules(name: str, cls: type[GroupingRule]) -> None:
    sheet = parse_stylesheet(f'@{name} some prelude {{ a {{ b: c }} /* d */ @media x {{ e {{}} }} }}')
    (rule,) = sheet
    assert isinstance(rule, cls)
    assert rule.prelude_text == 'some prelude'
    assert types(rule.rules) == ['STYLE', 'COMMENT', 'MEDIA']
    for child in rule.rules:
        assert child.parent_rule is rule
        assert child.parent_style_sheet is sheet
    inner = rule.rules[2]
    assert isinstance(inner, MediaRule)
    assert types(inner.rules) == ['STYLE']
    assert inner.rules[0].parent_rule is inner


def test_grouping_rule_errors() -> None:
    sheet = parse_stylesheet('@media screen; @supports (x: y); @media print { color: red; a {} }')
    assert types(sheet) == ['MEDIA']
    # Declarations are not valid directly in a grouping rule that is not inside a style rule
    assert kinds(sheet) == ['invalid-rule', 'invalid-rule', 'invalid-declaration']
    assert sheet.errors[2].message == "Declaration of 'color' is not allowed here"


def test_specific_grouping_attributes() -> None:
    media, supports, layer = parse_stylesheet('@media a, b and (x: f(1, 2)) {} @supports (x: y) {} @layer n {}')
    assert isinstance(media, MediaRule)
    assert list(media.media) == ['a', 'b and (x: f(1, 2))']
    assert len(media.media) == 2
    assert media.media[1] == 'b and (x: f(1, 2))'
    assert bool(media.media)
    assert isinstance(supports, SupportsRule)
    assert supports.condition_text == '(x: y)'
    assert isinstance(layer, LayerBlockRule)
    assert layer.name == 'n'


def test_media_list() -> None:
    assert list(MediaList('')) == []
    assert not MediaList('  ')
    # Invalid media text is kept exactly as written (requirements M2)
    ml = MediaList('(min-width:10px) and screen')
    assert ml.media_text == '(min-width:10px) and screen'
    assert list(ml) == ['(min-width:10px) and screen']
    assert repr(ml) == "<MediaList '(min-width:10px) and screen'>"


def test_style_rule_nesting() -> None:
    css = 'a { b: c; /* d */ &:hover { e: f } g: h; /* i */ @media print { j: k; l {} } }'
    sheet = parse_stylesheet(css)
    (rule,) = sheet
    assert isinstance(rule, StyleRule)
    assert types(rule.rules) == ['STYLE', 'NESTED_DECLARATIONS', 'COMMENT', 'MEDIA']
    hover, nested_decls, comment, media = rule.rules
    assert isinstance(hover, StyleRule)
    assert hover.selector_text == '&:hover'
    assert isinstance(nested_decls, NestedDeclarationsRule)
    assert serialize(nested_decls, PRESERVE) == 'g: h;'
    assert isinstance(comment, Comment)
    assert comment.text == ' i '
    assert isinstance(media, MediaRule)
    # Inside a style rule, declarations in grouping rules are valid nested declarations
    assert types(media.rules) == ['NESTED_DECLARATIONS', 'STYLE']
    for child in sheet.iter_rules():
        assert child.parent_style_sheet is sheet
    assert hover.parent_rule is rule
    assert media.rules[1].parent_rule is media
    assert sheet.errors == []


def test_lazy_blocks() -> None:
    rule = parse_stylesheet('a { b {} }').rules[0]
    assert isinstance(rule, StyleRule)
    assert rule._rules is None
    rules = rule.rules
    assert rules is rule.rules
    assert types(rules) == ['STYLE']
    # Constructed rules have no block
    assert len(StyleRule('x').rules) == 0


def test_lazy_errors_are_reported_late() -> None:
    errors: list[ParseError] = []
    sheet = parse_stylesheet('a { b: c; d e; }', log=errors.append)
    assert errors == []
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    _ = rule.rules
    assert [e.kind for e in errors] == ['invalid-rule']
    assert sheet.errors == errors
    assert (errors[0].line, errors[0].column) == (1, 11)


def test_page_rules() -> None:
    sheet = parse_stylesheet('@page :left { margin: 1in; @top-left { content: "x" } @bottom-center {} @media x {} }')
    (page,) = sheet
    assert isinstance(page, PageRule)
    assert page.selector_text == ':left'
    assert types(page.rules) == ['MARGIN', 'MARGIN']
    top, bottom = page.rules
    assert isinstance(top, MarginRule)
    assert top.name == 'top-left'
    assert isinstance(bottom, MarginRule)
    assert bottom.name == 'bottom-center'
    assert kinds(sheet) == ['invalid-rule']
    assert parse_stylesheet('@page {}').rules[0].type is RuleType.PAGE
    assert kinds(parse_stylesheet('@page; @font-face;')) == ['invalid-rule', 'invalid-rule']


def test_font_face_cannot_contain_rules() -> None:
    sheet = parse_stylesheet('@font-face { a {} src: url(x) }')
    rule = sheet.rules[0]
    assert isinstance(rule, FontFaceRule)
    assert rule.child_rules() is None
    rule._materialize()
    assert kinds(sheet) == ['invalid-rule']


def test_unknown_at_rules() -> None:
    css = '@Keyframes  k  { from { a: url(b.png) } } @-webkit-foo bar; @x'
    sheet = parse_stylesheet(css)
    keyframes, foo, x = sheet
    assert isinstance(keyframes, UnknownAtRule)
    assert (keyframes.name, keyframes.at_keyword, keyframes.prelude_text) == ('Keyframes', '@Keyframes', 'k')
    assert keyframes.block_text == ' from { a: url(b.png) } '
    assert isinstance(foo, UnknownAtRule)
    assert (foo.name, foo.prelude_text, foo.block_text) == ('-webkit-foo', 'bar', None)
    assert isinstance(x, UnknownAtRule)
    assert x.name == 'x'
    # Unknown at-rules are kept exactly (requirements P6)
    assert serialize(keyframes, PRESERVE) == '@Keyframes  k  { from { a: url(b.png) } }'


def test_iter_rules() -> None:
    sheet = parse_stylesheet('a { b {} } @media x { c {} @supports y { d {} } } /* e */ f {}')
    selectors = [r.selector_text for r in sheet.iter_rules(RuleType.STYLE) if isinstance(r, StyleRule)]
    assert selectors == ['a', 'b', 'c', 'd', 'f']
    assert types(RuleList(sheet.iter_rules(recursive=False))) == ['STYLE', 'MEDIA', 'COMMENT', 'STYLE']
    assert types(RuleList(sheet.iter_rules(RuleType.MEDIA, RuleType.SUPPORTS))) == ['MEDIA', 'SUPPORTS']
    assert sheet.rules.of_type(RuleType.STYLE) == [sheet.rules[0], sheet.rules[3]]
    # assert_type is checked by ty: one type gives rules of its class
    assert_type(sheet.rules.of_type(RuleType.STYLE), list[StyleRule])
    assert_type(sheet.rules.of_type(RuleType.MEDIA, RuleType.STYLE), list[Rule])
    assert_type(sheet.iter_rules(RuleType.MEDIA), Iterator[MediaRule])
    assert_type(sheet.iter_rules(RuleType.IMPORT, follow_imports=True), Iterator[ImportRule])
    assert_type(sheet.iter_rules(), Iterator[Rule])
    assert [r.selector_text for r in sheet.iter_rules(RuleType.STYLE)] == ['a', 'b', 'c', 'd', 'f']


@pytest.mark.parametrize('method', [RuleList.of_type, Stylesheet.iter_rules])
def test_rule_type_overloads(method: Callable[..., object]) -> None:
    # Every rule type has an overload, which names the class of its rules
    classes: dict[RuleType, type] = {}
    for overload in get_overloads(method):
        hints = get_type_hints(overload)
        if 'rule_type' in hints:
            (rule_type,) = get_args(hints['rule_type'])
            (cls,) = get_args(hints['return'])
            classes[rule_type] = cls
    assert set(classes) == set(RuleType)
    sheet = parse_stylesheet(ALL_RULE_TYPES)
    assert {r.type for r in sheet.iter_rules()} == set(RuleType)
    for rule in sheet.iter_rules():
        assert type(rule) is classes[rule.type]


def test_browser_index_path() -> None:
    # requirements P13: indices as in the browser CSSOM, where comments and @charset do not exist
    sheet = parse_stylesheet(
        '@charset "a"; /* x */ @namespace "u"; a {} @media y { /* z */ b {} @supports q { c {} } }'
    )
    paths = {
        r.selector_text: sheet.browser_index_path(r)
        for r in sheet.iter_rules(RuleType.STYLE)
        if isinstance(r, StyleRule)
    }
    assert paths == {'a': (1,), 'b': (2, 0), 'c': (2, 1, 0)}
    with pytest.raises(ValueError, match='does not exist in the browser CSSOM'):
        sheet.browser_index_path(sheet.rules[0])
    with pytest.raises(ValueError, match='not part of this stylesheet'):
        sheet.browser_index_path(parse_stylesheet('a {}').rules[0])


@pytest.mark.parametrize(
    ('css', 'expected'),
    [
        # Style rules whose selector list does not parse, and at-rules browsers do not know, are dropped
        ('*html p {} @-foo-bar {x} .target {}', {'.target': (0,)}),
        ('p[a!=b] {} @media x { @-moz-document url(y) { c {} } @foo; .target {} }', {'.target': (0, 0)}),
        # At-rules that kgcss does not model but browsers do keep their slot
        (
            '@keyframes k { from {} } @-webkit-keyframes k {} @property --x {} @counter-style c {} .target {}',
            {'.target': (4,)},
        ),
        ('@font-feature-values F {} @font-palette-values --p {} @view-transition {} .target {}', {'.target': (3,)}),
        # Unless they are nested in a style rule, where browsers drop them; relative selectors are valid there
        ('a { @keyframes k {} > .target {} } b {}', {'a': (0,), '> .target': (0, 0), 'b': (1,)}),
        # Selectors are only checked for syntax
        ('p:unknown-pseudo {} .target {}', {'p:unknown-pseudo': (0,), '.target': (1,)}),
    ],
)
def test_browser_index_path_skips_dropped_rules(css: str, expected: dict[str, tuple[int, ...]]) -> None:
    sheet = parse_stylesheet(css)
    paths: dict[str, tuple[int, ...]] = {}
    for rule in sheet.iter_rules():
        if isinstance(rule, StyleRule) and rule.selector_text in ('a', 'b', '> .target', '.target', 'p:unknown-pseudo'):
            paths[rule.selector_text] = sheet.browser_index_path(rule)
            assert sheet.rule_at_browser_index_path(paths[rule.selector_text]) is rule
    assert paths == expected


def test_browser_index_path_of_dropped_rules() -> None:
    sheet = parse_stylesheet('*html p {} @-foo {} @document url(x) { a {} } b { @keyframes k {} }')
    star, unknown, document, b = sheet.rules
    assert isinstance(document, kgcss.DocumentRule)
    assert isinstance(b, StyleRule)
    for rule in (star, unknown, document, document.rules[0], b.rules[0]):
        with pytest.raises(ValueError, match='does not exist in the browser CSSOM'):
            sheet.browser_index_path(rule)
    assert sheet.rule_at_browser_index_path((0,)) is b
    with pytest.raises(IndexError):
        sheet.rule_at_browser_index_path((0, 0))


def test_rule_list() -> None:
    sheet = parse_stylesheet('a {} b {} c {}')
    rules = sheet.rules
    assert rules.index(rules[1]) == 1
    assert rules[1:] == [rules[1], rules[2]]
    assert rules[-1] is rules[2]
    with pytest.raises(ValueError, match='not in the list'):
        rules.index(StyleRule('a'))
    assert repr(rules) == '<RuleList of 3 rules>'


def test_errors_and_logging(caplog: pytest.LogCaptureFixture) -> None:
    css = 'a {} @import "x"; "unclosed\n'
    sheet = parse_stylesheet(css)
    assert [(e.kind, e.line, e.column) for e in sheet.errors] == [
        ('misplaced-rule', 1, 6),
        ('invalid-rule', 1, 19),
        ('newline-in-string', 1, 28),
    ]
    offsets = [e.offset for e in sheet.errors]
    assert offsets == sorted(offsets)
    received: list[ParseError] = []
    parse_stylesheet(css, log=received.append)
    assert [e.kind for e in received] == kinds(sheet)
    logger = logging.getLogger('kgcss-test')
    with caplog.at_level(logging.WARNING, logger='kgcss-test'):
        parse_stylesheet(css, href='book/style.css', log=logger)
    assert caplog.messages[0] == 'book/style.css:1:6: @import rule ignored, it is not allowed after other rules'
    assert len(caplog.messages) == len(sheet.errors)


def test_preserve_serialization() -> None:
    css = '@charset "x";\n/* c */ a{b:c}  @media   x{ y {} } junk; @import "late";\n'
    sheet = parse_stylesheet(css)
    # An unmodified sheet is written back exactly, even the parts that were dropped
    assert serialize(sheet, PRESERVE) == css
    assert [serialize(r, PRESERVE) for r in sheet] == ['@charset "x";', '/* c */', 'a{b:c}', '@media   x{ y {} }']
    # The pretty format drops the empty @media rule
    assert serialize(sheet) == '@charset "x";\n/* c */\na {\n  b: c;\n}'


def test_bytes_input() -> None:
    sheet = parse_stylesheet(codecs.BOM_UTF8 + b'@charset "utf-8"; a { b: "\xc3\xa9" }')
    assert sheet.encoding == 'utf-8'
    assert sheet.rules[1].column == 19
    assert parse_stylesheet(b'@charset "latin1"; a { b: "\xe9" }').encoding == 'windows-1252'
    assert parse_stylesheet('a{}').encoding is None


def test_constructed_objects() -> None:
    sheet = Stylesheet(href='x.css')
    assert (len(sheet), sheet.href, sheet.errors, dict(sheet.namespaces)) == (0, 'x.css', [], {})
    rule: Rule = MediaRule('screen')
    assert rule.line is None
    assert repr(rule) == "<MediaRule 'screen'>"
    assert ImportRule('a.css', 'print').media.media_text == 'print'


def test_parsing_is_linear() -> None:
    # A quadratic loop once crept into building the top level rules
    import time

    def best(css: str) -> float:
        times = []
        for _ in range(5):
            start = time.perf_counter()
            for rule in parse_stylesheet(css).iter_rules():
                assert rule.line is not None
            times.append(time.perf_counter() - start)
        return min(times)

    unit = ''.join(f'.c{i} {{ a: b }} @media x {{ .d{i} {{ c: d }} }}\n' for i in range(500))
    small, large = best(unit), best(unit * 4)
    # Linear would be 4x, quadratic 16x. Allow generous noise.
    assert large < small * 9, (small, large)


def test_iter_declaration_blocks() -> None:
    # requirements 3.4: every declaration block, at any depth
    css = (
        'a{b:c; d{e:f} g:h} @media x { i{j:k} } @page { l:m; @top-left { n:o } } @font-face { src: url(x) } '
        '@supports (a:b) { p{q:r} } @keyframes k { from { s:t } } @import "i.css";'
    )
    sheet = kgcss.parse_stylesheet(css.replace('@import "i.css";', ''))
    blocks = [b.to_css(single_line=True) for b in sheet.iter_declaration_blocks()]
    assert blocks == ['b: c', 'e: f', 'g: h', 'j: k', 'l: m', 'n: o', 'src: url(x)', 'q: r']
    sheet = kgcss.parse_stylesheet('@import "i.css"; a{b:c}', href='http://e/', fetcher=lambda url: ('utf-8', 'z{y:x}'))
    assert [b.to_css(single_line=True) for b in sheet.iter_declaration_blocks()] == ['b: c']
    assert [b.to_css(single_line=True) for b in sheet.iter_declaration_blocks(follow_imports=True)] == ['y: x', 'b: c']
    # The blocks are the rules' own: changing them changes the sheet
    for b in sheet.iter_declaration_blocks():
        b.set('color', 'red')
    assert 'color: red' in sheet.css_text


def test_encoding_from_charset() -> None:
    # requirements 3.1, S4: for text, the encoding is what @charset says; for bytes, what decoded them
    assert kgcss.parse_stylesheet('@charset "latin1"; a{}').encoding == 'latin1'
    assert kgcss.parse_stylesheet('a{}').encoding is None
    assert kgcss.parse_stylesheet(b'a{}').encoding == 'utf-8'


def test_rule_at_browser_index_path() -> None:
    sheet = kgcss.parse_stylesheet(
        '@charset "utf-8"; /* c */ @namespace x url(y); a{} @media x { /* c */ b{} @supports (c:d) { e{} } }'
    )
    assert sheet.rule_at_browser_index_path((0,)).type is kgcss.RuleType.NAMESPACE
    b = sheet.rule_at_browser_index_path((2, 0))
    assert isinstance(b, kgcss.StyleRule)
    assert b.selector_text == 'b'
    e = sheet.rule_at_browser_index_path((2, 1, 0))
    assert isinstance(e, kgcss.StyleRule)
    assert e.selector_text == 'e'
    assert e.line == 1
    for bad in ((), (9,), (1, 0), (2, 5), (-1,)):
        with pytest.raises(IndexError):
            sheet.rule_at_browser_index_path(bad)
