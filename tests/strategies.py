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
