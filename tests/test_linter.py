# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss.lint(): the API, fixing, disable comments and lazy loading.

What the rules report is checked against stylelint in
test_linter_stylelint_cases.py and test_linter_vs_stylelint.py.
"""

import importlib
import subprocess
import sys
from collections.abc import Collection

import pytest

from kgcss import LINT_RULES, LintProblem, LintRule, SyntaxProblem, SyntaxProblemKind, TextEdit, lint


def rules_of(css: str, disabled: Collection[LintRule] = ()) -> list[str]:
    return [p.rule.value for p in lint(css, disabled=disabled).problems]


def test_problem() -> None:
    (p,) = lint('a{}\n@foo  bar{b:c}').problems[:1]
    assert p == LintProblem(LintRule.BLOCK_NO_EMPTY, 'Empty block', (), 1, 3, 1, 2, 1, 4)
    (p,) = [p for p in lint('a{}\n@foo  bar{b:c}').problems if p.rule is LintRule.AT_RULE_NO_UNKNOWN]
    assert (p.message, p.args, p.pos, p.end, p.line, p.column, p.end_line, p.end_column) == (
        'Unknown at-rule "@foo"',
        ('@foo',),
        4,
        8,
        2,
        1,
        2,
        5,
    )
    assert p.fix is None


def test_order_and_disabled() -> None:
    css = '@x{}a{b:c;;}'
    assert rules_of(css) == ['at-rule-no-unknown', 'block-no-empty', 'no-extra-semicolons']
    assert rules_of(css, disabled=[LintRule.BLOCK_NO_EMPTY]) == ['at-rule-no-unknown', 'no-extra-semicolons']
    assert lint(css, disabled=list(LintRule)).problems == ()


def test_fix() -> None:
    css = 'a{b:c;;};\n;b{}'
    result = lint(css)
    assert [p.fix for p in result.problems if p.fix] == [TextEdit(6, 7, ''), TextEdit(8, 9, ''), TextEdit(10, 11, '')]
    fixed = lint(css, fix=True)
    assert fixed.fixed == 'a{b:c;}\nb{}'
    assert [p.rule for p in fixed.problems] == [LintRule.BLOCK_NO_EMPTY]  # what is left, in the fixed text
    assert fixed.problems[0].line == 2
    assert lint('a{b:c}', fix=True).fixed is None
    assert lint(';', fix=True).fixed == ''


def test_byte_order_mark() -> None:
    (p,) = lint('﻿;a{b:c}').problems
    # Offsets index the text, line and column are stylelint's, which does not count the mark
    assert (p.pos, p.line, p.column, p.fix) == (1, 1, 1, TextEdit(1, 2, ''))
    assert lint('﻿;a{b:c}', fix=True).fixed == '﻿a{b:c}'


def test_columns_are_code_points() -> None:
    (p,) = lint('a{content:"\U0001f600"}b{}').problems
    assert (p.pos, p.column) == (15, 16)  # stylelint counts UTF-16 code units: 17


def test_lines() -> None:
    (p,) = lint('a{b:c}\r\n\n  x{}').problems
    assert (p.line, p.column) == (3, 4)  # lines end at line feeds


@pytest.mark.parametrize(
    ('css', 'kind', 'message', 'pos', 'end'),
    [
        ('a{', SyntaxProblemKind.UNCLOSED_BLOCK, 'Unclosed block', 0, None),
        ('a{b:"c}', SyntaxProblemKind.UNCLOSED_STRING, 'Unclosed string', 4, None),
        ('a{} /* b', SyntaxProblemKind.UNCLOSED_COMMENT, 'Unclosed comment', 4, None),
        ('a{b:(c}', SyntaxProblemKind.UNCLOSED_BRACKET, 'Unclosed bracket', 4, 5),
        ('a b', SyntaxProblemKind.UNKNOWN_WORD, 'Unknown word a', 0, 1),
        ('a{}}', SyntaxProblemKind.UNEXPECTED_CLOSE, 'Unexpected }', 3, 4),
        ('a{b:c d:e}', SyntaxProblemKind.MISSED_SEMICOLON, 'Missed semicolon', 5, None),
        ('a{b::c}', SyntaxProblemKind.DOUBLE_COLON, 'Double colon', 4, 5),
        ('@ x{}', SyntaxProblemKind.AT_RULE_WITHOUT_NAME, 'At-rule without name', 0, 1),
    ],
)
def test_syntax_problems(css: str, kind: SyntaxProblemKind, message: str, pos: int, end: int | None) -> None:
    (sp,) = lint(css).syntax_problems
    assert (sp.kind, sp.message, sp.pos, sp.end) == (kind, message, pos, end)
    assert isinstance(sp, SyntaxProblem)


def test_rules_run_despite_syntax_problems() -> None:
    result = lint('a{} b{c:d;;')
    assert [sp.kind for sp in result.syntax_problems] == [SyntaxProblemKind.UNCLOSED_BLOCK]
    assert [p.rule for p in result.problems] == [LintRule.BLOCK_NO_EMPTY, LintRule.NO_EXTRA_SEMICOLONS]
    assert lint('a{} b{c:d;;', fix=True).fixed == 'a{} b{c:d;'


@pytest.mark.parametrize(
    ('css', 'rules'),
    [
        ('/* stylelint-disable */\na{}', []),
        ('/* stylelint-disable block-no-empty */\na{}@x{}', ['at-rule-no-unknown']),
        ('/* stylelint-disable */\na{}\n/* stylelint-enable */\nb{}', ['block-no-empty']),
        ('a{} /* stylelint-disable-line */\nb{}', ['block-no-empty']),
        ('/* stylelint-disable-next-line */\na{}\nb{}', ['block-no-empty']),
        ('a{b:c;;}', ['no-extra-semicolons']),
        ('a{b:c;;} /* stylelint-disable-line @stylistic/no-extra-semicolons */', []),
        ('a{b:c;;} /* stylelint-disable-line no-extra-semicolons -- kgcss name */', []),
        ('a /* stylelint-disable-line */ {}', []),
        ('/* stylelint-enable */a{}', ['block-no-empty']),  # makes no sense: stylelint stops, kgcss ignores it
    ],
)
def test_disable_comments(css: str, rules: list[str]) -> None:
    assert rules_of(css) == rules


def test_rules() -> None:
    assert list(LINT_RULES) == list(LintRule)
    for rule, info in LINT_RULES.items():
        assert info.rule is rule
        assert info.url.startswith('https://')
        assert info.url.endswith('/' + rule.value)
        assert info.stylelint_name.endswith(rule.value)
        assert callable(importlib.import_module(info.module).check)
    assert {rule for rule, info in LINT_RULES.items() if info.fixable} == {LintRule.NO_EXTRA_SEMICOLONS}


def test_modules_are_imported_when_used() -> None:
    code = """
import sys
import kgcss
from kgcss import LintRule, lint

def loaded(name):
    return name in sys.modules

assert not loaded('kgcss.linter._engine') and not loaded('kgcss.linter._postcss')
assert not loaded('kgcss._css_data')
lint('a{}', disabled=[LintRule.AT_RULE_NO_UNKNOWN])
assert loaded('kgcss.linter._rules.block_no_empty') and not loaded('kgcss.linter._rules.at_rule_no_unknown')
assert not loaded('kgcss._css_data')
lint('@foo{}')
assert loaded('kgcss._css_data')
"""
    subprocess.run([sys.executable, '-c', code], check=True)
