# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-duplicate-selectors: stylelint's lib/rules/no-duplicate-selectors.

Selectors are compared normalized (without whitespace and comments, their
parts in a canonical order) and with nesting resolved, within the same
at-rule context.
"""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import JS_WHITESPACE, AtRule, Root, Rule
from kgcss.linter._selector_helpers import (
    at_rule_context,
    normalize_selector,
    resolve_nested_selectors_for_rule,
    stripped_selector_source,
)
from kgcss.linter._selector_parser import Container as SelectorContainer
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._selector_parser import Root as SelectorRoot
from kgcss.linter._utils import is_keyframe_rule, is_standard_syntax_rule, parse_selector_or_none, rule_selector

OPTIONS: Final = frozenset({'disallowInList', 'ignoreSelectors'})


def selector_line(selector: SelectorNode) -> int:
    source = selector.source
    return source.start.line if source is not None and source.start is not None else 1


def check(root: Root, ctx: Context) -> None:
    contexts: dict[AtRule | None, dict[str, int]] = {}
    disallow_in_list = bool(ctx.options.get('disallowInList'))

    def complain(rule: Rule, selector: str, index: int, end_index: int, line: int) -> None:
        ctx.report(
            rule,
            f'Duplicate selector "{selector}", first used at line {line}',
            (selector, str(line)),
            index=index,
            end_index=end_index,
        )

    for rule in root.walk_rules():
        if is_keyframe_rule(rule) or not is_standard_syntax_rule(rule):
            continue
        seen = contexts.setdefault(at_rule_context(rule), {})
        rule_line = ctx.source.position(rule.start or 0).line
        if disallow_in_list:
            for entry in resolve_nested_selectors_for_rule(rule):
                for resolved in list(entry.resolved.nodes):
                    normalized = normalize_selector(resolved, ctx.cache).string
                    if ctx.matches('ignoreSelectors', normalized):
                        continue
                    if (line := seen.get(normalized)) is None:
                        seen[normalized] = selector_line(entry.selector) + rule_line - 1
                        continue
                    assert isinstance(entry.selector, SelectorContainer)
                    text, index, end_index = stripped_selector_source(entry.selector)
                    complain(rule, text, index, end_index, line)
            continue
        # The selectors of the rule itself
        in_list: dict[str, int] = {}
        if (parsed := parse_selector_or_none(rule_selector(rule))) is not None:
            for selector in list(parsed.nodes):
                normalized = normalize_selector(selector, ctx.cache).string
                if ctx.matches('ignoreSelectors', normalized):
                    continue
                if (line := in_list.get(normalized)) is None:
                    in_list[normalized] = selector_line(selector) + rule_line - 1
                    continue
                assert isinstance(selector, SelectorContainer)
                text, index, end_index = stripped_selector_source(selector)
                complain(rule, text, index, end_index, line)
        # The selector list as a whole, against those of other rules
        nodes = [n for entry in resolve_nested_selectors_for_rule(rule) for n in entry.resolved.nodes]
        if not nodes or any(ctx.matches('ignoreSelectors', normalize_selector(n, ctx.cache).string) for n in nodes):
            continue
        normalized = normalize_selector(SelectorRoot(value='', nodes=nodes), ctx.cache).string
        if (line := seen.get(normalized)) is None:
            seen[normalized] = rule_line
            continue
        text = rule_selector(rule).strip(JS_WHITESPACE)
        complain(rule, text, 0, len(text), line)
