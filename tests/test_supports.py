# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""@supports conditions.

The cases come from CSS Conditional Rules Level 3, 4 and 5. Property tests check the round trip
through to_css() and evaluation against a truth table.
"""

import doctest
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import kgcss.supports
from kgcss import (
    ImportRule,
    SupportsCondition,
    SupportsDeclaration,
    SupportsFunction,
    SupportsRule,
    parse_stylesheet,
    parse_supports_condition,
    supports_matches,
)
from kgcss.media import GeneralEnclosed
from kgcss.supports import SupportsAnd, SupportsNode, SupportsNot, SupportsOr

SUPPORTED = {('display', 'grid'), ('display', 'flex'), ('color', 'red'), ('--x', '{a}')}


def declaration(d: SupportsDeclaration) -> bool:
    return (d.name, d.value) in SUPPORTED


@pytest.mark.parametrize(
    ('css', 'expected', 'canonical'),
    [
        ('(display: grid)', True, '(display: grid)'),
        ('( DISPLAY :grid )', True, '(display: grid)'),
        ('(display: table)', False, '(display: table)'),
        ('not (display: grid)', False, 'not (display: grid)'),
        ('NOT (display: table)', True, 'not (display: table)'),
        ('(display: grid) and (color: red)', True, '(display: grid) and (color: red)'),
        ('(display: grid) and (color: blue)', False, '(display: grid) and (color: blue)'),
        ('(display: table) or (color: red)', True, '(display: table) or (color: red)'),
        ('(display: table) OR (color: blue)', False, '(display: table) or (color: blue)'),
        ('((display: grid))', True, '(display: grid)'),
        (
            '(not (display: table)) and ((color: red) or (a: b))',
            True,
            '(not (display: table)) and ((color: red) or (a: b))',
        ),
        ('(display: grid !important)', True, '(display: grid !important)'),
        ('(--x: {a})', True, '(--x: {a})'),
        ('(--X: {a})', False, '(--X: {a})'),  # custom properties are case-sensitive
        # Comments and whitespace are not significant
        ('/* a */ (display: /* b */ grid) /* c */', True, '(display: grid)'),
        # <general-enclosed> is false, not unknown as in media queries, so not makes it true
        ('(foo bar)', False, '(foo bar)'),
        ('not (foo bar)', True, 'not (foo bar)'),
        ('foo(display: grid)', False, 'foo(display: grid)'),
        ('not(display: table)', False, 'not(display: table)'),  # not( is a function
        ('(display: grid; color: red)', False, '(display: grid; color: red)'),
        ('()', False, '()'),
        # Left open by the end of the input, closed as browsers do
        ('(display: grid', True, '(display: grid)'),
        ('(display: table) or selector(a', True, '(display: table) or selector(a)'),
        ('(display: grid) or (foo bar)', True, '(display: grid) or (foo bar)'),
        # The functions of <supports-feature>: by default, selector() if kgcss can parse its selector
        ('selector(a > b:hover)', True, 'selector(a > b:hover)'),
        ('SELECTOR( a b )', True, 'selector(a b)'),
        ('selector(a, b)', False, 'selector(a, b)'),  # one <complex-selector>, not a list
        ('selector(a >)', False, 'selector(a >)'),
        ('selector()', False, 'selector()'),
        ('font-tech(color-COLRv1)', False, 'font-tech(color-COLRv1)'),
        ('font-format(woff2)', False, 'font-format(woff2)'),
        ('at-rule(@media)', False, 'at-rule(@media)'),
    ],
)
def test_conditions(css: str, expected: bool, canonical: str) -> None:
    c = parse_supports_condition(css)
    assert c.valid, c.error
    assert c.text == css
    assert c.matches(declaration=declaration) is expected
    assert supports_matches(css, declaration=declaration) is expected
    assert c.to_css() == canonical
    assert parse_supports_condition(canonical).to_css() == canonical


@pytest.mark.parametrize(
    'css',
    [
        '',
        '/* */',
        'display: grid',  # a bare declaration is only valid in supports() of @import
        '(a: b) and (c: d) or (e: f)',
        '(a: b) or (c: d) and (e: f)',
        'not (a: b) and (c: d)',
        'not not (a: b)',
        '(a: b) and',
        'and (a: b)',
        '(a: b) (c: d)',
        '(a: b) and(c: d)',  # and( is a function
        '[a: b]',
        'a',
    ],
)
def test_invalid(css: str) -> None:
    c = parse_supports_condition(css)
    assert not c.valid
    assert c.error
    assert c.root is None
    assert c.to_css() == 'not all'
    # Nothing supports a condition that does not parse
    assert not c.matches(declaration=lambda d: True, function=lambda f: True)
    assert not supports_matches(css, declaration=lambda d: True)


def test_model() -> None:
    c = parse_supports_condition('(a: b) and (not (c: d !important)) and selector(e) and (f g)')
    assert c.root == SupportsAnd(
        (
            SupportsDeclaration('a', 'b'),
            SupportsNot(SupportsDeclaration('c', 'd', important=True)),
            SupportsFunction('selector', 'e'),
            GeneralEnclosed('(f g)'),
        )
    )
    assert list(c.declarations()) == [SupportsDeclaration('a', 'b'), SupportsDeclaration('c', 'd', True)]
    assert list(parse_supports_condition('x').declarations()) == []
    # Empty values are passed on, for the callback to decide
    assert parse_supports_condition('(--x:)').root == SupportsDeclaration('--x', '')
    assert parse_supports_condition('(--x:)').to_css() == '(--x:)'
    # Results are cached by text
    assert parse_supports_condition('(a: b)') is parse_supports_condition('(a: b)')


def test_function_callback() -> None:
    seen: list[SupportsFunction] = []

    def function(f: SupportsFunction) -> bool:
        seen.append(f)
        return f.name == 'font-format' and f.argument.lower() in ('woff', 'woff2')

    css = 'font-format(WOFF2) and (not font-tech(color-sbix)) and (not selector(a))'
    assert supports_matches(css, declaration=declaration, function=function)
    assert seen == [
        SupportsFunction('font-format', 'WOFF2'),
        SupportsFunction('font-tech', 'color-sbix'),
        SupportsFunction('selector', 'a'),
    ]


def test_callbacks_are_asked_only_when_needed() -> None:
    seen: list[str] = []

    def record(d: SupportsDeclaration) -> bool:
        seen.append(d.name)
        return d.name == 'a'

    assert supports_matches('(a: 1) or (b: 2)', declaration=record)
    assert seen == ['a']
    seen.clear()
    assert not supports_matches('(b: 2) and (a: 1)', declaration=record)
    assert seen == ['b']


def test_supports_rule() -> None:
    sheet = parse_stylesheet(
        '@supports (display: grid) { a { x: y } } @supports (display: table) { b { x: y } } @supports nonsense { c {} }'
    )
    rules = sheet.rules.of_type(kgcss.RuleType.SUPPORTS)
    assert [r.matches(declaration=declaration) for r in rules] == [True, False, False]
    assert [r.condition.valid for r in rules] == [True, True, False]
    assert rules[0].condition.root == SupportsDeclaration('display', 'grid')
    # The condition follows condition_text, and reading it changes nothing
    rule = rules[1]
    assert rule.condition.text == rule.condition_text == '(display: table)'
    rule.condition_text = 'not (display: table)'
    assert rule.matches(declaration=declaration)
    assert sheet.css_text.count('not (display: table)') == 1
    # The rules of the @supports rules that apply, as a stylizer would want them
    applied = [
        r.selector_text
        for r in sheet.iter_rules(kgcss.RuleType.STYLE)
        if not isinstance(r.parent_rule, SupportsRule) or r.parent_rule.matches(declaration=declaration)
    ]
    assert applied == ['a', 'b']
    assert SupportsRule('selector(p)').matches(declaration=declaration)


def test_import_supports() -> None:
    sheet = parse_stylesheet(
        '@import "a.css" supports(display: grid); @import "b.css" supports((display: table) or (color: red));'
        '@import "c.css" supports(not (display: grid)); @import "d.css"; @import "e.css" supports(a b)'
    )
    rules = [r for r in sheet.rules if isinstance(r, ImportRule)]
    assert [r.supports_matches(declaration=declaration) for r in rules] == [True, True, False, True, False]
    first = rules[0].supports_condition
    assert first is not None
    assert first.text == 'display: grid'
    assert first.root == SupportsDeclaration('display', 'grid')
    assert rules[3].supports_condition is None
    second = rules[1].supports_condition
    assert second is not None
    assert second.to_css() == '(display: table) or (color: red)'
    assert ImportRule('x', supports='selector(a)').supports_matches(declaration=declaration)


def test_doctests() -> None:
    assert doctest.testmod(kgcss.supports).failed == 0


# Round trip and evaluation of random conditions

NAMES = ('display', 'color', 'a', '--x')
VALUES = ('grid', 'red', '1px', '{a}', 'b c')
FUNCTIONS = (
    SupportsFunction('selector', 'a > b'),
    SupportsFunction('selector', 'a,'),
    SupportsFunction('font-tech', 'variations'),
    SupportsFunction('font-format', 'woff2'),
    SupportsFunction('at-rule', '@media'),
)
LEAVES = st.one_of(
    st.builds(SupportsDeclaration, st.sampled_from(NAMES), st.sampled_from(VALUES), st.booleans()),
    st.sampled_from(FUNCTIONS),
    st.sampled_from((GeneralEnclosed('(foo bar)'), GeneralEnclosed('fn(1, 2)'))),
)


@st.composite
def conditions(draw: st.DrawFn, depth: int = 0) -> SupportsNode:
    kind = draw(st.integers(0, 4)) if depth < 3 else 0
    if kind <= 1:
        return draw(LEAVES)
    if kind == 2:
        return SupportsNot(draw(conditions(depth + 1)))
    cs = tuple(draw(st.lists(conditions(depth + 1), min_size=2, max_size=3)))
    return SupportsAnd(cs) if kind == 3 else SupportsOr(cs)


def oracle(c: SupportsNode, answers: dict[object, bool]) -> bool:
    """The truth of a condition, as plain boolean logic over the answers for its leaves."""
    match c:
        case SupportsNot():
            return not oracle(c.condition, answers)
        case SupportsAnd():
            return all(oracle(x, answers) for x in c.conditions)
        case SupportsOr():
            return any(oracle(x, answers) for x in c.conditions)
        case GeneralEnclosed():
            return False
        case _:
            return answers[c]


@pytest.mark.slow
def test_strategy_covers_the_structures() -> None:
    # The strategy gives every kind of node, at the top and nested
    counts: Counter[str] = Counter()

    @settings(max_examples=2000, database=None)
    @given(conditions())
    def count(c: SupportsNode) -> None:
        counts['top:' + type(c).__name__] += 1
        if isinstance(c, SupportsNot | SupportsAnd | SupportsOr):
            inner = (c.condition,) if isinstance(c, SupportsNot) else c.conditions
            counts.update('nested:' + type(x).__name__ for x in inner)

    count()
    kinds = ('SupportsNot', 'SupportsAnd', 'SupportsOr', 'SupportsDeclaration', 'SupportsFunction', 'GeneralEnclosed')
    # Hypothesis does not repeat examples, and there are only a few different leaves, so few are at the top
    for kind in kinds:
        assert counts[f'top:{kind}'] > 0, counts
        assert counts[f'nested:{kind}'] > 100, counts


@given(conditions())
def test_round_trip(c: SupportsNode) -> None:
    css = c.to_css()
    again = parse_supports_condition(css)
    assert again.valid, (css, again.error)
    assert again.root == c, css
    assert again.to_css() == css


@given(conditions(), st.data())
def test_evaluation(c: SupportsNode, data: st.DataObject) -> None:
    answers: dict[object, bool] = {}

    def answer(leaf: SupportsDeclaration | SupportsFunction) -> bool:
        if leaf not in answers:
            answers[leaf] = data.draw(st.booleans())
        return answers[leaf]

    got = parse_supports_condition(c.to_css()).matches(declaration=answer, function=answer)
    # Every leaf that decides the result was asked, and the oracle reuses the answers
    for leaf in [*FUNCTIONS, *(SupportsDeclaration(n, v, i) for n in NAMES for v in VALUES for i in (False, True))]:
        answers.setdefault(leaf, False)
    assert got == oracle(c, answers)


@given(st.text())
def test_any_text(text: str) -> None:
    c = parse_supports_condition(text)
    assert isinstance(c, SupportsCondition)
    assert c.valid == (c.root is not None)
    assert isinstance(c.matches(declaration=lambda d: True), bool)
    if c.valid:
        assert parse_supports_condition(c.to_css()).to_css() == c.to_css()
