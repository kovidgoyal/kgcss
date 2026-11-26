# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""declaration-block-no-duplicate-custom-properties: stylelint's rule of that name."""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import each_declaration_block, is_custom_property, is_standard_syntax_property

OPTIONS: Final = frozenset({'ignoreProperties'})


def check(root: Root, ctx: Context) -> None:
    for decls in each_declaration_block(root):
        seen: set[str] = set()
        for decl in decls:
            prop = decl.prop
            if (
                not is_standard_syntax_property(prop)
                or not is_custom_property(prop)
                or ctx.matches('ignoreProperties', prop)
            ):
                continue
            if prop in seen:
                ctx.report(decl, f'Duplicate custom property "{prop}"', (prop,), word=prop)
            else:
                seen.add(prop)
