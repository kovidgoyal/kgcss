# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""declaration-block-no-duplicate-properties: stylelint's rule of that name.

The fix removes the declaration that does not take effect. The ignore value
consecutive-duplicates-with-different-syntaxes, which needs css-tree's value
parser, is not supported.
"""

from typing import Final

from kgcss.linter._engine import Context, removal_edit
from kgcss.linter._messages import DUPLICATE_PROPERTY
from kgcss.linter._postcss import Declaration, Root
from kgcss.linter._utils import (
    each_declaration_block,
    is_custom_property,
    is_standard_syntax_property,
    vendor_unprefixed,
)

OPTIONS: Final = frozenset({'ignore', 'ignoreProperties'})
OPTION_VALUES: Final = {
    'ignore': frozenset(
        {
            'consecutive-duplicates',
            'consecutive-duplicates-with-different-values',
            'consecutive-duplicates-with-same-prefixless-values',
        }
    )
}


def check(root: Root, ctx: Context) -> None:
    ignore_duplicates = ctx.ignores('consecutive-duplicates')
    ignore_different_values = ctx.ignores('consecutive-duplicates-with-different-values')
    ignore_prefixless = ctx.ignores('consecutive-duplicates-with-same-prefixless-values')

    def report(node: Declaration, word: str) -> None:
        ctx.report(node, DUPLICATE_PROPERTY, (word,), word=word, fix=removal_edit(ctx.css, node, ctx.cache))

    for block in each_declaration_block(root):
        decls: list[Declaration] = []
        index_of: dict[str, int] = {}  # decls.findIndex() by the lowercase property, which all of decls[i] have
        previous = ''
        for decl in block:
            prop = decl.prop
            lower = prop.lower()
            if not is_standard_syntax_property(prop) or is_custom_property(prop):
                continue
            if ctx.matches('ignoreProperties', prop) or lower == 'src':
                continue
            index = index_of.get(lower, -1)
            consecutive = previous == lower
            previous = lower
            if index == -1:
                index_of[lower] = len(decls)
                decls.append(decl)
                continue
            duplicate = decls[index]
            if ignore_different_values or ignore_prefixless:
                same_prefixless = vendor_unprefixed(decl.value) == vendor_unprefixed(duplicate.value or '')
                if consecutive and not (ignore_prefixless and not same_prefixless):
                    if decl.value == (duplicate.value or ''):
                        decls[index] = decl
                        report(duplicate, duplicate.prop)
                    continue
            elif ignore_duplicates and consecutive:
                continue
            # The declaration that does not take effect: the earlier one, unless only it is !important
            if not decl.important and duplicate.important:
                report(decl, prop)
            else:
                decls[index] = decl
                report(duplicate, duplicate.prop)
