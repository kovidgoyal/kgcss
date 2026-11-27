# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""function-linear-gradient-no-nonstandard-direction: stylelint's rule of that name."""

import re
from collections.abc import Iterator
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import JS_WHITESPACE, Declaration, Root
from kgcss.linter._utils import (
    char_at,
    declaration_value_index,
    function_arguments_search,
    is_standard_syntax_value,
    js_trim_str,
    vendor_prefix,
)
from kgcss.linter._value_parser import ValueNode, parse, stringify

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


def is_nonstandard(expression: str, with_to: bool) -> bool:
    """Whether the first argument of a gradient is a nonstandard direction."""
    first = js_trim_str(expression.split(',', 1)[0])
    if not is_standard_syntax_value(first) or first.startswith('var(') or RE_IN.search(first):
        return False
    if RE_DIGIT.match(first[:1]):
        return not RE_ANGLE.search(first)
    return RE_DIRECTION.search(first) is not None and not is_standard_direction(first, with_to)


def check_function(decl: Declaration, node: ValueNode, ctx: Context) -> None:
    """What stylelint does for each function of a value: check the gradients of its text, itself included."""

    def callback(expression: str, expression_index: int) -> None:
        if is_nonstandard(expression, not vendor_prefix(node.value)):
            index = declaration_value_index(decl) + node.source_index + expression_index
            end_index = index + len(expression.split(',', 1)[0].rstrip(JS_WHITESPACE))
            ctx.report(decl, 'Nonstandard direction', index=index, end_index=end_index)

    function_arguments_search(stringify(node).lower(), RE_NAME, callback)


def functions_with_descendants(nodes: list[ValueNode]) -> tuple[list[ValueNode], list[int]]:
    """The function nodes in the order iter_nodes() gives them, and where the functions inside each end."""
    order: list[ValueNode] = []
    ends: list[int] = []
    stack: list[tuple[Iterator[ValueNode], int]] = [(iter(nodes), -1)]
    while stack:
        it, owner = stack[-1]
        node = next(it, None)
        if node is None:
            stack.pop()
            if owner >= 0:
                ends[owner] = len(order)
            continue
        if node.type == 'function':
            order.append(node)
            ends.append(len(order))
            if node.nodes is not None:
                stack.append((iter(node.nodes), len(order) - 1))
    return order, ends


def check_nested(decl: Declaration, order: list[ValueNode], ends: list[int], ctx: Context) -> None:
    """check_function() for every function, deciding each gradient once.

    stylelint checks the gradients in the text of every function, so a gradient
    is checked again for each function around it, and reported again if it is
    nonstandard. What a check gives depends only on the gradient, its position
    and whether the outer function has a vendor prefix, so it is computed once
    here: for nested values the checks were cubic in their depth. The text of
    a gradient and its position are the same in the text of any function around
    it, as long as lowercasing keeps every character one character, which the
    caller checks.
    """
    start = declaration_value_index(decl)
    decided: dict[tuple[int, bool], tuple[int, int] | None] = {}

    def decide(g: ValueNode, with_to: bool) -> tuple[int, int] | None:
        key = (id(g), with_to)
        if key not in decided:
            # function_arguments_search() on the text of the gradient alone: its first function is itself
            source = stringify(g).lower()
            top = parse(source)[0]
            expression_index = top.source_index + len(top.value) + 1
            end = top.source_end_index - (1 if char_at(source, top.source_end_index - 1) == ')' else 0)
            expression = source[expression_index:end]
            ans = None
            if is_nonstandard(expression, with_to):
                index = start + g.source_index + expression_index
                ans = index, index + len(expression.split(',', 1)[0].rstrip(JS_WHITESPACE))
            decided[key] = ans
        return decided[key]

    for i, outer in enumerate(order):
        with_to = not vendor_prefix(outer.value)
        for g in order[i : ends[i]]:
            if RE_NAME.search(g.value) and (problem := decide(g, with_to)) is not None:
                ctx.report(decl, 'Nonstandard direction', index=problem[0], end_index=problem[1])


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        value = decl.value
        if not RE_CALL.search(value):
            continue
        order, ends = functions_with_descendants(parse(value))
        if len(value.lower()) == len(value):
            check_nested(decl, order, ends, ctx)
        else:
            for node in order:
                check_function(decl, node, ctx)
