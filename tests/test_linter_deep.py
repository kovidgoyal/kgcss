# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""lint() on deeply nested text: it must not raise, and must not take time out of proportion.

stylelint throws on much of this text (postcss-selector-parser cannot copy
selectors nested beyond 256 levels, the @csstools parsers stop at 512, and
deep recursion exhausts the stack of Node), where kgcss skips the part it
cannot handle, as for other input on which stylelint throws.
"""

import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss import LintRule, lint
from kgcss.linter._engine import Context, Report, Source
from kgcss.linter._postcss import Declaration, parse
from kgcss.linter._rules import function_linear_gradient_no_nonstandard_direction as gradient
from kgcss.linter._selector_helpers import at_rule_context
from kgcss.linter._value_parser import parse as parse_value
from kgcss.linter._value_parser import stringify


def shapes(n: int) -> dict[str, str]:
    return {
        'rules': 'a{b:c;' * n + '}' * n,
        'rules-amp': '.a{&.b{' * n + 'c:d' + '}}' * n,
        'rules-list': 'a,b{c:d;' * n + '}' * n,
        'rules-unclosed': 'a{b:c;' * n,
        'media': '@media x{' * n + 'a{b:c}' + '}' * n,
        'media-params': '@media ' + '(' * n + 'x' + ')' * n + '{a{b:c}}',
        'supports': '@supports ' + 'not (' * n + 'a:b' + ')' * n + '{a{b:c}}',
        'pseudo': ':is(' * n + 'a' + ')' * n + '{b:c}',
        'not': ':not(' * n + '.a' + ')' * n + '{b:c}',
        'pseudo-unclosed': ':is(' * n + 'a{b:c}',
        'value': 'a{b:' + 'f(' * n + 'x' + ')' * n + '}',
        'calc': 'a{b:' + 'calc(' * n + '1px + 2px' + ')' * n + '}',
        'calc-op': 'a{b:calc(' + '(1px+' * n + '1' + ')' * n + ')}',
        'parens': 'a{b:' + '(' * n + 'x' + ')' * n + '}',
        'brackets': 'a{b:' + '[' * n + 'x' + ']' * n + '}',
        'value-unclosed': 'a{b:' + 'f(' * n + '}',
        'var': 'a{--x:' + 'var(' * n + '--y' + ')' * n + ';b:var(--x)}',
        'custom-braces': 'a{--x:' + '{' * n + '}' * n + '}',
        'linear': 'a{background:' + 'linear-gradient(' * n + 'top, red, blue' + ')' * n + '}',
        'url': 'a{b:' + 'url(' * n + '}',
        'keyframes': '@keyframes x{' + '0%{' * n + '}' * n + '}',
    }


@pytest.mark.parametrize('n', [130, 300, 600])
def test_no_exceptions(n: int) -> None:
    for kind, css in shapes(n).items():
        for fix in (False, True):
            try:
                lint(css, fix=fix)
            except Exception as e:
                raise AssertionError(f'{kind}@{n} fix={fix}') from e


@pytest.mark.slow
def test_very_deep() -> None:
    for kind, css in shapes(20_000).items():
        if kind == 'linear':
            css = shapes(1000)[kind]  # with a problem reported for every pair of nested gradients
        start = time.monotonic()
        lint(css)
        assert time.monotonic() - start < 60, kind


def test_value_parser_deep() -> None:
    value = 'f(' * 100_000 + 'x' + ')' * 100_000
    assert stringify(parse_value(value)) == value


def test_at_rule_context() -> None:
    root, _ = parse('@m{a{b{c{d:e}}} @n{f{g{}}}} h{i{}} @o{}' + 'x{' * 5000 + '}' * 5000)
    rules = list(root.walk_rules())
    cache: dict[object, object] = {}
    # Answers known for the outer rules first, and for the inner ones first
    for order in (rules, rules[::-1]):
        for rule in order:
            assert at_rule_context(rule, cache) is at_rule_context(rule)
        cache.clear()


def gradient_reports(value: str, nested: bool) -> list[Report]:
    css = f'a{{background:{value}}}'
    root, _ = parse(css)
    decl = next(iter(root.walk_decls()), None)
    if decl is None:  # such as with an unclosed #{, which makes the value a block
        return []
    assert isinstance(decl, Declaration)
    reports: list[Report] = []
    ctx = Context(LintRule.FUNCTION_LINEAR_GRADIENT_NO_NONSTANDARD_DIRECTION, Source(css), reports, {}, {})
    order, ends = gradient.functions_with_descendants(parse_value(decl.value))
    if nested:
        gradient.check_nested(decl, order, ends, ctx)
    else:
        for node in order:
            gradient.check_function(decl, node, ctx)
    return reports


PARTS = (
    'linear-gradient(', '-webkit-linear-gradient(', 'LINEAR-GRADIENT(', 'f(', '(', ')', ')', ', ', ',', ' ', 'top',
    'left', 'to top', 'to left left', 'bottom right', '45deg', '1.5turn', 'red', 'blue', 'var(--x)', 'in srgb',
    '"a,b"', "'", '/* c */', 'url(x)', '\\', '#{', '}', 'İ', 'ß',
)  # fmt: skip


@given(st.lists(st.sampled_from(PARTS), max_size=14).map(''.join))
def test_gradient_nested_is_check_function(value: str) -> None:
    if len(value.lower()) == len(value):
        assert gradient_reports(value, True) == gradient_reports(value, False)


@pytest.mark.parametrize(
    'value',
    [
        'linear-gradient(top, red, blue)',
        'linear-gradient(linear-gradient(top, red), left)',
        '-webkit-linear-gradient(linear-gradient(top, red), left)',
        'linear-gradient(-webkit-linear-gradient(to top, red), left)',
        'f(linear-gradient(45, red), linear-gradient(bottom, red',
        'linear-gradient(' * 20 + 'top, red' + ')' * 20,
        'linear-gradient(' * 20 + 'top, red',
    ],
)
def test_gradient_nested_examples(value: str) -> None:
    assert gradient_reports(value, True) == gradient_reports(value, False)
