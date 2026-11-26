# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""named-grid-areas-no-invalid: stylelint's lib/rules/named-grid-areas-no-invalid."""

import re
from typing import Final, NamedTuple

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Declaration, Root
from kgcss.linter._utils import declaration_value_index, is_standard_syntax_value, js_split_whitespace, js_trim_str
from kgcss.linter._value_parser import iter_nodes, parse

RE_GRID_AREAS: Final = re.compile(r'^(?:grid|grid-template|grid-template-areas)$', re.IGNORECASE)


class Area(NamedTuple):
    values: list[str]
    index: int
    end_index: int


def is_contiguous_and_rectangular(areas: list[list[str]], name: str) -> bool:
    """As stylelint checks it: the rows that have name have it in the same columns."""
    rows = [[i for i, cell in enumerate(row) if cell == name] for row in areas]
    for i, x in enumerate(rows):
        for y in rows[i + 1 :]:
            if x and y and x != y:
                return False
    return True


def not_contiguous_or_rectangular(areas: list[list[str]]) -> list[str]:
    names = dict.fromkeys(cell for row in areas for cell in row)
    names.pop('.', None)
    return [name for name in names if not is_contiguous_and_rectangular(areas, name)]


def check_decl(decl: Declaration, ctx: Context) -> None:
    start = declaration_value_index(decl)

    def complain(message: str, args: tuple[str, ...], index: int, end_index: int) -> None:
        ctx.report(decl, message, args, index=start + index, end_index=start + end_index)

    areas: list[Area] = []
    report_sent = False
    for node in iter_nodes(parse(decl.value)):
        if node.type != 'string':
            continue
        if node.value == '':
            complain('Expected cell token within string', (), node.source_index, node.source_end_index)
            report_sent = True
            continue
        areas.append(Area(js_split_whitespace(js_trim_str(node.value)), node.source_index, node.source_end_index))
    if report_sent or not areas:
        return
    first = areas[0]
    if other := next((a for a in areas if len(a.values) != len(first.values)), None):
        complain('Expected same number of cell tokens in each string', (), other.index, other.end_index)
        return
    for name in sorted(not_contiguous_or_rectangular([a.values for a in areas])):
        area = next(a for a in areas if name in a.values)
        complain(f'Expected single filled-in rectangle for "{name}"', (name,), area.index, area.end_index)


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        value = decl.value
        if RE_GRID_AREAS.match(decl.prop) and is_standard_syntax_value(value) and js_trim_str(value.lower()) != 'none':
            check_decl(decl, ctx)
