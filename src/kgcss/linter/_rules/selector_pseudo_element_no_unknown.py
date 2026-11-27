# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""selector-pseudo-element-no-unknown: stylelint's rule of that name."""

from typing import Final

from kgcss._css_data import PSEUDO_ELEMENTS
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root
from kgcss.linter._selector_parser import Pseudo
from kgcss.linter._utils import (
    is_standard_syntax_rule,
    is_standard_syntax_selector,
    parse_selector_or_none,
    rule_selector,
    selector_index,
    vendor_prefix,
)

OPTIONS: Final = frozenset({'ignorePseudoElements'})


def check(root: Root, ctx: Context) -> None:
    for rule in root.walk_rules():
        if ':' not in rule.selector or not is_standard_syntax_rule(rule):
            continue
        parsed = parse_selector_or_none(rule_selector(rule), ctx.cache)
        if parsed is None:
            continue
        pseudos = parsed.find_all(Pseudo)
        for pseudo in pseudos:
            value = pseudo.value
            if not is_standard_syntax_selector(value) or value[:2] != '::':
                continue
            name = value[2:]
            if ctx.matches('ignorePseudoElements', name) or vendor_prefix(name) or name.lower() in PSEUDO_ELEMENTS:
                continue
            index = selector_index(pseudo)
            ctx.report(
                rule, f'Unknown pseudo-element selector "{value}"', (value,), index=index, end_index=index + len(value)
            )
