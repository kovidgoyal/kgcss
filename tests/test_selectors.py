# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Selector parsing, the selector tree and specificity (plan 03, milestone 1).

The parser, pseudo-element, specificity and parse error tests are ported
from calibre's css_selectors (src/css_selectors/tests.py). The trees are
compared in css_selectors' text form, through cs_repr(). Where the expected
values differ from css_selectors, a comment says why.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss import (
    CSSSyntaxError,
    SelectorSyntaxError,
    StyleRule,
    invalid_selectors,
    parse_selectors,
    parse_stylesheet,
)
from kgcss.selectors import (
    AttributeSelector,
    ClassSelector,
    Combinator,
    ComplexSelector,
    CompoundSelector,
    FunctionalPseudoClass,
    IdSelector,
    LangPseudoClass,
    NestingSelector,
    NthPseudoClass,
    PseudoClass,
    PseudoElement,
    SelectorPseudoClass,
    TypeSelector,
    build_tree,
    compile_selectors,
)
from kgcss.tokenizer import TokenType, tokenize

from .strategies import css_text

# ---------------------------------------------------------------------------
# css_selectors' text form of a tree
# ---------------------------------------------------------------------------


def cs_repr(tree: ComplexSelector) -> str:
    """The repr() of css_selectors' parsed_tree for the same selector, without the pseudo-element. The
    arguments of the nth pseudo-classes are written as An+B rather than as tokens."""
    result = ''
    for i, (combinator, compound) in enumerate(tree.parts):
        current = cs_compound(compound)
        if i == 0:
            result = current
        else:
            assert combinator is not None
            comb = '<followed>' if combinator is Combinator.DESCENDANT else combinator.value
            result = f'CombinedSelector[{result} {comb} {current}]'
    return result


def cs_relative(tree: ComplexSelector) -> str:
    combinator = tree.parts[0][0]
    assert combinator is not None
    comb = '<followed>' if combinator is Combinator.DESCENDANT else combinator.value
    inner = ComplexSelector(((None, tree.parts[0][1]), *tree.parts[1:]))
    return f'RelativeSelector[{comb} {cs_repr(inner)}]'


def cs_args(text: str) -> str:
    values = [
        str(t.representation if t.type is TokenType.NUMBER else t.value)
        for t in tokenize(text)
        if t.type in (TokenType.IDENT, TokenType.STRING, TokenType.NUMBER)
    ]
    return repr(values)


def cs_compound(compound: CompoundSelector) -> str:
    t = compound.type
    if t is None or t.namespace in (None, '*'):
        result = f'Element[{t.name if t else "*"}]'
    else:
        result = f'Element[{t.namespace}|{t.name}]'
    for s in compound.subclasses:
        match s:
            case IdSelector():
                result = f'Hash[{result}#{s.name}]'
            case ClassSelector():
                result = f'Class[{result}.{s.name}]'
            case AttributeSelector():
                name = f'{s.namespace}|{s.name}' if s.namespace else s.name
                if s.operator is None:
                    result = f'Attrib[{result}[{name}]]'
                else:
                    flag = f' {s.case}' if s.case else ''
                    result = f'Attrib[{result}[{name} {s.operator} {s.value!r}{flag}]]'
            case PseudoClass():
                result = f'Pseudo[{result}:{s.name}]'
            case SelectorPseudoClass():
                if s.name == 'not':
                    result = f'Negation[{result}:not({", ".join(map(cs_repr, s.selectors))})]'
                elif s.name == 'has':
                    result = f'Relation[{result}:has({", ".join(map(cs_relative, s.selectors))})]'
                else:
                    result = f'Matching[{result}:{s.name}({", ".join(map(cs_repr, s.selectors))})]'
            case NthPseudoClass():
                of = f' of {", ".join(map(cs_repr, s.of))}' if s.of is not None else ''
                result = f'Function[{result}:{s.name}({s.a}n{s.b:+d}{of})]'
            case LangPseudoClass():
                result = f'Function[{result}:lang({list(s.ranges)!r})]'
            case FunctionalPseudoClass():
                result = f'Function[{result}:{s.name}({cs_args(s.arguments)})]'
            case _:
                raise AssertionError(f'Not in css_selectors: {s!r}')
    return result


def parse_trees(css: str) -> list[ComplexSelector]:
    return [build_tree(c) for c in compile_selectors(css)]


def repr_parse(css: str) -> list[str]:
    trees = parse_trees(css)
    for tree in trees:
        assert tree.pseudo_element is None
    return [cs_repr(t) for t in trees]


def parse_many(first: str, *others: str) -> list[str]:
    result = repr_parse(first)
    for other in others:
        assert repr_parse(other) == result, other
    return result


# ---------------------------------------------------------------------------
# Ported from css_selectors
# ---------------------------------------------------------------------------


def test_parser() -> None:
    assert parse_many('*') == ['Element[*]']
    assert parse_many('*|*') == ['Element[*]']
    assert parse_many('*|foo') == ['Element[foo]']
    assert parse_many('foo|*') == ['Element[foo|*]']
    assert parse_many('foo|bar') == ['Element[foo|bar]']
    # This will never match, but it is valid:
    assert parse_many('#foo#bar') == ['Hash[Hash[Element[*]#foo]#bar]']
    assert parse_many(
        'div>.foo', 'div> .foo', 'div >.foo', 'div > .foo', 'div \n>  \t \t .foo', 'div\r>\n\n\n.foo', 'div\f>\f.foo'
    ) == ['CombinedSelector[Element[div] > Class[Element[*].foo]]']
    assert parse_many('td.foo,.bar', 'td.foo, .bar', 'td.foo\t\r\n\f ,\t\r\n\f .bar') == [
        'Class[Element[td].foo]',
        'Class[Element[*].bar]',
    ]
    assert parse_many('div, td.foo, div.bar span') == [
        'Element[div]',
        'Class[Element[td].foo]',
        'CombinedSelector[Class[Element[div].bar] <followed> Element[span]]',
    ]
    assert parse_many('div > p') == ['CombinedSelector[Element[div] > Element[p]]']
    assert parse_many('td:first') == ['Pseudo[Element[td]:first]']
    assert parse_many('td :first') == ['CombinedSelector[Element[td] <followed> Pseudo[Element[*]:first]]']
    assert parse_many('a[name]', 'a[ name\t]') == ['Attrib[Element[a][name]]']
    assert parse_many('a [name]') == ['CombinedSelector[Element[a] <followed> Attrib[Element[*][name]]]']
    assert parse_many('a[rel="include"]', 'a[rel = include]') == ["Attrib[Element[a][rel = 'include']]"]
    assert parse_many("a[hreflang |= 'en']", 'a[hreflang|=en]') == ["Attrib[Element[a][hreflang |= 'en']]"]
    # css_selectors keeps the tokens of An+B, here they are evaluated
    assert parse_many('div:nth-child(10)') == ['Function[Element[div]:nth-child(0n+10)]']
    assert parse_many(':nth-child(2n+2)') == ['Function[Element[*]:nth-child(2n+2)]']
    assert parse_many('div:nth-of-type(10)') == ['Function[Element[div]:nth-of-type(0n+10)]']
    assert parse_many('div div:nth-of-type(10) .aclass') == [
        'CombinedSelector[CombinedSelector[Element[div] <followed> '
        'Function[Element[div]:nth-of-type(0n+10)]] '
        '<followed> Class[Element[*].aclass]]'
    ]
    assert parse_many('label:only') == ['Pseudo[Element[label]:only]']
    assert parse_many('a:lang(fr)') == ["Function[Element[a]:lang(['fr'])]"]
    assert parse_many('div:contains("foo")') == ["Function[Element[div]:contains(['foo'])]"]
    assert parse_many('div#foobar') == ['Hash[Element[div]#foobar]']
    assert parse_many('div:not(div.foo)') == ['Negation[Element[div]:not(Class[Element[div].foo])]']
    assert parse_many('td ~ th') == ['CombinedSelector[Element[td] ~ Element[th]]']

    # Level 4 selectors
    assert parse_many(':is(a, .b)', ':is( a , .b )', ':IS(a,.b)', ':is(a,/* x */.b)') == [
        'Matching[Element[*]:is(Element[a], Class[Element[*].b])]'
    ]
    assert parse_many(':where(a > b)') == ['Matching[Element[*]:where(CombinedSelector[Element[a] > Element[b]])]']
    assert parse_many('p:matches(.x)') == ['Matching[Element[p]:matches(Class[Element[*].x])]']
    # :is() and :where() use forgiving selector lists
    assert parse_many(':is(a, 3x, ::before, b)', ':is(a, , b)', ':is(a, b,)', ':is(a, :before, b)') == [
        'Matching[Element[*]:is(Element[a], Element[b])]'
    ]
    assert parse_many(':is()', ':is( )') == ['Matching[Element[*]:is()]']
    assert parse_many(':where(#)') == ['Matching[Element[*]:where()]']
    assert parse_many(':not(a, .b c)') == [
        'Negation[Element[*]:not(Element[a], CombinedSelector[Class[Element[*].b] <followed> Element[c]])]'
    ]
    assert parse_many(':not(:not(a))') == ['Negation[Element[*]:not(Negation[Element[*]:not(Element[a])])]']
    assert parse_many('div:has(> p, + span, a b, ~ i)', 'div:has(>p,+span,a b,~i)') == [
        'Relation[Element[div]:has(RelativeSelector[> Element[p]], '
        'RelativeSelector[+ Element[span]], '
        'RelativeSelector[<followed> CombinedSelector[Element[a] <followed> Element[b]]], '
        'RelativeSelector[~ Element[i]])]'
    ]
    assert parse_many('.ChapterS:has(+ .SubChapter)', '.ChapterS:has(+.SubChapter)') == [
        'Relation[Class[Element[*].ChapterS]:has(RelativeSelector[+ Class[Element[*].SubChapter]])]'
    ]
    assert parse_many('li:nth-child(2n+1 of .a)', 'li:nth-child( 2n+1  OF  .a )') == [
        'Function[Element[li]:nth-child(2n+1 of Class[Element[*].a])]'
    ]
    assert parse_many('li:nth-last-child(odd of .a, b > c)') == [
        'Function[Element[li]:nth-last-child(2n+1 of Class[Element[*].a], CombinedSelector[Element[b] > Element[c]])]'
    ]
    assert parse_many('a[rel=tag i]', "a[rel='tag'I]", 'a[ rel = tag  i ]') == ["Attrib[Element[a][rel = 'tag' i]]"]
    assert parse_many('a[rel=tag s]', 'a[rel="tag" S]') == ["Attrib[Element[a][rel = 'tag' s]]"]
    assert parse_many(':is(div:has(> p), :nth-child(2 of :is(a, b))) ~ :not(c)') == [
        'CombinedSelector[Matching[Element[*]:is('
        'Relation[Element[div]:has(RelativeSelector[> Element[p]])], '
        'Function[Element[*]:nth-child(0n+2 of Matching[Element[*]:is(Element[a], Element[b])])])] '
        '~ Negation[Element[*]:not(Element[c])]]'
    ]
    assert parse_many(':is(a, b), :where(c)') == [
        'Matching[Element[*]:is(Element[a], Element[b])]',
        'Matching[Element[*]:where(Element[c])]',
    ]


def parse_pseudo(css: str) -> list[tuple[str, str | None]]:
    result = []
    for tree in parse_trees(css):
        pe = tree.pseudo_element
        if pe is None:
            pseudo = None
        elif pe.arguments is not None:
            pseudo = f'FunctionalPseudoElement[::{pe.name}({cs_args(pe.arguments)})]'
        else:
            pseudo = pe.name
        result.append((cs_repr(tree), pseudo))
    return result


def parse_one(css: str) -> tuple[str, str | None]:
    result = parse_pseudo(css)
    assert len(result) == 1
    return result[0]


def test_pseudo_elements() -> None:
    assert parse_one('foo') == ('Element[foo]', None)
    assert parse_one('*') == ('Element[*]', None)
    assert parse_one(':empty') == ('Pseudo[Element[*]:empty]', None)

    # Special cases for CSS 2.1 pseudo-elements
    assert parse_one(':BEfore') == ('Element[*]', 'before')
    assert parse_one(':aftER') == ('Element[*]', 'after')
    assert parse_one(':First-Line') == ('Element[*]', 'first-line')
    assert parse_one(':First-Letter') == ('Element[*]', 'first-letter')

    assert parse_one('::befoRE') == ('Element[*]', 'before')
    assert parse_one('::AFter') == ('Element[*]', 'after')
    assert parse_one('::firsT-linE') == ('Element[*]', 'first-line')
    assert parse_one('::firsT-letteR') == ('Element[*]', 'first-letter')

    assert parse_one('::text-content') == ('Element[*]', 'text-content')
    assert parse_one('::attr(name)') == ('Element[*]', "FunctionalPseudoElement[::attr(['name'])]")

    assert parse_one('::Selection') == ('Element[*]', 'selection')
    assert parse_one('foo:after') == ('Element[foo]', 'after')
    assert parse_one('foo::selection') == ('Element[foo]', 'selection')
    assert parse_one('lorem#ipsum ~ a#b.c[href]:empty::selection') == (
        'CombinedSelector[Hash[Element[lorem]#ipsum] ~ Pseudo[Attrib[Class[Hash[Element[a]#b].c][href]]:empty]]',
        'selection',
    )

    assert parse_one(':is(a, b)::before') == ('Matching[Element[*]:is(Element[a], Element[b])]', 'before')
    assert parse_one('p:has(> a):after') == ('Relation[Element[p]:has(RelativeSelector[> Element[a]])]', 'after')

    assert parse_pseudo('foo:before, bar, baz:after') == [
        ('Element[foo]', 'before'),
        ('Element[bar]', None),
        ('Element[baz]', 'after'),
    ]


def specificity(css: str) -> tuple[int, int, int]:
    selectors = compile_selectors(css)
    assert len(selectors) == 1
    return selectors[0].specificity()


def test_specificity() -> None:
    assert specificity('*') == (0, 0, 0)
    assert specificity(' foo') == (0, 0, 1)
    assert specificity(':empty ') == (0, 1, 0)
    assert specificity(':before') == (0, 0, 1)
    assert specificity('*:before') == (0, 0, 1)
    assert specificity(':nth-child(2)') == (0, 1, 0)
    assert specificity('.bar') == (0, 1, 0)
    assert specificity('[baz]') == (0, 1, 0)
    assert specificity('[baz="4"]') == (0, 1, 0)
    assert specificity('[baz^="4"]') == (0, 1, 0)
    assert specificity('#lipsum') == (1, 0, 0)

    assert specificity(':not(*)') == (0, 0, 0)
    assert specificity(':not(foo)') == (0, 0, 1)
    assert specificity(':not(.foo)') == (0, 1, 0)
    assert specificity(':not([foo])') == (0, 1, 0)
    assert specificity(':not(:empty)') == (0, 1, 0)
    assert specificity(':not(#foo)') == (1, 0, 0)

    assert specificity('foo:empty') == (0, 1, 1)
    assert specificity('foo:before') == (0, 0, 2)
    assert specificity('foo::before') == (0, 0, 2)
    assert specificity('foo:empty::before') == (0, 1, 2)

    assert specificity('#lorem + foo#ipsum:first-child > bar:first-line') == (2, 1, 3)

    # Level 4: :is(), :not() and :has() take the specificity of their most
    # specific argument, :where() has zero specificity
    assert specificity(':is(#a, .b, c)') == (1, 0, 0)
    assert specificity(':matches(.a .b)') == (0, 2, 0)
    assert specificity(':is(c)') == (0, 0, 1)
    assert specificity(':is()') == (0, 0, 0)
    assert specificity(':where(#a, .b)') == (0, 0, 0)
    assert specificity('a:where(#a) .b') == (0, 1, 1)
    assert specificity(':not(#a, .b)') == (1, 0, 0)
    assert specificity(':not(a b, c)') == (0, 0, 2)
    assert specificity(':has(> #a, .b)') == (1, 0, 0)
    assert specificity('p:has(a b)') == (0, 0, 3)
    assert specificity('li:nth-child(2n)') == (0, 1, 1)
    assert specificity('li:nth-child(2n of .a, #b)') == (1, 1, 1)
    assert specificity('li:nth-last-child(2n of p)') == (0, 1, 2)
    assert specificity('[a=b i]') == (0, 1, 0)
    assert specificity('.ChapterS:has(+ .SubChapter)') == (0, 2, 0)


# (text, position of the error). Messages differ from css_selectors, which also give the position
# of the end of the input where an error is about a whole function, where we give its start.
PARSE_ERRORS = [
    ('attributes(href)/html/body/a', 0),
    ('attributes(href)', 0),
    ('html/body/a', 4),
    (' ', 1),
    ('div, ', 5),
    (' , div', 1),
    ('p, , div', 3),
    ('div > ', 6),
    ('  > div', 2),
    ('foo|#bar', 4),
    ('#.foo', 0),
    ('.#foo', 1),
    (':#foo', 1),
    ('[*]', 2),
    ('[foo|]', 5),
    ('[#]', 1),
    ('[foo=#]', 5),
    ('[href]a', 6),
    ('[rel:stylesheet]', 4),
    ('[rel=stylesheet', 15),
    (':lang(fr', 8),
    (':contains("foo', 14),
    ('foo!', 3),
    ('li:before a', 10),
    (':not(:before)', 5),
    (':is(a', 5),
    (':is(a, :not(b)', 14),
    (':not()', 5),
    (':not( )', 6),
    (':not(a,)', 7),
    (':not(a, 3x)', 8),
    (':not(a:after)', 6),
    (':has()', 5),
    (':has(> > a)', 7),
    (':has(a >)', 8),
    (':has(::before)', 5),
    (':has(:has(a))', 5),
    (':has(:not(:has(a)))', 10),
    ('li:nth-child(of .a)', 13),
    ('li:nth-child(2 of)', 17),
    ('li:nth-child(2 of a,)', 20),
    ('li:nth-child(2 .a)', 15),
    ('li:nth-child(2 of a::before)', 19),
    ('li:nth-child(2', 14),
    ('[a=b x]', 5),
    ('[a=b i x]', 7),
    ('[a i]', 3),
    # Not in css_selectors
    ('[a!=b]', 2),  # != is not CSS (plan 03, D6); css_selectors parsed it and failed to match it
    ('#1', 0),  # an ID must be an identifier
    ('a)', 1),
    ('a{', 1),
    ('a;b', 1),
    ('.', 1),
    ('a|', 2),
    (':nth-child(2.5n)', 11),
    (':nth-child(+ n)', 11),
    (':lang()', 6),
    (':lang(a b)', 8),
    ('a::before b', 10),
    ('> a', 0),  # only in nested rules
    # Found by the fuzzer: the name in the message was cut in the middle of a UTF-8 sequence
    (':has(::b' + 'å' * 40 + ')', 5),
    ('a /* x', 2),  # tokenizer errors: an unclosed comment, string or url
    ('a "x', 4),
]


@pytest.mark.parametrize(('css', 'pos'), PARSE_ERRORS)
def test_parse_errors(css: str, pos: int) -> None:
    with pytest.raises(SelectorSyntaxError) as e:
        compile_selectors(css)
    assert e.value.pos == pos, str(e.value)
    assert e.value.text == css
    # SelectorSyntaxError is a CSSSyntaxError too
    assert isinstance(e.value, CSSSyntaxError)
    assert isinstance(e.value, ValueError)


@pytest.mark.parametrize(
    'css',
    [
        '[rel=stylesheet]',
        ':lang(fr)',
        ':not(:not(a))',
        ':is(a, b)',
        ':is()',
        ':where(3x, #)',
        ':is(:has(a))',
        # css_selectors does not allow these: Selectors Level 4 allows pseudo-classes after a
        # pseudo-element, and :has() in a forgiving list inside :has() is dropped rather than
        # making the whole selector invalid.
        'a:before:empty',
        ':has(> :is(b, :has(a)))',
    ],
)
def test_parse_no_errors(css: str) -> None:
    compile_selectors(css)


# ---------------------------------------------------------------------------
# The tree
# ---------------------------------------------------------------------------


def one(css: str, nested: bool = False) -> ComplexSelector:
    compiled = compile_selectors(css, nested)
    assert len(compiled) == 1
    return build_tree(compiled[0])


def test_tree() -> None:
    tree = one('ns|div#x.y[z|a="b" i]:hover:is(.p, q) > *|e + |f ~ g')
    combinators = [c for c, _ in tree.parts]
    assert combinators == [None, Combinator.CHILD, Combinator.NEXT_SIBLING, Combinator.SUBSEQUENT_SIBLING]
    first = tree.parts[0][1]
    assert first.type == TypeSelector('div', 'ns')
    assert first.subclasses == (
        IdSelector('x'),
        ClassSelector('y'),
        AttributeSelector('a', 'z', '=', 'b', 'i'),
        PseudoClass('hover'),
        SelectorPseudoClass('is', (one('.p'), one('q'))),
    )
    assert [p[1].type for p in tree.parts[1:]] == [TypeSelector('e', '*'), TypeSelector('f', ''), TypeSelector('g')]
    assert one('*').parts[0][1].type == TypeSelector('*')
    assert one('[|a]').parts[0][1].subclasses == (AttributeSelector('a', ''),)
    # Escapes are resolved in names and values, the name of pseudo-classes is lowercased
    assert one('.\\31 23#\\66 oo:HOVER').parts[0][1].subclasses == (
        ClassSelector('123'),
        IdSelector('foo'),
        PseudoClass('hover'),
    )
    assert one(':lang(fr, "de-*")').parts[0][1].subclasses == (LangPseudoClass(('fr', 'de-*')),)
    assert one(':dir( rtl )').parts[0][1].subclasses == (FunctionalPseudoClass('dir', 'rtl'),)
    assert one('a::before:hover::marker').parts[0][1].pseudo_elements == (
        PseudoElement('before', pseudo_classes=(PseudoClass('hover'),)),
        PseudoElement('marker'),
    )
    assert one('a:after').pseudo_element == PseudoElement('after', legacy=True)
    assert one('::part( x y )').pseudo_element == PseudoElement('part', 'x y')
    # Nesting: & anywhere in a compound, and relative selectors in nested rules
    assert one('.a&').parts[0][1].subclasses == (ClassSelector('a'), NestingSelector())
    rel = one('> .a', nested=True)
    assert rel.parts[0][0] is Combinator.CHILD
    assert one('.a', nested=True).parts[0][0] is None
    # In :has() the first combinator is always given, descendant by default
    has = one(':has(a, > b)').parts[0][1].subclasses[0]
    assert isinstance(has, SelectorPseudoClass)
    assert [s.parts[0][0] for s in has.selectors] == [Combinator.DESCENDANT, Combinator.CHILD]


@pytest.mark.parametrize(
    ('anb', 'a', 'b'),
    [
        ('odd', 2, 1), ('EVEN', 2, 0), ('0', 0, 0), ('-3', 0, -3), ('+3', 0, 3), ('n', 1, 0), ('-n', -1, 0),
        ('+n', 1, 0), ('N', 1, 0), ('2n', 2, 0), ('-2n', -2, 0), ('+2n', 2, 0), ('2n+1', 2, 1), ('2n-1', 2, -1),
        ('2n + 1', 2, 1), ('2n - 1', 2, -1), ('2n- 1', 2, -1), ('2n -1', 2, -1), ('n+5', 1, 5), ('-n+5', -1, 5),
        ('+n-5', 1, -5), ('n- 5', 1, -5), ('-n- 5', -1, -5), ('-n-5', -1, -5), (' 3n + 2 ', 3, 2),
        ('99999999999n', 2147483647, 0),
    ],
)  # fmt: skip
def test_anb(anb: str, a: int, b: int) -> None:
    nth = one(f':nth-child({anb})').parts[0][1].subclasses[0]
    assert nth == NthPseudoClass('nth-child', a, b)


SELECTORS = [
    'a',
    'ns|a > *|b + |c ~ d e',
    '#x.y[z]:hover',
    '[a="b" i][c|=d s][e^=f][g$=h][i*=j][k~=l]',
    ':is(a, .b > c):where(#d):not(e, f g):has(> h, + i, j)',
    'li:nth-child(2n+1 of .a, b > c):nth-last-of-type(-n+3)',
    ':lang(fr, "de-*")',
    'a::before:hover',
    'p:first-letter',
    '::part(x)',
    '.\\31 23 #\\#x .a\\ b',
    ':contains("x")',
    '& > .a',
    '.a &.b',
]


@pytest.mark.parametrize('css', SELECTORS)
def test_spans_and_round_trip(css: str) -> None:
    check_tree(css)


def check_tree(css: str, nested: bool = False) -> None:
    for compiled in compile_selectors(css, nested):
        tree = build_tree(compiled)
        text = css[compiled.start : compiled.end]
        assert tree.span == (0, len(text))
        check_spans(tree, text)
        # Written back as text, the selector parses to the same tree
        again = compile_selectors(tree.to_css(), nested)
        assert len(again) == 1
        assert build_tree(again[0]) == tree, tree.to_css()


def check_spans(tree: ComplexSelector, text: str) -> None:
    """Every node is inside its parent, and simple selectors do not overlap."""
    stack = [tree]
    while stack:
        t = stack.pop()
        start, end = t.span
        assert 0 <= start <= end <= len(text)
        previous = start
        for _, compound in t.parts:
            assert start <= compound.span[0] <= compound.span[1] <= end
            for s in compound:
                assert previous <= s.span[0] <= s.span[1] <= compound.span[1], (s, text)
                previous = s.span[1]
                inner = (
                    s.selectors
                    if isinstance(s, SelectorPseudoClass)
                    else (s.of or () if isinstance(s, NthPseudoClass) else ())
                )
                for child in inner:
                    assert s.span[0] <= child.span[0] <= child.span[1] <= s.span[1]
                    stack.append(child)
    # The simple selectors that are names start with what identifies them
    for s in tree.walk():
        piece = text[s.span[0] : s.span[1]]
        match s:
            case IdSelector():
                assert piece.startswith('#')
            case ClassSelector():
                assert piece.startswith('.')
            case AttributeSelector():
                assert piece.startswith('[')
                assert piece.endswith(']')
            case PseudoElement() | PseudoClass() | SelectorPseudoClass() | NthPseudoClass():
                assert piece.startswith(':')
            case NestingSelector():
                assert piece == '&'


def test_walk() -> None:
    tree = one('a.b:is(.c, d:not(#e)):nth-child(2 of .f)::before:hover')
    names = []
    for s in tree.walk():
        if isinstance(s, (ClassSelector, IdSelector, TypeSelector, PseudoClass, PseudoElement)):
            names.append(type(s).__name__ + ':' + s.name)
    assert names == [
        'TypeSelector:a', 'ClassSelector:b', 'ClassSelector:c', 'TypeSelector:d', 'IdSelector:e',
        'ClassSelector:f', 'PseudoElement:before', 'PseudoClass:hover',
    ]  # fmt: skip
    classes = {s.name for s in tree.walk() if isinstance(s, ClassSelector)}
    assert classes == {'b', 'c', 'f'}


# ---------------------------------------------------------------------------
# The CSSOM
# ---------------------------------------------------------------------------


def test_selector_objects() -> None:
    sheet = parse_stylesheet('a.b > c, #d::before { x: y } .e { }')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    first, second = rule.selectors
    assert first.specificity == (0, 1, 2)
    assert second.specificity == (1, 0, 1)
    assert first.pseudo_element is None
    pe = second.pseudo_element
    assert pe is not None
    assert pe.name == 'before'
    assert first.parsed == one('a.b > c')
    assert {s.name for s in first.walk() if isinstance(s, ClassSelector)} == {'b'}
    # Changing the text changes the parse
    first.text = '#x'
    assert first.specificity == (1, 0, 0)
    assert rule.selector_text == '#x, #d::before'
    # parse_selectors() gives a list that is not in any rule
    selectors = parse_selectors('p , .q')
    assert [s.text for s in selectors] == ['p', '.q']
    assert [s.specificity for s in selectors] == [(0, 0, 1), (0, 1, 0)]
    rule.selectors.append(selectors[1])
    assert rule.selector_text == '#x, #d::before, .q'
    with pytest.raises(SelectorSyntaxError):
        parse_selectors('a, b!')


def test_invalid_selectors_in_sheets() -> None:
    # Rules with selectors that do not parse are kept (D4), they raise when parsed
    sheet = parse_stylesheet('a!b, c { x: y } @media print { d e!f { x: y } } g { x: y }')
    rule = sheet.rules[0]
    assert isinstance(rule, StyleRule)
    bad, good = rule.selectors
    assert good.specificity == (0, 0, 1)
    with pytest.raises(SelectorSyntaxError):
        _ = bad.specificity
    with pytest.raises(SelectorSyntaxError):
        _ = bad.parsed
    found = invalid_selectors(sheet)
    assert [(r.line, r.column, s.text, e.pos) for r, s, e in found] == [(1, 1, 'a!b', 1), (1, 32, 'd e!f', 3)]
    assert 'a!b' in sheet.css_text


def test_nested_rules() -> None:
    sheet = parse_stylesheet('.p, #q { > a { x: y } & .b { x: y } @media print { + c { x: y } } }')
    outer = sheet.rules[0]
    assert isinstance(outer, StyleRule)
    child, nest, media = outer.rules
    assert isinstance(child, StyleRule)
    assert isinstance(nest, StyleRule)
    assert child.selectors[0].parsed.parts[0][0] is Combinator.CHILD
    # & counts as the most specific parent selector, here #q
    assert nest.selectors[0].specificity == (1, 1, 0)
    assert child.selectors[0].specificity == (0, 0, 1)
    children = media.child_rules()
    assert children is not None
    inner = children[0]
    assert isinstance(inner, StyleRule)
    assert inner.selectors[0].parsed.parts[0][0] is Combinator.NEXT_SIBLING
    # Relative selectors are only valid in nested rules
    child.selector_text = '~ d'
    with pytest.raises(SelectorSyntaxError):
        outer.selector_text = '> d'
    # & outside a nested rule is :scope
    assert parse_selectors('&.x')[0].specificity == (0, 2, 0)


def test_compile_cache() -> None:
    assert compile_selectors('a b c') is compile_selectors('a b c')
    assert compile_selectors('a b c', True) is not compile_selectors('a b c')


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

idents = st.sampled_from(('a', 'div', 'P', 'x-y', '\\31 x', 'é', '_z', '--c'))
strings = st.sampled_from(('"a b"', "'c'", 'v', '"\\""'))
anbs = st.sampled_from(('odd', 'even', '3', '-n+2', '2n+1', ' n - 1 ', '+n', '-3n'))


@st.composite
def simple_selector(draw: st.DrawFn, depth: int) -> str:
    choices = ['class', 'id', 'attribute', 'plain', 'nth-of-type']
    if depth < 2:
        choices += ['selector-pseudo', 'has', 'nth-child-of', 'lang']
    match draw(st.sampled_from(choices)):
        case 'class':
            return '.' + draw(idents)
        case 'id':
            return '#' + draw(idents)
        case 'attribute':
            ns = draw(st.sampled_from(('', 'ns|', '*|', '|')))
            op = draw(st.sampled_from(('', '=', '~=', '|=', '^=', '$=', '*=')))
            rest = (draw(strings) + draw(st.sampled_from(('', ' i', ' s', ' I')))) if op else ''
            return f'[{ns}{draw(idents)}{op}{rest}]'
        case 'plain':
            return draw(st.sampled_from((':hover', ':first-child', ':ROOT', ':empty', '&')))
        case 'nth-of-type':
            return f':nth-of-type({draw(anbs)})'
        case 'selector-pseudo':
            name = draw(st.sampled_from(('is', 'where', 'not', 'matches')))
            return f':{name}({draw(selector_list(depth + 1))})'
        case 'has':
            return f':has({draw(selector_list(depth + 1))})'
        case 'nth-child-of':
            return f':nth-child({draw(anbs)} of {draw(selector_list(depth + 1))})'
    return f':lang({draw(st.sampled_from(("fr", '"de-*", en')))})'


@st.composite
def compound(draw: st.DrawFn, depth: int) -> str:
    text = draw(st.sampled_from(('', '*', 'a', 'ns|b', '*|c', '|d')))
    for _ in range(draw(st.integers(0 if text else 1, 3))):
        text += draw(simple_selector(depth))
    return text


@st.composite
def complex_selector(draw: st.DrawFn, depth: int) -> str:
    text = draw(compound(depth))
    for _ in range(draw(st.integers(0, 3))):
        text += draw(st.sampled_from((' ', ' > ', '+', ' ~ ', '/**/ '))) + draw(compound(depth))
    return text


@st.composite
def selector_list(draw: st.DrawFn, depth: int = 0) -> str:
    return ', '.join(draw(complex_selector(depth)) for _ in range(draw(st.integers(1, 3))))


pseudo_elements = st.sampled_from(('', '::before', ':after', '::marker:hover', '::part(x)'))


@given(selector_list(), pseudo_elements)
def test_generated_selectors(css: str, pe: str) -> None:
    # Pseudo-elements are not valid inside functional pseudo-classes, so add one at the end only
    text = css + pe
    try:
        compiled = compile_selectors(text)
    except SelectorSyntaxError:
        compiled = None
    if compiled is None:
        # :has() inside :has() is the only problem these can have
        assert ':has(' in css
        return
    for c in compiled:
        assert c.specificity() == c.specificity((0, 1, 0))
    check_tree(text)


@given(css_text)
def test_any_text(css: str) -> None:
    # Any text gives selectors or a SelectorSyntaxError with a position in the text
    for nested in (False, True):
        error: SelectorSyntaxError | None = None
        try:
            compiled = compile_selectors(css, nested)
        except SelectorSyntaxError as e:
            error, compiled = e, ()
        if error is not None:
            assert 0 <= error.pos <= len(css)
        for c in compiled:
            tree = build_tree(c)
            check_spans(tree, css[c.start : c.end])
            assert build_tree(compile_selectors(tree.to_css(), nested)[0]) == tree
