# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss.linter compared with stylelint on random text, all rules at once.

The warnings must be the same, and so must the result of fixing, where the
text has no syntax error. Skipped unless the stylelint oracle is installed
(see tests/stylelint_oracle.py).
"""

import re
from collections.abc import Iterator
from typing import Final

import pytest
from hypothesis import assume, given, reject
from hypothesis import strategies as st

from kgcss.linter import LINT_RULES, lint

from .linter_compare import as_stylelint, sort_key
from .strategies import css_text, lint_css
from .stylelint_fixtures import ExpectedWarning
from .stylelint_oracle import Stylelint, StylelintWarning, require_oracle
from .test_linter_postcss import RE_SURROGATE_PAIR
from .test_linter_stylelint_cases import IGNORED_MESSAGES

require_oracle()

CONFIG: Final = {info.stylelint_name: True for info in LINT_RULES.values()}
# stylelint reports disable comments that make no sense as a CssSyntaxError, and lints nothing; kgcss ignores them
BAD_DISABLE: Final = re.compile(
    r'(have already been disabled|has already been disabled|has not been disabled|No rules have been disabled)'
    r' \(CssSyntaxError\)$'
)


@pytest.fixture(scope='module')
def oracle() -> Iterator[Stylelint]:
    with Stylelint() as s:
        yield s


def expected_warning(w: StylelintWarning) -> ExpectedWarning:
    ans: ExpectedWarning = {'message': w['text'], 'line': w['line'], 'column': w['column']}
    if 'endLine' in w:
        ans['endLine'] = w['endLine']
    if 'endColumn' in w:
        ans['endColumn'] = w['endColumn']
    return ans


def check(oracle: Stylelint, css: str) -> None:
    result = oracle.lint(css, CONFIG)
    if any(BAD_DISABLE.search(w['text']) for w in result['warnings']):
        reject()
    expected = [expected_warning(w) for w in result['warnings'] if not w['text'].startswith(IGNORED_MESSAGES)]
    ours = lint(css)
    actual = as_stylelint(css, ours)
    for w in actual:
        w.pop('fix', None)  # stylelint gives no edit info unless asked
    assert sorted(actual, key=sort_key) == sorted(expected, key=sort_key)
    if not ours.syntax_problems and '<' not in css:  # stylelint's print escapes <!-- and </style
        fixed = oracle.lint(css, CONFIG, fix=True)['code']
        ours_fixed = lint(css, fix=True).fixed
        assert (css if ours_fixed is None else ours_fixed) == fixed


@pytest.mark.parametrize(
    'css',
    [
        '',
        'a{}',
        'a{b:c;;} ;',
        '@foo{} @media x{a{}}',
        'a{\n/* stylelint-disable-line */}',
        '/* stylelint-disable */\na{}\n/* stylelint-enable */\nb{}',
        '\ufeff;a{}',
        'a{b:c};;\n;',
    ],
)
def test_examples(oracle: Stylelint, css: str) -> None:
    check(oracle, css)


@given(st.one_of(lint_css, css_text))
def test_random(oracle: Stylelint, css: str) -> None:
    assume(not RE_SURROGATE_PAIR.search(css))  # a high and a low surrogate are one character in JavaScript
    check(oracle, css)
