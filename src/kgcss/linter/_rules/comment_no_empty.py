# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""comment-no-empty: stylelint's lib/rules/comment-no-empty."""

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root


def check(root: Root, ctx: Context) -> None:
    for comment in root.walk_comments():
        if not comment.text:
            ctx.report(comment, 'Empty comment')
