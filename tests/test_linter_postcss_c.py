# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C version of the PostCSS parser (csrc/postcss.c) against the Python one in kgcss.linter._postcss.

Both parsers must build the same trees, raws, parents and positions
included, and raise the same errors, for any text.
"""

import gc
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss._kgcss import PostcssNode, postcss_parse
from kgcss.linter._postcss import (
    NODE_CLASSES,
    AtRule,
    Comment,
    Container,
    CssSyntaxError,
    Declaration,
    Node,
    Root,
    Rule,
    SafeParser,
    c_parse,
    parse,
    python_parse,
    to_string,
)

from .linter_c_checks import check_postcss
from .strategies import css_text, lint_css


def check(css: str) -> None:
    check_postcss(css)


EXAMPLES = (
    '',
    ' ',
    'a{b:c;;} /*x*/ @m x{d:e}',
    'a{',
    'a{b:c}}',
    'a{b:c d:e}',
    'a{b::c}',
    'a{:b:c}',
    '@{}',
    '@',
    'a{b:url(x;y)}',
    'a{b:url( "x" )}',
    'a{b:url(x',
    'a{b:URL(x\\)y)}',
    'url(a',
    ',url(a)',
    'a{b:"x}',
    'a{b:"x\\"y"}',
    "a{b:'x",
    'a{b:c} /* x',
    '/*',
    '/**/',
    '/*/',
    '/* \u00a0 */',
    'a{b:(c}',
    'a{b:(c;d)}',
    'a{b:(c\nd)}',
    'a{b:[c}',
    'a { color : red ! important ; }',
    'a{b:c!IMPORTANT;d:e !ie}',
    'a{b:c important}',
    'a{b:c ! important}',
    'a{b:c !/**/ important}',
    'a{b:c!important d}',
    'a{--x:{y:z};}',
    'a{--x: a /* b */ ;}',
    'a{--x:}',
    'a{_b:c;*d:e}',
    'a{filter:progid:DXImageTransform.Microsoft.gradient(x=1)}',
    'a/*c*/b{c/**/d:e/*f*/}',
    'a{b:c/**/,/**/d}',
    '@media x;;@import "y"',
    '@media x',
    '@media x }',
    '@media (a{b}) {c{d:e}}',
    'a{};b{};',
    'a{b:c}\n;\n',
    'x\\:y{a\\:b:c}',
    '\\61 x{}',
    '\\',
    '\\\\',
    'a\\',
    ':root{--a: }',
    'a{b: c \t\n}',
    '@media (a:b) { @c d; e{f:g} }',
    'a{:b}',
    '@ x{}',
    'a b',
    'a: b',
    'a{b c}',
    'a{b:c d:e;f}',
    'a{b c:d}',
    'a{x:y b:c d:e}',
    'a{x:y b:c d:e;}',
    'a{x:y  /*c*/ b:c !important d : e f:g}',
    'a{x:y b:c ! important e:f}',
    'a{x:y b:(c:d) e:f progid:g h:i}',
    'a{ _x:y *b:c d:e important}',
    '}',
    'a{b:c}}}',
    '{}',
    '{{}}',
    'a{b{c{d:e',
    ';',
    'a{b:c; ; ;}',
    '\ud800{a:\udfff}',
    '\x00{\x00:\x00}',
    'é{ü:ö}',
    '\U0001f600{a:b}',
)


@pytest.mark.parametrize('css', EXAMPLES)
def test_examples(css: str) -> None:
    check(css)


@given(css_text)
def test_random(css: str) -> None:
    check(css)


@given(lint_css)
def test_random_lint_css(css: str) -> None:
    check(css)


@given(st.text(alphabet='a:;{}()[]"\'/*\\@! \n-_url,', max_size=40))
def test_random_punctuation(css: str) -> None:
    check(css)


def test_parse_matches_python() -> None:
    css = 'a{b:c d:e} f{g:h}'
    root, err = parse(css)
    root2, err2 = python_parse(css)
    assert err is not None
    assert err2 is not None
    assert (err.reason, err.template, err.args, err.offset, err.end) == (
        err2.reason,
        err2.template,
        err2.args,
        err2.offset,
        err2.end,
    )
    assert to_string(root) == to_string(root2) == 'a{b:c; d:e} f{g:h}'  # the safe parser splits the declaration


def test_node_fields() -> None:
    root = c_parse('a{b:c} @x; /**/', False)
    rule, at_rule, comment = root.nodes or []
    assert isinstance(rule, Rule)
    assert isinstance(at_rule, AtRule)
    assert isinstance(comment, Comment)
    assert at_rule.nodes is None
    decl = (rule.nodes or [])[0]
    assert isinstance(decl, Declaration)
    # Fields a node type does not have raise AttributeError
    assert not hasattr(decl, 'nodes')
    assert not hasattr(comment, 'nodes')
    assert not hasattr(comment, 'prop')
    # The tree is freed when the root goes (nodes and parents make cycles, so the collector frees them)
    del root, rule, at_rule, comment, decl
    gc.collect()


def test_bad_arguments() -> None:
    with pytest.raises(TypeError):
        postcss_parse(1, False, NODE_CLASSES)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        postcss_parse('', False, NODE_CLASSES[:4])
    with pytest.raises(TypeError):
        postcss_parse('', False, (PostcssNode, int, Rule, Declaration, Comment))  # ty: ignore[invalid-argument-type]

    class WithSlot(Root):
        __slots__ = ('x',)

    with pytest.raises(TypeError):
        postcss_parse('', False, (WithSlot, AtRule, Rule, Declaration, Comment))


@pytest.mark.slow
def test_deep_nesting() -> None:
    depth = 100_000
    css = 'a{' * depth
    assert c_parse(css, True).end == len(css)
    with pytest.raises(CssSyntaxError):
        c_parse(css, False)
    root = c_parse('@m{' * depth, True)
    node: Node = root
    n = 0
    while isinstance(node, Container) and node.nodes:
        node = node.nodes[0]
        n += 1
    assert n == depth
    del root, node
    gc.collect()


def test_missed_semicolons() -> None:
    # The safe parser splits a declaration at every missed semicolon. PostCSS does so by recursion, with a copy of
    # the remaining tokens at each level, which took memory quadratic in their number
    check('a{x:y ' + 'b:c ' * 3000 + '}')
    n = 200_000
    root = c_parse('a{x:y ' + 'b:c ' * n + '}', True)
    rule = (root.nodes or [])[0]
    assert isinstance(rule, Rule)
    assert len(rule.nodes or []) == n + 1


def test_huge_input() -> None:
    css = 'a{b:c}' * 50_000 + 'x{' + 'y:z;' * 50_000
    assert to_string(c_parse(css, True)) == to_string(SafeParser(css).parse())
    assert sys.getrefcount(css) >= 2
