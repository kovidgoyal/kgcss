# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Linting stylesheets with the rules of stylelint.

lint() checks a stylesheet with rules that behave as stylelint's rules of the same names do (stylelint 18,
and @stylistic/stylelint-plugin for no-extra-semicolons). It parses as
stylelint does, with a port of the PostCSS parser, so that the rules see
what stylelint's rules see, and reports what PostCSS cannot parse as
SyntaxProblem.

The results are data rather than text: each LintProblem has its rule, the
arguments of its message, its position and, for rules that can fix what
they find, the edit that fixes it.

The rules are implemented in their own modules, imported the first time
they are used.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum, unique
from types import MappingProxyType
from typing import Final


@unique
class LintRule(StrEnum):
    """The rules: the value is the name stylelint gives the rule."""

    AT_RULE_NO_UNKNOWN = 'at-rule-no-unknown'
    BLOCK_NO_EMPTY = 'block-no-empty'
    NO_EMPTY_SOURCE = 'no-empty-source'
    NO_EXTRA_SEMICOLONS = 'no-extra-semicolons'


@dataclass(frozen=True, slots=True)
class LintRuleInfo:
    """What is known about a rule."""

    rule: LintRule
    description: str
    #: The documentation of the rule, on the stylelint (or @stylistic) website
    url: str
    #: Whether the rule can fix (some of) the problems it finds
    fixable: bool
    #: The name stylelint knows it by, which disable comments use ('@stylistic/...' for the plugin's rules)
    stylelint_name: str
    #: The module that implements it
    module: str


def _stylelint(rule: LintRule, description: str, fixable: bool = False) -> LintRuleInfo:
    module = 'kgcss.linter._rules.' + rule.value.replace('-', '_')
    return LintRuleInfo(
        rule, description, f'https://stylelint.io/user-guide/rules/{rule.value}', fixable, rule.value, module
    )


#: Every rule, in the order of LintRule
LINT_RULES: Final[Mapping[LintRule, LintRuleInfo]] = MappingProxyType(
    {
        info.rule: info
        for info in (
            _stylelint(LintRule.AT_RULE_NO_UNKNOWN, 'Disallow unknown at-rules'),
            _stylelint(LintRule.BLOCK_NO_EMPTY, 'Disallow empty blocks'),
            _stylelint(LintRule.NO_EMPTY_SOURCE, 'Disallow empty sources'),
            LintRuleInfo(
                LintRule.NO_EXTRA_SEMICOLONS,
                'Disallow extra semicolons',
                'https://stylelint-stylistic.github.io/rules/no-extra-semicolons',
                True,
                '@stylistic/no-extra-semicolons',
                'kgcss.linter._rules.no_extra_semicolons',
            ),
        )
    }
)


@dataclass(frozen=True, slots=True)
class TextEdit:
    """Replace source[pos:end] with text."""

    pos: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class LintProblem:
    """Something a rule found.

    pos and end index the linted text. line and column (1-based) and end_line
    and end_column (exclusive) are those stylelint reports. Lines are
    separated by line feeds and columns count code points; a byte order mark
    at the start is not counted.
    """

    rule: LintRule
    #: The message, as stylelint words it, without stylelint's " (rule-name)" suffix
    message: str
    #: The values in the message, such as the name of an unknown property
    args: tuple[str, ...]
    pos: int
    end: int
    line: int
    column: int
    end_line: int
    end_column: int
    #: How to fix the problem, if the rule can
    fix: TextEdit | None = None


@unique
class SyntaxProblemKind(Enum):
    """The kinds of CssSyntaxError that PostCSS reports."""

    UNCLOSED_BLOCK = 'Unclosed block'
    UNCLOSED_STRING = 'Unclosed string'
    UNCLOSED_COMMENT = 'Unclosed comment'
    UNCLOSED_BRACKET = 'Unclosed bracket'
    UNKNOWN_WORD = 'Unknown word'
    UNEXPECTED_CLOSE = 'Unexpected }'
    MISSED_SEMICOLON = 'Missed semicolon'
    DOUBLE_COLON = 'Double colon'
    AT_RULE_WITHOUT_NAME = 'At-rule without name'


@dataclass(frozen=True, slots=True)
class SyntaxProblem:
    """Where PostCSS, and so stylelint, cannot parse the text (stylelint's CssSyntaxError).

    PostCSS stops at the first such problem, and so does stylelint. The rules
    still run here, on the tree that postcss-safe-parser recovers, which is
    what stylelint lints with fix: 'lax'. Positions are as in LintProblem; end,
    end_line and end_column are None when PostCSS gives no end.
    """

    kind: SyntaxProblemKind
    #: The message, as PostCSS words it ('Unknown word foo')
    message: str
    pos: int
    end: int | None
    line: int
    column: int
    end_line: int | None
    end_column: int | None


@dataclass(frozen=True, slots=True)
class LintResult:
    """The result of lint()."""

    #: In order of position
    problems: tuple[LintProblem, ...]
    #: What PostCSS cannot parse: at most one problem, as PostCSS stops at the first
    syntax_problems: tuple[SyntaxProblem, ...]
    #: With fix=True, the fixed text, if anything was fixed
    fixed: str | None = None


def lint(
    source: str,
    *,
    disabled: Collection[LintRule] = (),
    fix: bool = False,
) -> LintResult:
    """Lint a stylesheet with every rule but those disabled.

    The declarations of a style attribute can be linted as they are: PostCSS
    parses declarations outside any rule, and the rules check them as a
    declaration block. Disable comments (``/* stylelint-disable */`` and the
    like) turn rules off as they do in stylelint; they name rules by
    LintRuleInfo.stylelint_name, or by the LintRule value. With fix=True,
    the problems that rules can fix are fixed, and the result has the fixed
    text and the problems left in it.
    """
    from kgcss.linter._engine import lint as run

    return run(source, frozenset(LINT_RULES) - frozenset(disabled), fix=fix)


__all__ = [
    'LINT_RULES',
    'LintProblem',
    'LintResult',
    'LintRule',
    'LintRuleInfo',
    'SyntaxProblem',
    'SyntaxProblemKind',
    'TextEdit',
    'lint',
]
