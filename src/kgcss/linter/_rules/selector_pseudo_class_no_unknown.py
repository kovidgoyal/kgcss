# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""selector-pseudo-class-no-unknown: stylelint's lib/rules/selector-pseudo-class-no-unknown."""

import re
from typing import Final

from kgcss._css_data import (
    AT_RULE_PAGE_PSEUDO_CLASSES,
    LEVEL_ONE_AND_TWO_PSEUDO_ELEMENTS,
    PSEUDO_CLASSES,
    PSEUDO_ELEMENTS,
    WEBKIT_SCROLLBAR_PSEUDO_CLASSES,
    WEBKIT_SCROLLBAR_PSEUDO_ELEMENTS,
)
from kgcss.linter._engine import Context
from kgcss.linter._postcss import AtRule, Node, Root, Rule
from kgcss.linter._selector_parser import Combinator, Nesting, Pseudo
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._utils import (
    at_rule_param_index,
    at_rule_params,
    is_standard_syntax_at_rule,
    is_standard_syntax_rule,
    is_standard_syntax_selector,
    parse_selector_or_none,
    rule_selector,
    selector_index,
    vendor_prefix,
)

OPTIONS: Final = frozenset({'ignorePseudoClasses'})
RE_LEADING_COLONS: Final = re.compile(r'^:*')


def pseudos_of(selector: str, cache: dict[object, object]) -> list[Pseudo]:
    parsed = parse_selector_or_none(selector, cache)
    return [] if parsed is None else parsed.find_all(Pseudo)


def has_nested_webkit_scrollbar_ancestor(node: Node, cache: dict[object, object]) -> bool:
    current = node.parent
    while current is not None:
        if isinstance(current, Rule):
            selector = rule_selector(current)
            if '::' in selector and any(
                p.value.startswith('::') and p.value.lower()[2:] in WEBKIT_SCROLLBAR_PSEUDO_ELEMENTS
                for p in pseudos_of(selector, cache)
            ):
                return True
        current = current.parent
    return False


def check_selector(selector: str, node: Rule | AtRule, ctx: Context) -> None:
    for pseudo in pseudos_of(selector, ctx.cache):
        value = pseudo.value
        if not is_standard_syntax_selector(value) or value.startswith((':--', '::')):
            continue
        name = RE_LEADING_COLONS.sub('', value, count=1).lower()
        if name in LEVEL_ONE_AND_TWO_PSEUDO_ELEMENTS or ctx.matches('ignorePseudoClasses', value[1:]):
            continue
        prefixed = vendor_prefix(name)
        if isinstance(node, AtRule) and node.name == 'page':
            if name in AT_RULE_PAGE_PSEUDO_CLASSES:
                continue
            index = at_rule_param_index(node) + selector_index(pseudo)
        elif name in PSEUDO_ELEMENTS and not prefixed:
            index = selector_index(pseudo)
        else:
            if prefixed or name in PSEUDO_CLASSES:
                continue
            prev: SelectorNode | None = pseudo
            crossed_combinator = nesting_in_compound = False
            while prev is not None:
                prev = prev.prev()
                if prev is not None:
                    if isinstance(prev, Combinator):
                        crossed_combinator = True
                    if isinstance(prev, Nesting) and not crossed_combinator:
                        nesting_in_compound = True
                    if prev.value.startswith('::'):
                        break
            if prev is not None:
                if (
                    prev.value.lower()[2:] in WEBKIT_SCROLLBAR_PSEUDO_ELEMENTS
                    and name in WEBKIT_SCROLLBAR_PSEUDO_CLASSES
                ):
                    continue
            elif (
                name in WEBKIT_SCROLLBAR_PSEUDO_CLASSES
                and nesting_in_compound
                and has_nested_webkit_scrollbar_ancestor(node, ctx.cache)
            ):
                continue
            index = selector_index(pseudo)
        ctx.report(
            node, f'Unknown pseudo-class selector "{value}"', (value,), index=index, end_index=index + len(value)
        )


def check(root: Root, ctx: Context) -> None:
    for node in root.walk():
        if isinstance(node, Rule):
            if not is_standard_syntax_rule(node):
                continue
            selector = rule_selector(node)
        elif isinstance(node, AtRule) and node.name == 'page' and node.params:
            if not is_standard_syntax_at_rule(node):
                continue
            selector = at_rule_params(node)
        else:
            continue
        if selector and ':' in selector:
            check_selector(selector, node, ctx)
