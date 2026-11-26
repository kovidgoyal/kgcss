# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The extracted stylelint test cases agree with the real stylelint.

This checks tools/extract_stylelint_tests.py and the oracle, not kgcss: every
case in tests/stylelint-rules is linted by stylelint, and checked the way
stylelint's own test harness checks it. Skipped unless the oracle is
installed (see tests/stylelint_oracle.py).
"""

from collections.abc import Iterator

import pytest

from .stylelint_fixtures import Case, cases
from .stylelint_oracle import Stylelint, require_oracle

require_oracle()

pytestmark = pytest.mark.slow

CASES = tuple(cases())


@pytest.fixture(scope='module')
def stylelint() -> Iterator[Stylelint]:
    with Stylelint() as s:
        yield s


def test_every_rule_has_cases() -> None:
    assert len({c.rule for c in CASES}) == 32


@pytest.mark.parametrize('case', CASES, ids=[c.id for c in CASES])
def test_case(stylelint: Stylelint, case: Case) -> None:
    rules = {case.rule: case.config}
    result = stylelint.lint(case.code, rules, compute_edit_info=case.compute_edit_info)
    assert result['parseErrors'] == []
    actual = [{'text': text} for text in result['invalidOptionWarnings']] + [dict(w) for w in result['warnings']]
    assert len(actual) == len(case.warnings), actual
    for got, expected in zip(actual, case.warnings, strict=True):
        if 'message' in expected:
            assert got['text'] == expected['message']
        for key in ('line', 'column', 'endLine', 'endColumn', 'fix'):
            if key in got and key in expected:
                assert got[key] == expected[key], key
    if case.fixed is not None:
        fixed = stylelint.lint(case.code, rules, fix=True)
        assert fixed['code'] == case.fixed
