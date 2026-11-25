# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Component values and the generic CSS Syntax Level 3 parse algorithms.

Blocks and functions are resolved by the tokenizer in the same pass, so
ComponentValueList, Block and Function are cheap views over a TokenStream:
items are only turned into Python objects when they are accessed.

The parse functions produce the generic syntax tree: at-rules and qualified
rules with a raw prelude and {} block, and declarations. What a block holds
depends on the rule (a style rule has declarations and nested rules, an
unknown at-rule has anything), so blocks are parsed further on demand with
parse_block_contents() or parse_rule_list().
"""

from kgcss._kgcss import (
    AtRule,
    Block,
    ComponentValueList,
    Declaration,
    Function,
    NodeList,
    ParseError,
    QualifiedRule,
    Token,
    parse_block_contents,
    parse_component_value_list,
    parse_one_component_value,
    parse_one_declaration,
    parse_one_rule,
    parse_rule_list,
)
from kgcss._kgcss import parse_stylesheet as _parse_stylesheet
from kgcss.decode import Buffer, decode_stylesheet

type ComponentValue = Token | Block | Function
type Rule = AtRule | QualifiedRule
# Items of a NodeList. Tokens are always comments.
type Node = AtRule | QualifiedRule | Declaration | Token


def parse_stylesheet(
    source: str | Buffer,
    /,
    *,
    comments: bool = True,
    protocol_encoding: str | None = None,
    environment_encoding: str | None = None,
) -> NodeList:
    """Parse a stylesheet into at-rules and qualified rules (CSS Syntax "parse a stylesheet").

    source can be text, or bytes which are decoded as described in
    decode_stylesheet(), using protocol_encoding and environment_encoding.
    The encoding used is available as NodeList.encoding (None for text), and
    positions refer to the decoded text, NodeList.stream.source. With
    comments=True, comments between rules are included as COMMENT tokens.
    """
    if isinstance(source, str):
        return _parse_stylesheet(source, comments=comments)
    text, encoding = decode_stylesheet(
        source, protocol_encoding=protocol_encoding, environment_encoding=environment_encoding
    )
    return _parse_stylesheet(text, comments=comments, encoding=encoding)


__all__ = [
    'AtRule',
    'Block',
    'ComponentValue',
    'ComponentValueList',
    'Declaration',
    'Function',
    'Node',
    'NodeList',
    'ParseError',
    'QualifiedRule',
    'Rule',
    'Token',
    'parse_block_contents',
    'parse_component_value_list',
    'parse_one_component_value',
    'parse_one_declaration',
    'parse_one_rule',
    'parse_rule_list',
    'parse_stylesheet',
]
