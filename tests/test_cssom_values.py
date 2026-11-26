# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The object model: parsed values."""

import pytest

from kgcss import Property, PropertyValue, StyleRule, parse_style, parse_stylesheet, parse_value
from kgcss.cssom.values import (
    Dimension,
    Function,
    Hash,
    Ident,
    Number,
    Operator,
    Other,
    Percentage,
    Separator,
    SimpleBlock,
    String,
    UnicodeRange,
    Url,
)
from kgcss.tokenizer import TokenType


def texts(value: PropertyValue) -> list[str]:
    return [c.css_text for c in value]


def test_components_exclude_separators() -> None:
    # requirements V1
    v = parse_value('italic 12px/1.2 "My Font", serif')
    assert len(v) == 5
    assert texts(v) == ['italic', '12px', '1.2', '"My Font"', 'serif']
    assert [i.css_text for i in v.items] == ['italic', '12px', '/', '1.2', '"My Font"', ',', 'serif']
    assert [type(i) for i in v.items if isinstance(i, Separator)] == [Separator, Separator]
    assert v.css_text == 'italic 12px/1.2 "My Font", serif'
    assert v[-1] == Ident('serif', 'serif')
    assert v[1:3] == (Dimension('12px', 12, 'px'), Number('1.2', 1.2))


def test_component_types() -> None:
    # requirements V2
    v = parse_value('a "b\\"c" 700 1.5 -0.5e1 50% 1.0E1px #fff #1a url(x.png) url( "y z" ) + : @k')
    assert [c.type for c in v] == [
        'ident', 'string', 'number', 'number', 'number', 'percentage', 'dimension',
        'hash', 'hash', 'url', 'url', 'operator', 'operator', 'other',
    ]  # fmt: skip
    ident, string, n700, n15, nexp, pct, dim, h1, h2, u1, u2, plus, colon, other = v
    assert isinstance(ident, Ident)
    assert ident.value == 'a'
    assert isinstance(string, String)
    assert string.value == 'b"c'
    assert isinstance(n700, Number)
    assert type(n700.value) is int
    assert str(n700.value) == '700'
    assert isinstance(n15, Number)
    assert n15.value == 1.5
    assert isinstance(nexp, Number)
    assert nexp.value == -5.0
    assert isinstance(pct, Percentage)
    assert pct.value == 50
    # The css_parser bug of reporting e1px as the unit is gone
    assert isinstance(dim, Dimension)
    assert (dim.value, dim.unit) == (10.0, 'px')
    assert isinstance(h1, Hash)
    assert (h1.value, h1.is_id) == ('fff', True)
    assert isinstance(h2, Hash)
    assert (h2.value, h2.is_id) == ('1a', False)
    # requirements V3
    assert isinstance(u1, Url)
    assert (u1.url, u1.value, u1.css_text) == ('x.png', 'x.png', 'url(x.png)')
    assert isinstance(u2, Url)
    assert u2.url == 'y z'
    assert isinstance(plus, Operator)
    assert plus.value == '+'
    assert isinstance(colon, Operator)
    assert isinstance(other, Other)
    assert other.token_type is TokenType.AT_KEYWORD


def test_functions_and_blocks() -> None:
    # requirements V3 and V4: urls inside functions, function names and arguments
    v = parse_value('image-set(url(a.png) 1x, url("b.png") 2x) local(My\\ Font) calc(1px + (2 * 3)) [a b]')
    image_set, local, calc, block = v
    assert isinstance(image_set, Function)
    assert (image_set.name, image_set.value) == ('image-set', 'image-set')
    assert [c.url for c in image_set.arguments if isinstance(c, Url)] == ['a.png', 'b.png']
    assert texts(image_set.arguments) == ['url(a.png)', '1x', 'url("b.png")', '2x']
    assert isinstance(local, Function)
    assert local.arguments[0] == Ident('My\\ Font', 'My Font')
    assert isinstance(calc, Function)
    assert texts(calc.arguments) == ['1px', '+', '(2 * 3)']
    inner = calc.arguments[2]
    assert isinstance(inner, SimpleBlock)
    assert inner.opener == '('
    assert texts(inner.content) == ['2', '*', '3']
    assert isinstance(block, SimpleBlock)
    assert block.value is block.content
    # url() with a string argument is a Url, other url-like functions are not
    assert isinstance(parse_value("URL('x')")[0], Url)
    assert isinstance(parse_value('url("a" "b")')[0], Function)


def test_comments_and_whitespace_are_not_components() -> None:
    v = parse_value('  a /* x */ b  ')
    assert texts(v) == ['a', 'b']
    assert v.css_text == 'a /* x */ b'
    assert len(parse_value('')) == 0
    assert not parse_value(' /* x */ ')


def test_unicode_range() -> None:
    v = parse_value('U+0025-00FF, u+4??, U+A5', property_name='unicode-range')
    assert [(c.start, c.end) for c in v if isinstance(c, UnicodeRange)] == [(0x25, 0xFF), (0x400, 0x4FF), (0xA5, 0xA5)]
    assert texts(v) == ['U+0025-00FF', 'u+4??', 'U+A5']
    assert [i.css_text for i in v.items] == ['U+0025-00FF', ',', 'u+4??', ',', 'U+A5']
    assert v[0].value == (0x25, 0xFF)  # ty: ignore[missing-attribute]
    # Invalid ranges are parsed as ordinary values
    for text in ('U+110000', 'U+1?2', 'U+1?-50', 'U+20-10', 'U+', 'U+1, x'):
        assert not any(isinstance(c, UnicodeRange) for c in parse_value(text, property_name='unicode-range')), text
    # Only for unicode-range
    assert not isinstance(parse_value('U+1')[0], UnicodeRange)


def test_equality() -> None:
    assert parse_value('a 1px') == parse_value('a 1px')
    assert parse_value('a 1px') != parse_value('a 2px')
    assert hash(parse_value('a, b')) == hash(parse_value('a, b'))
    assert parse_value('1px') != '1px'
    assert repr(parse_value('a b')) == "<PropertyValue 'a b'>"


@pytest.mark.parametrize(
    ('value', 'index', 'expected'),
    [
        # requirements V6: calibre's remove_property_value test
        ('black url(x) fixed', 1, 'black fixed'),
        ('a, b, c', 1, 'a, c'),
        ('a, b, c', 2, 'a, b'),
        ('a, b, c', 0, 'b, c'),
        ('"X", Y, serif', 2, '"X", Y'),
        ('12px/1.2 serif', 1, '12px serif'),
        ('12px/1.2 serif', 0, '1.2 serif'),
        ('a /* c */ b', 1, 'a'),
        ('a /* c */ b c', 2, 'a /* c */ b'),
        ('only', 0, ''),
        ('a / b, *', 1, 'a / *'),
        # Regressions found by property tests: never change the meaning of what remains
        ('.c\\31 {}', 2, '.c\\31 '),  # the space ending a hex escape belongs to it
        ('\\0+a', 1, '\\0  a'),  # a hex escape absorbs one space when joined
        ('a:\\\n', 0, ':\\\n'),  # a lone backslash keeps the newline that makes it one
        ('\\\na+', 1, '\\\n+'),
    ],
)
def test_text_without(value: str, index: int, expected: str) -> None:
    assert parse_value(value).text_without(index) == expected


def first_component(prop: Property) -> object:
    # A function call, so that the type checker does not keep a narrowed type across mutations
    return prop.parsed_value[0]


def test_property_parsed_value() -> None:
    sheet = parse_stylesheet('a { font: italic 12px/1.2 "X", serif; unicode-range: U+0-7F; color: red }')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    block = rule.style
    font = block.get_property('font')
    assert font is not None
    parsed = font.parsed_value
    assert parsed is font.parsed_value
    assert len(parsed) == 5
    assert block.get_parsed_value('font') is parsed
    assert block.get_parsed_value('nope') is None
    urange = block.get_parsed_value('unicode-range')
    assert urange is not None
    assert isinstance(urange[0], UnicodeRange)
    font.value = 'bold 2em serif'
    assert texts(font.parsed_value) == ['bold', '2em', 'serif']
    # The parsed value depends on the name
    color = block.get_property('color')
    assert color is not None
    color.value = 'U+1-2'
    assert isinstance(first_component(color), Ident)
    color.name = 'unicode-range'
    assert isinstance(first_component(color), UnicodeRange)
    # Constructed properties
    assert texts(Property('margin', '0 1px').parsed_value) == ['0', '1px']
    assert texts(Property('a', 'b c').copy().parsed_value) == ['b', 'c']


def test_remove_component() -> None:
    block = parse_style('background: black url(x) fixed')
    prop = block.get_property('background')
    assert prop is not None
    prop.remove_component(1)
    assert block['background'] == 'black fixed'
    assert texts(prop.parsed_value) == ['black', 'fixed']
    with pytest.raises(IndexError):
        prop.remove_component(5)


def test_trailing_lone_backslash() -> None:
    # Invalid CSS, but it must still round trip: without its newline the backslash would become an escape
    block = parse_style('x: a\\\n; y: b')
    x = block.get_property('x')
    assert x is not None
    assert x.value == 'a\\\n'
    assert x.parsed_value.css_text == x.value
    assert texts(parse_value(x.value)) == ['a', '\\']
