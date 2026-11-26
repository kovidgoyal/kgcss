# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""function-no-unknown: stylelint's lib/rules/function-no-unknown.

The function names come from the syntaxes css-tree knows. Values are read
into component values with kgcss's parser, as stylelint does with
@csstools/css-parser-algorithms; function names are unescaped.
"""

from collections.abc import Iterable, Iterator
from typing import Final

from kgcss._css_data import CSS_TREE_FUNCTIONS
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import declaration_value_index, is_standard_syntax_value, vendor_prefix
from kgcss.syntax import Block, Function, parse_component_value_list

OPTIONS: Final = frozenset({'ignoreFunctions'})


def functions(values: Iterable[object]) -> Iterator[Function]:
    for value in values:
        if isinstance(value, Function):
            yield value
            yield from functions(value.arguments)
        elif isinstance(value, Block):
            yield from functions(value.content)


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        value = decl.value
        if '(' not in value or not is_standard_syntax_value(value):
            continue
        for func in functions(parse_component_value_list(value)):
            name = func.name
            if name.startswith('--') or ctx.matches('ignoreFunctions', name):
                continue
            if vendor_prefix(name) or name.lower() in CSS_TREE_FUNCTIONS:
                continue
            start = declaration_value_index(decl)
            # The name, up to the ( that ends the function token
            ctx.report(
                decl,
                f'Unknown function "{name}"',
                (name,),
                index=start + func.pos,
                end_index=start + func.arguments.pos - 1,
            )
