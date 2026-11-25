# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Run the vendored css-parsing-tests conformance suite."""

from collections.abc import Callable

import pytest

from kgcss import ParseError
from kgcss.syntax import (
    AtRule,
    Block,
    Declaration,
    Function,
    NodeList,
    QualifiedRule,
    parse_block_contents,
    parse_component_value_list,
    parse_one_component_value,
    parse_one_declaration,
    parse_one_rule,
    parse_rule_list,
    parse_stylesheet,
)
from kgcss.tokenizer import Token

from .suite import (
    JSON,
    item_to_json,
    load_cases,
    node_list_to_json,
    node_to_json,
    normalize_declarations,
    normalize_expected,
    values_to_json,
)

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


# Syntax level cases where we deliberately differ from the suite, which
# follows the 2021 CR of CSS Syntax 3 rather than the current ED. Maps
# file -> input -> (our expected result, reason).
SYNTAX_DIFFERENCES: dict[str, dict[str, tuple[JSON, str]]] = {
    'declaration_list.json': {
        '@ media screen { div{;}} a:b;; @media print{div{': (
            dict(load_cases('blocks_contents.json'))['@ media screen { div{;}} a:b;; @media print{div{'],
            'The ED parses block contents with nesting, so the invalid at-rule is a nested qualified rule '
            '(the same input in blocks_contents.json has this expectation)',
        ),
    },
    'one_declaration.json': {
        'foo:;': (['declaration', 'foo', [], False], 'The ED stops a declaration value at ;'),
        'foo:;bar:;': (['declaration', 'foo', [], False], 'The ED stops a declaration value at ;'),
    },
}

LIST_PARSERS: dict[str, Callable[..., NodeList]] = {
    'stylesheet.json': parse_stylesheet,
    'rule_list.json': parse_rule_list,
    'blocks_contents.json': parse_block_contents,
    'declaration_list.json': parse_block_contents,
}


def syntax_cases(names: list[str]) -> list[tuple[str, str, JSON]]:
    return [(name, css, expected) for name in names for css, expected in load_cases(name)]


def expected_for(name: str, css: str, expected: JSON) -> JSON:
    if (diff := SYNTAX_DIFFERENCES.get(name, {}).get(css)) is not None:
        return normalize_declarations(diff[0])
    return normalize_declarations(normalize_expected(expected))


@pytest.mark.parametrize('kind', list(KIND_PREFIXES))
@pytest.mark.parametrize(('name', 'css', 'expected'), syntax_cases(list(LIST_PARSERS)), ids=case_id)
def test_syntax_lists(name: str, css: str, expected: JSON, kind: str) -> None:
    nodes = LIST_PARSERS[name](KIND_PREFIXES[kind] + css, comments=False)
    assert node_list_to_json(nodes) == expected_for(name, css, expected)


@pytest.mark.parametrize(
    ('name', 'css', 'expected'), syntax_cases(['one_rule.json', 'one_declaration.json']), ids=case_id
)
def test_syntax_single(name: str, css: str, expected: JSON) -> None:
    result: AtRule | QualifiedRule | Declaration | ParseError
    result = parse_one_rule(css) if name == 'one_rule.json' else parse_one_declaration(css)
    if isinstance(result, ParseError) and result.kind not in ('invalid-rule', 'invalid-declaration'):
        actual: JSON = ['error', result.kind]
    else:
        actual = node_to_json(result)
    assert actual == expected_for(name, css, expected)


def test_syntax_differences_are_in_suite() -> None:
    for name, diffs in SYNTAX_DIFFERENCES.items():
        assert set(diffs) <= {css for css, _ in load_cases(name)}


def bytes_case_id(value: object) -> str:
    if isinstance(value, dict):
        return repr(value.get('comment') or value['css_bytes'])[:40]
    return ''


@pytest.mark.parametrize(('case', 'expected'), load_cases('stylesheet_bytes.json'), ids=bytes_case_id)
def test_stylesheet_bytes(case: JSON, expected: JSON) -> None:
    assert isinstance(case, dict)
    css_bytes = case['css_bytes']
    assert isinstance(css_bytes, str)
    protocol, environment = case.get('protocol_encoding'), case.get('environment_encoding')
    assert protocol is None or isinstance(protocol, str)
    assert environment is None or isinstance(environment, str)
    nodes = parse_stylesheet(
        css_bytes.encode('latin-1'), comments=False, protocol_encoding=protocol, environment_encoding=environment
    )
    assert [node_list_to_json(nodes), nodes.encoding] == normalize_expected(expected)
