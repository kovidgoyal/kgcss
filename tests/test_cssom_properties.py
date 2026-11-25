# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Property based tests of the CSSOM."""

import contextlib
from collections.abc import Iterable

from hypothesis import given
from hypothesis import strategies as st

from kgcss import (
    Component,
    DeclarationBlock,
    Property,
    PropertyValue,
    Rule,
    RuleList,
    RuleType,
    SerializeOptions,
    StyleRule,
    Stylesheet,
    parse_style,
    parse_stylesheet,
    parse_value,
    serialize,
)
from kgcss.cssom.declarations import normalize_name
from kgcss.cssom.exceptions import CSSSyntaxError
from kgcss.cssom.selector_list import check_selector_list
from kgcss.cssom.serializer import source_is_complete
from kgcss.cssom.values import Function, Other, Separator, SimpleBlock, UnicodeRange

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
        if source_is_complete(rule._source):
            # Rules left open by the end of the input are closed, so only complete ones are verbatim
            assert serialize(rule, PRESERVE) in css
        assert rule.line is not None
        if (children := rule.child_rules()) is not None:
            for child in children:
                assert child.parent_rule is rule
        if rule.type not in NOT_IN_BROWSER:
            assert follow_browser_path(sheet, sheet.browser_index_path(rule)) is rule
    for rule in sheet.rules:
        assert rule.parent_rule is None


def reference_effective(block: DeclarationBlock) -> list[tuple[str, str, bool]]:
    """An independent implementation of the effective view."""
    winners: dict[str, Property] = {}
    last_seen: dict[str, int] = {}
    for i, item in enumerate(block.items):
        if not isinstance(item, Property):
            continue
        name = item.name
        last_seen[name] = i
        current = winners.get(name)
        if current is None or item.important or not current.important:
            winners[name] = item
    order = sorted(winners, key=last_seen.__getitem__)
    return [(n, winners[n].value, winners[n].important) for n in order]


def summary(block: DeclarationBlock) -> list[tuple[str, str, bool]]:
    return [(p.name, p.value, p.important) for p in block]


@given(css_text)
def test_effective_view_matches_reference(css: str) -> None:
    block = parse_style(css)
    assert summary(block) == reference_effective(block)
    for item in block.items:
        if isinstance(item, Property):
            assert item.parent is block


NAMES = st.sampled_from(['a', 'B', 'c', '--x', '--X'])
VALUES = st.sampled_from(['1', 'red', 'f(1, 2)', '"s"', 'url(x.png)', ''])
OPERATIONS = st.lists(
    st.one_of(
        st.tuples(st.just('set'), NAMES, VALUES, st.sampled_from(['', 'important'])),
        st.tuples(st.just('remove'), NAMES),
        st.tuples(st.just('remove_declaration'), st.integers(0, 10)),
        st.tuples(st.just('priority'), st.integers(0, 10), st.sampled_from(['', 'important'])),
    ),
    max_size=20,
)


@given(st.sampled_from(['', 'a: 1; b: 2; a: 3 !important', 'c: x; /* c */ --x: y; B: z; c: w']), OPERATIONS)
def test_mutations_keep_invariants(css: str, operations: list[tuple[object, ...]]) -> None:
    block = parse_style(css)
    for op in operations:
        match op:
            case ('set', str(name), str(value), str(priority)):
                block.set(name, value, priority)
                matching = [i for i in block.items if isinstance(i, Property) and i.name == normalize_name(name)]
                assert len(matching) == 1
                assert block.get_value(name) == value
            case ('remove', str(name)):
                block.remove(name)
                assert name not in block
            case ('remove_declaration', int(index)):
                props = [i for i in block.items if isinstance(i, Property)]
                if props:
                    prop = props[index % len(props)]
                    block.remove_declaration(prop)
                    assert prop.parent is None
            case ('priority', int(index), str(priority)):
                props = [i for i in block.items if isinstance(i, Property)]
                if props:
                    props[index % len(props)].priority = priority
        # The cached effective view always matches a fresh computation
        assert summary(block) == reference_effective(block)
        assert all(i.parent is block for i in block.items if isinstance(i, Property))


def check_value_tree(value: PropertyValue) -> None:
    stack = [value]
    while stack:
        v = stack.pop()
        assert len(v) == len([i for i in v.items if not isinstance(i, Separator)])
        for item in v.items:
            # Every component's text appears verbatim in its parent's text (requirements V6)
            assert item.css_text in v.css_text
            if isinstance(item, Function):
                stack.append(item.arguments)
            elif isinstance(item, SimpleBlock):
                stack.append(item.content)


def component_key(c: Component) -> tuple[str, object]:
    # Compared by meaning: the text of a hex escape can gain its terminating space when joined
    if isinstance(c, Function | SimpleBlock):
        return c.type, c.css_text
    return c.type, getattr(c, 'value', c.css_text)


@given(css_text)
def test_values(css: str) -> None:
    for name in (None, 'unicode-range'):
        value = parse_value(css, property_name=name)
        check_value_tree(value)
        for i in range(len(value)):
            # Removing a component leaves exactly the others
            # Reparse the same way: a value that was not a valid unicode-range list can become one
            is_urange = any(isinstance(c, UnicodeRange) for c in value)
            remaining = parse_value(value.text_without(i), property_name=name if is_urange else None)
            expected = [component_key(c) for j, c in enumerate(value) if j != i]
            if remaining.css_text and not any(isinstance(c, Other) for c in value):
                assert [component_key(c) for c in remaining] == expected, (css, i)


@given(css_text)
def test_parsed_values_of_declarations(css: str) -> None:
    for item in parse_style(css).items:
        if isinstance(item, Property):
            parsed = item.parsed_value
            check_value_tree(parsed)
            assert parsed.css_text == item.value


@given(css_text)
def test_pretty_serialization_is_idempotent(css: str) -> None:
    # requirements S2: ser(parse(ser(x))) == ser(x)
    for options in (SerializeOptions(), SerializeOptions(normalize=False), SerializeOptions(drop_empty_rules=False)):
        once = serialize(parse_stylesheet(css), options)
        twice = serialize(parse_stylesheet(once), options)
        assert twice == once, (css, options)


@given(css_text)
def test_declaration_serialization_is_idempotent(css: str) -> None:
    block = parse_style(css)
    multi = block.css_text
    assert parse_style(multi).css_text == multi
    single = block.to_css(single_line=True)
    assert '"' not in single
    assert '\n' not in single
    assert parse_style(single).to_css(single_line=True) == single


@given(css_text)
def test_preserve_serialization_of_unchanged_rules(css: str) -> None:
    sheet = parse_stylesheet(css)
    for rule in sheet.iter_rules():
        if source_is_complete(rule._source):
            # Rules left open by the end of the input are closed, so only complete ones are verbatim
            assert serialize(rule, PRESERVE) in css


RULE_TEXTS = st.sampled_from([
    'a { b: c }', 'x > y { z: 1px !important }', '@media print { p { q: r } }', '/* comment */',
    '@import "i.css"', '@namespace n "u"', '@layer l;', '@font-face { src: url(f.otf) }', '@foo bar',
    'd { e: f(', 'g { h: "i', '@media (x) { j {', 'k { &:hover { l: m } n: o }',
])  # fmt: skip
EDITS = st.lists(
    st.one_of(
        st.tuples(st.just('insert'), RULE_TEXTS, st.integers(0, 20)),
        st.tuples(st.just('add'), RULE_TEXTS),
        st.tuples(st.just('delete'), st.integers(0, 20)),
        st.tuples(st.just('css_text'), st.integers(0, 20), RULE_TEXTS),
        st.tuples(st.just('set'), st.integers(0, 20), st.sampled_from(['color', 'margin', '--v']), VALUES),
        st.tuples(st.just('selector'), st.integers(0, 20), st.sampled_from(['p', 'a, b', 'q > r'])),
    ),
    max_size=8,
)


@given(css_text, EDITS)
def test_edits_keep_preserve_output_equivalent(css: str, edits: list[tuple[object, ...]]) -> None:
    sheet = parse_stylesheet(css)
    for edit in edits:
        rules = list(sheet.iter_rules())
        match edit:
            case ('insert', str(text), int(index)):
                with contextlib.suppress(ValueError, IndexError):
                    sheet.insert_rule(text, index % (len(sheet.rules) + 1))
            case ('add', str(text)):
                with contextlib.suppress(ValueError):
                    sheet.add(text)
            case ('delete', int(index)) if sheet.rules:
                sheet.delete_rule(index % len(sheet.rules))
            case ('css_text', int(index), str(text)) if rules:
                with contextlib.suppress(ValueError):
                    rules[index % len(rules)].css_text = text
            case ('set', int(index), str(name), str(value)) if rules:
                rule = rules[index % len(rules)]
                if isinstance(rule, StyleRule):
                    rule.style.set(name, value)
            case ('selector', int(index), str(selector)) if rules:
                rule = rules[index % len(rules)]
                if isinstance(rule, StyleRule):
                    rule.selector_text = selector
        for rule in sheet.iter_rules():
            assert rule.parent_style_sheet is sheet
            if (children := rule.child_rules()) is not None:
                assert all(child.parent_rule is rule for child in children)
    pretty = serialize(sheet)
    # Writing the edited sheet in preserve mode and reading it back gives the same sheet
    assert serialize(parse_stylesheet(serialize(sheet, PRESERVE))) == pretty
    assert serialize(parse_stylesheet(pretty)) == pretty
    assert serialize(sheet.copy()) == pretty


@given(css_text)
def test_url_rewriting_changes_only_urls(css: str) -> None:
    from kgcss import iter_urls, replace_urls, replace_urls_in_text
    from kgcss.cssom.urls import find_urls
    from kgcss.syntax import parse_component_value_list

    def mapped(url: str) -> str:
        return 'X' + url

    out = replace_urls_in_text(css, mapped)
    assert replace_urls_in_text(css, lambda u: u) is css

    # Outside the URLs, found separately in the input and the output, the text is unchanged
    def outside_urls(text: str) -> str:
        parts, pos = [], 0
        for span in find_urls(parse_component_value_list(text), top_level_rules=True):
            parts.append(text[pos : span.start])
            pos = span.end
        return ''.join(parts) + text[pos:]

    assert outside_urls(out) == outside_urls(css)
    # The object model sees the same URLs, mapped
    sheet = parse_stylesheet(css)
    urls = list(iter_urls(sheet))
    assert list(iter_urls(parse_stylesheet(out))) == [mapped(u) for u in urls]
    replace_urls(sheet, mapped)
    assert list(iter_urls(sheet)) == [mapped(u) for u in urls]


import_lines = st.sampled_from((
    '@import "a.css";', '@import url(b.css) print;', '@import "c.css" layer(x) supports(display: grid) screen;',
    '@import "missing.css";', '@import "main.css";', '@import "a.css" layer;', '@charset "utf-8";',
    '@namespace svg url(http://svg);', '@layer p, q;', '/* c */',
))  # fmt: skip
sheet_text = st.builds(lambda head, body: ' '.join(head) + body, st.lists(import_lines, max_size=6), css_text)


@given(sheet_text, sheet_text, sheet_text, sheet_text)
def test_resolve_imports_gives_a_valid_sheet(main: str, a: str, b: str, c: str) -> None:
    # Whatever the sheets contain, the result has every rule in a valid place and fetching terminates
    from kgcss import resolve_imports

    files = {'http://x/main.css': main, 'http://x/a.css': a, 'http://x/b.css': b, 'http://x/c.css': c}
    calls: list[str] = []

    def fetch(url: str) -> tuple[None, str] | None:
        calls.append(url)
        return (None, files[url]) if url in files else None

    sheet = parse_stylesheet(main, href='http://x/main.css', fetcher=fetch)
    out = resolve_imports(sheet)
    text = serialize(out, SerializeOptions(style='preserve', drop_empty_rules=False))
    again = parse_stylesheet(text)
    assert not [e for e in again.errors if e.kind == 'misplaced-rule'], text
    assert len(calls) <= 64

    # Style rules with invalid selectors, such as a;@media x {}, can parse differently once nested,
    # and browsers ignore them and their contents
    def valid(rule: Rule | None) -> bool:
        while rule is not None:
            if isinstance(rule, StyleRule) and not valid_selector(rule.selector_text):
                return False
            rule = rule.parent_rule
        return True

    def valid_styles(rules: Iterable[Rule]) -> int:
        return sum(1 for r in rules if isinstance(r, StyleRule) and valid(r))

    assert valid_styles(sheet.iter_rules(follow_imports=True)) == valid_styles(again.iter_rules())


def valid_selector(text: str) -> bool:
    # A coarse check: selectors are not parsed yet (plan 03). A selector cannot end with a colon,
    # and nested, a:{b} is a declaration.
    try:
        check_selector_list(text)
    except CSSSyntaxError:
        return False
    return not text.rstrip().endswith(':')
