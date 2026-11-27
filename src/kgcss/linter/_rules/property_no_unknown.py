# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""property-no-unknown: stylelint's lib/rules/property-no-unknown."""

from typing import Final

from kgcss._css_data import (
    CSS_TREE_PROPERTIES,
    NESTING_SUPPORTED_AT_KEYWORDS,
    PREVIOUSLY_KNOWN_PREFIXED_PROPERTIES,
    PREVIOUSLY_KNOWN_PROPERTIES,
)
from kgcss.linter._engine import Context
from kgcss.linter._postcss import AtRule, Root, Rule
from kgcss.linter._utils import (
    ancestors,
    css_tree_property,
    is_custom_property,
    is_descriptor_declaration,
    is_standard_syntax_declaration,
    is_standard_syntax_property,
    vendor_prefix,
)

OPTIONS: Final = frozenset({'checkPrefixed', 'ignoreAtRules', 'ignoreProperties', 'ignoreSelectors'})


def check(root: Root, ctx: Context) -> None:
    check_prefixed = bool(ctx.options.get('checkPrefixed'))
    # Without the option no ancestor can match it, so the ancestors of deeply nested declarations need no walk
    ignore_at_rules = bool(ctx.options.get('ignoreAtRules'))
    for decl in root.walk_decls():
        prop = decl.prop
        if not is_standard_syntax_declaration(decl) or is_descriptor_declaration(decl, NESTING_SUPPORTED_AT_KEYWORDS):
            continue
        if not is_standard_syntax_property(prop) or is_custom_property(prop):
            continue
        if (not check_prefixed and vendor_prefix(prop)) or ctx.matches('ignoreProperties', prop):
            continue
        if isinstance(decl.parent, Rule) and ctx.matches('ignoreSelectors', decl.parent.selector):
            continue
        if ignore_at_rules and any(
            isinstance(n, AtRule) and ctx.matches('ignoreAtRules', n.name) for n in ancestors(decl)
        ):
            continue
        if css_tree_property(prop, CSS_TREE_PROPERTIES) is not None or prop.lower() in PREVIOUSLY_KNOWN_PROPERTIES:
            continue
        if check_prefixed and prop.lower() in PREVIOUSLY_KNOWN_PREFIXED_PROPERTIES:
            continue
        ctx.report(decl, f'Unknown property "{prop}"', (prop,), word=prop)
