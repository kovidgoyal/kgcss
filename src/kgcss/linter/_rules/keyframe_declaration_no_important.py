# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""keyframe-declaration-no-important: stylelint's lib/rules/keyframe-declaration-no-important."""

import re
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import JS_WHITESPACE, Root, to_string

RE_KEYFRAMES: Final = re.compile(r'^(-(o|moz|ms|webkit)-)?keyframes$', re.IGNORECASE)
RE_IMPORTANT: Final = re.compile(rf'![{re.escape(JS_WHITESPACE)}]*important\b', re.IGNORECASE | re.ASCII)


def check(root: Root, ctx: Context) -> None:
    for keyframes in root.walk_at_rules():
        if not RE_KEYFRAMES.match(keyframes.name):
            continue
        for decl in keyframes.walk_decls():  # nested @keyframes report their declarations again, as in stylelint
            if decl.important and (m := RE_IMPORTANT.search(to_string(decl))):
                ctx.report(decl, 'Disallowed !important', index=m.start(), end_index=m.end())
