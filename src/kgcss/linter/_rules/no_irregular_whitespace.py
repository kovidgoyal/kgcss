# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-irregular-whitespace: stylelint's lib/rules/no-irregular-whitespace.

Irregular whitespace in strings and comments is allowed (in values and in
the attribute selectors of selectors). As in stylelint, the whitespace found
before a node (raws.before) or after its block is placed by offsets counted
from the start of the node.
"""

import re
from collections.abc import Callable
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Node, Root
from kgcss.linter._selector_parser import Attribute, Comment, SelectorError, parse_selector
from kgcss.linter._selector_parser import Node as SelectorNode
from kgcss.linter._utils import (
    at_rule_param_index,
    at_rule_params,
    declaration_value,
    declaration_value_index,
    node_end_index,
    rule_selector,
)
from kgcss.linter._value_parser import ValueNode, iter_nodes, parse, stringify

# The irregular whitespace characters: VT, FF, NBSP, NEL, Ogham space, Mongolian vowel separator, BOM,
# the spaces from en quad to zero width space, line and paragraph separators, narrow NBSP,
# medium mathematical space and ideographic space
IRREGULAR: Final = ''.join(
    map(
        chr,
        (
            0x0B,
            0x0C,
            0xA0,
            0x85,
            0x1680,
            0x180E,
            0xFEFF,
            *range(0x2000, 0x200C),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        ),
    )
)
RE_IRREGULAR: Final = re.compile(f'[{IRREGULAR}]')
RE_IRREGULARS: Final = re.compile(f'[{IRREGULAR}]+')
RE_GRID: Final = re.compile(r'^(?:grid|grid-template|grid-template-areas)$', re.IGNORECASE)


def replace_irregular(text: str) -> str:
    return RE_IRREGULARS.sub(' ', text)


def normalize_value(value: str, should_normalize: Callable[[ValueNode], bool]) -> str:
    """With the irregular whitespace in the nodes that may have it replaced."""
    if not RE_IRREGULAR.search(value):
        return value
    nodes = parse(value)
    for node in iter_nodes(nodes):
        if should_normalize(node):
            node.value = replace_irregular(node.value)
    return stringify(nodes)


def normalize_decl(value: str | None, prop: str = '') -> str | None:
    if not value:
        return None
    ignore_strings = bool(prop) and RE_GRID.match(prop.lower()) is not None
    return normalize_value(value, lambda n: (n.type == 'string' and not ignore_strings) or n.type == 'comment')


def normalize_selector(selector: str) -> str:
    if not RE_IRREGULAR.search(selector):
        return selector
    try:
        root = parse_selector(selector)
    except SelectorError:
        return selector  # stylelint throws
    nodes: list[SelectorNode] = []
    root.walk(nodes.append)
    for node in nodes:
        if node.value and isinstance(node, (Attribute, Comment)):
            node.value = replace_irregular(node.value)
    return str(root)


def check(root: Root, ctx: Context) -> None:
    if not RE_IRREGULAR.search(root.css):
        return

    def validate(node: Node, value: str | None, start: int) -> None:
        if not value:
            return
        for m in RE_IRREGULARS.finditer(value):
            ctx.report(node, 'Irregular whitespace', index=start + m.start(), end_index=start + m.end())

    def clean(*texts: str | None) -> bool:
        """Whether none of the texts of a node has irregular whitespace: then no part of them can."""
        return RE_IRREGULAR.search(''.join(t for t in texts if t)) is None

    for at_rule in root.walk_at_rules():
        raws = at_rule.raws
        if clean(raws.before, at_rule.name, raws.after_name, at_rule_params(at_rule), raws.between, raws.after):
            continue
        validate(at_rule, raws.before, 0)
        validate(at_rule, at_rule.name, 1)
        validate(at_rule, raws.after_name, 1 + len(at_rule.name))
        params = at_rule_params(at_rule)
        validate(at_rule, normalize_value(params, lambda n: n.type == 'string'), at_rule_param_index(at_rule))
        validate(at_rule, raws.between, at_rule_param_index(at_rule) + len(params))
        validate(at_rule, raws.after, node_end_index(at_rule))
    for rule in root.walk_rules():
        selector = rule_selector(rule)
        if clean(rule.raws.before, selector, rule.raws.between, rule.raws.after):
            continue
        validate(rule, rule.raws.before, 0)
        validate(rule, normalize_selector(selector), 0)
        validate(rule, rule.raws.between, len(selector))
        validate(rule, rule.raws.after, node_end_index(rule))
    for decl in root.walk_decls():
        if clean(decl.raws.before, decl.prop, decl.raws.between, declaration_value(decl)):
            continue
        validate(decl, decl.raws.before, 0)
        validate(decl, normalize_decl(decl.prop), 0)
        validate(decl, normalize_decl(decl.raws.between), len(decl.prop))
        validate(decl, normalize_decl(declaration_value(decl), decl.prop), declaration_value_index(decl))
