# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Disable comments, as stylelint's lib/assignDisabledRanges.mjs reads them.

/* stylelint-disable */, /* stylelint-disable a, b */, /* stylelint-enable */,
/* stylelint-disable-line */ and /* stylelint-disable-next-line */ turn rules
off for ranges of lines, in comments between nodes and in selectors, params
and values. Where stylelint stops at a command that makes no sense (enabling
what is not disabled, disabling twice), reporting it as a CssSyntaxError and
linting nothing, the command is ignored here, so that linting goes on.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from kgcss.linter._postcss import AtRule, Comment, Declaration, Node, Root, Rule, Tokenizer, js_trim
from kgcss.linter._utils import RE_JS_WHITESPACE, is_configuration_comment

ALL: Final = 'all'
DISABLE: Final = '-disable'
DISABLE_LINE: Final = '-disable-line'
DISABLE_NEXT_LINE: Final = '-disable-next-line'
ENABLE: Final = '-enable'
RE_DESCRIPTION: Final = re.compile(r'\s-{2,}\s')


@dataclass(slots=True)
class DisabledRange:
    start: int  # line
    end: int | None = None  # line, inclusive; None to the end of the text


class CommandError(Exception):
    pass


type Ranges = dict[str, list[DisabledRange]]
# line_of(node, index, end_index): the start and end lines of node.rangeBy({index, endIndex}), or of the node
type LineOf = Callable[[Node, int | None, int | None], tuple[int, int]]


def command_of(text: str) -> str:
    """extractConfigurationComment()."""
    return RE_JS_WHITESPACE.split(text, maxsplit=1)[0].replace('stylelint', '', 1)


def command_rules(command: str, text: str) -> list[str]:
    """getCommandRules(): the rules a command names, or all of them."""
    rules_text = RE_DESCRIPTION.split(text[len('stylelint' + command) :], maxsplit=1)[0]
    rules = [r.strip() for r in rules_text.strip().split(',') if r]
    return rules or [ALL]


class Assigner:
    def __init__(self) -> None:
        self.ranges: Ranges = {ALL: []}

    def disabled(self, rule: str) -> bool:
        """ruleIsDisabled()."""
        ranges = self.ranges.get(rule)
        return bool(ranges) and not ranges[-1].end

    def ensure(self, rule: str) -> list[DisabledRange]:
        if rule not in self.ranges:
            self.ranges[rule] = [DisabledRange(r.start, r.end or None) for r in self.ranges[ALL]]
        return self.ranges[rule]

    def start(self, line: int, rule: str) -> None:
        self.ensure(rule).append(DisabledRange(line))

    def end(self, line: int, rule: str) -> None:
        ranges = self.ranges.get(rule)
        if ranges:
            ranges[-1].end = line

    def disable_line(self, line: int, rule: str) -> None:
        if self.disabled(ALL):
            raise CommandError('All rules have already been disabled')
        if rule == ALL:
            for name in list(self.ranges):
                if not self.disabled(name):
                    self.start(line, name)
                    self.end(line, name)
        else:
            if self.disabled(rule):
                raise CommandError(f'"{rule}" has already been disabled')
            self.start(line, rule)
            self.end(line, rule)

    def disable(self, line: int, text: str) -> None:
        for rule in command_rules(DISABLE, text):
            if self.disabled(rule):
                raise CommandError(f'"{rule}" has already been disabled')
            if rule == ALL:
                for name in list(self.ranges):
                    self.start(line, name)
            else:
                self.start(line, rule)

    def enable(self, end_line: int, text: str) -> None:
        for rule in command_rules(ENABLE, text):
            if rule == ALL:
                if all(not ranges or ranges[-1].end for ranges in self.ranges.values()):
                    raise CommandError('No rules have been disabled')
                for name, ranges in self.ranges.items():
                    if not ranges or not ranges[-1].end:
                        self.end(end_line, name)
                continue
            if self.disabled(ALL) and rule not in self.ranges:
                self.ranges[rule] = [DisabledRange(r.start, r.end or None) for r in self.ranges[ALL]]
                self.end(end_line, rule)
                continue
            if self.disabled(rule):
                self.end(end_line, rule)
                continue
            raise CommandError(f'"{rule}" has not been disabled')

    def comment(self, text: str, start_line: int, end_line: int) -> None:
        """checkComment()."""
        command = command_of(text)
        try:
            if command == DISABLE_LINE:
                for rule in command_rules(command, text):
                    self.disable_line(start_line, rule)
            elif command == DISABLE_NEXT_LINE:
                for rule in command_rules(command, text):
                    self.disable_line(end_line + 1, rule)
            elif command == DISABLE:
                self.disable(start_line, text)
            elif command == ENABLE:
                self.enable(end_line, text)
        except CommandError:
            pass  # stylelint reports a CssSyntaxError and lints nothing


def is_configuration_text(text: str) -> bool:
    """isConfigurationComment() of a text."""
    return bool(text) and command_of(text) in (DISABLE, DISABLE_LINE, DISABLE_NEXT_LINE, ENABLE)


def disabled_ranges(root: Root, line_of: LineOf) -> Ranges:
    """The disabled ranges of lines of each rule (stylelint's names), and of 'all'."""
    assigner = Assigner()

    def comments_in(node: Node, part: str | None, offset: int) -> None:
        if not part or '/*' not in part or '*/' not in part:
            return
        tokenizer = Tokenizer(part, ignore_errors=True)
        while (token := tokenizer.next_token()) is not None:
            if token.type != 'comment' or token.start is None or token.end is None:
                continue
            text = js_trim(token.text[2:-2])
            if is_configuration_text(text):
                start, end = line_of(node, token.start + offset, token.end + 1 + offset)
                assigner.comment(text, start, end)

    for node in root.walk():
        if isinstance(node, Comment):
            if is_configuration_comment(node):
                start, end = line_of(node, None, None)
                assigner.comment(node.text, start, end)
        elif isinstance(node, Rule):
            selector = node.raws.selector.raw if node.raws.selector is not None else None
            comments_in(node, selector, 0)
            comments_in(node, node.raws.between, len(selector) if selector is not None else len(node.selector))
        elif isinstance(node, AtRule):
            offset = len(node.name) + 1
            comments_in(node, node.raws.after_name, offset)
            offset += len(node.raws.after_name or '')
            params = node.raws.params.raw if node.raws.params is not None else None
            comments_in(node, params, offset)
            offset += len(params) if params is not None else len(node.params)
            comments_in(node, node.raws.between, offset)
        elif isinstance(node, Declaration):
            offset = len(node.prop)
            comments_in(node, node.raws.between, offset)
            offset += len(node.raws.between or '')
            comments_in(node, node.raws.value.raw if node.raws.value is not None else None, offset)
    return assigner.ranges


def is_disabled(ranges: Ranges, names: tuple[str, ...], line: int) -> bool:
    """isDisabledOnLine(), for a rule with any of names."""
    rule_ranges = next((ranges[name] for name in names if name in ranges), None)
    if rule_ranges is None:
        rule_ranges = ranges.get(ALL, [])
    return any(r.start <= line and (r.end is None or r.end >= line) for r in rule_ranges)
