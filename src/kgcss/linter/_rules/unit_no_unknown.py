# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""unit-no-unknown: stylelint's lib/rules/unit-no-unknown.

Values and the params of @media are read with @csstools' tokenizer and
parsers. The unit x is known only for resolutions: in image-set() and
image-resolution, and in the resolution media features.
"""

import re
from typing import Final

from kgcss._css_data import UNITS
from kgcss.linter._csstools import (
    ComponentValue,
    ComponentWalkEntry,
    CSSToken,
    NestingDepthError,
    TokenType,
    is_function_node,
    is_token_node,
    parse_list_of_component_values,
    tokenize,
    walk,
)
from kgcss.linter._engine import Context
from kgcss.linter._media_query import MediaWalkEntry, is_media_feature, parse_from_tokens
from kgcss.linter._postcss import AtRule, Declaration, Root
from kgcss.linter._utils import (
    at_rule_param_index,
    at_rule_params,
    declaration_value,
    declaration_value_index,
    is_standard_syntax_at_rule,
    is_standard_syntax_declaration,
    is_standard_syntax_value,
    vendor_unprefixed,
)

OPTIONS: Final = frozenset({'ignoreFunctions', 'ignoreUnits'})
KNOWN_UNITS: Final = UNITS - {'x'}  # x is often a typo for px: it is known only where a resolution is
RE_DIMENSION: Final = re.compile(r'\d[%\w-]', re.ASCII)
RE_UNIT: Final = re.compile(r'(?<=[0-9])[-A-Za-z_][-A-Za-z0-9_]*')
RE_NO_DIMENSIONS: Final = re.compile(r'#[-A-Za-z0-9_]+|"[^"\n\r\f]*"|\'[^\'\n\r\f]*\'|url\([^"\'()]*\)', re.IGNORECASE)
RE_RESOLUTION_FEATURE: Final = re.compile(r'^(?:min-|max-)?resolution$', re.IGNORECASE)
RE_MEDIA: Final = re.compile(r'^media$', re.IGNORECASE)
RE_UNICODE_RANGE: Final = re.compile(r'^unicode-range$', re.IGNORECASE)
RE_FONT_FACE: Final = re.compile(r'^font-face$', re.IGNORECASE)

type State = dict[str, bool]


def tokens_with_unknown_units(value: str, quick: bool = True) -> list[CSSToken] | None:
    if not RE_DIMENSION.search(value):
        return None
    # A quick check: in ASCII text without escapes, the unit of a dimension token is a run of ident
    # characters after a digit. When all such runs are known units, no unit can be unknown.
    # Hashes, strings and unquoted urls, which hold no dimension, are left out of the check.
    if quick and value.isascii() and '\\' not in value:
        text = RE_NO_DIMENSIONS.sub(' ', value)
        if all(unit.lower() in KNOWN_UNITS for unit in RE_UNIT.findall(text)):
            return None
    tokens = tokenize(value)
    if any(t.type is TokenType.DIMENSION and (t.numeric_data.unit or '').lower() not in KNOWN_UNITS for t in tokens):
        return tokens
    return None


def check_node(ctx: Context, node: AtRule | Declaration, start: int, value: ComponentValue, state: State) -> None:
    if is_function_node(value):
        name = value.get_name()
        lower = name.lower()
        if lower == 'url' or ctx.matches('ignoreFunctions', name):
            state['ignored'] = True
        elif vendor_unprefixed(lower) == 'image-set':
            state['allow_x'] = True
        return
    if not is_token_node(value) or value.value.type is not TokenType.DIMENSION:
        return
    token = value.value
    unit = token.numeric_data.unit or ''
    if ctx.matches('ignoreUnits', unit):
        return
    lower = unit.lower()
    if (lower == 'x' and state['allow_x']) or lower in KNOWN_UNITS:
        return
    index = start + token.end + 1 - len(lower)
    ctx.report(node, f'Unknown unit "{unit}"', (unit,), index=index, end_index=index + len(lower))


def check(root: Root, ctx: Context) -> None:
    for at_rule in root.walk_at_rules():
        if not RE_MEDIA.match(at_rule.name) or not is_standard_syntax_at_rule(at_rule):
            continue
        if (tokens := tokens_with_unknown_units(at_rule_params(at_rule), not ctx.options)) is None:
            continue
        start = at_rule_param_index(at_rule)

        def visit_media(
            entry: MediaWalkEntry[State], index: int | str, at_rule: AtRule = at_rule, start: int = start
        ) -> None:
            state, node = entry.state, entry.node
            if state is None or state['ignored']:
                return
            if is_media_feature(node):
                if RE_RESOLUTION_FEATURE.match(node.get_name()):
                    state['allow_x'] = True
            elif is_function_node(node) or is_token_node(node):
                check_node(ctx, at_rule, start, node, state)

        try:
            queries = parse_from_tokens(tokens)
        except NestingDepthError:  # blocks nested too deeply, where stylelint throws
            continue
        for query in queries:
            query.walk(visit_media, {'ignored': False, 'allow_x': False})

    for decl in root.walk_decls():
        if not is_standard_syntax_declaration(decl):
            continue
        parent = decl.parent
        if RE_UNICODE_RANGE.match(decl.prop) and isinstance(parent, AtRule) and RE_FONT_FACE.match(parent.name):
            continue
        value = declaration_value(decl)
        if not is_standard_syntax_value(value) or (tokens := tokens_with_unknown_units(value, not ctx.options)) is None:
            continue
        start = declaration_value_index(decl)

        def visit(
            entry: ComponentWalkEntry[State], index: int | str, decl: Declaration = decl, start: int = start
        ) -> None:
            state, node = entry.state, entry.node
            if state is None or state['ignored']:
                return
            if is_function_node(node) or is_token_node(node):
                check_node(ctx, decl, start, node, state)

        initial = {'ignored': False, 'allow_x': decl.prop.lower() == 'image-resolution'}
        try:
            nodes = parse_list_of_component_values(tokens)
        except NestingDepthError:  # functions or blocks nested too deeply, where stylelint throws
            continue
        walk(nodes, visit, initial)
