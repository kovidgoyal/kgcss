# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss: fast and comprehensive CSS handling with a C99 backend."""

from kgcss._version import __version__
from kgcss.serialize import serialize_identifier, serialize_string
from kgcss.syntax import (
    AtRule,
    Block,
    ComponentValue,
    ComponentValueList,
    Declaration,
    Function,
    Node,
    NodeList,
    QualifiedRule,
    Rule,
    parse_block_contents,
    parse_component_value_list,
    parse_one_component_value,
    parse_one_declaration,
    parse_one_rule,
    parse_rule_list,
    parse_stylesheet,
)
from kgcss.tokenizer import ParseError, Token, TokenStream, TokenType, tokenize

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
    'TokenStream',
    'TokenType',
    '__version__',
    'parse_block_contents',
    'parse_component_value_list',
    'parse_one_component_value',
    'parse_one_declaration',
    'parse_one_rule',
    'parse_rule_list',
    'parse_stylesheet',
    'serialize_identifier',
    'serialize_string',
    'tokenize',
]
