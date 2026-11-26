# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-descending-specificity: stylelint's lib/rules/no-descending-specificity.

Selectors are grouped by their last compound selector without pseudo-classes,
within the same at-rule context; a selector that is less specific than an
earlier one of its group is reported.
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
from kgcss.linter._selector_parser import Combinator, Pseudo
from kgcss.linter._selector_parser import Container as SelectorContainer
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._selector_tools import Specificity, compare, selector_specificity
from kgcss.linter._utils import is_standard_syntax_rule, postcss_list_comma

OPTIONS: Final = frozenset({'ignore'})
OPTION_VALUES: Final = {'ignore': frozenset({'selectors-within-list'})}


class Entry(NamedTuple):
    selector: str
    resolved: str
    specificity: Specificity
    line: int


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
    contexts: dict[AtRule | None, dict[str, list[Entry]]] = {}
    ignore_lists = ctx.ignores('selectors-within-list')

    def check_selector(
        resolved: SelectorNode, selector: SelectorNode, rule: Rule, context: dict[str, list[Entry]]
    ) -> None:
        assert isinstance(resolved, SelectorContainer)
        assert isinstance(selector, SelectorContainer)
        reference = last_compound_without_pseudo_classes(resolved, ctx.cache)
        if reference is None:
            return
        specificity = selector_specificity(resolved)
        text, index, end_index = stripped_selector_source(selector)
        resolved_text = stripped_selector_source(resolved).selector
        if resolved_text == text:
            resolved_text = ''
        entry = Entry(text, resolved_text, specificity, ctx.source.position(rule.start or 0).line)
        prior = context.get(reference)
        if prior is None:
            context[reference] = [entry]
            return
        for p in prior:
            if compare(specificity, p.specificity) < 0:
                subsequent = f'"{text}"' + (f' ("{resolved_text}")' if resolved_text else '')
                before = f'"{p.selector}"' + (f' ("{p.resolved}")' if p.resolved else '')
                ctx.report(
                    rule,
                    f'Expected selector {subsequent} to come before selector {before}, at line {p.line}',
                    (text, p.selector, str(p.line), resolved_text, p.resolved),
                    index=index,
                    end_index=end_index,
                )
                break
        prior.append(entry)

    for rule in root.walk_rules():
        if not is_standard_syntax_rule(rule):
            continue
        if ignore_lists and len(postcss_list_comma(rule.selector)) > 1:
            continue
        if not has_declaration(rule):
            continue
        context = contexts.setdefault(at_rule_context(rule), {})
        for entry in resolve_nested_selectors_for_rule(rule):
            for resolved in list(entry.resolved.nodes):
                check_selector(resolved, entry.selector, rule, context)
