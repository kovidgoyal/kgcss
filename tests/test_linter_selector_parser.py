# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The ports of postcss-selector-parser, @csstools/selector-specificity and
@csstools/selector-resolve-nested against the JavaScript libraries.

The trees (every property of every node, and what each node prints as), the
errors, the specificities and the resolved nested selectors must be the same.
Skipped unless the stylelint oracle is installed (see tests/stylelint_oracle.py).
"""

import re
from collections.abc import Iterator

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from kgcss.linter._selector_parser import (
    ID,
    UNDEFINED,
    Attribute,
    ClassName,
    Container,
    Node,
    Pseudo,
    Root,
    SelectorError,
    Tag,
    is_pseudo_class,
    is_pseudo_element,
    parse_selector,
)
from kgcss.linter._selector_tools import (
    Specificity,
    compare,
    resolve_nested_selector,
    selector_specificity,
)

from .stylelint_oracle import Stylelint, require_oracle, utf16_offsets

require_oracle()

RE_SURROGATE_PAIR = re.compile('[\ud800-\udbff][\udc00-\udfff]')
RE_ASTRAL = re.compile('[\U00010000-\U0010ffff]')
RAW_NAMES = {'insensitive_flag': 'insensitiveFlag', 'sensitive_flag': 'sensitiveFlag'}
DROPPED_KEYS = frozenset(('_constructed', 'lastEach', 'indexes', '_error'))

type Tree = dict[str, object]


@pytest.fixture(scope='module')
def oracle() -> Iterator[Stylelint]:
    with Stylelint() as s:
        yield s


# Dumping trees {{{


def parts(obj: object, names: tuple[str, ...]) -> dict[str, object]:
    ans: dict[str, object] = {}
    for name in names:
        part = getattr(obj, name)
        if part is not None:
            ans[name] = {k: v for k in ('before', 'after') if (v := getattr(part, k)) is not None}
    return ans


def dump(node: Node, offsets: list[int] | None) -> Tree:
    """The tree as tests/stylelint_oracle.mjs dumps it, after normalize(). offsets None drops sources."""
    ans: Tree = {'type': node.type, 'toString': node.to_string()}
    if isinstance(node, Attribute):
        if node.value_or_none is not None:
            ans['value'] = node.value_or_none
        if node.attribute is not None:
            ans['attribute'] = node.attribute
        if node.operator is not None:
            ans['operator'] = node.operator
        if node._quote_mark is not UNDEFINED:
            ans['quoteMark'] = node._quote_mark
        for key in ('insensitive', 'sensitive'):
            if (v := getattr(node, key)) is not None:
                ans[key] = v
    else:
        ans['value'] = node.value
    namespace = getattr(node, 'namespace', None)
    if namespace is not None:
        ans['namespace'] = namespace
    if offsets is not None and node.source is not None:
        source: dict[str, object] = {}
        for key in ('start', 'end'):
            pos = getattr(node.source, key)
            if pos is not None:
                source[key] = {'line': pos.line, 'column': pos.column}
        ans['source'] = source
    if node.source_index is not None:
        ans['sourceIndex'] = node.source_index
    spaces: dict[str, object] = {'before': node.spaces.before, 'after': node.spaces.after}
    spaces.update(parts(node.spaces, ('attribute', 'operator', 'value', 'insensitive')))
    ans['spaces'] = spaces
    raws: dict[str, object] = {}
    for key in ('value', 'namespace', 'attribute', 'operator', 'insensitive_flag', 'sensitive_flag'):
        if (v := getattr(node.raws, key)) is not None:
            raws[RAW_NAMES.get(key, key)] = v
    if (rs := node.raws.spaces) is not None:
        raw_spaces = {k: v for k in ('before', 'after') if (v := getattr(rs, k)) is not None}
        raw_spaces.update(parts(rs, ('attribute', 'operator', 'value', 'insensitive', 'namespace')))
        raws['spaces'] = raw_spaces
    if raws:
        ans['raws'] = raws
    if isinstance(node, Root) and node.trailing_comma:
        ans['trailingComma'] = True
    if isinstance(node, Container):
        ans['nodes'] = [dump(child, offsets) for child in node.nodes]
    return ans


def normalize(tree: Tree, offsets: list[int] | None) -> Tree:
    """The JavaScript dump, with Python names, Python indexes, and sources dropped when offsets is None."""
    ans: Tree = {}
    for key, value in tree.items():
        if key in DROPPED_KEYS:
            continue
        name = key.lstrip('_')
        if name == 'nodes':
            assert isinstance(value, list)
            ans['nodes'] = [normalize(child, offsets) for child in value]
        elif name == 'source':
            if offsets is not None:
                ans['source'] = value
        elif name == 'sourceIndex':
            assert isinstance(value, int)
            ans['sourceIndex'] = value if offsets is None else py_index(value, offsets)
        elif name == 'raws':
            if value != {}:
                ans['raws'] = value
        else:
            ans[name] = value
    if ans['type'] in ('root', 'selector'):
        ans.setdefault('value', '')
    return ans


def py_index(offset: int, offsets: list[int]) -> int:
    return offsets[offset] if offset < len(offsets) else offset - len(offsets) + offsets[-1] + 1


def js_result(response: dict[str, object], text: str) -> Tree | tuple[str, str]:
    """The normalized tree of an oracle response, or the (name, message) of its error."""
    if 'error' in response:
        error = response['error']
        assert isinstance(error, dict)
        return error['name'], error['message']
    tree = response['tree']
    assert isinstance(tree, dict)
    return normalize(tree, positions(text))


def positions(text: str) -> list[int] | None:
    """The offsets to convert JavaScript indexes with, or None when columns differ (astral characters)."""
    return None if RE_ASTRAL.search(text) else utf16_offsets(text)


def py_result(text: str) -> Tree | tuple[str, str]:
    try:
        root = parse_selector(text)
        return dump(root, positions(text))
    except SelectorError as e:
        return e.name, e.message


def check_parse(oracle: Stylelint, text: str) -> Tree | tuple[str, str]:
    expected = js_result(oracle.parse_selector(text), text)
    if positions(text) is None and isinstance(expected, dict):
        expected = fix_indexes(expected, utf16_offsets(text))
    actual = py_result(text)
    assert actual == expected, text
    return actual


def fix_indexes(tree: Tree, offsets: list[int]) -> Tree:
    ans = dict(tree)
    if isinstance(idx := ans.get('sourceIndex'), int):
        ans['sourceIndex'] = py_index(idx, offsets)
    if isinstance(nodes := ans.get('nodes'), list):
        ans['nodes'] = [fix_indexes(child, offsets) for child in nodes]
    return ans


# }}}

# Strategies {{{

FRAGMENTS = (
    # names
    'a',
    'div',
    'é',
    '\U0001f600',
    '-x',
    '--y',
    '1a',
    'of',
    'OF',
    'i',
    'I',
    's',
    'S',
    'x',
    'n',
    '2n+1',
    '-n+3',
    '%a',
    '50.5%',
    'from',
    'to',
    '\xa0',
    '\u3000',
    # escapes
    '\\',
    '\\31 ',
    '\\:',
    '\\\\',
    '\\0',
    '\\110000',
    '\\d800',
    '\\\n',
    '\\ ',
    '\\.',
    '\\#',
    '\\66 oo',
    '\\e9',
    '\\\r\n',
    '\\1F600',
    '\\]',
    '\\(',
    # punctuation
    '.',
    '#',
    '#{',
    '@{',
    '{',
    '}',
    '*',
    '&',
    '|',
    '||',
    '>>>',
    '>',
    '+',
    '~',
    ',',
    ':',
    '::',
    '(',
    ')',
    '[',
    ']',
    '=',
    '~=',
    '|=',
    '^=',
    '$=',
    '*=',
    '$',
    '^',
    '!',
    ';',
    '/',
    '/deep/',
    '/DEEP/',
    '/d\\65 ep/',
    '@',
    '%',
    # strings and comments
    '"',
    "'",
    '"a b"',
    "'x\\'y'",
    '"\\""',
    '""',
    '/*',
    '*/',
    '/* c */',
    '/**/',
    '/*\n*/',
    # whitespace
    ' ',
    '  ',
    '\t',
    '\n',
    '\r\n',
    '\f',
    '\r',
    # pseudos
    ':not(',
    ':is(',
    ':has(',
    ':hover',
    '::before',
    ':before',
    ':AFTER',
    ':nth-child(',
    ':nth-last-child(',
    ':where(',
    '::slotted(',
    '::highlight(',
    ':host(',
    ':host-context(',
    ':local(',
    ':global(',
    '::view-transition-group(',
    ':-moz-any(',
    ':any(',
    ':matches(',
    ':active-view-transition',
    '::part(',
    ':first-line',
    '::-webkit-scrollbar',
    # namespaces and attributes
    'ns|',
    '*|',
    '|',
    '[a]',
    '[ns|a="b" i]',
    '[a=b s]',
    '[ *|a ]',
    '[|a]',
    '[a|=b]',
    '[a$=b]',
    '[a = "b" I ]',
    '[#{$x}]',
    '[a=b/**/i]',
    '[a/**/=b]',
    '[ns|a]',
    '[ a ]',
    '[]',
    '[*]',
    '[ /**/ ]',
    'ns|.a',
    '[a="b""c"]',
    # surrogates
    '\ud800',
    '\udc00',
)


@st.composite
def fragment_text(draw: st.DrawFn) -> str:
    return ''.join(draw(st.lists(st.sampled_from(FRAGMENTS), max_size=14)))


IDENTS = st.sampled_from(['a', 'div', 'é', 'x\\:y', 'b\\31 ', '\U0001f600', '-x', 'of', 'i', 'n', '\\66 oo', 'A'])
SPACE = st.sampled_from(['', ' ', '  ', '\n', '\t', ' /* c */ ', '/**/'])
QUOTED = st.sampled_from(['"v"', "'v w'", '"a\\"b"', "''", '"é"'])


@st.composite
def attribute(draw: st.DrawFn) -> str:
    ns = draw(st.sampled_from(['', 'ns|', '*|', '|']))
    name = draw(IDENTS)
    sp = draw(SPACE)
    if draw(st.booleans()):
        return f'[{sp}{ns}{name}{sp}]'
    op = draw(st.sampled_from(['=', '~=', '|=', '^=', '$=', '*=']))
    value = draw(st.one_of(IDENTS, QUOTED))
    flag = draw(st.sampled_from(['', ' i', ' I', ' s', ' S', 'i', ' x', '/**/i']))
    return f'[{sp}{ns}{name}{draw(SPACE)}{op}{draw(SPACE)}{value}{flag}{draw(SPACE)}]'


def simple_selectors(selectors: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    pseudo_names = st.sampled_from(
        [
            ':not',
            ':is',
            ':has',
            ':where',
            ':nth-child',
            ':nth-last-child',
            '::slotted',
            '::highlight',
            ':host',
            ':host-context',
            ':local',
            ':global',
            '::view-transition-group',
            ':-moz-any',
            ':any',
            '::part',
            ':NOT',
        ]
    )
    nth = st.sampled_from(['2n+1', '2n+1 of ', 'odd of ', 'n OF ', '-n+3', ' 3 '])
    return st.one_of(
        st.builds(lambda ns, n: ns + n, st.sampled_from(['', 'ns|', '*|', '|']), IDENTS),
        st.builds(lambda ns: ns + '*', st.sampled_from(['', 'ns|', '*|', '|'])),
        st.builds(lambda n: '.' + n, IDENTS),
        st.builds(lambda n: '#' + n, IDENTS),
        st.just('&'),
        attribute(),
        st.sampled_from([':hover', '::before', ':before', '::after', ':first-letter', ':active-view-transition']),
        st.builds(
            lambda name, prefix, args, sp: f'{name}({sp}{prefix}{", ".join(args)}{sp})',
            pseudo_names,
            st.one_of(st.just(''), nth),
            st.lists(selectors, min_size=1, max_size=3),
            SPACE,
        ),
    )


def complex_selectors(selectors: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    compound = st.lists(simple_selectors(selectors), min_size=1, max_size=4).map(''.join)
    combinator = st.sampled_from([' ', ' > ', '>', ' + ', '~', ' >>> ', ' || ', ' /deep/ ', '\n', ' /* c */ '])
    return st.builds(
        lambda first, rest: first + ''.join(c + s for c, s in rest),
        compound,
        st.lists(st.tuples(combinator, compound), max_size=3),
    )


structured = st.recursive(IDENTS, complex_selectors, max_leaves=10)


@st.composite
def structured_list(draw: st.DrawFn) -> str:
    items = draw(st.lists(structured, min_size=1, max_size=3))
    return draw(st.sampled_from([', ', ',', ' , ', ',\n'])).join(items)


@st.composite
def selector_text(draw: st.DrawFn) -> str:
    kind = draw(st.integers(0, 2))
    if kind == 0:
        text = draw(fragment_text())
    elif kind == 1:
        text = draw(structured_list())
    else:  # a structured selector with some noise in it
        text = draw(structured_list())
        pos = draw(st.integers(0, len(text)))
        text = text[:pos] + draw(st.sampled_from(FRAGMENTS)) + text[pos:]
    assume(not RE_SURROGATE_PAIR.search(text))
    return text


# }}}

# Parsing {{{


@pytest.mark.parametrize(
    'text',
    [
        '',
        ' ',
        'a',
        'a.b\\:c > [ns|x="y" i]',
        ':not(a, b) /*c*/ *|d',
        'a /deep/ b',
        'a /DE\\45 P/ b',
        '[a',
        'a(',
        '(',
        '(a b)',
        'a (b) c',
        'a()()()',
        '[a=b *c]',
        '[a=b i/**/x]',
        'ns|.a',
        'ns|#a',
        ' a , b ',
        'a,',
        'a, ',
        ':not(a, )',
        ':not( )',
        ':not( /*c*/ )',
        '&|a',
        '|a',
        '*|*',
        'a|b|c',
        'a|',
        '[*|*]',
        '[]',
        '[=]',
        '[a=]',
        '[a="b"c]',
        '[a=b c]',
        '[a=\\62 I s]',
        '[a=b I s]',
        '[é|a]',
        '[a="b" "c"]',
        '["b"]',
        '[a==b]',
        '[a=b=c]',
        ':nth-child(2n+1 of .a, .b#c)',
        'a\\',
        'a\\\nb',
        '"unclosed',
        '/* unclosed',
        '/* a\nb */ c',
        'a\n/*\n\n*/\nb',
        'a\n  > b',
        '50.5%',
        '1.5.a',
        'a#{$b}.c',
        '.a.b.c#d#e',
        'a=b.c',
        ':hover.a(',
        ':',
        '::',
        ':(',
        'a!',
        'a;b',
        ']',
        ')',
        'a > > b',
        'a >',
        '> a',
        'a /b',
        'a/b/c',
        '.\U0001f600 > #\U0001f600',
        '\ud800.\udc00',
        '[a="\\"\\\\"]',
        '.a\\.b',
        '.-1',
        '.\\31 a',
        '*',
        '&',
        'a & b',
        '&.a:hover &-b',
    ],
)
def test_parse(oracle: Stylelint, text: str) -> None:
    check_parse(oracle, text)


def nested(depth: int, inner: str = 'a') -> str:
    return ':is(' * depth + inner + ')' * depth


@pytest.mark.parametrize(
    'text', [nested(255), nested(256), nested(257), nested(256, ':is'), nested(256, 'a :is') + ', b']
)
def test_nesting_limit(oracle: Stylelint, text: str) -> None:
    check_parse(oracle, text)


@given(selector_text())
def test_parse_property(oracle: Stylelint, text: str) -> None:
    check_parse(oracle, text)


def test_strategy_coverage() -> None:
    """The strategy reaches every kind of node and the interesting errors."""
    kinds: dict[str, int] = {}
    errors: dict[str, int] = {}

    def count(node: Node) -> None:
        kinds[node.type] = kinds.get(node.type, 0) + 1

    @given(selector_text())
    def collect(text: str) -> None:
        try:
            root = parse_selector(text)
        except SelectorError as e:
            key = re.sub(r"'.*'|\".*\"", 'X', e.message)
            errors[key] = errors.get(key, 0) + 1
            return
        root.walk(count)
        kinds['ok'] = kinds.get('ok', 0) + 1

    collect()
    for kind in ('ok', 'tag', 'class', 'id', 'attribute', 'pseudo', 'combinator', 'nesting', 'universal', 'comment'):
        assert kinds.get(kind, 0) > 0, (kind, kinds)
    assert len(errors) >= 5, errors


# }}}

# Node API {{{


def first_selector(text: str) -> Container:
    ans = parse_selector(text).nodes[0]
    assert isinstance(ans, Container)
    return ans


def test_api() -> None:
    root = parse_selector('a.b > c, d:not(.e)')
    sel = root.first
    assert isinstance(sel, Container)
    assert [n.type for n in sel.nodes] == ['tag', 'class', 'combinator', 'tag']
    b = sel.at(1)
    assert b is not None
    assert b.prev() is sel.first
    assert b.next() is sel.at(2)
    assert sel.first is not None
    assert sel.first.prev() is None  # at(-1) is undefined in JavaScript, not the last node
    assert sel.at(-1) is None
    assert sel.length == 4
    assert root.last is not None
    assert root.last.parent is root

    seen: list[str] = []

    def visit(node: Node) -> bool | None:
        seen.append(str(node))
        return False if node.type == 'pseudo' else None

    assert root.walk(visit) is False
    assert seen == ['a.b > c', 'a', '.b', ' > ', 'c', ' d:not(.e)', ' d', ':not(.e)']

    # Removing the node being visited does not skip the next one
    visited: list[str] = []

    def remove_classes(node: Node) -> None:
        visited.append(node.type)
        if node.type == 'class':
            node.remove()

    root.walk(remove_classes)
    assert visited.count('tag') == 3
    assert str(root) == 'a > c, d:not()'

    c = sel.at(2)
    assert c is not None
    clone = sel.clone()
    assert clone.parent is None
    assert str(clone) == str(sel)
    x = parse_selector('x').nodes[0]
    assert isinstance(x, Container)
    c.replace_with(x.nodes[0], x.nodes[0].clone())
    assert c.parent is None
    assert str(sel) == 'a > xx'
    assert str(clone) == 'a > c'

    pseudos: list[Pseudo] = []
    root.walk_pseudos(pseudos.append)
    assert [p.value for p in pseudos] == [':not']
    assert is_pseudo_class(pseudos[0])
    assert not is_pseudo_element(pseudos[0])
    assert is_pseudo_element(first_selector(':BEFORE').clone().nodes[0])

    tags: list[Tag] = []
    root.walk_tags(tags.append)
    assert [t.value for t in tags] == ['a', 'x', 'x', 'd']


def test_escapes() -> None:
    root = parse_selector('.u-m\\00002b#a\\:b x\\61 y [a\\:b="\\63"]')
    nodes = root.nodes[0]
    assert isinstance(nodes, Container)
    cls, id_, _, tag, _, attr = nodes.nodes
    assert isinstance(cls, ClassName)
    assert cls.value == 'u-m+'
    assert cls.raws.value == 'u-m\\00002b'
    cls.value = cls.value  # the setter normalizes the escaping, as stylelint relies on
    assert cls.raws.value == 'u-m\\+'
    assert str(cls) == '.u-m\\+'
    assert isinstance(id_, ID)
    assert (id_.value, id_.raws.value) == ('a:b', 'a\\:b')
    assert (tag.value, tag.raws.value) == ('xay', 'x\\61 y')
    assert isinstance(attr, Attribute)
    assert (attr.attribute, attr.value, attr.quote_mark, attr.quoted) == ('a:b', 'c', '"', True)
    assert attr.raws.value == '"\\63"'
    assert attr.offset_of('value') == 6
    assert attr.qualified_attribute == 'a\\:b'
    assert first_selector('[a]').clone().nodes[0].to_string() == '[a]'
    bare = first_selector('[a]')
    assert isinstance(bare.nodes[0], Attribute)
    assert bare.nodes[0].value_or_none is None


# }}}

# Specificity and nesting {{{


def specificities(text: str) -> list[dict[str, int]]:
    root = parse_selector(text)
    ans = [selector_specificity(root)]
    root.walk(lambda node: ans.append(selector_specificity(node)))
    return [s._asdict() for s in ans]


def check_specificity(oracle: Stylelint, text: str) -> None:
    response = oracle.selector_specificity(text)
    if 'error' in response:
        with pytest.raises(SelectorError):
            parse_selector(text)
        return
    assert specificities(text) == response['specificities'], text


@pytest.mark.parametrize(
    'text',
    [
        'a',
        '*',
        '#a .b [c] :hover ::before d',
        ':not(#a, .b) :is(a, b c) :where(#x) :has(> #y)',
        ':nth-child(2n+1 of #a, .b) :nth-last-child(odd) :nth-child(2n OF b)',
        '::slotted(#a) ::highlight(*) ::highlight(a) ::view-transition-group(*) ::view-transition-new(.a)',
        ':host(#a) :host-context(.b) :host :local(#a .b) :global(c)',
        ':active-view-transition-type(x) :-moz-any(#a) :any(#a) :matches(#a, b)',
        ':BEFORE :first-line ::-webkit-scrollbar & a',
    ],
)
def test_specificity(oracle: Stylelint, text: str) -> None:
    check_specificity(oracle, text)


@given(selector_text())
def test_specificity_property(oracle: Stylelint, text: str) -> None:
    check_specificity(oracle, text)


def test_compare() -> None:
    assert compare(Specificity(1, 0, 0), Specificity(0, 9, 9)) > 0
    assert compare(Specificity(0, 1, 2), Specificity(0, 1, 3)) < 0
    assert compare(Specificity(0, 1, 2), Specificity(0, 1, 2)) == 0


def check_nested(oracle: Stylelint, selector: str, parent: str, ignore: bool = False) -> None:
    response = oracle.resolve_nested(selector, parent, ignore_implicit_nesting=ignore)
    # Positions come from both texts, so they are only compared when the code units are the code points
    offsets = positions(selector + parent)
    try:
        actual: Tree | tuple[str, str] = dump(
            resolve_nested_selector(parse_selector(selector), parse_selector(parent), ignore_implicit_nesting=ignore),
            offsets,
        )
    except SelectorError as e:
        actual = e.name, e.message
    if 'error' in response:
        error = response['error']
        assert isinstance(error, dict)
        assert actual == (error['name'], error['message']), (selector, parent)
        return
    tree = response['tree']
    assert isinstance(tree, dict)
    expected = normalize(tree, offsets)
    assert isinstance(actual, dict), (selector, parent, actual)
    if offsets is None:
        actual, expected = strip_indexes(actual), strip_indexes(expected)
    assert actual == expected, (selector, parent)


def strip_indexes(tree: Tree) -> Tree:
    ans = {k: v for k, v in tree.items() if k != 'sourceIndex'}
    if isinstance(nodes := ans.get('nodes'), list):
        ans['nodes'] = [strip_indexes(child) for child in nodes]
    return ans


@pytest.mark.parametrize(
    ('selector', 'parent'),
    [
        ('a', '.p'),
        ('&', '.p, .q'),
        ('& b, c', '.p > .q'),
        ('> a', '.p'),
        ('&:hover', 'a::before'),
        ('div &', 'span'),
        (':has(&)', '.p'),
        ('&&', '.p, .q, .r'),
        ('*&', '*'),
        ('a&', 'b'),
        ('.x &.y', '#a b, c'),
        ('& & & &', '.a, .b, .c, .d, .e, .f, .g, .h, .i, .j'),
        ('&', '&'),
        ('a, /* c */ b', '.p'),
        ('"s" &', 'a'),
        ('a"s"b&', '.p'),
        ('&"s"a.c"t"', 'x.p.q'),
        ('b &', 'a b c'),
        ('& &', ', '.join(['.a'] * 2600)),
        ('&::before', 'a::after'),
    ],
)
def test_resolve_nested(oracle: Stylelint, selector: str, parent: str) -> None:
    check_nested(oracle, selector, parent)
    check_nested(oracle, selector, parent, True)


@given(structured_list(), structured_list(), st.booleans())
def test_resolve_nested_property(oracle: Stylelint, selector: str, parent: str, ignore: bool) -> None:
    assume(not RE_SURROGATE_PAIR.search(selector + parent))
    try:
        parse_selector(selector)
        parse_selector(parent)
    except SelectorError:
        assume(False)
    check_nested(oracle, selector, parent, ignore)


# }}}
