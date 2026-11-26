# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The CSS object model: a mutable tree of stylesheets, rules and declarations.

The tree is built from the generic syntax tree of
kgcss.syntax. Blocks are parsed on first access, under a per-sheet lock.
"""

from kgcss.cssom.declarations import DeclarationBlock, DeclarationList, Property, parse_style
from kgcss.cssom.exceptions import (
    CSSSyntaxError,
    ExpressionError,
    InvalidValueError,
    SelectorError,
    SelectorSyntaxError,
)
from kgcss.cssom.imports import ImportFetcher, resolve_imports
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
from kgcss.cssom.selector_list import Selector, SelectorList, invalid_selectors, parse_selectors
from kgcss.cssom.serializer import SerializeOptions, serialize
from kgcss.cssom.state import ErrorHandler
from kgcss.cssom.stylesheet import NamespaceMap, Stylesheet, parse_rule, parse_stylesheet
from kgcss.cssom.urls import iter_urls, remove_url_values, replace_urls, replace_urls_in_text
from kgcss.cssom.values import Component, PropertyValue, parse_value

__all__ = [
    'CSSSyntaxError',
    'CharsetRule',
    'Comment',
    'Component',
    'ContainerRule',
    'DeclarationBlock',
    'DeclarationList',
    'DocumentRule',
    'ErrorHandler',
    'ExpressionError',
    'FontFaceRule',
    'GroupingRule',
    'ImportFetcher',
    'ImportRule',
    'InvalidValueError',
    'LayerBlockRule',
    'LayerStatementRule',
    'MarginRule',
    'MediaList',
    'MediaRule',
    'NamespaceMap',
    'NamespaceRule',
    'NestedDeclarationsRule',
    'PageRule',
    'Property',
    'PropertyValue',
    'Rule',
    'RuleList',
    'RuleType',
    'ScopeRule',
    'Selector',
    'SelectorError',
    'SelectorList',
    'SelectorSyntaxError',
    'SerializeOptions',
    'StartingStyleRule',
    'StyleRule',
    'Stylesheet',
    'SupportsRule',
    'UnknownAtRule',
    'invalid_selectors',
    'iter_urls',
    'parse_rule',
    'parse_selectors',
    'parse_style',
    'parse_stylesheet',
    'parse_value',
    'remove_url_values',
    'replace_urls',
    'replace_urls_in_text',
    'resolve_imports',
    'serialize',
]
