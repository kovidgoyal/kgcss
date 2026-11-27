# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""string-no-newline: stylelint's lib/rules/string-no-newline.

Strings in selectors, at-rule params and values are tokenized with
@csstools' tokenizer, and every string that a newline cuts short is
reported, as the tokenizer reports it.
"""

import re
from typing import Final

from kgcss.linter._csstools import ParseError, ParseErrorMessage, ParseErrorWithToken, tokenize
from kgcss.linter._engine import Context
from kgcss.linter._messages import NEWLINE_IN_STRING
from kgcss.linter._postcss import AtRule, Declaration, Node, Root, Rule
from kgcss.linter._utils import at_rule_param_index, declaration_value_index, is_standard_syntax_selector

OPTIONS: Final = frozenset({'ignore'})
RE_NEWLINE: Final = re.compile(r'\r?\n')


def check_text(ctx: Context, node: Node, text: str, start: int) -> None:
    if not RE_NEWLINE.search(text) or ('"' not in text and "'" not in text):  # a newline in a string
        return

    def on_error(err: ParseError) -> None:
        if isinstance(err, ParseErrorWithToken) and err.message == ParseErrorMessage.UNEXPECTED_NEW_LINE_IN_STRING:
            token = err.token
            ctx.report(node, NEWLINE_IN_STRING, index=start + token.start, end_index=start + token.end + 1)

    tokenize(text, on_parse_error=on_error)


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
