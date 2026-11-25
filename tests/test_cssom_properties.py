# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Property based tests of the CSSOM."""

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
    Stylesheet,
    parse_style,
    parse_stylesheet,
    parse_value,
    serialize,
)
from kgcss.cssom.declarations import normalize_name
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
        assert serialize(rule, PRESERVE) in css
