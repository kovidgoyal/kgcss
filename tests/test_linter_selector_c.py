# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The C versions of unesc(), cssesc() and tokenize() (csrc/linter.c) against the Python ones.

The Python versions in kgcss.linter._selector_parser are the reference: the
C ones must give exactly the same results, errors included, for any text.
"""

import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from kgcss._kgcss import selector_cssesc, selector_tokenize, selector_unesc
from kgcss.linter._selector_parser import (
    SelectorError,
    cssesc,
    python_cssesc,
    python_tokenize,
    python_unesc,
    tokenize,
    unesc,
)

from .strategies import any_char

# Characters that matter to escapes and selectors, and any others
selector_chars = st.one_of(
    st.sampled_from('\\0123456789abcdefABCDEFxyz -_.#:,>+~|*&!;()[]=$^/\'"\t\n\r\f\x00\x7f\u00e9\ud800\U0001f600'),
    any_char,
)
selector_text = st.text(selector_chars, max_size=40)
ESCAPES = st.sampled_from(('\\', '\\\\', '\\31 ', '\\0', '\\110000', '\\d800', '\\FFFFFF ', '\\a\r\n', '\\ab\t'))
escaped_text = st.lists(st.one_of(ESCAPES, selector_text), max_size=6).map(''.join)

EXAMPLES = (
    '',
    'a',
    '\\',
    '\\\\',
    'a\\',
    '\\31 x',
    '\\31  x',
    '\\0',
    '\\d800',
    '\\110000',
    '\\FFFFFF',
    '\\1234567',
    '\\a\r\nb',
    '\\ab\r\n',
    '\\a\tb',
    '--x',
    '-1',
    '-',
    '1a',
    '\\-',
    'a b',
    '\u00e9\U0001f600\ud800',
    'a\\"b\'c',
    '\\\\31 x',
    '\\31  ',
    '\x7f\x00\x1f',
)


@pytest.mark.parametrize('text', EXAMPLES)
def test_unesc_examples(text: str) -> None:
    assert unesc(text) == python_unesc(text)


@given(escaped_text)
def test_unesc_random(text: str) -> None:
    assert unesc(text) == python_unesc(text)


def check_cssesc(text: str) -> None:
    for is_identifier in (False, True):
        for quotes in ('single', 'double'):
            for wrap in (False, True):
                kw = {'is_identifier': is_identifier, 'quotes': quotes, 'wrap': wrap}
                assert cssesc(text, **kw) == python_cssesc(text, **kw), kw


@pytest.mark.parametrize('text', EXAMPLES)
def test_cssesc_examples(text: str) -> None:
    check_cssesc(text)


@given(escaped_text)
def test_cssesc_random(text: str) -> None:
    check_cssesc(text)


def outcome(fn: object, text: str) -> object:
    assert callable(fn)
    try:
        return fn(text)
    except SelectorError as e:
        return str(e)


@pytest.mark.parametrize(
    'text',
    [
        *EXAMPLES,
        'a > b + c ~ d || e',
        '/* x */a',
        '/* x\ny */a\n/* z\n*/b',
        '/*\n*/x',
        '/* x',
        '"a',
        '"a\\"',
        '"a\\\\"b',
        "'a'",
        'a/b',
        'a/',
        '/',
        '\n\na\n',
        ' \n',
        '[a="b"]',
        ':not(.a, #b)',
        '\\61 bc d',
        'a\\',
        'a\\\n',
        '&.x',
        'a\\ff',
        'a\\ffffff b',
        'a\\ffffffb',
    ],
)
def test_tokenize_examples(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


@given(escaped_text)
def test_tokenize_random(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


@given(st.text(st.sampled_from('a \n/*"\'\\:,>|'), max_size=30))
def test_tokenize_random_punctuation(text: str) -> None:
    assert outcome(tokenize, text) == outcome(python_tokenize, text)


def test_bad_arguments() -> None:
    with pytest.raises(TypeError):
        selector_unesc(1)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        selector_tokenize(None)  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        selector_cssesc('a', True, True)  # ty: ignore[missing-argument]


def test_threads() -> None:
    from .test_threads import STRESS

    text = '.a\\31 b > #c\\:d:not(.e, [f="g"]) /* h */ ~ i' * 20
    expected = (python_tokenize(text), python_unesc(text), python_cssesc(text, is_identifier=True))
    n = 4
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            barrier.wait()
            for _ in range(50 * STRESS):
                assert (tokenize(text), unesc(text), cssesc(text, is_identifier=True)) == expected
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
