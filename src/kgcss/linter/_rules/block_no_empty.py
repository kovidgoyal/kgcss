# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""block-no-empty: stylelint's lib/rules/block-no-empty."""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._messages import EMPTY_BLOCK
from kgcss.linter._postcss import JS_WHITESPACE, AtRule, Comment, Root, Rule
from kgcss.linter._utils import before_block_string, is_configuration_comment

OPTIONS: Final = frozenset({'ignore'})


def check(root: Root, ctx: Context) -> None:
    for statement in root.walk():
        if not isinstance(statement, (Rule, AtRule)) or statement.nodes is None:
            continue
        ignore_comments = ctx.ignores('comments')
        if any(
            not (isinstance(child, Comment) and (ignore_comments or is_configuration_comment(child)))
            for child in statement.nodes
        ):
            continue
        after = statement.raws.after
        if after is not None and after.strip(JS_WHITESPACE):
            continue
        index = len(before_block_string(statement, no_raw_before=True))
        if statement.raws.between is None:  # SugarSS
            index -= 1
        start = ctx.source.position_by(statement, index)
        end = ctx.source.node_range(statement)[1]
        ctx.report(statement, EMPTY_BLOCK, start=start, end=end)
