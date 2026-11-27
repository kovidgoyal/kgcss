# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C versions of unesc(), cssesc(), tokenize() and the parser (csrc/linter.c) against the Python ones.

The Python versions in kgcss.linter._selector_parser are the reference: the
C ones must give exactly the same results, errors included, for any text.
"""

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss._kgcss import SelectorParser, selector_cssesc, selector_tokenize, selector_unesc
from kgcss.linter import _selector_parser
from kgcss.linter._selector_helpers import python_normalize_selector
from kgcss.linter._selector_parser import (
    C_PARSER,
    ClassName,
    Container,
    Node,
    Pseudo,
    Root,
    Selector,
    SelectorError,
    Tag,
    cssesc,
    python_cssesc,
    python_parse_selector,
    python_tokenize,
    python_unesc,
    tokenize,
    unesc,
)

from .linter_c_checks import all_selector_nodes, check_print_and_normalize, check_selector_parse, dump_selector
from .strategies import any_char

# Characters that matter to escapes and selectors, and any others
selector_chars = st.one_of(
    st.sampled_from('\\0123456789abcdefABCDEFxyz -_.#:,>+~|*&!;()[]=$^/\'"\t\n\r\f\x00\x7f\u00e9\ud800\U0001f600'),
    any_char,
)
selector_text = st.text(selector_chars, max_size=40)
ESCAPES = st.sampled_from(('\\', '\\\\', '\\31 ', '\\0', '\\110000', '\\d800', '\\FFFFFF ', '\\a\r\n', '\\ab\t'))
escaped_text = st.lists(st.one_of(ESCAPES, selector_text), max_size=6).map(''.join)

EXAMPLES = (
    '',
    'a',
    '\\',
    '\\\\',
    'a\\',
    '\\31 x',
    '\\31  x',
    '\\0',
    '\\d800',
    '\\110000',
    '\\FFFFFF',
    '\\1234567',
    '\\a\r\nb',
    '\\ab\r\n',
    '\\a\tb',
    '--x',
    '-1',
    '-',
    '1a',
    '\\-',
    'a b',
    '\u00e9\U0001f600\ud800',
    'a\\"b\'c',
    '\\\\31 x',
    '\\31  ',
    '\x7f\x00\x1f',
)


@pytest.mark.parametrize('text', EXAMPLES)
def test_unesc_examples(text: str) -> None:
    assert unesc(text) == python_unesc(text)


@given(escaped_text)
def test_unesc_random(text: str) -> None:
    assert unesc(text) == python_unesc(text)


def check_cssesc(text: str) -> None:
    for is_identifier in (False, True):
        for quotes in ('single', 'double'):
            for wrap in (False, True):
                kw = {'is_identifier': is_identifier, 'quotes': quotes, 'wrap': wrap}
                assert cssesc(text, **kw) == python_cssesc(text, **kw), kw


@pytest.mark.parametrize('text', EXAMPLES)
def test_cssesc_examples(text: str) -> None:
    check_cssesc(text)


@given(escaped_text)
def test_cssesc_random(text: str) -> None:
    check_cssesc(text)


def outcome(fn: object, text: str) -> object:
    assert callable(fn)
    try:
        return fn(text)
    except SelectorError as e:
        return str(e)


@pytest.mark.parametrize(
    'text',
    [
        *EXAMPLES,
        'a > b + c ~ d || e',
        '/* x */a',
        '/* x\ny */a\n/* z\n*/b',
        '/*\n*/x',
        '/* x',
        '"a',
        '"a\\"',
        '"a\\\\"b',
        "'a'",
        'a/b',
        'a/',
        '/',
        '\n\na\n',
        ' \n',
        '[a="b"]',
        ':not(.a, #b)',
        '\\61 bc d',
        'a\\',
        'a\\\n',
        '&.x',
        'a\\ff',
        'a\\ffffff b',
        'a\\ffffffb',
    ],
)
def test_tokenize_examples(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


@given(escaped_text)
def test_tokenize_random(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


@given(st.text(st.sampled_from('a \n/*"\'\\:,>|'), max_size=30))
def test_tokenize_random_punctuation(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


def test_bad_arguments() -> None:
    with pytest.raises(TypeError):
        selector_unesc(1)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        selector_tokenize(None)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        selector_cssesc('a', True, True)  # ty: ignore[missing-argument]


# The selector parser


def dump_node(node: Node, parent: Container | None) -> dict[str, object]:
    return dump_selector(node, parent)


def check_parse(text: str) -> bool:
    return check_selector_parse(text)


SELECTOR_EXAMPLES = [
    '',
    ' ',
    'a',
    '.a.b#c',
    'a > b',
    'a b',
    'a  ~  b',
    'a\n+\tb',
    'a >b',
    'a> b',
    '>a',
    'a >',
    'a ||b',
    ':not(.a, b)',
    ':not( .a , b )',
    ':is(:not(a))',
    ':is((a))',
    ':not(a',
    'a:not()',
    ':nth-child(2n + 1 of .a)',
    'a, b',
    'a,',
    'a,,b',
    ',a',
    'a , b ',
    ' a',
    'a\n',
    'x:hover::before',
    '::-webkit-scrollbar',
    ':::x',
    'a:',
    '.sm\\:p-4',
    '.\\31 0',
    '#a\\#b',
    'a\\.b.c',
    'a\\',
    'a\\ b',
    '10.5%',
    '1.5.%',
    '& > *',
    '&.a',
    '*.a',
    '*|a',
    'a|b',
    '|a',
    'a#{b}',
    '#{a}.b',
    'a$b',
    'a^=b',
    '$a',
    '.a/* x */ .b',
    'a /deep/ b',
    'a / b',
    '[a]',
    'a"b"',
    'a;',
    'a!',
    ':hover(a)',
    'a:hover.b(c)',
    'a.b:c(d)',
    'é.ü#ö',
    '\U0001f600',
    '\ud800',
    ':not(' * 300 + ')' * 300,
]


@pytest.mark.parametrize('text', SELECTOR_EXAMPLES)
def test_selector_parser_examples(text: str) -> None:
    check_parse(text)


def test_selector_parser_parses_common_selectors() -> None:
    for text in ('a', '.a.b#c', 'a > b', 'a b', ':not(.a, b)', 'a, b', 'x:hover::before', '.sm\\:p-4', '& > *', 'a,'):
        assert check_parse(text), text


SELECTOR_PARTS = st.sampled_from(
    (
        'a',
        'div',
        '.a',
        '#b',
        '.sm\\:p',
        '\\31 x',
        '\\',
        '*',
        '&',
        ':hover',
        '::before',
        ':not(',
        ':is(',
        '(',
        ')',
        ',',
        ' ',
        '  ',
        '\n',
        '>',
        '+',
        '~',
        '||',
        '|',
        '/',
        '/* c */',
        '[x=y]',
        '"s"',
        '$',
        '^',
        '=',
        '1',
        '.5',
        '%',
        '#{',
        '}',
        '!',
        'é',
    )
)
selectors = st.lists(SELECTOR_PARTS, max_size=12).map(''.join)


@given(selectors)
def test_selector_parser_random(text: str) -> None:
    check_parse(text)


@given(
    st.lists(
        st.sampled_from(('a', '.b', '#c', ' ', ' > ', ', ', ':not(', ')', ':hover', '*', '&', '+')), max_size=10
    ).map(''.join)
)
def test_selector_parser_random_supported(text: str) -> None:
    check_parse(text)


@given(selector_text)
def test_selector_parser_random_text(text: str) -> None:
    check_parse(text)


def test_selector_parser_bad_arguments() -> None:
    with pytest.raises(TypeError):
        SelectorParser()  # ty: ignore[missing-argument]
    with pytest.raises(TypeError):
        SelectorParser(*[Root] * 12, print)
    with pytest.raises(TypeError):
        C_PARSER.parse(1)  # ty: ignore[invalid-argument-type]


C_PARSER_CLASSES: tuple[type, ...] = (
    _selector_parser.Root, _selector_parser.Selector, _selector_parser.Pseudo, _selector_parser.ClassName,
    _selector_parser.ID, _selector_parser.Tag, _selector_parser.Universal, _selector_parser.Combinator,
    _selector_parser.Nesting, _selector_parser.String, _selector_parser.Comment, _selector_parser.Attribute,
)  # fmt: skip


def test_selector_parser_foreign_slots() -> None:
    # The parser writes to the slots at the offsets of their descriptors, so descriptors of another class, which
    # may lie past the end of the instances or share an offset, must be refused
    sp = _selector_parser
    classes = list(C_PARSER_CLASSES)

    class Big:
        __slots__ = ('s0', 's1', 's2')

    names = ('_value', 'parent', 'raws', 'source', 'source_index', 'spaces', '_namespace')
    for descr in (Big.__dict__['s2'], Big.__dict__['s0']):
        classes[5] = type('Tag', (), {**dict.fromkeys(names, descr), '__slots__': ()})
        with pytest.raises(TypeError, match='is not a slot'):
            SelectorParser(*classes, sp._attribute_node)
    # Two names for one slot
    classes[5] = type('Tag', (sp.Tag,), {'raws': sp.Node.__dict__['source'], '__slots__': ()})
    with pytest.raises(TypeError, match='same slot'):
        SelectorParser(*classes, sp._attribute_node)

    class Tag(sp.Tag):
        __slots__ = ()

    # The descriptors of a base are those of the subclass too
    classes[5] = Tag
    root = SelectorParser(*classes, sp._attribute_node).parse('a')
    assert isinstance(root, sp.Root)
    selector = root.nodes[0]
    assert isinstance(selector, sp.Selector)
    assert type(selector.nodes[0]) is Tag


def test_selector_parser_attribute_node() -> None:
    sp = _selector_parser
    classes = C_PARSER_CLASSES

    def broken(field: str, value: object) -> Callable[[str, list[sp.Token], int], sp.Attribute]:
        def attribute_node(css: str, tokens: list[sp.Token], position: int) -> sp.Attribute:
            node = sp._attribute_node(css, tokens, position)
            if value is None:
                delattr(node, field)
            else:
                setattr(node, field, value)
            return node

        return attribute_node

    # The parser changes the records of the node, so they must be records of the right types
    for field, value in (('spaces', None), ('spaces', 'x'), ('source', 'x'), ('source', None), ('raws', 1)):
        parser = SelectorParser(*classes, broken(field, value))
        with pytest.raises(TypeError, match='with its records'):
            parser.parse('a [b] c')
    assert SelectorParser(*classes, broken('raws', sp.Raws())).parse('[b]') is not NotImplemented

    def fail(exc: BaseException) -> Callable[[str, list[sp.Token], int], sp.Attribute]:
        def attribute_node(css: str, tokens: list[sp.Token], position: int) -> sp.Attribute:
            raise exc

        return attribute_node

    # An error in the selector is left to the Python parser, but not errors that it cannot help with
    assert SelectorParser(*classes, fail(sp.SelectorError('x'))).parse('[b]') is NotImplemented
    for exc in (MemoryError(), KeyboardInterrupt()):
        with pytest.raises(type(exc)):
            SelectorParser(*classes, fail(exc)).parse('[b]')


# Printing and normalizing


@pytest.mark.parametrize('text', SELECTOR_EXAMPLES)
def test_print_and_normalize_examples(text: str) -> None:
    check_print_and_normalize(text)


@pytest.mark.parametrize(
    'text',
    [
        'b a, a b, a',
        'a.b, a.b, .b.a',
        ':not(b, a, b)',
        '.b#a.c > d::before.e:hover',
        'a:first-line.b',
        'a:FIRST-LETTER.b',
        '.x\\:y .\\31 0',
        '.a\\.b',
        'a /* c */ b',
        '[x] .a',
        'ns|a .b',
        'a, b,',
        '& .a, .a &',
        '.b .a,.a .b',
    ],
)
def test_normalize_examples(text: str) -> None:
    assert check_print_and_normalize(text)[1]


@given(selectors)
def test_print_and_normalize_random(text: str) -> None:
    check_print_and_normalize(text)


@given(selector_text)
def test_print_and_normalize_random_text(text: str) -> None:
    check_print_and_normalize(text)


def test_normalize_nested_and_changed() -> None:
    from kgcss.linter._selector_helpers import normalize_selector
    from kgcss.linter._selector_tools import resolve_nested_selector

    parent = python_parse_selector('.b, .a')
    child = python_parse_selector('& > c, d &')
    resolved = resolve_nested_selector(child, parent)
    check_nodes = all_selector_nodes(resolved)
    for node in check_nodes:
        c = C_PARSER.normalize(node)
        if c is not NotImplemented:
            assert isinstance(c, tuple)
            assert c[1] == python_normalize_selector(node).string
    # Trees changed after parsing: values that are no strings are left to Python
    tree = python_parse_selector('.a b')
    selector = tree.nodes[0]
    assert isinstance(selector, Container)
    tag = selector.nodes[-1]
    tag._value = 1  # ty: ignore[invalid-assignment]
    # C prints the nodes it can and leaves the others to their Python _stringify()
    assert C_PARSER.to_string(tag) == tag.to_string() == '1'
    assert C_PARSER.to_string(tree) == tree.to_string() == '.a 1'
    c = C_PARSER.normalize(tree)
    assert isinstance(c, tuple)
    assert c[1] == normalize_selector(tree).string == python_normalize_selector(tree).string == '.a 1'
    assert dump_node(c[0], None) == dump_node(python_normalize_selector(tree).node, None)
    # A node during an iteration of each()
    tree = python_parse_selector('.a b')

    def visit(node: Node) -> None:
        assert C_PARSER.normalize(tree) is NotImplemented
        assert normalize_selector(tree).string == '.a b'

    tree.each(visit)


def walk_outcome(tree: Root, cls: type[Node]) -> object:
    walked: list[Node] = []

    def visit(node: Node) -> None:
        if isinstance(node, cls):
            walked.append(node)

    try:
        tree.walk(visit)
    except SelectorError as e:
        return str(e)
    return walked


def find_all_outcome(tree: Root, cls: type[Node]) -> object:
    try:
        return tree.find_all(cls)
    except SelectorError as e:
        return str(e)


def check_find_all(text: str) -> None:
    try:
        tree = python_parse_selector(text)
    except SelectorError:
        return
    for cls in (Pseudo, Tag, ClassName, Selector, Node):
        assert find_all_outcome(tree, cls) == walk_outcome(tree, cls)


@pytest.mark.parametrize('text', [*SELECTOR_EXAMPLES, ':not(' * 140 + 'a' + ')' * 140])
def test_find_all_examples(text: str) -> None:
    check_find_all(text)


@given(selectors)
def test_find_all_random(text: str) -> None:
    check_find_all(text)
