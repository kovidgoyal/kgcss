# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The templates of the messages of the linter: every message is template.format(*args).

They are defined here, and only here, so that LINT_RULES can list them
without importing the rule modules, which import their templates from here.
The templates are the English of stylelint (and PostCSS) with the values
replaced by explicit indices into the args of the problem, so that
translations can reorder them. Literal braces are doubled.

This module must not import kgcss.linter: kgcss.linter imports it.
"""

from typing import Final

# Syntax errors, as PostCSS words them
UNCLOSED_BLOCK: Final = 'Unclosed block'
UNCLOSED_STRING: Final = 'Unclosed string'
UNCLOSED_COMMENT: Final = 'Unclosed comment'
UNCLOSED_BRACKET: Final = 'Unclosed bracket'
UNKNOWN_WORD: Final = 'Unknown word {0}'
UNEXPECTED_CLOSE: Final = 'Unexpected }}'
MISSED_SEMICOLON: Final = 'Missed semicolon'
DOUBLE_COLON: Final = 'Double colon'
AT_RULE_WITHOUT_NAME: Final = 'At-rule without name'

# The rules, in the order of LintRule
UNKNOWN_ANNOTATION: Final = 'Unknown annotation "{0}"'
UNKNOWN_AT_RULE: Final = 'Unknown at-rule "{0}"'
EMPTY_BLOCK: Final = 'Empty block'
INVALID_HEX_COLOR: Final = 'Invalid hex color "{0}"'
EMPTY_COMMENT: Final = 'Empty comment'
MISSING_VAR_FUNCTION: Final = 'Missing var function for "{0}"'
DUPLICATE_CUSTOM_PROPERTY: Final = 'Duplicate custom property "{0}"'
DUPLICATE_PROPERTY: Final = 'Duplicate property "{0}"'
# args: (shorthand, overridden longhand)
OVERRIDDEN_PROPERTY: Final = 'Overridden property "{1}" by shorthand "{0}"'
DUPLICATE_FONT_FAMILY_NAME: Final = 'Duplicate font-family name "{0}"'
MISSING_GENERIC_FONT_FAMILY: Final = 'Missing generic font family'
SPACE_BEFORE_OPERATOR: Final = 'Expected single space before "{0}" operator'
SPACE_AFTER_OPERATOR: Final = 'Expected single space after "{0}" operator'
NONSTANDARD_DIRECTION: Final = 'Nonstandard direction'
UNKNOWN_FUNCTION: Final = 'Unknown function "{0}"'
DUPLICATE_KEYFRAME_SELECTOR: Final = 'Duplicate keyframe selector "{0}"'
DISALLOWED_IMPORTANT: Final = 'Disallowed !important'
UNKNOWN_MEDIA_FEATURE_NAME: Final = 'Unknown media feature name "{0}"'
EXPECTED_CELL_TOKEN: Final = 'Expected cell token within string'
EXPECTED_SAME_CELL_COUNT: Final = 'Expected same number of cell tokens in each string'
EXPECTED_RECTANGLE: Final = 'Expected single filled-in rectangle for "{0}"'
# args: (selector, earlier selector, line of the earlier selector, selector resolved, earlier selector
# resolved); a resolved selector is only in the message when the selector is nested, and is '' otherwise
DESCENDING_SPECIFICITY: Final = 'Expected selector "{0}" to come before selector "{1}", at line {2}'
DESCENDING_SPECIFICITY_RESOLVED: Final = 'Expected selector "{0}" ("{3}") to come before selector "{1}", at line {2}'
DESCENDING_SPECIFICITY_RESOLVED_BEFORE: Final = (
    'Expected selector "{0}" to come before selector "{1}" ("{4}"), at line {2}'
)
DESCENDING_SPECIFICITY_RESOLVED_BOTH: Final = (
    'Expected selector "{0}" ("{3}") to come before selector "{1}" ("{4}"), at line {2}'
)
DUPLICATE_IMPORT_RULE: Final = 'Duplicate @import rule {0}'
# args: (selector, line of its first use)
DUPLICATE_SELECTOR: Final = 'Duplicate selector "{0}", first used at line {1}'
EMPTY_SOURCE: Final = 'Empty source'
EXTRA_SEMICOLON: Final = 'Unexpected extra semicolon'
INVALID_DOUBLE_SLASH_COMMENT: Final = 'Invalid double-slash CSS comment'
INVALID_POSITION_IMPORT_RULE: Final = 'Invalid position for @import rule'
IRREGULAR_WHITESPACE: Final = 'Irregular whitespace'
UNKNOWN_PROPERTY: Final = 'Unknown property "{0}"'
UNKNOWN_PSEUDO_CLASS: Final = 'Unknown pseudo-class selector "{0}"'
UNKNOWN_PSEUDO_ELEMENT: Final = 'Unknown pseudo-element selector "{0}"'
UNKNOWN_TYPE_SELECTOR: Final = 'Unknown type selector "{0}"'
NEWLINE_IN_STRING: Final = 'Newline in string'
UNKNOWN_UNIT: Final = 'Unknown unit "{0}"'

#: The templates of each rule, by the name of the rule (LintRule.value)
RULE_MESSAGES: Final[dict[str, tuple[str, ...]]] = {
    'annotation-no-unknown': (UNKNOWN_ANNOTATION,),
    'at-rule-no-unknown': (UNKNOWN_AT_RULE,),
    'block-no-empty': (EMPTY_BLOCK,),
    'color-no-invalid-hex': (INVALID_HEX_COLOR,),
    'comment-no-empty': (EMPTY_COMMENT,),
    'custom-property-no-missing-var-function': (MISSING_VAR_FUNCTION,),
    'declaration-block-no-duplicate-custom-properties': (DUPLICATE_CUSTOM_PROPERTY,),
    'declaration-block-no-duplicate-properties': (DUPLICATE_PROPERTY,),
    'declaration-block-no-shorthand-property-overrides': (OVERRIDDEN_PROPERTY,),
    'font-family-no-duplicate-names': (DUPLICATE_FONT_FAMILY_NAME,),
    'font-family-no-missing-generic-family-keyword': (MISSING_GENERIC_FONT_FAMILY,),
    'function-calc-no-unspaced-operator': (SPACE_BEFORE_OPERATOR, SPACE_AFTER_OPERATOR),
    'function-linear-gradient-no-nonstandard-direction': (NONSTANDARD_DIRECTION,),
    'function-no-unknown': (UNKNOWN_FUNCTION,),
    'keyframe-block-no-duplicate-selectors': (DUPLICATE_KEYFRAME_SELECTOR,),
    'keyframe-declaration-no-important': (DISALLOWED_IMPORTANT,),
    'media-feature-name-no-unknown': (UNKNOWN_MEDIA_FEATURE_NAME,),
    'named-grid-areas-no-invalid': (EXPECTED_CELL_TOKEN, EXPECTED_SAME_CELL_COUNT, EXPECTED_RECTANGLE),
    'no-descending-specificity': (
        DESCENDING_SPECIFICITY,
        DESCENDING_SPECIFICITY_RESOLVED,
        DESCENDING_SPECIFICITY_RESOLVED_BEFORE,
        DESCENDING_SPECIFICITY_RESOLVED_BOTH,
    ),
    'no-duplicate-at-import-rules': (DUPLICATE_IMPORT_RULE,),
    'no-duplicate-selectors': (DUPLICATE_SELECTOR,),
    'no-empty-source': (EMPTY_SOURCE,),
    'no-extra-semicolons': (EXTRA_SEMICOLON,),
    'no-invalid-double-slash-comments': (INVALID_DOUBLE_SLASH_COMMENT,),
    'no-invalid-position-at-import-rule': (INVALID_POSITION_IMPORT_RULE,),
    'no-irregular-whitespace': (IRREGULAR_WHITESPACE,),
    'property-no-unknown': (UNKNOWN_PROPERTY,),
    'selector-pseudo-class-no-unknown': (UNKNOWN_PSEUDO_CLASS,),
    'selector-pseudo-element-no-unknown': (UNKNOWN_PSEUDO_ELEMENT,),
    'selector-type-no-unknown': (UNKNOWN_TYPE_SELECTOR,),
    'string-no-newline': (NEWLINE_IN_STRING,),
    'unit-no-unknown': (UNKNOWN_UNIT,),
}
