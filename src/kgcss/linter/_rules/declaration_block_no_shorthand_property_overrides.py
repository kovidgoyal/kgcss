# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""declaration-block-no-shorthand-property-overrides: stylelint's rule of that name."""

from kgcss._css_data import (
    LONGHAND_SUB_PROPERTIES_OF_SHORTHAND_PROPERTIES,
    PHYSICAL_SHORTHAND_TO_FLOW_RELATIVE_PROPERTIES,
    SHORTHAND_TO_RESET_TO_INITIAL_PROPERTY,
)
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import each_declaration_block, vendor_prefix, vendor_unprefixed


def check(root: Root, ctx: Context) -> None:
    for decls in each_declaration_block(root):
        declarations: dict[str, str] = {}
        for decl in decls:
            prop = decl.prop
            unprefixed = vendor_unprefixed(prop).lower()
            prefix = vendor_prefix(prop).lower()
            overridden = dict.fromkeys(
                (
                    *LONGHAND_SUB_PROPERTIES_OF_SHORTHAND_PROPERTIES.get(unprefixed, ()),
                    *SHORTHAND_TO_RESET_TO_INITIAL_PROPERTY.get(unprefixed, ()),
                    *PHYSICAL_SHORTHAND_TO_FLOW_RELATIVE_PROPERTIES.get(unprefixed, ()),
                )
            )
            declarations[prop.lower()] = prop
            for name in overridden:
                if original := declarations.get(prefix + name):
                    ctx.report(
                        decl, f'Overridden property "{original}" by shorthand "{prop}"', (prop, original), word=prop
                    )
