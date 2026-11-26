# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""custom-property-no-missing-var-function: stylelint's rule of that name.

A custom property name declared in the stylesheet (by a declaration or by
@property) that is used outside var() is reported, unless the property's
value can be a <dashed-ident> (anchor-name and the like), as css-tree says.
"""

import re
from typing import Final

from kgcss._css_data import CSS_TREE_DASHED_IDENT_PROPERTIES, CSS_TREE_PROPERTIES
from kgcss.linter._engine import Context
from kgcss.linter._postcss import Declaration, Root
from kgcss.linter._utils import css_tree_property, declaration_value_index, is_custom_property
from kgcss.linter._value_parser import ValueNode, parse

RE_PROPERTY: Final = re.compile(r'^property$', re.IGNORECASE)
RE_TRAILING_SEMICOLONS: Final = re.compile(r';+$')
RE_DASHED_WORD: Final = re.compile(r'--[^\x00-\x20"\',:/()]*')


def declared_names(root: Root) -> set[str]:
    """getDeclaredCustomPropertyNames()."""
    names: set[str] = set()
    for at_rule in root.walk_at_rules():
        if RE_PROPERTY.match(at_rule.name):
            names.update(n.value for n in parse(at_rule.params) if n.type == 'word' and is_custom_property(n.value))
    names.update(decl.prop for decl in root.walk_decls() if decl.prop.startswith('--'))
    return names


def accepts_dashed_ident(prop: str) -> bool:
    """lexer.matchProperty(prop, '--test') matches: never for a custom or unknown property."""
    if prop.startswith('--'):
        return False
    name = css_tree_property(prop, CSS_TREE_PROPERTIES)
    return name is not None and name in CSS_TREE_DASHED_IDENT_PROPERTIES


def is_dashed_ident(node: ValueNode) -> bool:
    return node.type == 'word' and node.value.startswith('--')


def is_comma(node: ValueNode) -> bool:
    return node.type == 'div' and node.value == ','


def check(root: Root, ctx: Context) -> None:
    known = declared_names(root)

    def visit(node: ValueNode, decl: Declaration) -> None:
        if node.type == 'function':
            name = node.value.lower()
            args = node.nodes or []
            if name == 'var':
                args = args[1:]
            elif name == 'running':
                child = args[0] if args else None
                if child is not None and child.type == 'function' and child.value.lower() == 'var':
                    args = (child.nodes or [])[1:]
                else:
                    return
            elif name in ('anchor', 'anchor-size'):  # only the <anchor-name> is exempt
                comma = next((i for i, a in enumerate(args) if is_comma(a)), -1)
                args = [a for i, a in enumerate(args) if not (is_dashed_ident(a) and (comma == -1 or i < comma))]
            elif name == 'style':  # only the property name of a style() query is exempt
                colon = False
                for arg in args:
                    if arg.type == 'div' and arg.value == ':':
                        colon = True
                    elif colon:
                        visit(arg, decl)
                return
            for arg in args:
                visit(arg, decl)
            return
        if not is_dashed_ident(node):
            return
        value = RE_TRAILING_SEMICOLONS.sub('', node.value)  # postcss-value-parser keeps semicolons in words
        if value in known:
            index = declaration_value_index(decl) + node.source_index
            ctx.report(decl, f'Missing var function for "{value}"', (value,), index=index, end_index=index + len(value))

    if not known:
        return

    def may_have_problem(value: str) -> bool:
        """A quick check: a problem needs a word of the value that is a known name.

        postcss-value-parser ends words at whitespace, quotes, commas, colons,
        slashes and parentheses; escapes, * (in calc()) and unbalanced
        parentheses make the words differ, and such values are checked in full.
        """
        if '\\' in value or '*' in value or value.count('(') != value.count(')'):
            return True
        return any(word.rstrip(';') in known for word in RE_DASHED_WORD.findall(value))

    for decl in root.walk_decls():
        if '--' in decl.value and may_have_problem(decl.value) and not accepts_dashed_ident(decl.prop):
            for node in parse(decl.value):
                visit(node, decl)
