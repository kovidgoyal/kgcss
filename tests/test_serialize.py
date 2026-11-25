# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import annotations

from collections.abc import Callable

import pytest

from kgcss import serialize_identifier, serialize_string


@pytest.mark.parametrize(
    ('src', 'expected'),
    [
        ('', '""'),
        ('abc', '"abc"'),
        ('a"b', '"a\\"b"'),
        ('a\\b', '"a\\\\b"'),
        ('a\x00b', '"a�b"'),
        ('a\nb', '"a\\a b"'),
        ('\x1f\x7f', '"\\1f \\7f "'),
        ("it's", '"it\'s"'),
        ('日本語 😀', '"日本語 😀"'),
    ],
)
def test_serialize_string(src: str, expected: str) -> None:
    assert serialize_string(src) == expected


@pytest.mark.parametrize(
    ('src', 'expected'),
    [
        ('', ''),
        ('abc', 'abc'),
        ('-', '\\-'),
        ('--', '--'),
        ('--x', '--x'),
        ('1a', '\\31 a'),
        ('-1a', '-\\31 a'),
        ('a1', 'a1'),
        ('a b', 'a\\ b'),
        ('a.b#c', 'a\\.b\\#c'),
        ('a\x00', 'a�'),
        ('\x01', '\\1 '),
        ('_x', '_x'),
        ('日本', '日本'),
        ('😀', '😀'),
    ],
)
def test_serialize_identifier(src: str, expected: str) -> None:
    assert serialize_identifier(src) == expected


@pytest.mark.parametrize('func', [serialize_string, serialize_identifier])
def test_rejects_non_str(func: Callable[[str], str]) -> None:
    with pytest.raises(TypeError):
        func(b'abc')  # type: ignore[arg-type]
