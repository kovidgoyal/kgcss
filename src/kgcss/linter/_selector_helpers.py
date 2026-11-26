# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Ports of stylelint's selector helpers: getStrippedSelectorSource(),
normalizeSelector(), resolveNestedSelectorsForRule() and findAtRuleContext().

normalizeSelector() sorts with JavaScript's localeCompare(); the order only
serves to compare selectors with each other, so any total order gives the
same results, and code point order is used.
"""

import re
from typing import Final, NamedTuple, cast

from kgcss.linter._postcss import JS_WHITESPACE, AtRule, Container, Rule
from kgcss.linter._selector_parser import (
    Attribute,
    ClassName,
    Combinator,
    Comment,
    SelectorError,
    is_pseudo_class,
    is_pseudo_element,
)
from kgcss.linter._selector_parser import Container as SelectorContainer
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._selector_parser import Root as SelectorRoot
from kgcss.linter._selector_parser import Selector as SelectorSelector
from kgcss.linter._selector_tools import resolve_nested_selector
from kgcss.linter._utils import is_standard_syntax_rule, parse_selector_or_none, rule_selector

RE_SCOPE: Final = re.compile(r'^scope$', re.IGNORECASE)


class StrippedSource(NamedTuple):
    selector: str
    index: int
    end_index: int


def stripped_selector_source(node: SelectorContainer) -> StrippedSource:
    """getStrippedSelectorSource(): the selector without the comments and spaces around it."""
    nodes = node.nodes
    first_index = next((i for i, n in enumerate(nodes) if not isinstance(n, Comment)), -1)
    last_index = next((i for i in range(len(nodes) - 1, -1, -1) if not isinstance(nodes[i], Comment)), -1)
    if first_index == -1:
        selector = str(node).strip(JS_WHITESPACE)
        index = node.source_index or 0
        return StrippedSource(selector, index, index + len(selector))
    first, last = nodes[first_index], nodes[last_index]
    if last.raw_space_after:
        last = last.clone()
    last.raw_space_after = ''
    selector = (''.join(str(n) for n in nodes[first_index:last_index]) + str(last)).strip(JS_WHITESPACE)
    index = first.source_index or 0
    return StrippedSource(selector, index, index + len(selector))


TYPE_PRIORITY: Final = {
    'universal': 0,
    'tag': 1,
    'nesting': 2,
    'id': 3,
    'class': 4,
    'attribute': 5,
    'pseudo': 6,
    'comment': 7,
}


def _whitespace_and_comments(node: SelectorNode) -> None:
    if isinstance(node, Comment):
        node.remove()
        return
    node.raw_space_before = ''
    node.raw_space_after = ''
    node.spaces.before = ''
    node.spaces.after = ''
    if isinstance(node, Attribute):
        node.spaces.insensitive = node.spaces.operator = node.spaces.attribute = node.spaces.value = None
        node.raws.spaces = None


def _escaping(node: SelectorNode) -> None:
    if isinstance(node, ClassName) and node.raws.value:
        node.value = node.value  # re-escapes into raws.value


def _sort_compounds(nodes: list[SelectorNode]) -> list[SelectorNode]:
    groups: list[list[SelectorNode]] = []
    current: list[SelectorNode] = []
    for child in nodes:
        if isinstance(child, Combinator) or is_pseudo_element(child):
            groups.extend((current, [child]))
            current = []
        else:
            current.append(child)
    groups.append(current)
    for group in groups:
        group.sort(key=lambda n: (TYPE_PRIORITY.get(n.type, 2**53 - 1), str(n)))
    return [n for group in groups for n in group]


def _order(node: SelectorNode) -> None:
    if not isinstance(node, SelectorContainer):
        return
    for child in list(node.nodes):
        _order(child)
    if isinstance(node, SelectorRoot) or is_pseudo_class(node) or is_pseudo_element(node):
        unique: dict[str, SelectorNode] = {}
        for child in sorted(node.nodes, key=str):
            unique.setdefault(str(child), child)
        keep = {id(n): i for i, n in enumerate(unique.values())}
        node.nodes = sorted((n for n in node.nodes if id(n) in keep), key=lambda n: keep[id(n)])
    if isinstance(node, SelectorSelector):
        node.nodes = _sort_compounds(node.nodes)


class Normalized(NamedTuple):
    node: SelectorNode
    string: str


def normalize_selector(node: SelectorNode, cache: dict[object, object] | None = None) -> Normalized:
    """normalizeSelector(): a copy without whitespace, comments and duplicates, in a canonical order.

    As stylelint does, results are cached by the text of the selector, in cache
    (which lives for one run of lint()).
    """
    key = ('normalize_selector', str(node))
    if cache is not None and isinstance(cached := cache.get(key), Normalized):
        return cached
    normalized = node.clone()
    _escaping(normalized)
    _whitespace_and_comments(normalized)
    if isinstance(normalized, SelectorContainer):

        def visit(child: SelectorNode) -> None:
            _escaping(child)
            _whitespace_and_comments(child)

        normalized.walk(visit)
    _order(normalized)
    ans = Normalized(normalized, str(normalized))
    if cache is not None:
        cache[key] = ans
    return ans


class Resolved(NamedTuple):
    selector: SelectorNode
    resolved: SelectorRoot
    nested: bool


def resolve_nested_selectors_for_rule(rule: Rule, cache: dict[object, object] | None = None) -> list[Resolved]:
    """resolveNestedSelectorsForRule(): each selector of the rule, with what it resolves to when nested.

    Where resolving raises (too many combinations), where stylelint throws, the rule is skipped. As
    stylelint does, the result is cached for the rule, in cache (which lives for one run of lint()).
    """
    key = ('resolve_nested_selectors_for_rule', id(rule))
    if cache is not None and key in cache:
        return cast('list[Resolved]', cache[key])
    ans = _resolve_nested_selectors_for_rule(rule, cache)
    if cache is not None:
        cache[key] = ans
    return ans


def _resolve_nested_selectors_for_rule(rule: Rule, cache: dict[object, object] | None) -> list[Resolved]:
    own = parse_selector_or_none(rule_selector(rule), cache)
    if own is None:
        return []
    ancestors: list[Container] = []
    parent = rule.parent
    while parent is not None:
        ancestors.append(parent)
        parent = parent.parent
    ast: SelectorRoot | None = None
    try:
        for child in reversed(ancestors):
            if isinstance(child, AtRule) and RE_SCOPE.match(child.name):
                ast = parse_selector_or_none(':where(:scope)')
                continue
            if not isinstance(child, Rule):
                continue
            if not is_standard_syntax_rule(child):
                return []
            child_ast = parse_selector_or_none(rule_selector(child), cache)
            if child_ast is None:
                return []
            ast = child_ast if ast is None else resolve_nested_selector(child_ast, ast)
        ans = []
        for selector in own.nodes:
            root = SelectorRoot(value='', nodes=[selector])
            if ast is None:
                ans.append(Resolved(selector, root, False))
            else:
                ans.append(Resolved(selector, resolve_nested_selector(root, ast), True))
        return ans
    except SelectorError:
        return []


def at_rule_context(rule: Rule) -> AtRule | None:
    """findAtRuleContext(): the closest at-rule around the rule, through rules only."""
    parent = rule.parent
    while isinstance(parent, Rule):
        parent = parent.parent
    return parent if isinstance(parent, AtRule) else None
