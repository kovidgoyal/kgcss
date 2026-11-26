# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-empty-source: stylelint's lib/rules/no-empty-source."""

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root, js_trim
from kgcss.linter._utils import is_configuration_comment


def check(root: Root, ctx: Context) -> None:
    nodes = root.nodes or []
    if any(not is_configuration_comment(child) for child in nodes):
        return
    # A non-standard syntax, such as JSX, that leaves the text in raws.after
    if not nodes and js_trim(root.raws.after or ''):
        return
    ctx.report(root, 'Empty source')
