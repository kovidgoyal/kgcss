# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C versions of the string predicates of kgcss.linter._utils (csrc/linter.c) against the Python ones."""

from collections.abc import Callable

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss.linter._utils import (
    has_interpolation,
    is_standard_syntax_property,
    is_standard_syntax_selector,
    is_standard_syntax_value,
    python_has_interpolation,
    python_is_standard_syntax_property,
    python_is_standard_syntax_selector,
    python_is_standard_syntax_value,
)

from .strategies import any_char

PAIRS: list[tuple[Callable[[str], bool], Callable[[str], bool]]] = [
    (has_interpolation, python_has_interpolation),
    (is_standard_syntax_property, python_is_standard_syntax_property),
    (is_standard_syntax_selector, python_is_standard_syntax_selector),
    (is_standard_syntax_value, python_is_standard_syntax_value),
]

# What the regular expressions look for, and what ends their matches
PARTS = (
    '{', '}', '@{', '#{', '$(', ')', '(', '(@', '$', '@', '.', '.$', '.a', '.a-b_1(', ':', ':extend', '%', '<%', '%>',
    '//', '+', '+_', '-', '*', '/', '__MSG_', '__', '_', 'a', 'x', ' ', '\t', '\n', '\r', '\u2028', '\u2029', '\u00a0',
    '\ufeff', '\u3000', '\u00e9', '\U0001f600',
)  # fmt: skip
texts = st.lists(st.one_of(st.sampled_from(PARTS), any_char), max_size=16).map(''.join)


def check(text: str) -> None:
    for c, python in PAIRS:
        assert c(text) == python(text), (python, text)


@pytest.mark.parametrize(
    'text',
    [
        '',
        '{}',
        '{a}',
        '{}}',
        'a{ b',
        '@{x}',
        '#{x}',
        '$()',
        '$(a)',
        '$( ',
        '%a',
        'a:',
        'a:extend',
        '.a(b)c',
        '.a(b)\nc',
        '.a(b)',
        '.a\n(b)c',
        '.a-b(c) d',
        'a(@b)',
        'a(@b)\n',
        'a(@\nb)',
        '(@)',
        '(@)\n\n',
        'a)',
        'a:b)',
        '<%a',
        'a%>',
        'a//b',
        '$a',
        'a.$b',
        '@a',
        'a+',
        'a+_',
        '-$a',
        '+@a',
        '*a',
        '/$',
        'a.$',
        '.$',
        'x\n.$',
        'a.b(',
        'a.(',
        '\u2028a.b(',
        '__MSG_a__',
        '__MSG___',
        '__MSG_a b__',
        'x__MSG_ab__c',
        '-{a}',
        '-$(a)',
    ],
)
def test_examples(text: str) -> None:
    check(text)


@given(texts)
def test_random(text: str) -> None:
    check(text)


@given(st.text(st.sampled_from('{}()$@.:%<>/+_-*aMSG\n\u2028 '), max_size=20))
def test_random_punctuation(text: str) -> None:
    check(text)


def test_bad_arguments() -> None:
    for c, _ in PAIRS:
        with pytest.raises(TypeError):
            c(1)  # ty: ignore[invalid-argument-type]
