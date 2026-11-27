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
from .stylelint_oracle import RE_ESCAPED_ASTRAL, RE_SURROGATE_PAIR, Stylelint, StylelintWarning, require_oracle
from .test_linter_stylelint_cases import IGNORED_MESSAGES

require_oracle()

RE_ESCAPED_ASTRAL_HEX: Final = re.compile(r'\\0*[1-9a-fA-F][0-9a-fA-F]{4,5}')
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
    ans: ExpectedWarning = {'message': w['text']}
    for key in ('line', 'column', 'endLine', 'endColumn'):
        if w.get(key) is not None:
            ans[key] = w[key]
    return ans


def check(oracle: Stylelint, css: str) -> None:
    crash = ''
    try:
        result = oracle.lint(css, CONFIG)
    except RuntimeError as err:
        crash = str(err)
        result = None
    if result is None:
        # stylelint crashes on some input, such as keyframe-declaration-no-important on "! /**/ important", and
        # no-irregular-whitespace on a selector with irregular whitespace that postcss-selector-parser rejects
        # ("a\f: a {}"), where kgcss skips the selector
        assert 'TypeError' in crash or 'AssertionError' in crash or 'postcss-selector-parser' in crash
        reject()
        return
    if any(BAD_DISABLE.search(w['text']) for w in result['warnings']):
        reject()
    expected = [expected_warning(w) for w in result['warnings'] if not w['text'].startswith(IGNORED_MESSAGES)]
    ours = lint(css)
    actual = as_stylelint(css, ours)
    for w in actual:
        w.pop('fix', None)  # stylelint gives no edit info unless asked
    if len(expected) == 1 and 'line' not in expected[0]:
        # PostCSS reports "Unknown word" with no position when the word is whitespace; kgcss at offset 0
        assert [w['message'] for w in actual] == [expected[0]['message']]
        return
    assert sorted(actual, key=sort_key) == sorted(expected, key=sort_key)
    # stylelint prints the fixed tree, which escapes <!-- and </style, and writes a U+FFFE mark as U+FEFF
    if not ours.syntax_problems and '<' not in css and not css.startswith('\ufffe'):
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
    assume(not RE_ESCAPED_ASTRAL.search(css))  # see test_linter_postcss: JavaScript splits such a character
    # A CSS escape of a character outside the BMP (\\1f600): stylelint adds the JavaScript length of the
    # unescaped name (2 UTF-16 code units) to a position in the text, where kgcss adds its length (1)
    assume(not RE_ESCAPED_ASTRAL_HEX.search(css))
    check(oracle, css)
