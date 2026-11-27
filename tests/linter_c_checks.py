# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Differential checks of the C code of the linter against the Python versions it ports.

check(text) runs every C function of the linter on text and compares what it
gives with its Python reference, raising AssertionError for a difference.
The fuzzer (fuzz/fuzz_linter.c) calls it, so this module needs nothing but
kgcss: no pytest, no hypothesis.
"""

from kgcss import _kgcss
from kgcss.linter import _postcss, _selector_helpers, _selector_parser, _utils, _value_parser


def dump_record(value: object) -> object:
    """A record (of csrc/records.c) as its type name and fields, anything else as it is."""
    fields: tuple[str, ...] | None = getattr(type(value), '_fields', None)
    if fields is not None and type(value).__module__ == 'kgcss':
        return (type(value).__name__, *(dump_record(getattr(value, f)) for f in fields))
    return value


# PostCSS


def dump_postcss(node: _postcss.Node, parent: _postcss.Container | None) -> dict[str, object]:
    """Everything about a PostCSS node and its descendants, checking their parents."""
    assert node.parent is parent
    ans: dict[str, object] = {'class': type(node).__name__, 'start': node.start, 'end': node.end}
    for key in ('name', 'params', 'selector', 'prop', 'value', 'important', 'text'):
        if hasattr(node, key):
            ans[key] = getattr(node, key)
    ans['raws'] = dump_record(node.raws)
    if isinstance(node, _postcss.Root):
        ans['css'] = node.css
        assert node.descendants is None
        assert node.by_type == {}
    if isinstance(node, _postcss.Container):
        ans['nodes'] = None if node.nodes is None else [dump_postcss(child, node) for child in node.nodes]
    else:
        assert not hasattr(node, 'nodes')
    return ans


def postcss_outcome(css: str, safe: bool, in_c: bool) -> object:
    try:
        if in_c:
            root = _postcss.c_parse(css, safe)
        else:
            root = (_postcss.SafeParser(css) if safe else _postcss.Parser(css)).parse()
    except _postcss.CssSyntaxError as err:
        return err.reason, err.offset, err.end
    return dump_postcss(root, None)


def check_postcss(css: str) -> None:
    for safe in (False, True):
        assert postcss_outcome(css, safe, True) == postcss_outcome(css, safe, False), f'safe={safe}'


# Selectors


def dump_selector(node: _selector_parser.Node, parent: _selector_parser.Container | None) -> dict[str, object]:
    """Every slot of a selector node and its descendants, checking their parents."""
    assert node.parent is parent
    ans: dict[str, object] = {'class': type(node).__name__}
    for cls in type(node).__mro__:
        for name in getattr(cls, '__slots__', ()):
            if name not in ('parent', 'nodes'):
                ans[name] = dump_record(getattr(node, name))
    if isinstance(node, _selector_parser.Container):
        ans['nodes'] = [dump_selector(child, node) for child in node.nodes]
    return ans


def all_selector_nodes(node: _selector_parser.Node) -> list[_selector_parser.Node]:
    ans = [node]
    if isinstance(node, _selector_parser.Container):
        for child in node.nodes:
            ans.extend(all_selector_nodes(child))
    return ans


def check_selector_parse(text: str) -> bool:
    """Compare the trees of the parsers: return whether the C parser parsed text."""
    ans = _selector_parser.C_PARSER.parse(text)
    try:
        expected = _selector_parser.python_parse_selector(text)
    except _selector_parser.SelectorError:
        assert ans is NotImplemented
        return False
    if ans is NotImplemented:
        return False
    assert isinstance(ans, _selector_parser.Root)
    assert dump_selector(ans, None) == dump_selector(expected, None)
    assert str(ans) == str(expected)
    return True


def check_print_and_normalize(text: str) -> tuple[int, int]:
    """Compare C and Python on the trees of both parsers: return how many prints and normalizations C did."""
    try:
        trees = [_selector_parser.python_parse_selector(text)]
    except _selector_parser.SelectorError:
        return 0, 0
    if (tree := _selector_parser.C_PARSER.parse(text)) is not NotImplemented:
        assert isinstance(tree, _selector_parser.Root)
        trees.append(tree)
    printed = normalized = 0
    for tree in trees:
        for node in all_selector_nodes(tree):
            c = _selector_parser.C_PARSER.to_string(node)
            if c is not NotImplemented:
                printed += 1
                assert c == node.to_string()
            assert str(node) == node.to_string()
            before = dump_selector(node, node.parent)
            c = _selector_parser.C_PARSER.normalize(node)
            assert dump_selector(node, node.parent) == before  # the node itself does not change
            if c is NotImplemented:
                continue
            normalized += 1
            assert isinstance(c, tuple)
            ours, string = c
            expected = _selector_helpers.python_normalize_selector(node)
            assert string == expected.string
            assert dump_selector(ours, None) == dump_selector(expected.node, None)
    return printed, normalized


def selector_outcome(fn: object, text: str) -> object:
    assert callable(fn)
    try:
        return fn(text)
    except _selector_parser.SelectorError as e:
        return str(e)


def check_selector_strings(text: str) -> None:
    assert _selector_parser.unesc(text) == _selector_parser.python_unesc(text)
    for is_identifier in (False, True):
        for quotes in ('single', 'double'):
            for wrap in (False, True):
                kw = {'is_identifier': is_identifier, 'quotes': quotes, 'wrap': wrap}
                assert _selector_parser.cssesc(text, **kw) == _selector_parser.python_cssesc(text, **kw), kw
    tokenize, python_tokenize = _selector_parser.tokenize, _selector_parser.python_tokenize
    assert selector_outcome(tokenize, text) == selector_outcome(python_tokenize, text)


# Values and the predicates of _utils


def dump_value(node: _value_parser.ValueNode) -> tuple[object, ...]:
    return (
        node.type,
        node.value,
        node.before,
        node.after,
        node.quote,
        node.unclosed,
        node.source_index,
        node.source_end_index,
        None if node.nodes is None else [dump_value(n) for n in node.nodes],
    )


def check_value(value: str) -> None:
    assert [dump_value(n) for n in _value_parser.parse(value)] == [
        dump_value(n) for n in _value_parser.python_parse(value)
    ]


PREDICATES = (
    (_utils.has_interpolation, _utils.python_has_interpolation),
    (_utils.is_standard_syntax_property, _utils.python_is_standard_syntax_property),
    (_utils.is_standard_syntax_selector, _utils.python_is_standard_syntax_selector),
    (_utils.is_standard_syntax_value, _utils.python_is_standard_syntax_value),
)


def check_predicates(text: str) -> None:
    for c, python in PREDICATES:
        assert c(text) == python(text), (python, text)


def check(text: str) -> None:
    """Every differential check on text."""
    check_postcss(text)
    check_selector_parse(text)
    check_print_and_normalize(text)
    check_selector_strings(text)
    check_value(text)
    check_predicates(text)
    assert isinstance(_kgcss.GIL_DISABLED_BUILD, int)
