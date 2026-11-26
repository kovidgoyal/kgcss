# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""string-no-newline: stylelint's lib/rules/string-no-newline.

Strings in selectors, at-rule params and values are tokenized, and every
string that a newline cuts short (a bad-string token) is reported, from its
quote to the newline.
"""

import re
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import AtRule, Declaration, Node, Root, Rule
from kgcss.linter._utils import at_rule_param_index, declaration_value_index, is_standard_syntax_selector
from kgcss.tokenizer import TokenType, tokenize

OPTIONS: Final = frozenset({'ignore'})
RE_NEWLINE: Final = re.compile(r'\r?\n')


def check_text(ctx: Context, node: Node, text: str, start: int) -> None:
    if not RE_NEWLINE.search(text):
        return
    for token in tokenize(text):
        if token.type is TokenType.BAD_STRING:
            ctx.report(node, 'Newline in string', index=start + token.pos, end_index=start + token.end)


def check(root: Root, ctx: Context) -> None:
    for node in root.walk():
        if isinstance(node, AtRule):
            if not ctx.ignores('at-rule-preludes'):
                check_text(ctx, node, node.params, at_rule_param_index(node))
        elif isinstance(node, Declaration):
            if not ctx.ignores('declaration-values'):
                check_text(ctx, node, node.value, declaration_value_index(node))
        elif isinstance(node, Rule) and RE_NEWLINE.search(node.selector) and is_standard_syntax_selector(node.selector):
            check_text(ctx, node, node.selector, 0)
