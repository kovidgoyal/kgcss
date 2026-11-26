# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Hypothesis strategies for CSS-like text."""

from hypothesis import strategies as st

# Fragments that exercise the interesting paths of the tokenizer and parser
FRAGMENTS = (
    *'abzAZ_-+.,:;!@#%&*/\\"\'(){}[]<>=~|^$? \t\n\r\f',
    '0', '7', '1e3', '1E-2', '.5', '+.5', '10px', '5%', '1e', '1e+',
    'url(', 'URL( ', 'u\\72l(', 'rgb(', 'calc(', 'var(--x)',
    '/*', '*/', '/**/', '<!--', '-->', '!important', '!IMPORTANT', 'important',
    '--', '--x:', '@media', '@import', '@charset "x";', '&:hover',
    '\\', '\\\n', '\\\r\n', '\\31 ', '\\0', '\\d800', '\\110000', '\\5f', '\r\n',
    'U+1?', 'u+a', '\x00', '\x7f', '\x01', 'é', 'Ā', '\ud800', '\udfff', '�', '\U0001f600',
)  # fmt: skip

# Any code point, including lone surrogates and NUL
any_char = st.characters(exclude_categories=())

# Unstructured: random fragments and characters
random_css = st.lists(
    st.one_of(st.sampled_from(FRAGMENTS), any_char),
    max_size=60,
).map(''.join)

# Structured: plausible rules and declarations, nested and sometimes broken
idents = st.sampled_from(('a', 'b', 'color', 'margin', '--x', '-webkit-y', 'w\\69 dth', 'é', '\\31 x', 'IMPORTANT'))
value_parts = st.sampled_from((
    'red', '1px', '-.5em', '50%', '"s"', "'t'", 'url(a.png)', 'url( "b" )', 'rgb(1, 2, 3)', 'calc(1px + (2 * 3))',
    'var(--x, {a})', '{b}', '!ie', '!', 'important', '/* c */', '#fff', '\\', '}', ';', 'f(', '[x', 'u+1?',
))  # fmt: skip
values = st.lists(value_parts, max_size=5).map(' '.join)
importance = st.sampled_from(('', '', ' !important', '!IMPORTANT', ' ! /**/ important', ' !imp\\6frtant', ' !ie'))
declarations = st.builds(
    lambda name, space, value, imp: f'{name}{space}:{value}{imp}',
    idents,
    st.sampled_from(('', ' ', '/**/')),
    values,
    importance,
)
selectors = st.sampled_from(
    (
        'a',
        'a > b',
        '&:hover',
        '.c\\31',
        '#id',
        '[x="y"]',
        'p::before',
        '<!--',
        ':is(a, b)',
        '--x:',
        '*html p',
        'p[a!=b]',
    )
)
at_preludes = st.sampled_from((
    '@media screen', '@supports (x: y)', '@import url(a.css)', '@charset "x"', '@page :first', '@foo', '@layer a, b',
    '@keyframes k', '@-moz-document url(x)',
))  # fmt: skip
closers = st.sampled_from(('}', '}', '}', ''))


def blocks(children: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    contents = st.lists(children, max_size=4).map(';'.join)
    return st.one_of(
        st.builds(lambda sel, c, close: f'{sel} {{{c}{close}', selectors, contents, closers),
        st.builds(lambda at, c, close: f'{at} {{{c}{close}', at_preludes, contents, closers),
        st.builds(lambda at: f'{at};', at_preludes),
    )


items = st.recursive(declarations, blocks, max_leaves=12)
separators = st.sampled_from(('', ' ', ';', '\n', '/* x */', '\r\n'))
structured_css = st.lists(
    st.tuples(st.one_of(items, items, st.sampled_from(FRAGMENTS)), separators),
    max_size=8,
).map(lambda parts: ''.join(a + b for a, b in parts))

css_text = st.one_of(random_css, structured_css)

# For the linter: what its rules look at (semicolons, blocks, at-rules) and disable comments
LINT_FRAGMENTS = (
    'a{}', 'a{ }', 'a{;}', 'a{b:c}', 'a{b:c;;}', 'a{b:c;}', ';', ';;', ' ; ', '\n', ' ', '{}', '}', '{',
    '@foo;', '@foo{}', '@foo x;', '@media x{}', '@media x{a{}}', '@-moz-x{}', '@FONT-FACE{}', '@charset "x";',
    '@unknown x{a{b:c;;};}', '/**/', '/* x */', '/* ; */', '"a;b"', 'url(;)', 'a{/* c */}', 'a/**/{}', 'a{b:/**/c;;}',
    '/* stylelint-disable */', '/* stylelint-enable */', '/* stylelint-disable block-no-empty */',
    '/* stylelint-disable-line */', '/* stylelint-disable-next-line block-no-empty, at-rule-no-unknown */',
    '/* stylelint-enable block-no-empty */', '/* stylelint-disable @stylistic/no-extra-semicolons -- why */',
    'a{/* stylelint-disable-line */}', 'a /* stylelint-disable-line */ {}',
    # declarations for the rules that read values
    'a{color:#ff}', 'a{color:#ffff;background:url(#x) #12}', 'a{colr:red}', 'a{-webkit-foo:1}', 'a{*zoom:1}',
    'a{font-family:Arial, Arial}', 'a{font: 12px/1.5 "Helvetica Neue", serif}', 'a{font-family:a, "b", c}',
    'a{font:caption}', 'a{font-family: math}', 'a{font-family: var(--f)}', '@font-face{font-family:x}',
    'a{grid-template-areas:"a b" "a"}', 'a{grid:"a a" "a ."}', 'a{grid-template-areas:""}',
    'a{margin-top:1px;margin:0}', 'a{border-color:red;border:0}', 'a{--x:1;--x:2}', 'a{--y:1;color:--y}',
    'a{anchor-name:--y}', '@property --z{syntax:"*"}', 'a{width:--z}', 'a{color:red !foo}', 'a{b:c ! ie}',
    'a{background:linear-gradient(top, red, blue)}', 'a{background:-webkit-linear-gradient(to left, red, blue)}',
    'a{background:linear-gradient(45, red)}', 'a{background:linear-gradient(to top top, red)}',
    '@import "a.css";', '@import url(a.css) screen;', '@import "a.css" layer(x) supports(a:b) print, screen;',
    '@keyframes k{from{color:red!important}}', '@-webkit-keyframes k{to{b:c !IMPORTANT}}',
    '//a{}', 'a{//b:c}', 'a, // b {}', '/**/', '/* */', '/*\n*/',
    'a{content:"x\ny"}', "a[b='c\nd']{}", '@import "a\nb";', 'a{b:foo(1) bar(x) -webkit-baz() --f() calc(1px)}',
    'a{width:c\\61 lc(1px)}', 'a{b:[x(y)] (z(w))}',
    # selectors
    'a:hover{color:red}', 'a:hovr{color:red}', 'a::befor{color:red}', 'a:before{color:red}', 'a::-moz-x{color:red}',
    'foo{color:red}', 'my-elem{color:red}', 'svg|circle{color:red}', 'foreignObject{color:red}',
    'li:nth-child(2n of p){color:red}',
    ':root{color:red}', '#id .c{color:red}', '.c{color:red}', '.c, .c{color:red}', 'a b{color:red}', 'b{color:red}',
    'a:not(.x){color:red}', '&:hover{color:red}', '.p{.c{color:red}}', '.p{& .c{color:red}}', '@media x{.c{color:red}}',
    '::-webkit-scrollbar:horizontal{color:red}', '@page :first{color:red}', '@page :foo{color:red}',
    '@keyframes k{from{color:red}FROM{color:blue}0%{a:b}}', 'a\u00a0b{color:red}', 'a{color:\u2003red}',
    'a{width:calc(1px+2px)}', 'a{width:calc(1px -2px)}', 'a{width:calc(1px  +  2px)}', 'a{width:calc(1px+(2px-3px))}',
    'a{width:calc(1px- 2px)}', 'a{width:calc(1px -\n2px)}', 'a{width:10pix}', 'a{width:1x}', 'a{width:1PX}',
    'a{background:image-set("a.png" 2x)}', 'a{image-resolution:2x}', 'a{b:url(1pix)}',
    '@media (min-width: 10pix){}', '@media (widht: 1px){}', '@media screen and (max-resolution: 2x){}',
    '@media (--custom){}', '@media (100px < width < 200px){}', '@media (foo: 1) and (bar){}',
    '@media not all and (x){}',
    'a{color:red;color:blue}', 'a{color:red !important;COLOR:blue}', 'a{x:1;color:red;y:2;color:red;}',
    'a{content:"\u00a0"}', '[title="\u2003"]{color:red}', 'a[x=y i]{color:red}', 'a /* c */ > b{color:red}',
)  # fmt: skip
lint_css = st.lists(st.sampled_from(LINT_FRAGMENTS), max_size=12).map(''.join)
