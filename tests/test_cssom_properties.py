# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Property based tests of the CSSOM."""

from hypothesis import given

from kgcss import Rule, RuleList, RuleType, SerializeOptions, Stylesheet, parse_stylesheet, serialize

from .strategies import css_text

PRESERVE = SerializeOptions(style='preserve')
NOT_IN_BROWSER = (RuleType.COMMENT, RuleType.CHARSET)


def follow_browser_path(sheet: Stylesheet, path: tuple[int, ...]) -> Rule:
    rules: RuleList | None = sheet.rules
    rule: Rule | None = None
    for index in path:
        assert rules is not None
        rule = [r for r in rules if r.type not in NOT_IN_BROWSER][index]
        rules = rule.child_rules()
    assert rule is not None
    return rule


@given(css_text)
def test_cssom_invariants(css: str) -> None:
    sheet = parse_stylesheet(css)
    offsets = [e.offset for e in sheet.errors]
    assert offsets == sorted(offsets)
    assert serialize(sheet, PRESERVE) == css
    for rule in sheet.iter_rules():
        assert rule.parent_style_sheet is sheet
        assert serialize(rule, PRESERVE) in css
        assert rule.line is not None
        if (children := rule.child_rules()) is not None:
            for child in children:
                assert child.parent_rule is rule
        if rule.type not in NOT_IN_BROWSER:
            assert follow_browser_path(sheet, sheet.browser_index_path(rule)) is rule
    for rule in sheet.rules:
        assert rule.parent_rule is None
