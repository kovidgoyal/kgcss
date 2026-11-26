# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""font-family-no-missing-generic-family-keyword: stylelint's rule of that name."""

from typing import Final

from kgcss._css_data import FONT_FAMILY_KEYWORDS, SYSTEM_FONT_KEYWORDS
from kgcss.linter._engine import Context
from kgcss.linter._fonts import RE_FONT_FAMILY_PROPERTY, find_font_family
from kgcss.linter._postcss import AtRule, Root
from kgcss.linter._utils import declaration_value_index, is_standard_syntax_value, is_variable, postcss_list_comma

IGNORED_AT_RULES: Final = frozenset({'font-face', 'font-palette-values'})


def is_last_font_family_variable(value: str) -> bool:
    last = postcss_list_comma(value)[-1]
    return is_variable(last) or not is_standard_syntax_value(last)


OPTIONS: Final = frozenset({'ignoreFontFamilies'})


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if not RE_FONT_FAMILY_PROPERTY.match(decl.prop):
            continue
        parent = decl.parent
        if isinstance(parent, AtRule) and parent.name.lower() in IGNORED_AT_RULES:
            continue
        if decl.prop == 'font' and decl.value.lower() in SYSTEM_FONT_KEYWORDS:
            continue
        if is_last_font_family_variable(decl.value):
            continue
        families = find_font_family(decl.value)
        if not families or any(n.type == 'word' and n.value.lower() in FONT_FAMILY_KEYWORDS for n in families):
            continue
        if any(ctx.matches('ignoreFontFamilies', n.value) for n in families):
            continue
        start = declaration_value_index(decl)
        last = families[-1]
        ctx.report(
            decl,
            'Missing generic font family',
            index=start + last.source_index,
            end_index=start + last.source_end_index,
        )
