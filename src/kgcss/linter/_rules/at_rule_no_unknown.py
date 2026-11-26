# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""at-rule-no-unknown: stylelint's lib/rules/at-rule-no-unknown."""

from kgcss._css_data import AT_KEYWORDS
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._utils import is_standard_syntax_at_rule, vendor_prefix


def check(root: Root, ctx: Context) -> None:
    for at_rule in root.walk_at_rules():
        if not is_standard_syntax_at_rule(at_rule):
            continue
        name = at_rule.name
        if vendor_prefix(name) or name.lower() in AT_KEYWORDS:
            continue
        at_name = '@' + name
        ctx.report(at_rule, f'Unknown at-rule "{at_name}"', (at_name,), word=at_name)
