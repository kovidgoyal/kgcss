# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Run the vendored css-parsing-tests conformance suite."""

import pytest

from kgcss import Block, Function, ParseError, Token, parse_component_value_list, parse_one_component_value

from .suite import JSON, item_to_json, load_cases, normalize_expected, values_to_json

# Inputs where we deliberately differ from the suite, with the reason. Our
# actual behaviour for these is tested in test_tokenizer.py.
NO_UNICODE_RANGE_TOKEN = 'CSS Syntax 3 has no unicode-range token, <urange> is parsed at the value layer'
EXPECTED_DIFFERENCES: dict[str, str] = {
    'u+1 U+10 U+100 U+1000 U+10000 U+100000 U+1000000': NO_UNICODE_RANGE_TOKEN,
    'u+? u+1? U+10? U+100? U+1000? U+10000? U+100000?': NO_UNICODE_RANGE_TOKEN,
    'u+?? U+1?? U+10?? U+100?? U+1000?? U+10000??': NO_UNICODE_RANGE_TOKEN,
    'u+??? U+1??? U+10??? U+100??? U+1000???': NO_UNICODE_RANGE_TOKEN,
    'u+???? U+1???? U+10???? U+100????': NO_UNICODE_RANGE_TOKEN,
    'u+????? U+1????? U+10?????': NO_UNICODE_RANGE_TOKEN,
    'u+?????? U+1??????': NO_UNICODE_RANGE_TOKEN,
    'u+1-2 U+100000-2 U+1000000-2 U+10-200000': NO_UNICODE_RANGE_TOKEN,
    'ù+12 Ü+12 u +12 U+ 12 U+12 - 20 U+1?2 U+1?-50': NO_UNICODE_RANGE_TOKEN,
}

# Prefixes that force the source str into each PEP 393 kind, so that all
# three instantiations of the C tokenizer are exercised. Comments are dropped
# from the output, so the expected results are unchanged.
KIND_PREFIXES = {'ucs1': '', 'ucs2': '/*Ā*/', 'ucs4': '/*\U0001f600*/'}


def case_id(value: object) -> str:
    return repr(value)[:30] if isinstance(value, str) else ''


def component_value_cases() -> list[tuple[str, JSON]]:
    return load_cases('component_value_list.json')


@pytest.mark.parametrize('kind', list(KIND_PREFIXES))
@pytest.mark.parametrize(('css', 'expected'), component_value_cases(), ids=case_id)
def test_component_value_list(css: str, expected: JSON, kind: str) -> None:
    if css in EXPECTED_DIFFERENCES:
        pytest.skip(EXPECTED_DIFFERENCES[css])
    values = parse_component_value_list(KIND_PREFIXES[kind] + css)
    assert values_to_json(values) == normalize_expected(expected)


def one_value_to_json(value: Token | Block | Function | ParseError) -> JSON:
    if isinstance(value, ParseError):
        return ['error', value.kind]
    (ans,) = item_to_json(value)
    return ans


@pytest.mark.parametrize(('css', 'expected'), load_cases('one_component_value.json'), ids=case_id)
def test_one_component_value(css: str, expected: JSON) -> None:
    assert one_value_to_json(parse_one_component_value(css)) == normalize_expected(expected)


def test_expected_differences_are_in_suite() -> None:
    inputs = {css for css, _ in component_value_cases()}
    assert set(EXPECTED_DIFFERENCES) <= inputs
