# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""removal_edit() with a cache, which prints each block once, against printing the block without the node."""

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss import LintRule, lint
from kgcss.linter._engine import LCP_BLOCK, _CommonPrefixes, _Removals, removal_edit
from kgcss.linter._postcss import Container, Node, Root, parse

from .strategies import css_text, lint_css

# Blocks where removing a child changes the semicolons or raws of others, with comments, custom properties and
# childless at-rules at the ends, and repeated text, where the narrowed edit lies far from the child
PARTS = (
    'a:b', 'a:b;', 'a: b ;', ' A:b;', 'c:d', '--x:y', '--x:{y}', '@m;', '@m x', '@m{a:b}', 'r{a:b}', 'r{a:b;}',
    '/**/', '/* c */', ' ', '\n', ';', 'a:b !important', '<', '<!--', 'a:b;a:b;a:b;', 'r{}', '@m{}', 'q{a:b',
)  # fmt: skip
blocks = st.lists(st.sampled_from(PARTS), max_size=12).map(''.join)
sheets = st.one_of(
    blocks,
    blocks.map(lambda b: f'x{{{b}}}'),
    st.tuples(blocks, blocks).map(lambda t: f'{t[0]} @media x{{{t[1]}}}'),
    blocks.map(lambda b: f'x{{{b}'),
    css_text,
    lint_css,
)


def children(root: Root) -> list[Node]:
    ans: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, Container) and node.nodes:
            ans.extend(node.nodes)
            stack.extend(node.nodes)
    return ans


def check(css: str) -> int:
    """The number of nodes whose edit the cache computed without printing."""
    root, _ = parse(css)
    cache: dict[object, object] = {}
    fast = 0
    for node in children(root):
        expected = removal_edit(css, node)
        assert removal_edit(css, node, cache) == expected, (css, node)
        parent = node.parent
        assert parent is not None
        if isinstance(cache.get(('removal', id(parent))), _Removals):
            fast += 1
    return fast


@pytest.mark.parametrize(
    'css',
    [
        'a{b:c;b:c;b:c}',
        'a{b:c;b:c;b:c;}',
        'a{b:c; /* x */ b:d /* y */}',
        'a{--x:y;b:c}',
        'a{b:c;--x:y}',
        'a{@m;b:c}',
        'a{b:c;@m}',
        'b:c; b:d',
        '/* x */ b:c; b:d',
        'b:c',
        'a{b:c}',
        'a{b:c;b:c',
        'a{x:y b:c b:c}',
        'a{b:c;b:<}',
        '@m{a{b:c;}b:c;b:d}',
        '/*',
        'a:b;/*',
    ],
)
def test_examples(css: str) -> None:
    check(css)


@given(sheets)
def test_random(css: str) -> None:
    check(css)


def naive_lcp(a: str, b: str, i: int, j: int) -> int:
    n = 0
    while i + n < len(a) and j + n < len(b) and a[i + n] == b[j + n]:
        n += 1
    return n


# Periodic texts with a few changes, longer than a block, so that the queries skip blocks and use those of
# earlier queries
periodic = st.tuples(
    st.text(alphabet='ab', min_size=1, max_size=5),
    st.integers(1, 3 * LCP_BLOCK),
    st.lists(st.tuples(st.integers(0, 4 * LCP_BLOCK), st.sampled_from('abc')), max_size=4),
)


def make_text(unit: str, repeat: int, changes: list[tuple[int, str]]) -> str:
    chars = list((unit * repeat)[: 4 * LCP_BLOCK])
    for pos, c in changes:
        if pos < len(chars):
            chars[pos] = c
    return ''.join(chars)


@given(periodic, periodic, st.lists(st.tuples(st.integers(0, 4 * LCP_BLOCK), st.integers(0, 4 * LCP_BLOCK))))
def test_common_prefixes(
    a: tuple[str, int, list[tuple[int, str]]],
    b: tuple[str, int, list[tuple[int, str]]],
    queries: list[tuple[int, int]],
) -> None:
    ta, tb = make_text(*a), make_text(*b)
    for x, y in ((ta, tb), (ta, ta)):
        prefixes = _CommonPrefixes(x, y)
        for i, j in [*queries, (0, 0), (len(x), 0), (0, len(y))]:
            i, j = min(i, len(x)), min(j, len(y))
            assert prefixes.lcp(i, j) == naive_lcp(x, y, i, j), (x, y, i, j)


def test_strategy_takes_the_fast_path() -> None:
    # The cached edits must be most of those of the random sheets, else the comparison tests little
    fast = total = 0

    @given(blocks.map(lambda b: f'x{{{b}}}'))
    def count(css: str) -> None:
        nonlocal fast, total
        fast += check(css)
        total += len(children(parse(css)[0]))

    count()
    assert fast > total / 2, (fast, total)


# Not with missed semicolons: a block that does not print as its text is printed whole in every edit, as
# stylelint computes them, so their size is quadratic
@pytest.mark.parametrize('block', ['b:c;', 'b:c; d:e;', 'b:c /* x */;', 'b:c;/* x */'])
def test_many_duplicates_in_linear_time(block: str) -> None:
    def duration(n: int) -> float:
        css = 'a{x:y; ' + block * n + '}'
        start = time.perf_counter()
        problems = lint(css, disabled=frozenset(LintRule) - {LintRule.DECLARATION_BLOCK_NO_DUPLICATE_PROPERTIES})
        assert len(problems.problems) >= n - 1
        return time.perf_counter() - start

    duration(100)  # load the rule
    small, large = duration(2000), duration(16000)
    # Quadratic time would be 64 times as long; allow for noise
    assert large < 25 * small + 0.5, (small, large)
