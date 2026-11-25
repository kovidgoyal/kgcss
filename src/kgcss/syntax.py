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
    parse_stylesheet,
)

type ComponentValue = Token | Block | Function
type Rule = AtRule | QualifiedRule
# Items of a NodeList. Tokens are always comments.
type Node = AtRule | QualifiedRule | Declaration | Token

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
    'parse_block_contents',
    'parse_component_value_list',
    'parse_one_component_value',
    'parse_one_declaration',
    'parse_one_rule',
    'parse_rule_list',
    'parse_stylesheet',
]
