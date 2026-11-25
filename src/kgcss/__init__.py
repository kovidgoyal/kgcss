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
    DocumentRule,
    ErrorHandler,
    FontFaceRule,
    GroupingRule,
    ImportRule,
    LayerBlockRule,
    LayerStatementRule,
    MarginRule,
    MediaList,
    MediaRule,
    NamespaceRule,
    NestedDeclarationsRule,
    PageRule,
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
    parse_stylesheet,
    serialize,
)
from kgcss.decode import decode_stylesheet, lookup_encoding
from kgcss.escaping import serialize_identifier, serialize_string
from kgcss.tokenizer import ParseError

__all__ = [
    'CharsetRule',
    'Comment',
    'ContainerRule',
    'DocumentRule',
    'ErrorHandler',
    'FontFaceRule',
    'GroupingRule',
    'ImportRule',
    'LayerBlockRule',
    'LayerStatementRule',
    'MarginRule',
    'MediaList',
    'MediaRule',
    'NamespaceRule',
    'NestedDeclarationsRule',
    'PageRule',
    'ParseError',
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
    'parse_stylesheet',
    'serialize',
    'serialize_identifier',
    'serialize_string',
]
