# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""selector-type-no-unknown: stylelint's lib/rules/selector-type-no-unknown."""

import re
from typing import Final

from kgcss._css_data import (
    A_N_PLUS_B_NOTATION_PSEUDO_CLASSES,
    A_N_PLUS_B_OF_S_NOTATION_PSEUDO_CLASSES,
    HTML_TYPE_SELECTORS,
    HYPHEN_CONTAINING_TYPE_SELECTORS,
    LINGUISTIC_PSEUDO_CLASSES,
    MATHML_TYPE_SELECTORS,
    SHADOW_TREE_PSEUDO_ELEMENTS,
    SVG_TYPE_SELECTORS,
)
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._selector_parser import Nesting, Pseudo, SelectorError, Tag, is_pseudo_class, is_pseudo_element
from kgcss.linter._utils import (
    is_keyframe_rule,
    is_standard_syntax_rule,
    normalize_pseudo_name,
    parse_selector_or_none,
    rule_selector,
    selector_index,
)

OPTIONS: Final = frozenset({'ignore', 'ignoreNamespaces', 'ignoreTypes'})
OPTION_VALUES: Final = {'ignore': frozenset({'custom-elements', 'default-namespace'})}
RE_STARTS_A_TAG_NAME: Final = re.compile(r'(?:[^.#\[:a-z-]|^)[a-z]', re.IGNORECASE)
RE_STARTS_LOWER: Final = re.compile(r'^[a-z]')
IGNORED_PSEUDO_ELEMENTS: Final = frozenset(
    {
        '::highlight',
        '::view-transition-group',
        '::view-transition-image-pair',
        '::view-transition-new',
        '::view-transition-old',
    }
)
IGNORED_PSEUDO_CLASSES: Final = frozenset(
    {':active-view-transition-type', ':dir', ':-moz-locale-dir', ':state', ':lang'}
)
ARGUMENT_PSEUDOS: Final = (
    A_N_PLUS_B_NOTATION_PSEUDO_CLASSES
    | A_N_PLUS_B_OF_S_NOTATION_PSEUDO_CLASSES
    | LINGUISTIC_PSEUDO_CLASSES
    | SHADOW_TREE_PSEUDO_ELEMENTS
)


def is_standard_syntax_type_selector(tag: Tag) -> bool:
    """isStandardSyntaxTypeSelector(): not an argument of :nth-child() and the like, nor &-suffix."""
    parent = tag.parent
    grand = parent.parent if parent is not None else None
    if parent is None or grand is None:
        return False
    if grand.value and isinstance(grand, Pseudo) and normalize_pseudo_name(grand.value) in ARGUMENT_PSEUDOS:
        return False
    if isinstance(tag.prev(), Nesting):
        return False
    value = tag.value
    return not value.startswith('%') and not (value.startswith('/') and value.endswith('/'))


def is_custom_element(name: str) -> bool:
    """isCustomElement()."""
    if not RE_STARTS_LOWER.match(name) or '-' not in name or name.lower() != name:
        return False
    return name not in HYPHEN_CONTAINING_TYPE_SELECTORS


def is_ignored_argument(tag: Tag) -> bool:
    parent = tag.parent
    node = parent.parent if parent is not None else None
    if is_pseudo_element(node):
        return isinstance(node, Pseudo) and node.value in IGNORED_PSEUDO_ELEMENTS
    return is_pseudo_class(node) and isinstance(node, Pseudo) and node.value in IGNORED_PSEUDO_CLASSES


def check(root: Root, ctx: Context) -> None:
    for rule in root.walk_rules():
        if (
            not RE_STARTS_A_TAG_NAME.search(rule.selector)
            or not is_standard_syntax_rule(rule)
            or is_keyframe_rule(rule)
        ):
            continue
        parsed = parse_selector_or_none(rule_selector(rule), ctx.cache)
        if parsed is None:
            continue
        try:
            tags = parsed.find_all(Tag)
        except SelectorError:  # nested too deeply to walk, where stylelint throws
            continue
        for tag in tags:
            if not is_standard_syntax_type_selector(tag):
                continue
            name = tag.value
            if ctx.ignores('custom-elements') and is_custom_element(name):
                continue
            namespace = tag.namespace
            if ctx.ignores('default-namespace') and not isinstance(namespace, str):
                continue
            if isinstance(namespace, str) and ctx.matches('ignoreNamespaces', namespace):
                continue
            if ctx.matches('ignoreTypes', name) or is_ignored_argument(tag):
                continue
            lower = name.lower()
            if lower in HTML_TYPE_SELECTORS or name in SVG_TYPE_SELECTORS or lower in MATHML_TYPE_SELECTORS:
                continue
            index = selector_index(tag)
            ctx.report(rule, f'Unknown type selector "{name}"', (name,), index=index, end_index=index + len(name))
