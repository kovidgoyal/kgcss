# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss: fast and comprehensive CSS handling with a C99 backend.

The top level API is the CSS object model (kgcss.cssom). The lower layers it
is built on are available as submodules: kgcss.tokenizer (tokens) and
kgcss.syntax (component values and the generic CSS Syntax parse tree).
"""

from kgcss._version import __version__
from kgcss.cssom import (
    CharsetRule,
    Comment,
    ContainerRule,
    CSSSyntaxError,
    DeclarationBlock,
    DeclarationList,
    DocumentRule,
    ErrorHandler,
    FontFaceRule,
    GroupingRule,
    ImportRule,
    InvalidValueError,
    LayerBlockRule,
    LayerStatementRule,
    MarginRule,
    MediaList,
    MediaRule,
    NamespaceRule,
    NestedDeclarationsRule,
    PageRule,
    Property,
    Rule,
    RuleList,
    RuleType,
    ScopeRule,
    SerializeOptions,
    StartingStyleRule,
    StyleRule,
    Stylesheet,
    SupportsRule,
    UnknownAtRule,
    parse_style,
    parse_stylesheet,
    serialize,
)
from kgcss.decode import decode_stylesheet, lookup_encoding
from kgcss.escaping import serialize_identifier, serialize_string
from kgcss.tokenizer import ParseError

__all__ = [
    'CSSSyntaxError',
    'CharsetRule',
    'Comment',
    'ContainerRule',
    'DeclarationBlock',
    'DeclarationList',
    'DocumentRule',
    'ErrorHandler',
    'FontFaceRule',
    'GroupingRule',
    'ImportRule',
    'InvalidValueError',
    'LayerBlockRule',
    'LayerStatementRule',
    'MarginRule',
    'MediaList',
    'MediaRule',
    'NamespaceRule',
    'NestedDeclarationsRule',
    'PageRule',
    'ParseError',
    'Property',
    'Rule',
    'RuleList',
    'RuleType',
    'ScopeRule',
    'SerializeOptions',
    'StartingStyleRule',
    'StyleRule',
    'Stylesheet',
    'SupportsRule',
    'UnknownAtRule',
    '__version__',
    'decode_stylesheet',
    'lookup_encoding',
    'parse_style',
    'parse_stylesheet',
    'serialize',
    'serialize_identifier',
    'serialize_string',
]
