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

    ANNOTATION_NO_UNKNOWN = 'annotation-no-unknown'
    AT_RULE_NO_UNKNOWN = 'at-rule-no-unknown'
    BLOCK_NO_EMPTY = 'block-no-empty'
    COLOR_NO_INVALID_HEX = 'color-no-invalid-hex'
    COMMENT_NO_EMPTY = 'comment-no-empty'
    CUSTOM_PROPERTY_NO_MISSING_VAR_FUNCTION = 'custom-property-no-missing-var-function'
    DECLARATION_BLOCK_NO_DUPLICATE_CUSTOM_PROPERTIES = 'declaration-block-no-duplicate-custom-properties'
    DECLARATION_BLOCK_NO_SHORTHAND_PROPERTY_OVERRIDES = 'declaration-block-no-shorthand-property-overrides'
    FONT_FAMILY_NO_DUPLICATE_NAMES = 'font-family-no-duplicate-names'
    FONT_FAMILY_NO_MISSING_GENERIC_FAMILY_KEYWORD = 'font-family-no-missing-generic-family-keyword'
    FUNCTION_LINEAR_GRADIENT_NO_NONSTANDARD_DIRECTION = 'function-linear-gradient-no-nonstandard-direction'
    FUNCTION_NO_UNKNOWN = 'function-no-unknown'
    KEYFRAME_DECLARATION_NO_IMPORTANT = 'keyframe-declaration-no-important'
    NAMED_GRID_AREAS_NO_INVALID = 'named-grid-areas-no-invalid'
    NO_DUPLICATE_AT_IMPORT_RULES = 'no-duplicate-at-import-rules'
    NO_EMPTY_SOURCE = 'no-empty-source'
    NO_EXTRA_SEMICOLONS = 'no-extra-semicolons'
    NO_INVALID_DOUBLE_SLASH_COMMENTS = 'no-invalid-double-slash-comments'
    NO_INVALID_POSITION_AT_IMPORT_RULE = 'no-invalid-position-at-import-rule'
    PROPERTY_NO_UNKNOWN = 'property-no-unknown'
    STRING_NO_NEWLINE = 'string-no-newline'


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


# The rules of @stylistic/stylelint-plugin
_STYLISTIC: Final = frozenset({LintRule.NO_EXTRA_SEMICOLONS})
_FIXABLE: Final = frozenset({LintRule.NO_EXTRA_SEMICOLONS})
_DESCRIPTIONS: Final = {
    LintRule.FUNCTION_NO_UNKNOWN: 'Disallow unknown functions',
    LintRule.STRING_NO_NEWLINE: 'Disallow invalid newlines within strings',
    LintRule.NO_DUPLICATE_AT_IMPORT_RULES: 'Disallow duplicate @import rules',
    LintRule.NAMED_GRID_AREAS_NO_INVALID: 'Disallow invalid named grid areas',
    LintRule.FUNCTION_LINEAR_GRADIENT_NO_NONSTANDARD_DIRECTION: (
        'Disallow non-standard direction values for linear gradient functions'
    ),
    LintRule.FONT_FAMILY_NO_MISSING_GENERIC_FAMILY_KEYWORD: (
        'Disallow a missing generic family keyword within font families'
    ),
    LintRule.FONT_FAMILY_NO_DUPLICATE_NAMES: 'Disallow duplicate names within font families',
    LintRule.CUSTOM_PROPERTY_NO_MISSING_VAR_FUNCTION: 'Disallow missing var function for custom properties',
    LintRule.COLOR_NO_INVALID_HEX: 'Disallow invalid hex colors',
    LintRule.ANNOTATION_NO_UNKNOWN: 'Disallow unknown annotations',
    LintRule.AT_RULE_NO_UNKNOWN: 'Disallow unknown at-rules',
    LintRule.BLOCK_NO_EMPTY: 'Disallow empty blocks',
    LintRule.COMMENT_NO_EMPTY: 'Disallow empty comments',
    LintRule.DECLARATION_BLOCK_NO_DUPLICATE_CUSTOM_PROPERTIES: (
        'Disallow duplicate custom properties within declaration blocks'
    ),
    LintRule.DECLARATION_BLOCK_NO_SHORTHAND_PROPERTY_OVERRIDES: (
        'Disallow shorthand properties that override related longhand properties'
    ),
    LintRule.KEYFRAME_DECLARATION_NO_IMPORTANT: 'Disallow invalid !important within keyframe declarations',
    LintRule.NO_EMPTY_SOURCE: 'Disallow empty sources',
    LintRule.NO_EXTRA_SEMICOLONS: 'Disallow extra semicolons',
    LintRule.NO_INVALID_DOUBLE_SLASH_COMMENTS: (
        'Disallow double-slash comments (//...) as they are not supported by CSS'
    ),
    LintRule.NO_INVALID_POSITION_AT_IMPORT_RULE: 'Disallow invalid position @import rules',
    LintRule.PROPERTY_NO_UNKNOWN: 'Disallow unknown properties',
}


def _info(rule: LintRule) -> LintRuleInfo:
    module = 'kgcss.linter._rules.' + rule.value.replace('-', '_')
    if rule in _STYLISTIC:
        url = f'https://stylelint-stylistic.github.io/rules/{rule.value}'
        name = '@stylistic/' + rule.value
    else:
        url = f'https://stylelint.io/user-guide/rules/{rule.value}'
        name = rule.value
    return LintRuleInfo(rule, _DESCRIPTIONS[rule], url, rule in _FIXABLE, name, module)


#: Every rule, in the order of LintRule
LINT_RULES: Final[Mapping[LintRule, LintRuleInfo]] = MappingProxyType({rule: _info(rule) for rule in LintRule})


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
    options: Mapping[LintRule, Mapping[str, object]] | None = None,
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

    options gives rules the secondary options of their stylelint
    documentation, under the same names: ``{LintRule.SELECTOR_TYPE_NO_UNKNOWN:
    {'ignore': ['custom-elements']}}``. Where stylelint takes a regular
    expression, a compiled pattern or a string such as ``'/^my-/i'`` works.
    An option that a rule does not support raises ValueError.
    """
    from kgcss.linter._engine import lint as run

    return run(source, frozenset(LINT_RULES) - frozenset(disabled), options or {}, fix=fix)


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
