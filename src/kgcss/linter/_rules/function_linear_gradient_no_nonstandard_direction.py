# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""function-linear-gradient-no-nonstandard-direction: stylelint's rule of that name."""

import re
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import JS_WHITESPACE, Declaration, Root
from kgcss.linter._utils import (
    declaration_value_index,
    function_arguments_search,
    is_standard_syntax_value,
    js_trim_str,
    vendor_prefix,
)
from kgcss.linter._value_parser import ValueNode, iter_nodes, parse, stringify

FUNCTION: Final = '(?:-webkit-|-moz-|-o-|-ms-)?linear-gradient'
RE_CALL: Final = re.compile(FUNCTION + r'\(', re.IGNORECASE)
RE_NAME: Final = re.compile(f'^{FUNCTION}$', re.IGNORECASE)
DIRECTION: Final = 'top|left|bottom|right'
RE_DIRECTION: Final = re.compile(DIRECTION, re.IGNORECASE)
RE_WITH_TO: Final = re.compile(f'^to ({DIRECTION})(?: ({DIRECTION}))?$', re.IGNORECASE)
RE_WITHOUT_TO: Final = re.compile(f'^({DIRECTION})(?: ({DIRECTION}))?$', re.IGNORECASE)
RE_DIGIT: Final = re.compile(r'[0-9.]')
RE_ANGLE: Final = re.compile(r'^[0-9.]+(?:deg|grad|rad|turn)$')
RE_IN: Final = re.compile(r'\bin\b', re.IGNORECASE | re.ASCII)


def is_standard_direction(source: str, with_to: bool) -> bool:
    m = (RE_WITH_TO if with_to else RE_WITHOUT_TO).search(source)
    # In stylelint, matches.length is always 3: one side, or two different ones (case-sensitively)
    return m is not None and (m.group(2) is None or m.group(1) != m.group(2))


def check_function(decl: Declaration, node: ValueNode, ctx: Context) -> None:
    def callback(expression: str, expression_index: int) -> None:
        args = expression.split(',')
        first = js_trim_str(args[0])
        if not is_standard_syntax_value(first) or first.startswith('var(') or RE_IN.search(first):
            return
        if RE_DIGIT.match(first[:1]):
            if RE_ANGLE.search(first):
                return
        elif not RE_DIRECTION.search(first) or is_standard_direction(first, not vendor_prefix(node.value)):
            return
        index = declaration_value_index(decl) + node.source_index + expression_index
        ctx.report(decl, 'Nonstandard direction', index=index, end_index=index + len(args[0].rstrip(JS_WHITESPACE)))

    function_arguments_search(stringify(node).lower(), RE_NAME, callback)


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if RE_CALL.search(decl.value):
            for node in iter_nodes(parse(decl.value)):
                if node.type == 'function':
                    check_function(decl, node, ctx)
