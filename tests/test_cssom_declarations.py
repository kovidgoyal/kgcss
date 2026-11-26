# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The object model: declaration blocks and properties."""

import gc
import weakref

import pytest

from kgcss import (
    Comment,
    DeclarationBlock,
    FontFaceRule,
    InvalidValueError,
    MarginRule,
    NestedDeclarationsRule,
    PageRule,
    ParseError,
    Property,
    SerializeOptions,
    StyleRule,
    parse_style,
    parse_stylesheet,
    serialize,
)
from kgcss.syntax import parse_block_contents

PRESERVE = SerializeOptions(style='preserve')


def style_of(css: str) -> DeclarationBlock:
    rule = parse_stylesheet(f'a {{ {css} }}').rules[0]
    assert isinstance(rule, StyleRule)
    return rule.style


def full(block: DeclarationBlock) -> list[str]:
    return [
        f'{i.name}:{i.value}' + ('!' if i.important else '') if isinstance(i, Property) else f'/*{i.text}*/'
        for i in block.items
    ]


def effective(block: DeclarationBlock) -> list[str]:
    return [f'{p.name}:{p.value}' + ('!' if p.important else '') for p in block]


def test_effective_view() -> None:
    block = style_of('color: red; Margin: 0 !important; COLOR: blue; margin: 1px; /* c */ padding: 2px; --X: a; --x: b')
    # important beats later normal declarations, otherwise the last one wins,
    # ordered by each name's last occurrence, names lowercased except custom properties
    assert effective(block) == ['color:blue', 'margin:0!', 'padding:2px', '--X:a', '--x:b']
    assert block.keys() == ['color', 'margin', 'padding', '--X', '--x']
    assert len(block) == 5
    assert block['color'] == block['COLOR'] == 'blue'
    assert 'MARGIN' in block
    assert 'nope' not in block
    assert 1 not in block
    with pytest.raises(KeyError):
        block['nope']
    assert block.get_value('nope') == ''
    assert block.get_value('nope', 'x') == 'x'
    assert block.get_value('margin') == '0'
    p = block.get_property('margin')
    assert p is not None
    assert p.important
    assert block.get_property('nope') is None
    assert full(block) == [
        'color:red', 'margin:0!', 'color:blue', 'margin:1px', '/* c */', 'padding:2px', '--X:a', '--x:b',
    ]  # fmt: skip


def test_order_is_by_last_occurrence() -> None:
    assert effective(style_of('a: 1 !important; b: 2; a: 3')) == ['b:2', 'a:1!']
    assert effective(style_of('a: 1; b: 2; a: 3')) == ['b:2', 'a:3']


def test_empty_and_comments_only() -> None:
    block = style_of('/* only a comment */')
    assert len(block) == 0
    assert not block
    assert full(block) == ['/* only a comment */']
    assert not DeclarationBlock()


def test_parents() -> None:
    rule = parse_stylesheet('a { b: c; /* d */ }').rules[0]
    assert isinstance(rule, StyleRule)
    block = rule.style
    assert block.parent_rule is rule
    prop, comment = block.items
    assert isinstance(prop, Property)
    assert prop.parent is block
    assert isinstance(comment, Comment)
    assert comment.parent_rule is rule
    assert (prop.line, prop.column) == (1, 5)


def test_set() -> None:
    block = style_of('a: 1; b: 2; a: 3 !important; a: 4')
    old = block.get_property('a')
    p = block.set('A', '5')
    # The effective declaration is changed in place, other declarations of the name removed
    assert p is old
    assert full(block) == ['b:2', 'a:5']
    assert not p.important
    q = block.set('c', '6', 'important')
    assert q.parent is block
    assert full(block) == ['b:2', 'a:5', 'c:6!']
    block.set('c', ' 7 ', '!IMPORTANT')
    assert block['c'] == '7'
    for value in ('a; b', 'x }', 'f(', '"unclosed', 'red !important', 'url(a', '/* x'):
        with pytest.raises(InvalidValueError):
            block.set('d', value)
    with pytest.raises(ValueError, match='Invalid priority'):
        block.set('d', 'x', 'high')
    with pytest.raises(ValueError, match='cannot be empty'):
        block.set('', 'x')
    # Values that look odd but are structurally fine are accepted
    block.set('d', '{ x }')
    block.set('--e', 'a { b } ; ' * 0 + '[x] (y) {z}')
    assert full(block)[-2:] == ['d:{ x }', '--e:[x] (y) {z}']


def test_set_property() -> None:
    block = style_of('a: 1; b: 2; a: 3')
    other = style_of('a: 9')
    foreign = other.get_property('a')
    assert foreign is not None
    used = block.set_property(foreign)
    assert used is not foreign
    assert foreign.parent is other
    assert used.parent is block
    assert full(block) == ['b:2', 'a:9']
    fresh = Property('c', '4')
    assert block.set_property(fresh) is fresh
    assert full(block) == ['b:2', 'a:9', 'c:4']
    # Re-setting a property that is already in the block, but not effective
    block2 = style_of('a: 1; a: 2 !important')
    hidden = block2.items[0]
    assert isinstance(hidden, Property)
    assert block2.set_property(hidden) is hidden
    assert full(block2) == ['a:1']


def test_remove() -> None:
    block = style_of('a: 1; b: 2; a: 3 !important')
    assert block.remove('A') == '3'
    assert full(block) == ['b:2']
    assert block.remove('a') == ''
    assert block.remove('nope') == ''


def test_specific_declarations() -> None:
    block = style_of('margin: 0; margin: 1px 2px; color: red')
    first, second, _ = block.items
    assert isinstance(first, Property)
    assert isinstance(second, Property)
    block.remove_declaration(first)
    assert full(block) == ['margin:1px 2px', 'color:red']
    assert first.parent is None
    # Expanding a shorthand into longhands at the same position
    block.replace_declaration(second, [Property('margin-top', '1px'), Property('margin-right', '2px')])
    assert full(block) == ['margin-top:1px', 'margin-right:2px', 'color:red']
    with pytest.raises(ValueError, match='not in the list'):
        block.remove_declaration(second)


def parent_of(prop: Property) -> DeclarationBlock | None:
    # A function call, so that mypy does not keep a narrowed type across mutations
    return prop.parent


def test_declaration_list() -> None:
    block = style_of('a: 1; b: 2')
    items = block.items
    c = Property('c', '3')
    items.insert(1, c)
    assert full(block) == ['a:1', 'c:3', 'b:2']
    assert c.parent is block
    items.append(Comment(' x '))
    del items[0]
    assert full(block) == ['c:3', 'b:2', '/* x */']
    items[0] = Property('d', '4')
    assert parent_of(c) is None
    assert items.index(items[1]) == 1
    with pytest.raises(ValueError, match='belongs to another declaration block'):
        items.append(style_of('e: 5').items[0])
    items[1:] = [Property('f', '6')]
    assert full(block) == ['d:4', 'f:6']
    assert len(items) == 2
    assert repr(items) == '<DeclarationList of 2 items>'
    # A failed slice assignment leaves things as they were
    with pytest.raises(ValueError, match='another declaration block'):
        items[0:1] = [Property('g', '7'), style_of('h: 8').items[0]]
    assert full(block) == ['d:4', 'f:6']
    assert all(isinstance(p, Property) and p.parent is block for p in items)


def test_property() -> None:
    p = Property('Color', '  red  ')
    assert (p.name, p.value, p.priority, p.important, p.literal_priority) == ('color', 'red', '', False, '')
    assert p.parent is None
    assert p.line is None
    assert Property('--Custom', 'x').name == '--Custom'
    imp = Property('a', 'b', 'important')
    assert (imp.priority, imp.literal_priority) == ('important', '!important')
    assert repr(imp) == "<Property a: 'b' !important>"
    # Identity semantics
    assert Property('a', 'b') != Property('a', 'b')
    assert len({p, imp, p}) == 2
    # Renaming in place (kepubify)
    block = style_of('widows: 2')
    w = block.get_property('widows')
    assert w is not None
    w.name = '-calibre-removed-css-for-kobo-widows'
    assert block.keys() == ['-calibre-removed-css-for-kobo-widows']
    w.name = 'widows'
    assert block['widows'] == '2'
    w.value = '3'
    assert block['widows'] == '3'
    with pytest.raises(InvalidValueError):
        w.value = 'a; b'
    w.priority = 'important'
    assert block.get_property('widows') is w
    assert w.important
    c = w.copy()
    assert c.parent is None
    assert (c.name, c.value, c.priority) == ('widows', '3', 'important')


def test_validation() -> None:
    assert Property('a', '', validate=False).value == ''
    for value in ('', '  ', '"a\nb"', 'url(a b)'):
        with pytest.raises(InvalidValueError):
            Property('color', value, validate=True)
    assert Property('color', 'rgb(1, 2, 3)', validate=True).value == 'rgb(1, 2, 3)'
    # Structural problems are always errors
    with pytest.raises(InvalidValueError):
        Property('color', 'red; margin: 0')


def test_literal_priority() -> None:
    block = style_of('a: 1 !IMPORTANT; b: 2 ! /* x */ important /* y */; c: 3')
    a, b, c = (block.get_property(n) for n in 'abc')
    assert a is not None
    assert b is not None
    assert c is not None
    assert a.literal_priority == '!IMPORTANT'
    assert b.literal_priority == '! /* x */ important'
    assert c.literal_priority == ''
    b.priority = ''
    assert b.literal_priority == ''


def test_parse_style() -> None:
    # requirements P2 and P5
    block = parse_style('color: red; }}} margin: 0')
    assert full(block) == ['color:red']
    assert block.errors == []
    errors: list[ParseError] = []
    block = parse_style('a: 1; b c; d { }; e: 2 !important', log=errors.append)
    assert full(block) == ['a:1', 'e:2!']
    assert [(e.kind, e.column) for e in block.errors] == [('invalid-rule', 7), ('invalid-rule', 12)]
    assert errors == block.errors
    assert block.parent_rule is None


def test_rule_styles() -> None:
    css = '@font-face { font-family: X } @page { margin: 0; @top-left { content: "x" } } a { b: c; d {} e: f }'
    sheet = parse_stylesheet(css)
    font_face, page, style = sheet
    assert isinstance(font_face, FontFaceRule)
    assert full(font_face.style) == ['font-family:X']
    assert isinstance(page, PageRule)
    assert full(page.style) == ['margin:0']
    margin = page.rules[0]
    assert isinstance(margin, MarginRule)
    assert full(margin.style) == ['content:"x"']
    assert isinstance(style, StyleRule)
    assert full(style.style) == ['b:c']
    nested = style.rules[1]
    assert isinstance(nested, NestedDeclarationsRule)
    assert full(nested.style) == ['e:f']
    assert style.style is style.style
    for rule in (font_face, page, margin, style, nested):
        assert rule.style.parent_rule is rule
    # Constructed rules have empty blocks
    assert len(StyleRule('x').style) == 0
    assert len(NestedDeclarationsRule().style) == 0


def test_assigning_style() -> None:
    sheet = parse_stylesheet('a { b: c } x { y: z }')
    a, x = sheet.rules
    assert isinstance(a, StyleRule)
    assert isinstance(x, StyleRule)
    # Kobo empties rules with rule.style = ''
    a.style = ''
    assert len(a.style) == 0
    a.style = 'd: e; f {}'
    assert full(a.style) == ['d:e']
    # Errors in assigned text are positions in that text, they stay with the block
    assert [e.kind for e in a.style.errors] == ['invalid-rule']
    assert sheet.errors == []
    # Assigning a block that belongs to another rule copies it
    x.style = a.style
    assert x.style is not a.style
    assert full(x.style) == ['d:e']
    assert x.style.parent_rule is x
    free = parse_style('g: h; /* i */')
    a.style = free
    assert a.style is free
    assert free.parent_rule is a
    assert all(i.parent_rule is a for i in free.items if isinstance(i, Comment))


def test_modification_and_preserve_serialization() -> None:
    sheet = parse_stylesheet('a { b: c } @media x { d { e: f } } g { h: i }')
    a, media, g = sheet.rules
    assert isinstance(a, StyleRule)
    assert isinstance(g, StyleRule)
    assert serialize(sheet, PRESERVE)
    inner = media.child_rules()
    assert inner is not None
    d = inner[0]
    assert isinstance(d, StyleRule)
    # Reading does not count as a modification
    _ = d.style.get_value('e')
    assert serialize(sheet, PRESERVE) == 'a { b: c } @media x { d { e: f } } g { h: i }'
    d.style.set('e', 'g')
    # The changed rule and its ancestors are written in the pretty format, unchanged rules as they were
    # The original whitespace between rules is kept
    assert serialize(sheet, PRESERVE) == 'a { b: c } @media x {\n  d {\n    e: g;\n  }\n} g { h: i }'
    assert serialize(a, PRESERVE) == 'a { b: c }'
    prop = g.style.get_property('h')
    assert prop is not None
    prop.value = 'j'
    assert serialize(g, PRESERVE) == 'g {\n  h: j;\n}'


def test_parsed_properties_are_not_gc_tracked() -> None:
    # Properties cannot be in a reference cycle (parent is weak), so parsed ones are exempt from
    # garbage collection, which matters for the speed of reading large sheets
    sheet = parse_stylesheet('a { b: c; /* x */ d: e !important }')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    props = [p for p in rule.style.items if isinstance(p, Property)]
    assert [(p.name, p.value, p.priority) for p in props] == [('b', 'c', ''), ('d', 'e', 'important')]
    assert not any(gc.is_tracked(p) for p in props)
    assert all(p.parent is rule.style for p in props)
    # Properties created directly are ordinary objects
    assert gc.is_tracked(Property('x', 'y'))


def test_dropped_sheets_are_freed() -> None:
    def make() -> weakref.ref[DeclarationBlock]:
        sheet = parse_stylesheet('a { b: c } @media print { d { e: f } }')
        blocks = [r.style for r in sheet.iter_rules() if isinstance(r, StyleRule)]
        for block in blocks:
            for prop in block.items:
                _ = prop.parsed_value if isinstance(prop, Property) else None
        return weakref.ref(blocks[-1])

    ref = make()
    gc.collect()
    assert ref() is None


def test_property_outliving_its_block() -> None:
    def make() -> Property:
        block = parse_style('a: b')
        prop = block.get_property('a')
        assert prop is not None
        assert prop.parent is block
        return prop

    prop = make()
    gc.collect()
    assert prop.parent is None
    assert prop.value == 'b'
    prop.value = 'c'  # changing it is fine, with nothing to notify
    assert prop.css_text == 'a: c'


def test_build_properties_checks_its_arguments() -> None:
    from kgcss._kgcss import build_properties

    class Extra(Property):
        __slots__ = ('x',)

    nodes = parse_block_contents('a: b')
    with pytest.raises(TypeError):
        build_properties(nodes, Extra, None)
    with pytest.raises(TypeError):
        build_properties(nodes, str, None)  # type: ignore[type-var]
    with pytest.raises(TypeError):
        build_properties((), Property, None)  # type: ignore[arg-type]
    built, others = build_properties(nodes, Property, None)
    assert not others
    prop = built[0]
    assert isinstance(prop, Property)
    assert prop.name == 'a'
    assert prop.parent is None
