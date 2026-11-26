# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss.linter against the test cases of stylelint's own rules (tests/stylelint-rules).

Each case is checked as stylelint's test harness checks it: the warnings'
messages and positions, their edit info where a case gives it, and the result
of fixing. Cases with configs that kgcss does not support are left out.

Columns and edit ranges are converted from UTF-16 code units, which stylelint
counts, to code points. Where the text has a syntax error, stylelint reports
only that, so only that is compared. Deliberate differences are listed in
KNOWN_DIFFERENCES and IGNORED_MESSAGES.
"""

from typing import Final

import pytest

from kgcss.linter import LINT_RULES, lint

from .linter_compare import as_stylelint, sort_key
from .stylelint_fixtures import Case, cases

# Warnings that are not about the stylesheet, which kgcss does not give
IGNORED_MESSAGES: Final = (
    # @stylistic/stylelint-plugin asks for a core rule to be turned on when it sees @charset
    'The "@stylistic/" rules pass over "@charset"',
)

# Cases where kgcss differs on purpose: case id -> why
KNOWN_DIFFERENCES: Final[dict[str, str]] = {
    'no-extra-semicolons-0-reject-12': (
        'kgcss fixes by editing the text, where stylelint prints the whole tree again, '
        'and PostCSS escapes <!-- as \\3c !--'
    ),
}


RULE_BY_NAME: Final = {info.stylelint_name: rule for rule, info in LINT_RULES.items()}


def supported(case: Case) -> bool:
    return case.rule in RULE_BY_NAME and case.config in (True, [True])


CASES: Final = tuple(c for c in cases() if supported(c))


@pytest.mark.parametrize(
    'case',
    [
        pytest.param(c, marks=pytest.mark.xfail(reason=KNOWN_DIFFERENCES[c.id], strict=True))
        if c.id in KNOWN_DIFFERENCES
        else c
        for c in CASES
    ],
    ids=[c.id for c in CASES],
)
def test_case(case: Case) -> None:
    rule = RULE_BY_NAME[case.rule]
    disabled = frozenset(LINT_RULES) - {rule}
    expected = sorted((w for w in case.warnings if not w.get('message', '').startswith(IGNORED_MESSAGES)), key=sort_key)
    actual = sorted(as_stylelint(case.code, lint(case.code, disabled=disabled)), key=sort_key)
    assert len(actual) == len(expected), actual
    for got, want in zip(actual, expected, strict=True):
        for key in ('message', 'line', 'column', 'endLine', 'endColumn'):
            if key in want:
                assert got.get(key) == want.get(key), (key, got, want)
        if 'fix' in want and case.compute_edit_info:
            assert got.get('fix') == want['fix']
    if case.fixed is not None:
        fixed = lint(case.code, disabled=disabled, fix=True).fixed
        assert (case.code if fixed is None else fixed) == case.fixed


def test_every_rule_has_cases() -> None:
    assert {RULE_BY_NAME[c.rule] for c in CASES} == set(LINT_RULES)
