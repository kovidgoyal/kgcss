# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Running the rules: parsing, positions as stylelint reports them, and fixing.

A rule is a module in kgcss.linter._rules with a function check(root, ctx),
which reads the PostCSS tree and calls ctx.report() as stylelint's rules
call report(). The modules are imported when they are first used.
"""

import importlib
import re
from bisect import bisect_right
from collections.abc import Callable, Collection, Mapping
from typing import Final, NamedTuple, Protocol, cast

from kgcss.linter import LINT_RULES, LintProblem, LintResult, LintRule, SyntaxProblem, SyntaxProblemKind, TextEdit
from kgcss.linter._disables import disabled_ranges, is_disabled
from kgcss.linter._postcss import CssSyntaxError, Node, Root, parse

# Fixing one problem can leave or make another that a further pass fixes
MAX_FIX_PASSES: Final = 10
RE_NEWLINE: Final = re.compile(r'\r?\n')
BOMS: Final = ('\ufeff', '\ufffe')  # PostCSS strips either


class Position(NamedTuple):
    """An offset and its 1-based line and column, as PostCSS computes them."""

    offset: int
    line: int
    column: int


class Source:
    """The text PostCSS parsed (without a byte order mark) and its lines (PostCSS's Input)."""

    __slots__ = ('css', 'line_starts')

    def __init__(self, css: str) -> None:
        self.css = css
        starts = [0]
        find = css.find
        i = find('\n')
        while i != -1:
            starts.append(i + 1)
            i = find('\n', i + 1)
        self.line_starts = starts

    def position(self, offset: int) -> Position:
        """Input.fromOffset(): lines end at line feeds only."""
        line = bisect_right(self.line_starts, offset) - 1 if offset >= 0 else 0
        return Position(offset, line + 1, offset - self.line_starts[line] + 1)

    def node_range(self, node: Node) -> tuple[Position, Position]:
        """node.rangeBy({}): its end column is one past that of its last character."""
        start = self.position(node.start or 0)
        if node.end is None:
            return start, Position(start.offset + 1, start.line, start.column + 1)
        # A node ends at getPosition(last character) and a root at getPosition(the end of the text)
        last = self.position(node.end if isinstance(node, Root) else node.end - 1)
        return start, Position(node.end, last.line, last.column + 1)

    def range_by(
        self, node: Node, index: int | None = None, end_index: int | None = None, word: str | None = None
    ) -> tuple[Position, Position]:
        """node.rangeBy({index, endIndex} or {word})."""
        start, end = self.node_range(node)
        base = start.offset
        if word is not None:
            i = self.css[base : end.offset].find(word)
            if i != -1:
                start, end = self.position(base + i), self.position(base + i + len(word))
        else:
            if index is not None:
                start = self.position(base + index)
            if end_index is not None:
                end = self.position(base + end_index)
            elif index is not None:
                end = self.position(base + index + 1)
        if end.line < start.line or (end.line == start.line and end.column <= start.column):
            end = Position(start.offset + 1, start.line, start.column + 1)
        return start, end

    def position_by(self, node: Node, index: int) -> Position:
        """node.positionBy({index}): an index of 0 is the node's start, as it is falsy."""
        return self.position((node.start or 0) + index)


class Report(NamedTuple):
    rule: LintRule
    message: str
    args: tuple[str, ...]
    start: Position
    end: Position
    fix: TextEdit | None  # in offsets of Source.css


type Options = Mapping[str, object]


def matches_pattern(value: str, comparison: object) -> bool:
    """testAgainstStringOrRegExp(): a string, a '/regex/' or '/regex/i' string, or a compiled pattern."""
    if isinstance(comparison, re.Pattern):
        return comparison.search(value) is not None
    if not isinstance(comparison, str):
        return False
    if comparison.startswith('/') and comparison.endswith(('/', '/i')):
        if comparison.endswith('i'):
            return re.search(comparison[1:-2], value, re.IGNORECASE) is not None
        return re.search(comparison[1:-1], value) is not None
    return value == comparison


class Context:
    """What a rule is given besides the tree: where to report, the text and the rule's options."""

    __slots__ = ('options', 'reports', 'rule', 'source')

    def __init__(self, rule: LintRule, source: Source, reports: list[Report], options: Options) -> None:
        self.rule = rule
        self.source = source
        self.reports = reports
        self.options = options

    def matches(self, option: str, value: str) -> bool:
        """optionsMatches(): whether value is, or matches, the option or an item of it."""
        comparison = self.options.get(option)
        if not comparison:
            return False
        items = comparison if isinstance(comparison, (list, tuple)) else (comparison,)
        return any(matches_pattern(value, item) for item in items)

    def ignores(self, keyword: str) -> bool:
        """optionsMatches(options, 'ignore', keyword)."""
        return self.matches('ignore', keyword)

    @property
    def css(self) -> str:
        return self.source.css

    @property
    def newline(self) -> str:
        """stylelint's context.newline: the first line break of the text, or a line feed."""
        m = RE_NEWLINE.search(self.source.css)
        return m.group() if m else '\n'

    def report(
        self,
        node: Node,
        message: str,
        args: tuple[str, ...] = (),
        *,
        index: int | None = None,
        end_index: int | None = None,
        word: str | None = None,
        start: Position | None = None,
        end: Position | None = None,
        fix: TextEdit | None = None,
    ) -> None:
        """stylelint's report(): at start and end if given, else node.rangeBy() of the rest."""
        if start is not None and end is not None:
            rng = start, end
        else:
            rng = self.source.range_by(node, index, end_index, word)
        self.reports.append(Report(self.rule, message, args, rng[0], rng[1], fix))


class RuleModule(Protocol):
    check: Callable[[Root, Context], None]


def load_rule(rule: LintRule) -> RuleModule:
    return cast(RuleModule, importlib.import_module(LINT_RULES[rule].module))


def check_options(options: Mapping[LintRule, Options]) -> None:
    """Raise ValueError for options a rule does not have."""
    for rule, opts in options.items():
        supported: frozenset[str] = getattr(load_rule(rule), 'OPTIONS', frozenset())
        if unknown := set(opts) - supported:
            names = ', '.join(sorted(supported)) or 'none'
            raise ValueError(f'Unsupported options for {rule.value}: {", ".join(sorted(unknown))} (supported: {names})')


def syntax_problem(err: CssSyntaxError, source: Source, shift: int) -> SyntaxProblem:
    kind = next(k for k in SyntaxProblemKind if err.reason == k.value or err.reason.startswith(k.value + ' '))
    start = source.position(err.offset)
    if err.end is None:
        return SyntaxProblem(kind, err.reason, err.offset + shift, None, start.line, start.column, None, None)
    end = source.position(err.end)
    return SyntaxProblem(
        kind, err.reason, err.offset + shift, err.end + shift, start.line, start.column, end.line, end.column
    )


def run(text: str, rules: Collection[LintRule], options: Mapping[LintRule, Options]) -> LintResult:
    shift = 1 if text[:1] in BOMS else 0
    source = Source(text[shift:])
    root, err = parse(source.css)
    reports: list[Report] = []
    for rule in LintRule:  # in a fixed order
        if rule in rules:
            load_rule(rule).check(root, Context(rule, source, reports, options.get(rule, {})))
    if reports and 'stylelint' in source.css:

        def line_of(node: Node, index: int | None, end_index: int | None) -> tuple[int, int]:
            start, end = source.node_range(node) if index is None else source.range_by(node, index, end_index)
            return start.line, end.line

        ranges = disabled_ranges(root, line_of)
        reports = [
            r
            for r in reports
            if not is_disabled(ranges, (LINT_RULES[r.rule].stylelint_name, r.rule.value), r.start.line)
        ]
    problems = []
    for r in reports:
        fix = None if r.fix is None else TextEdit(r.fix.pos + shift, r.fix.end + shift, r.fix.text)
        problems.append(LintProblem(
            r.rule, r.message, r.args, r.start.offset + shift, r.end.offset + shift,
            r.start.line, r.start.column, r.end.line, r.end.column, fix,
        ))  # fmt: skip
    problems.sort(key=lambda p: (p.pos, p.end))  # stable: in rule order where positions are equal
    syntax = () if err is None else (syntax_problem(err, source, shift),)
    return LintResult(tuple(problems), syntax)


def apply_fixes(text: str, problems: tuple[LintProblem, ...]) -> str:
    """Apply the fixes that do not overlap an earlier one; the rest wait for the next pass."""
    edits = sorted({p.fix for p in problems if p.fix is not None}, key=lambda e: (e.pos, e.end))
    parts: list[str] = []
    done = 0
    for edit in edits:
        if edit.pos < done or (edit.pos == done and parts and edit.pos == edit.end):
            continue
        parts.append(text[done : edit.pos])
        parts.append(edit.text)
        done = edit.end
    parts.append(text[done:])
    return ''.join(parts)


def lint(
    source: str, rules: Collection[LintRule], options: Mapping[LintRule, Options], *, fix: bool = False
) -> LintResult:
    check_options(options)
    result = run(source, rules, options)
    if not fix:
        return result
    text = source
    for _ in range(MAX_FIX_PASSES):
        if not any(p.fix is not None for p in result.problems):
            break
        fixed = apply_fixes(text, result.problems)
        if fixed == text:
            break
        text = fixed
        result = run(text, rules, options)
    return LintResult(result.problems, result.syntax_problems, None if text == source else text)
