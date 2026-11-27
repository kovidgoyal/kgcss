# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""keyframe-block-no-duplicate-selectors: stylelint's rule of that name."""

from kgcss.linter._engine import Context
from kgcss.linter._messages import DUPLICATE_KEYFRAME_SELECTOR
from kgcss.linter._postcss import Root
from kgcss.linter._selector_helpers import stripped_selector_source
from kgcss.linter._selector_parser import Selector
from kgcss.linter._utils import RE_KEYFRAMES_NAME, is_standard_syntax_rule, parse_selector_or_none, rule_selector


def check(root: Root, ctx: Context) -> None:
    for keyframes in root.walk_at_rules():
        if not RE_KEYFRAMES_NAME.match(keyframes.name):
            continue
        seen: set[str] = set()
        for rule in keyframes.walk_rules():
            if (
                not is_standard_syntax_rule(rule)
                or (parsed := parse_selector_or_none(rule_selector(rule), ctx.cache)) is None
            ):
                continue
            for selector in parsed.nodes:
                assert isinstance(selector, Selector)
                text, index, end_index = stripped_selector_source(selector)
                key = text.lower()
                if key in seen:
                    ctx.report(rule, DUPLICATE_KEYFRAME_SELECTOR, (text,), index=index, end_index=end_index)
                else:
                    seen.add(key)
