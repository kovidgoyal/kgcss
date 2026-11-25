# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C token writer (csrc/writer.c) against its reference, the Python TokenWriter."""

import contextlib
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss import (
    DeclarationBlock,
    NestedDeclarationsRule,
    Property,
    Stylesheet,
    parse_stylesheet,
    serialize,
)
from kgcss._kgcss import write_values as c_write_values
from kgcss.cssom.rules import LazyBlockRule
from kgcss.cssom.serializer import (
    MODE_FLAGS,
    WRITE_ATTRIBUTE_SAFE,
    WRITE_KEEP_COMMENTS,
    WRITE_NORMALIZE,
    WRITE_RAW,
    SerializeOptions,
    _Unsafe,
    python_write_values,
)
from kgcss.syntax import parse_component_value_list

from .strategies import FRAGMENTS, css_text

type Mode = Literal['value', 'selector', 'verbatim']
MODES: tuple[Mode, ...] = ('value', 'selector', 'verbatim')

# Fragments for the parts of the writer that normalize: numbers, colors, url(), units, quotes in escapes
WRITER_FRAGMENTS = (
    *FRAGMENTS, '-0', '+0px', '007', '-007', '+7', '1.0', '1.50', '-1.0e1', '1e999', '-1e999', '0.0001', '1e16',
    '9999999999999999.5', '12345678901234567890', '0.1', '1.23456789012345678', '#FFAABB', '#aabbccdd', '#abc',
    '#aAbBcC', '#\\61 a', '0PX', '0q', '0Q', '0\\70x', '0%', '1.0%', 'url("a b")', 'url("a")', 'url( "\'" ',
    'url(\'x', 'URL(a)', '\\"', 'a\\"b', 'RGB(', '--Foo(', '\\72gb(', '"a\nb', ' > ', '+', '~', '[a="b" i]',
    '[a ~= b]', 'a, b', '\\a', '\\1F600', '\\12345', '\\1234567', '\\\\', '/* c */',
)  # fmt: skip
writer_text = st.one_of(
    css_text,
    st.lists(st.one_of(st.sampled_from(WRITER_FRAGMENTS), st.sampled_from(' \n,()[]{}')), max_size=30).map(''.join),
)


def python_result(text: str, mode: Mode, normalize: bool, keep_comments: bool, safe: bool, raw: bool) -> str | None:
    options = SerializeOptions(normalize=normalize, keep_comments=keep_comments)
    values = list(parse_component_value_list(text))  # a list, so the Python version is used
    try:
        return python_write_values(values, options, mode=mode, attribute_safe=safe, raw=raw)
    except _Unsafe:
        return None


def c_result(text: str, mode: Mode, normalize: bool, keep_comments: bool, safe: bool, raw: bool) -> object:
    flags = MODE_FLAGS[mode]
    flags |= WRITE_NORMALIZE if normalize else 0
    flags |= WRITE_KEEP_COMMENTS if keep_comments else 0
    flags |= WRITE_ATTRIBUTE_SAFE if safe else 0
    flags |= WRITE_RAW if raw else 0
    return c_write_values(parse_component_value_list(text), flags)


def check(text: str, mode: Mode, normalize: bool, keep_comments: bool, safe: bool, raw: bool) -> None:
    c = c_result(text, mode, normalize, keep_comments, safe, raw)
    if c is NotImplemented:
        # Only for a comment dropped from a selector
        assert mode == 'selector'
        assert not keep_comments or safe
        return
    assert c == python_result(text, mode, normalize, keep_comments, safe, raw)


@given(writer_text, st.sampled_from(MODES), st.booleans(), st.booleans(), st.booleans(), st.booleans())
def test_c_writer_matches_python(
    text: str, mode: Mode, normalize: bool, keep_comments: bool, safe: bool, raw: bool
) -> None:
    check(text, mode, normalize, keep_comments, safe, raw)


@pytest.mark.parametrize(
    'text',
    [
        '',
        ' ',
        '  a  ',
        '.5em -0 +0px 007 +7 1.0 1.50 1e999 0.0001 1e16 12345678901234567890 0.1',
        '0PX 0q 0\\70x 0% 1.0% (0px) f(0px) a0px -.5em\\ -.5em',
        '#FFAABB #aabbccdd #aAbBcC #\\61 a #a\\"',
        'url("a b") url("a") url( "\'" ) URL(a) u\\72l("x") url(\'x',
        '"a\nb" c',
        '\\',
        'a \\',
        'a\\',
        'a > b + c ~ d',
        'a>b',
        '[a = "b" i]',
        'a, b ,c',
        'a /* x */ b',
        'a/**/b',
        'RGB(1,2) --Foo(1) \\72gb(1)',
        '\\31 x \\12345 \\1234567',
        '\\0\n',
        'a\\0\n b',
        'a\\"b "x"',
        '{a: b} [c] (d',
        'é 😀 \ud800',
        '\x00 \x7f',
    ],
)
def test_c_writer_examples(text: str) -> None:
    for mode in MODES:
        for flags in range(16):
            check(text, mode, bool(flags & 1), bool(flags & 2), bool(flags & 4), bool(flags & 8))


def test_deep_nesting() -> None:
    text = '(' * 100000 + 'a' + ')' * 100000
    flags = WRITE_NORMALIZE | WRITE_KEEP_COMMENTS
    assert c_write_values(parse_component_value_list(text), flags) == text
    assert c_write_values(parse_component_value_list('f(' * 50000), flags) == 'f(' * 50000 + ')' * 50000


def test_rejects_other_types() -> None:
    with pytest.raises(TypeError):
        c_write_values([], 0)  # type: ignore[arg-type]


def serialize_both(sheet: Stylesheet, options: SerializeOptions) -> tuple[str, str]:
    """Pretty serialization with the C declaration writer, and with the Python one."""
    import kgcss.cssom.serializer as s

    with_c = serialize(sheet, options)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(s, 'c_write_declarations', lambda items, flags, indent: NotImplemented)
        with_python = serialize(sheet, options)
    return with_c, with_python


@given(
    css_text,
    st.booleans(),
    st.booleans(),
    st.sampled_from(('  ', '\t', '')),
    st.lists(st.tuples(st.integers(0, 50), st.sampled_from(WRITER_FRAGMENTS)), max_size=3),
)
def test_c_declarations_match_python(
    css: str, normalize: bool, keep_comments: bool, indent: str, edits: list[tuple[int, str]]
) -> None:
    sheet = parse_stylesheet(css)
    props = [p for b in blocks_of(sheet) for p in b.items if isinstance(p, Property)]
    for index, value in edits:
        # Changed values are written from their text rather than from the parsed node
        if props:
            with contextlib.suppress(ValueError):
                props[index % len(props)].value = value
    options = SerializeOptions(normalize=normalize, keep_comments=keep_comments, indent=indent)
    with_c, with_python = serialize_both(sheet, options)
    assert with_c == with_python


def blocks_of(sheet: Stylesheet) -> list[DeclarationBlock]:
    return [r.style for r in sheet.iter_rules() if isinstance(r, LazyBlockRule | NestedDeclarationsRule)]


def test_c_declarations_examples() -> None:
    css = 'a { --X: {a}  ; COLOR: Red !IMPORTANT; /* c */ w\\69 dth: 1.0PX; b: url( "x y" ); c: ; d: "\\\n" }'
    sheet = parse_stylesheet(css)
    for normalize in (True, False):
        for keep_comments in (True, False):
            with_c, with_python = serialize_both(
                sheet, SerializeOptions(normalize=normalize, keep_comments=keep_comments)
            )
            assert with_c == with_python
    assert serialize(sheet) == (
        'a {\n  --X: {a};\n  color: Red !important;\n  /* c */\n  width: 1PX;\n  b: url("x y");\n  c:;\n  d: "";\n}'
    )


@given(st.one_of(writer_text, css_text), st.booleans())
def test_c_find_urls_matches_python(css: str, top_level_rules: bool) -> None:
    from kgcss.cssom.urls import find_urls, python_find_urls

    values = parse_component_value_list(css)
    assert find_urls(values, top_level_rules=top_level_rules) == python_find_urls(
        values, top_level_rules=top_level_rules
    )


@pytest.mark.parametrize(
    'css',
    [
        '@import "a"; @import url(b) x; @IMPORT "c"; @namespace url(n); @namespace s "m"; @\\69mport "d";',
        'a { b: url(c) URL("d") u\\72l( "e" ) image-set("f" 1x, url(g) 2x) -WEBKIT-image-set("h") url("i" "j") }',
        '@namespace x url(y) url(z); url(w)',
        '@import "a" "b"',
        '@import /**/ url(a) "b"',
        'f("a") image-set(f("b"))',
        'url(',
        'url("a',
        'image-set("a',
        '@import "a',
        '[url(a)] {url(b)} (url(c)',
    ],
)
def test_c_find_urls_examples(css: str) -> None:
    from kgcss.cssom.urls import find_urls, python_find_urls

    for top in (True, False):
        values = parse_component_value_list(css)
        assert find_urls(values, top_level_rules=top) == python_find_urls(values, top_level_rules=top)
