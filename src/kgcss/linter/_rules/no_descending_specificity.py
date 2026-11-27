# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-descending-specificity: stylelint's lib/rules/no-descending-specificity.

Selectors are grouped by their last compound selector without pseudo-classes,
within the same at-rule context; a selector that is less specific than an
earlier one of its group is reported. A selector nested too deeply to copy,
on which stylelint throws, is skipped.
"""

from typing import Final, NamedTuple

from kgcss._css_data import PSEUDO_ELEMENTS
from kgcss.linter._engine import Context
from kgcss.linter._postcss import AtRule, Container, Declaration, Root, Rule
from kgcss.linter._selector_helpers import (
    at_rule_context,
    normalize_selector,
    resolve_nested_selectors_for_rule,
    stripped_selector_source,
)
from kgcss.linter._selector_parser import Combinator, Pseudo, SelectorError
from kgcss.linter._selector_parser import Container as SelectorContainer
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._selector_tools import Specificity, compare, selector_specificity
from kgcss.linter._utils import is_standard_syntax_rule, postcss_list_comma

OPTIONS: Final = frozenset({'ignore'})
OPTION_VALUES: Final = {'ignore': frozenset({'selectors-within-list'})}


class Entry(NamedTuple):
    selector: SelectorContainer
    resolved: SelectorContainer
    specificity: Specificity
    line: int

    def texts(self) -> tuple[str, str]:
        """The selector as written, and as resolved if it differs (getStrippedSelectorSource())."""
        text = stripped_selector_source(self.selector).selector
        resolved = stripped_selector_source(self.resolved).selector
        return text, '' if resolved == text else resolved


class Group:
    """The selectors with the same last compound, and the highest of their specificities."""

    __slots__ = ('entries', 'highest')

    def __init__(self, entry: Entry) -> None:
        self.entries = [entry]
        self.highest = entry.specificity

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)
        self.highest = max(self.highest, entry.specificity)


def last_compound_without_pseudo_classes(selector: SelectorContainer, cache: dict[object, object]) -> str | None:
    if not selector.nodes:
        return None
    normalized = normalize_selector(selector, cache).node
    assert isinstance(normalized, SelectorContainer)
    parts = normalized.split(lambda node: isinstance(node, Combinator))
    if not parts:
        return None
    kept = [
        n
        for n in parts[-1]
        if not isinstance(n, Pseudo) or n.value.startswith('::') or n.value.replace(':', '') in PSEUDO_ELEMENTS
    ]
    return ''.join(str(n) for n in kept) if kept else None


def has_declaration(node: Container) -> bool:
    return any(
        isinstance(child, Declaration)
        or (isinstance(child, AtRule) and child.nodes is not None and has_declaration(child))
        for child in node.nodes or ()
    )


def check(root: Root, ctx: Context) -> None:
    contexts: dict[AtRule | None, dict[str, Group]] = {}
    ignore_lists = ctx.ignores('selectors-within-list')

    def check_selector(resolved: SelectorNode, selector: SelectorNode, rule: Rule, context: dict[str, Group]) -> None:
        assert isinstance(resolved, SelectorContainer)
        assert isinstance(selector, SelectorContainer)
        reference = last_compound_without_pseudo_classes(resolved, ctx.cache)
        if reference is None:
            return
        entry = Entry(selector, resolved, selector_specificity(resolved), ctx.source.position(rule.start or 0).line)
        group = context.get(reference)
        if group is None:
            context[reference] = Group(entry)
            return
        # Specificities compare as tuples: there is an earlier, more specific selector only if the
        # highest is more specific, and then the first such is reported (stylelint compares with each)
        if compare(entry.specificity, group.highest) < 0:
            p = next(p for p in group.entries if compare(entry.specificity, p.specificity) < 0)
            text, resolved_text = entry.texts()
            prior_text, prior_resolved = p.texts()
            subsequent = f'"{text}"' + (f' ("{resolved_text}")' if resolved_text else '')
            before = f'"{prior_text}"' + (f' ("{prior_resolved}")' if prior_resolved else '')
            _, index, end_index = stripped_selector_source(selector)
            ctx.report(
                rule,
                f'Expected selector {subsequent} to come before selector {before}, at line {p.line}',
                (text, prior_text, str(p.line), resolved_text, prior_resolved),
                index=index,
                end_index=end_index,
            )
        group.add(entry)

    for rule in root.walk_rules():
        if not is_standard_syntax_rule(rule):
            continue
        if ignore_lists and len(postcss_list_comma(rule.selector)) > 1:
            continue
        if not has_declaration(rule):
            continue
        context = contexts.setdefault(at_rule_context(rule, ctx.cache), {})
        for entry in resolve_nested_selectors_for_rule(rule, ctx.cache):
            for resolved in list(entry.resolved.nodes):
                try:
                    check_selector(resolved, entry.selector, rule, context)
                except SelectorError:  # nested too deeply to copy or print, where stylelint throws
                    continue
