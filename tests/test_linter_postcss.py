# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The port of the PostCSS parser (kgcss.linter._postcss) against PostCSS itself.

The trees, raws included, and the errors must be the same. Skipped unless the
stylelint oracle is installed (see tests/stylelint_oracle.py).
"""

import re
from collections.abc import Iterator

import pytest
from hypothesis import assume, given

from kgcss.linter._postcss import Container, CssSyntaxError, Node, Parser, RawValue, SafeParser, to_string

from .strategies import css_text
from .stylelint_oracle import Stylelint, require_oracle, utf16_offsets

require_oracle()

RE_SURROGATE_PAIR = re.compile('[\ud800-\udbff][\udc00-\udfff]')
RE_ESCAPED_ASTRAL = re.compile('\\\\[\U00010000-\U0010ffff]')
RAW_NAMES = {'after_name': 'afterName', 'own_semicolon': 'ownSemicolon'}


@pytest.fixture(scope='module')
def oracle() -> Iterator[Stylelint]:
    with Stylelint() as s:
        yield s


def dump(node: Node) -> dict[str, object]:
    """The tree as tests/stylelint_oracle.mjs dumps PostCSS's."""
    ans: dict[str, object] = {'type': node.type, 'start': node.start, 'end': node.end}
    for key in ('name', 'params', 'selector', 'prop', 'value', 'text'):
        if hasattr(node, key):
            ans[key] = getattr(node, key)
    if getattr(node, 'important', False):
        ans['important'] = True
    raws: dict[str, object] = {}
    for key in node.raws.__slots__:
        value = getattr(node.raws, key)
        if value is not None:
            raws[RAW_NAMES.get(key, key)] = (
                {'raw': value.raw, 'value': value.value} if isinstance(value, RawValue) else value
            )
    ans['raws'] = raws
    ans['toString'] = to_string(node)
    if isinstance(node, Container) and node.nodes is not None:
        ans['nodes'] = [dump(child) for child in node.nodes]
    return ans


def to_code_points(tree: dict[str, object], offsets: list[int]) -> dict[str, object]:
    """Convert the UTF-16 offsets of a PostCSS tree to Python indexes."""
    ans = dict(tree)
    for key in ('start', 'end'):
        value = ans[key]
        if isinstance(value, int):
            ans[key] = offsets[value] if value < len(offsets) else value - len(offsets) + offsets[-1] + 1
    nodes = ans.get('nodes')
    if isinstance(nodes, list):
        ans['nodes'] = [to_code_points(child, offsets) for child in nodes]
    return ans


# A tree, or the reason, offset and end of an error
type Outcome = dict[str, object] | tuple[str, int, int | None]


def without_bom(css: str) -> str:
    """What PostCSS's Input parses: the text without a byte order mark (U+FEFF or U+FFFE) at its start."""
    return css[1:] if css[:1] in ('\ufeff', '\ufffe') else css


def parsed(css: str) -> Outcome:
    """Our result in the form of the oracle's, with Python offsets."""
    try:
        return dump(Parser(without_bom(css)).parse())
    except CssSyntaxError as err:
        return err.reason, err.offset, err.end


def expected_result(oracle: Stylelint, css: str, safe: bool = False) -> Outcome:
    offsets = utf16_offsets(without_bom(css))
    result = oracle.parse(css, safe=safe)
    if 'error' in result:
        e = result['error']
        end = e['end']
        return e['reason'], offsets[e['offset']], None if end is None else offsets[end]
    assert 'tree' in result
    tree = to_code_points(result['tree'], offsets)
    printed = tree['toString']
    if without_bom(css) != css and isinstance(printed, str):
        tree['toString'] = printed.removeprefix('\ufeff')  # PostCSS prints a root with the mark it had
    return tree


def check(oracle: Stylelint, css: str) -> None:
    ours, expected = parsed(css), expected_result(oracle, css)
    if RE_ESCAPED_ASTRAL.search(css) and isinstance(ours, tuple) and isinstance(expected, tuple):
        # JavaScript's backslash escape takes one UTF-16 code unit: half a character outside the BMP, which the
        # message of an "Unknown word" error then holds, and where its end is. Python strings cannot split one.
        ours, expected = ours[0].split(' ', 2)[:2], expected[0].split(' ', 2)[:2]
    assert ours == expected
    assert dump(SafeParser(without_bom(css)).parse()) == expected_result(oracle, css, safe=True)


@pytest.mark.parametrize(
    'css',
    [
        '',
        'a{b:c;;} /*x*/ @m x{d:e}',
        'a{',
        'a{b:c}}',
        'a{b:c d:e}',
        'a{b::c}',
        '@{}',
        'a{b:url(x;y)}',
        'a{b:url( "x" )}',
        'a{b:"x}',
        'a{b:c} /* x',
        'a{b:(c}',
        'a { color : red ! important ; }',
        'a{b:c!IMPORTANT;d:e !ie}',
        'a{--x:{y:z};}',
        'a{_b:c;*d:e}',
        'a{filter:progid:DXImageTransform.Microsoft.gradient(x=1)}',
        'a/*c*/b{c/**/d:e/*f*/}',
        '@media x;;@import "y"',
        'a{};b{};',
        'a{b:c}\n;\n',
        'x\\:y{a\\:b:c}',
        '\\61 x{}',
        ':root{--a: }',
        'a{b: c \t\n}',
        '@media (a:b) { @c d; e{f:g} }',
        'a{:b}',
        '@ x{}',
    ],
)
def test_examples(oracle: Stylelint, css: str) -> None:
    check(oracle, css)


@given(css_text)
def test_random(oracle: Stylelint, css: str) -> None:
    assume(not RE_SURROGATE_PAIR.search(css))  # a high and a low surrogate are one character in JavaScript
    check(oracle, css)
