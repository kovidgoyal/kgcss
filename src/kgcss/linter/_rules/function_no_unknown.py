# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""function-no-unknown: stylelint's lib/rules/function-no-unknown.

The function names come from the syntaxes css-tree knows. Values are read
with @csstools' tokenizer and parser, as stylelint reads them; function
names are unescaped.
"""

from typing import Final

from kgcss._css_data import CSS_TREE_FUNCTIONS
from kgcss.linter._csstools import (
    ComponentWalkEntry,
    FunctionNode,
    is_function_node,
    parse_list_of_component_values,
    tokenize,
    walk,
)
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import declaration_value_index, is_standard_syntax_value, vendor_prefix

OPTIONS: Final = frozenset({'ignoreFunctions'})


def functions(value: str) -> list[FunctionNode]:
    found: list[FunctionNode] = []

    def visit(entry: ComponentWalkEntry[dict[str, object]], index: int | str) -> None:
        if is_function_node(entry.node):
            found.append(entry.node)

    walk(parse_list_of_component_values(tokenize(value)), visit)
    return found


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        value = decl.value
        if '(' not in value or not is_standard_syntax_value(value):
            continue
        for func in functions(value):
            name = func.get_name()
            if name.startswith('--') or ctx.matches('ignoreFunctions', name):
                continue
            if vendor_prefix(name) or name.lower() in CSS_TREE_FUNCTIONS:
                continue
            start = declaration_value_index(decl)
            # From the name to the ( that ends the function token
            ctx.report(
                decl,
                f'Unknown function "{name}"',
                (name,),
                index=start + func.name.start,
                end_index=start + func.name.end,
            )
