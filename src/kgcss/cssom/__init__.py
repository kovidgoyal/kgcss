# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The CSS object model: a mutable tree of stylesheets, rules and declarations.

See docs/plan-02-cssom.md. The tree is built from the generic syntax tree of
kgcss.syntax. Blocks are parsed on first access, under a per-sheet lock.
"""

from kgcss.cssom.rules import (
    CharsetRule,
    Comment,
    ContainerRule,
    DocumentRule,
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
    StartingStyleRule,
    StyleRule,
    SupportsRule,
    UnknownAtRule,
)
from kgcss.cssom.serializer import SerializeOptions, serialize
from kgcss.cssom.state import ErrorHandler
from kgcss.cssom.stylesheet import Stylesheet, parse_stylesheet

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
    'parse_stylesheet',
    'serialize',
]
