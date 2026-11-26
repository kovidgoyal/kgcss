# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Ports of the helpers in stylelint's lib/utils that the rules share.

Each function names the stylelint helper it ports. JavaScript regular
expressions are translated with their ASCII-only \\w and \\d.
"""

import re
from collections.abc import Iterator
from typing import Final

from kgcss.linter._postcss import JS_WHITESPACE, AtRule, Comment, Container, Node, Rule

RE_VENDOR_PREFIX: Final = re.compile(r'^-[A-Za-z0-9_]+-')
RE_JS_WHITESPACE: Final = re.compile(f'[{re.escape(JS_WHITESPACE)}]')
CONFIGURATION_COMMANDS: Final = frozenset({'-disable', '-disable-line', '-disable-next-line', '-enable'})
RE_INTERPOLATION: Final = re.compile(r'@\{.+?\}|#\{.+?\}|\{.+?\}|\$\(.+?\)', re.DOTALL)


def vendor_prefix(name: str) -> str:
    """vendor.prefix(): '-webkit-' for '-webkit-foo', else ''."""
    m = RE_VENDOR_PREFIX.match(name)
    return m.group() if m else ''


def is_configuration_comment(node: Node) -> bool:
    """isConfigurationComment(): a /* stylelint-disable */ comment and its kin."""
    if not isinstance(node, Comment) or not node.text:
        return False
    command = RE_JS_WHITESPACE.split(node.text, maxsplit=1)[0]
    return command.replace('stylelint', '', 1) in CONFIGURATION_COMMANDS


def has_block(node: Node) -> bool:
    """hasBlock(): whether the rule or at-rule has a block, even an empty one."""
    return isinstance(node, Container) and node.nodes is not None


def rule_selector(rule: Rule) -> str:
    """getRuleSelector(): the selector as written, comments included."""
    raw = rule.raws.selector
    return raw.raw if raw is not None and raw.value == rule.selector else rule.selector


def at_rule_params(at_rule: AtRule) -> str:
    """getAtRuleParams(): the params as written, comments included."""
    raw = at_rule.raws.params
    return raw.raw if raw is not None and raw.value == at_rule.params else at_rule.params


def before_block_string(statement: Node, no_raw_before: bool = False) -> str:
    """beforeBlockString(): the text of a rule or at-rule up to its block."""
    raws = statement.raws
    result = '' if no_raw_before else raws.before or ''
    if isinstance(statement, Rule):
        result += rule_selector(statement)
    elif isinstance(statement, AtRule):
        result += f'@{statement.name}{raws.after_name or ""}{at_rule_params(statement)}'
    else:
        return ''
    return result + (raws.between or '')


def has_interpolation(text: str) -> bool:
    """hasInterpolation(): Less, SCSS, template or postcss-simple-vars interpolation."""
    return RE_INTERPOLATION.search(text) is not None


def is_standard_syntax_at_rule(at_rule: AtRule) -> bool:
    """isStandardSyntaxAtRule() of stylelint."""
    if at_rule.name.lower() == 'charset':
        return False
    if at_rule.nodes is None and at_rule.params == '':  # SCSS @content
        return False
    # A Less detached ruleset call: @detached-ruleset();
    return not (at_rule.nodes is None and at_rule.raws.after_name == '' and at_rule.params[:1] == '(')


def style_search(source: str, target: str, *, skip_function_arguments: bool = False) -> Iterator[int]:
    """The npm package style-search: where target is in source, outside comments and strings.

    As style-search does by default, it skips comments (block and //), strings
    and function names; with skip_function_arguments, also the arguments of
    functions. Only the options the rules use are ported, for a target of one
    character other than a quote or a parenthesis.
    """
    in_string = in_comment = in_line_comment = in_parens = in_function_args = False
    paren_count = 0
    quote = ''
    length = len(source)
    i = 0
    while i < length:
        c = source[i]
        prev = source[i - 1] if i > 0 else ''
        nxt = source[i + 1] if i + 1 < length else ''
        if not in_string and not in_comment and c == '/' and prev != '\\':
            if nxt == '*':
                in_comment = True
                i += 1
                continue
            if nxt == '/':
                in_comment = in_line_comment = True
                i += 1
                continue
        if in_comment:
            if not in_line_comment and c == '*' and prev != '\\' and nxt == '/' and prev != '/':
                in_comment = False
                i += 1
                continue
            if in_line_comment and c == '\n':
                in_comment = in_line_comment = False
            i += 1
            continue
        if not in_string and c in '"\'':
            if prev != '\\':
                quote = c
                in_string = True
            i += 1
            continue
        if in_string:
            if c == quote and prev != '\\':
                in_string = False
            i += 1
            continue
        if c == '(':
            paren_count += 1
            in_parens = True
            # /[a-zA-Z]/.test(source[i - 1]): at the start that is test(undefined), which is true
            if i == 0 or 'a' <= prev <= 'z' or 'A' <= prev <= 'Z':
                in_function_args = True
            i += 1
            continue
        if in_parens and c == ')':
            paren_count -= 1
            if paren_count == 0:
                in_parens = in_function_args = False
            i += 1
            continue
        if c == target and not is_function_name_at(source, i) and not (skip_function_arguments and in_function_args):
            yield i
        i += 1


RE_FUNCTION_NAME: Final = re.compile(r'[a-zA-Z]*\(')


def is_function_name_at(source: str, i: int) -> bool:
    return RE_FUNCTION_NAME.match(source, i) is not None
