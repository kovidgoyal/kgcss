# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import random

import pytest

from kgcss import (
    AtRule,
    Block,
    Declaration,
    NodeList,
    ParseError,
    QualifiedRule,
    Token,
    TokenType,
    parse_block_contents,
    parse_one_declaration,
    parse_one_rule,
    parse_rule_list,
    parse_stylesheet,
)


def summary(nodes: NodeList) -> list[str]:
    ans: list[str] = []
    for n in nodes:
        match n:
            case AtRule():
                ans.append(f'@{n.name}')
            case QualifiedRule():
                ans.append(f'rule:{n.prelude.text.strip()}')
            case Declaration():
                ans.append(f'{n.name}={n.value.text}' + ('!' if n.important else ''))
            case Token():
                ans.append(f'/*{n.value}*/')
    return ans


def contents(css: str) -> list[str]:
    return summary(parse_block_contents(css))


def decl(css: str) -> Declaration:
    d = parse_one_declaration(css)
    assert isinstance(d, Declaration), d
    return d


def test_types_cannot_be_instantiated() -> None:
    for cls in (AtRule, QualifiedRule, Declaration, NodeList):
        with pytest.raises(TypeError):
            cls()


def test_stylesheet_structure() -> None:
    css = '@charset "utf-8";\n/* c */ @import url(a.css) screen;\na > b { color: red }\n@media print { a { b: c } }'
    sheet = parse_stylesheet(css)
    assert summary(sheet) == ['@charset', '/* c */', '@import', 'rule:a > b', '@media']
    assert sheet.errors == ()
    assert len(sheet) == 5
    assert sheet[-1] is sheet[4]
    with pytest.raises(IndexError):
        sheet[5]
    charset, _, imp, rule, media = sheet
    assert isinstance(charset, AtRule)
    assert charset.source_text == '@charset "utf-8";'
    assert charset.block is None
    assert charset.prelude.text == ' "utf-8"'
    assert isinstance(imp, AtRule)
    assert imp.prelude.text == ' url(a.css) screen'
    assert (imp.line, imp.column) == (2, 9)
    assert isinstance(rule, QualifiedRule)
    assert rule.source_text == 'a > b { color: red }'
    assert rule.prelude.text == 'a > b '
    assert isinstance(rule.block, Block)
    assert rule.block.content.text == ' color: red '
    assert isinstance(media, AtRule)
    assert media.name == 'media'
    assert media.block is not None
    assert media.source_text == '@media print { a { b: c } }'
    assert sheet.stream.source == css
    assert repr(sheet) == '<NodeList of 5 nodes and 0 errors>'
    assert repr(media) == "<AtRule 'media' at 4:1>"
    assert repr(rule) == '<QualifiedRule at 3:1>'


def test_at_rule_extents() -> None:
    (r,) = parse_stylesheet('@foo bar ;  ')
    assert isinstance(r, AtRule)
    assert r.source_text == '@foo bar ;'
    (r,) = parse_stylesheet('@foo bar  /* x */  ')
    assert isinstance(r, AtRule)
    assert r.source_text == '@foo bar  /* x */'
    assert r.prelude.text == ' bar  /* x */  '
    (r,) = parse_stylesheet('@f\\6fo{}')
    assert isinstance(r, AtRule)
    assert r.name == 'foo'


def test_cdo_cdc() -> None:
    assert summary(parse_stylesheet('<!-- a{} -->')) == ['rule:a']
    rules = parse_rule_list('<!-- a{} -->')
    assert summary(rules) == ['rule:<!-- a']
    assert [e.kind for e in rules.errors] == ['invalid-rule']


def test_comments() -> None:
    css = '/* a */ x { /* b */ c: d /* e */; /* f */ } /* g */'
    assert summary(parse_stylesheet(css)) == ['/* a */', 'rule:x', '/* g */']
    assert summary(parse_stylesheet(css, comments=False)) == ['rule:x']
    (rule,) = parse_stylesheet(css, comments=False)
    assert isinstance(rule, QualifiedRule)
    # comments=False drops comments everywhere in that parse, including in the block
    assert summary(parse_block_contents(rule.block)) == ['c=d']
    (rule,) = [n for n in parse_stylesheet(css) if isinstance(n, QualifiedRule)]
    assert isinstance(rule, QualifiedRule)
    inner = parse_block_contents(rule.block)
    assert summary(inner) == ['/* b */', 'c=d', '/* f */']
    d = inner[1]
    assert isinstance(d, Declaration)
    # Comments at the ends of a value are not part of it, but are part of the declaration's text
    assert d.value.text == 'd'
    assert d.source_text == 'c: d /* e */'
    # Comments inside a value are kept as items
    v = decl('a: b /* c */ d').value
    assert v.text == 'b /* c */ d'
    assert [t.type for t in v] == [
        TokenType.IDENT,
        TokenType.WHITESPACE,
        TokenType.COMMENT,
        TokenType.WHITESPACE,
        TokenType.IDENT,
    ]


def test_declarations() -> None:
    d = decl('  Color : Red  ')
    assert (d.name, d.value.text, d.important) == ('Color', 'Red', False)
    assert d.source_text == 'Color : Red'
    assert (d.pos, d.end, d.line, d.column) == (2, 13, 1, 3)
    assert repr(d) == "<Declaration 'Color' at 1:3>"
    d = decl('a:')
    assert (d.value.text, len(d.value), d.source_text) == ('', 0, 'a:')
    d = decl('w\\69 dth: 1px')
    assert d.name == 'width'
    for css in ('a: b !important', 'a: b!IMPORTANT', 'a: b ! /* x */ important /* y */', 'a: b !\\69 mportant'):
        d = decl(css)
        assert (d.value.text, d.important) == ('b', True), css
        assert repr(d).endswith(' !important at 1:1>')
    for css in ('a: b !importantx', 'a: b important', 'a: b !important !', 'a: b !İmportant'):
        assert not decl(css).important, css
    assert decl('a: !important').value.text == ''
    # Unknown priorities stay in the value (requirements P15)
    d = decl('zoom: 1 !ie')
    assert (d.value.text, d.important) == ('1 !ie', False)
    for css, kind in [
        ('', 'empty'),
        (' /**/ ', 'empty'),
        ('a b', 'invalid-declaration'),
        ('1: x', 'invalid-declaration'),
    ]:
        err = parse_one_declaration(css)
        assert isinstance(err, ParseError)
        assert err.kind == kind


def test_custom_properties() -> None:
    d = decl('--x:  { a: b } ')
    assert (d.name, d.value.text) == ('--x', '{ a: b }')
    d = decl('--Y: var(--x, calc(1px + env(safe-area-inset-top)))')
    assert d.name == '--Y'
    assert d.value.text == 'var(--x, calc(1px + env(safe-area-inset-top)))'
    # A {} block mixed with other values is only allowed in custom properties
    assert summary(parse_block_contents('a: {b} c; --a: {b} c')) == ['rule:a:', '--a={b} c']
    # A qualified rule that looks like a custom property is dropped
    sheet = parse_stylesheet('--x: {a} b {}')
    assert summary(sheet) == ['rule:b']
    assert [e.kind for e in sheet.errors] == ['invalid-rule']


def test_block_contents_and_nesting() -> None:
    assert contents('a: b; &:hover { c: d } e: f') == ['a=b', 'rule:&:hover', 'e=f']
    assert contents('a:hover {c:1}') == ['rule:a:hover']
    assert contents('@media print { a: b } c: d') == ['@media', 'c=d']
    assert contents(';;; a: b;;') == ['a=b']
    nodes = parse_block_contents('a: b; c+:d; e: f')
    assert summary(nodes) == ['a=b', 'e=f']
    assert [(e.kind, e.offset) for e in nodes.errors] == [('invalid-rule', 6)]
    # Requirements P5: an invalid declaration is dropped and parsing stops at a top level }
    assert contents('color: red; }}} margin: 0') == ['color=red']
    assert contents('color: red; x: y }') == ['color=red', 'x=y']
    # A } inside parentheses is not top level
    assert contents('a: f(}); b: c') == ['a=f(})', 'b=c']


def test_parse_from_blocks() -> None:
    css = 'x { a: b; /* c */ y { d: e "unclosed\n } } @media screen { p { q: r } s { } }'
    sheet = parse_stylesheet(css)
    rule, media = sheet
    assert isinstance(rule, QualifiedRule)
    assert isinstance(media, AtRule)
    assert [e.kind for e in sheet.errors] == ['newline-in-string']
    inner = parse_block_contents(rule.block)
    assert summary(inner) == ['a=b', '/* c */', 'rule:y']
    # Errors from the tokenizer within the parsed range are included
    assert [e.kind for e in inner.errors] == ['newline-in-string']
    nested = inner[2]
    assert isinstance(nested, QualifiedRule)
    assert (nested.line, nested.column) == (1, 19)
    d = parse_block_contents(nested.block)[0]
    assert isinstance(d, Declaration)
    assert d.value.text == 'e "unclosed'
    assert media.block is not None
    assert summary(parse_rule_list(media.block)) == ['rule:p', 'rule:s']
    assert parse_rule_list(media.block).errors == ()
    with pytest.raises(TypeError):
        parse_one_rule(rule.block)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_block_contents(1)  # type: ignore[arg-type]


def test_unknown_at_rules_round_trip() -> None:
    # Requirements P6: unknown at-rules are kept with their exact text, urls and strings included
    css = '@supports (display: grid) and (not (x: url("a b.png"))) { a { b: url(c.png) } }'
    (rule,) = parse_stylesheet(css)
    assert isinstance(rule, AtRule)
    assert rule.source_text == css
    assert rule.name == 'supports'
    assert rule.block is not None
    assert summary(parse_rule_list(rule.block)) == ['rule:a']
    css = '@layer a, b; @container (min-width: 10em) { a { b: c } } @-webkit-keyframes x { 0% { a: b } }'
    sheet = parse_stylesheet(css)
    assert summary(sheet) == ['@layer', '@container', '@-webkit-keyframes']
    assert ' '.join(n.source_text for n in sheet) == css


def test_modern_values_round_trip() -> None:
    # Requirements P15
    for value in (
        'var(--x)',
        'calc(100% - (2 * var(--gap, 1em)))',
        'env(safe-area-inset-left, 0)',
        'image-set(url(a.png) 1x, url("b c.png") 2x)',
        'progid:DXImageTransform.Microsoft.gradient(startColorstr=#80000000)',
    ):
        d = decl(f'a: {value}')
        assert d.value.text == value


def test_parse_one_rule() -> None:
    r = parse_one_rule('  a { b: c }  ')
    assert isinstance(r, QualifiedRule)
    assert r.source_text == 'a { b: c }'
    r = parse_one_rule('@page :first { margin: 1in }')
    assert isinstance(r, AtRule)
    assert r.name == 'page'
    assert r.prelude.text == ' :first '
    for css, kind in [('', 'empty'), ('foo', 'invalid-rule'), ('a{} b', 'extra-input')]:
        err = parse_one_rule(css)
        assert isinstance(err, ParseError)
        assert err.kind == kind


def test_positions_of_nested_rules() -> None:
    # Requirements P12: positions for rules at any depth
    css = 'a {\n  b: c;\n  @media x {\n    d { e: f }\n  }\n}\n'
    (a,) = parse_stylesheet(css)
    assert isinstance(a, QualifiedRule)
    b, media = parse_block_contents(a.block)
    assert (b.line, b.column) == (2, 3)
    assert isinstance(media, AtRule)
    assert (media.line, media.column) == (3, 3)
    assert media.block is not None
    (d,) = parse_block_contents(media.block)
    assert (d.line, d.column) == (4, 5)
    assert isinstance(d, QualifiedRule)
    (e,) = parse_block_contents(d.block)
    assert (e.line, e.column) == (4, 9)


def test_errors_never_raise() -> None:
    for css in ('}', '{', ';', '@', '!', 'a{', '@a{', 'a:', '"', 'url(', '/*', '\\', ')', '];a{}}'):
        for parser in (parse_stylesheet, parse_rule_list, parse_block_contents):
            parser(css)
        parse_one_rule(css)
        parse_one_declaration(css)


def test_random_input_invariants() -> None:
    rng = random.Random(777)
    alphabet = [*'ab:;{}()[] @!-"\\/*,.', 'important', '--', 'url(', '/**/', '\n', 'é']
    for _ in range(3000):
        css = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        for parser in (parse_stylesheet, parse_rule_list, parse_block_contents):
            nodes = parser(css)
            pos = 0
            for n in nodes:
                assert n.pos >= pos, (css, n)
                assert n.source_text == css[n.pos : n.end]
                pos = n.end
                if isinstance(n, (AtRule, QualifiedRule)):
                    assert n.prelude.text == css[n.prelude.pos : n.prelude.end]
                    if n.block is not None:
                        assert n.block.source_text == css[n.block.pos : n.block.end]
                        parse_block_contents(n.block)
                elif isinstance(n, Declaration):
                    assert n.value.text == css[n.value.pos : n.value.end]
                    assert n.pos <= n.value.pos <= n.value.end <= n.end
            offsets = [e.offset for e in nodes.errors]
            assert offsets == sorted(offsets)
