# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""font-family-no-duplicate-names: stylelint's lib/rules/font-family-no-duplicate-names."""

from typing import Final

from kgcss._css_data import FONT_FAMILY_KEYWORDS
from kgcss.linter._engine import Context
from kgcss.linter._fonts import RE_FONT_FAMILY_PROPERTY, find_font_family
from kgcss.linter._messages import DUPLICATE_FONT_FAMILY_NAME
from kgcss.linter._postcss import Root
from kgcss.linter._utils import declaration_value_index, js_trim_str

OPTIONS: Final = frozenset({'ignoreFontFamilyNames'})


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        if not RE_FONT_FAMILY_PROPERTY.match(decl.prop):
            continue
        keywords: set[str] = set()
        names: set[str] = set()
        for node in find_font_family(decl.value):
            family = js_trim_str(node.value)
            if ctx.matches('ignoreFontFamilyNames', family):
                continue
            raw = node.quote + family + node.quote if node.type == 'string' else family
            if node.type == 'word' and family.lower() in FONT_FAMILY_KEYWORDS:
                seen = family.lower() in keywords
                keywords.add(family)  # not lowercased, as in stylelint
            else:
                seen = family in names
                names.add(family)
            if seen:
                index = declaration_value_index(decl) + node.source_index
                ctx.report(decl, DUPLICATE_FONT_FAMILY_NAME, (family,), index=index, end_index=index + len(raw))
