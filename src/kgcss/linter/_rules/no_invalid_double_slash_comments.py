# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-invalid-double-slash-comments: stylelint's lib/rules/no-invalid-double-slash-comments."""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import JS_WHITESPACE, Root, to_string
from kgcss.linter._utils import rule_selector

MESSAGE: Final = 'Invalid double-slash CSS comment'


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if decl.prop.startswith('//'):
            ctx.report(decl, MESSAGE)
    for rule in root.walk_rules():
        printed: str | None = None
        index = 0
        for value in rule_selector(rule).split(','):
            selector = value.lstrip(JS_WHITESPACE)
            if selector.startswith('//'):
                if printed is None:
                    printed = to_string(rule)
                i = index + len(value) - len(selector)
                comment = printed[i:].split(ctx.newline)[0]
                ctx.report(rule, MESSAGE, index=i, end_index=i + len(comment))
            index += len(value) + 1
