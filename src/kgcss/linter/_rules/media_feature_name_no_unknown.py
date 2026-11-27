# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""media-feature-name-no-unknown: stylelint's rule of that name.

The feature names are found as stylelint's findMediaFeatureNames() finds
them: in each top level parenthesized block of the params of @media, read as
a media query list by @csstools/media-query-list-parser, and, in what does
not parse as a media feature (general enclosed), in idents next to a colon
or a range operator.
"""

import re
from collections.abc import Iterator
from typing import Final

from kgcss._css_data import MEDIA_FEATURE_NAMES
from kgcss.linter._csstools import (
    CSSToken,
    NestingDepthError,
    TokenNode,
    TokenType,
    is_simple_block_node,
    is_token_node,
    is_token_open_paren,
    parse_comma_separated_list_of_component_values,
    tokenize,
)
from kgcss.linter._engine import Context
from kgcss.linter._media_query import (
    GeneralEnclosed,
    MediaQueryInvalid,
    MediaWalkEntry,
    is_general_enclosed,
    is_media_feature,
    parse_from_tokens,
)
from kgcss.linter._postcss import Root
from kgcss.linter._utils import at_rule_param_index, at_rule_params, vendor_prefix

OPTIONS: Final = frozenset({'ignoreMediaFeatureNames'})
RE_MEDIA: Final = re.compile(r'^media$', re.IGNORECASE)
RE_RANGE_OPERATOR: Final = re.compile(r'[<>=]')


def is_delim(node: TokenNode | None, value: str) -> bool:
    return node is not None and node.value.type is TokenType.DELIM and node.value.value_data.value == value


def top_level_tokens(node: GeneralEnclosed) -> list[CSSToken]:
    """topLevelTokenNodes(): the tokens directly in the block, without SCSS variables."""
    components = node.value.value
    if isinstance(components, CSSToken) or not components or isinstance(components[0], CSSToken):
        return []
    token_nodes = [c for c in components if is_token_node(c)]
    ans = []
    for i, component in enumerate(token_nodes):
        prev = token_nodes[i - 1] if i > 0 else None
        nxt = token_nodes[i + 1] if i + 1 < len(token_nodes) else None
        ident = component.value.type is TokenType.IDENT
        if is_delim(component, '$') or (ident and is_delim(prev, '$')):
            continue
        if is_delim(component, '.') or (ident and is_delim(nxt, '.')):
            continue
        ans.append(component.value)
    return ans


def is_range_delim(token: CSSToken | None) -> bool:
    return (
        token is not None
        and token.type is TokenType.DELIM
        and RE_RANGE_OPERATOR.search(token.value_data.value) is not None
    )


def media_feature_names(params: str) -> Iterator[CSSToken]:
    """findMediaFeatureNames(): the ident tokens of the media feature names in params."""
    found: list[CSSToken] = []

    def visit(entry: MediaWalkEntry[dict[str, object]], index: int | str) -> None:
        node = entry.node
        if is_media_feature(node):
            token = node.get_name_token()
            if token.type is TokenType.IDENT:
                found.append(token)
        if is_general_enclosed(node):
            tokens = top_level_tokens(node)
            for i, token in enumerate(tokens):
                if token.type is not TokenType.IDENT:
                    continue
                prev = tokens[i - 1] if i > 0 else None
                nxt = tokens[i + 1] if i + 1 < len(tokens) else None
                if (
                    (prev is None and nxt is not None and nxt.type is TokenType.COLON)
                    or is_range_delim(nxt)
                    or is_range_delim(prev)
                ):
                    found.append(token)

    for item in parse_comma_separated_list_of_component_values(tokenize(params)):
        for value in item:
            if not is_simple_block_node(value) or not is_token_open_paren(value.start_token):
                continue
            block_tokens = value.tokens()
            if any(t is None for t in block_tokens):
                continue  # an unclosed nested block, on which stylelint throws
            queries = parse_from_tokens([t for t in block_tokens if t is not None], preserve_invalid_media_queries=True)
            for query in queries:
                if not isinstance(query, MediaQueryInvalid):
                    query.walk(visit)
    return iter(found)


def check(root: Root, ctx: Context) -> None:
    for at_rule in root.walk_at_rules():
        if not RE_MEDIA.match(at_rule.name):
            continue
        try:
            names = list(media_feature_names(at_rule_params(at_rule)))
        except NestingDepthError:  # blocks nested too deeply, where stylelint throws
            continue
        for token in names:
            name = token.value_data.value
            if name.startswith('--') or ctx.matches('ignoreMediaFeatureNames', name):
                continue
            if vendor_prefix(name) or name.lower() in MEDIA_FEATURE_NAMES:
                continue
            start = at_rule_param_index(at_rule)
            ctx.report(
                at_rule,
                f'Unknown media feature name "{name}"',
                (name,),
                index=start + token.start,
                end_index=start + token.end + 1,
            )
