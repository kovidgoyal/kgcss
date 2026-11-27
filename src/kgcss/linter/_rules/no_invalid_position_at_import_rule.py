# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-invalid-position-at-import-rule: stylelint's lib/rules/no-invalid-position-at-import-rule."""

from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._messages import INVALID_POSITION_IMPORT_RULE
from kgcss.linter._postcss import AtRule, Root, to_string
from kgcss.linter._utils import is_standard_syntax_at_rule, is_standard_syntax_rule

OPTIONS: Final = frozenset({'ignoreAtRules'})


def check(root: Root, ctx: Context) -> None:
    invalid_position = False
    for node in root.walk():
        name = node.name.lower() if isinstance(node, AtRule) else ''
        if (
            isinstance(node, AtRule)
            and name not in ('charset', 'import')
            and not (name == 'layer' and node.nodes is None)
            and not ctx.matches('ignoreAtRules', node.name)
            and is_standard_syntax_at_rule(node)
        ) or is_standard_syntax_rule(node):
            invalid_position = True
            continue
        if isinstance(node, AtRule) and name == 'import' and invalid_position:
            ctx.report(node, INVALID_POSITION_IMPORT_RULE, word=to_string(node))
