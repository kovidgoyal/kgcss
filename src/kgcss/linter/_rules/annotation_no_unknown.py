# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""annotation-no-unknown: stylelint's lib/rules/annotation-no-unknown."""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import declaration_value, is_standard_syntax_value
from kgcss.linter._value_parser import iter_nodes, parse

OPTIONS: Final = frozenset({'ignoreAnnotations'})


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if not is_standard_syntax_value(decl.value) or decl.important or '!' not in decl.value:
            continue
        for node in iter_nodes(parse(declaration_value(decl))):
            if (
                node.type == 'word'
                and node.value.startswith('!')
                and not ctx.matches('ignoreAnnotations', node.value[1:])
            ):
                ctx.report(decl, f'Unknown annotation "{node.value}"', (node.value,), word=node.value)
