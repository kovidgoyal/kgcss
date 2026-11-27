# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""color-no-invalid-hex: stylelint's lib/rules/color-no-invalid-hex."""

import re
from collections.abc import Iterable
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._messages import INVALID_HEX_COLOR
from kgcss.linter._postcss import Root
from kgcss.linter._utils import declaration_value_index
from kgcss.linter._value_parser import ValueNode, parse

RE_HEX: Final = re.compile(r'#[0-9a-z]+', re.IGNORECASE)
RE_VALID_HEX: Final = re.compile(r'#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})', re.IGNORECASE)


def words(nodes: Iterable[ValueNode]) -> Iterable[ValueNode]:
    """The words of a value, but not in url() (or any function whose name ends with url)."""
    for node in nodes:
        if node.type == 'word':
            yield node
        elif node.type == 'function' and not node.value.endswith('url') and node.nodes:
            yield from words(node.nodes)


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if not RE_HEX.search(decl.value) or '[' in decl.value:  # '[': a Less map
            continue
        for node in words(parse(decl.value)):
            if (m := RE_HEX.match(node.value)) and not RE_VALID_HEX.fullmatch(hex_value := m.group()):
                index = declaration_value_index(decl) + node.source_index
                ctx.report(
                    decl,
                    INVALID_HEX_COLOR,
                    (hex_value,),
                    index=index,
                    end_index=index + len(hex_value),
                )
