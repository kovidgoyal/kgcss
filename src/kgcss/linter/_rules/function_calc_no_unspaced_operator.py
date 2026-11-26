# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""function-calc-no-unspaced-operator: stylelint's rule of that name.

The + and - operators of math functions must have a single space on both
sides. As in stylelint, the value is read with @csstools' tokenizer and
parser, with dimensions such as 1px-2px split at the dash, and a fix changes
the parsed value, which is then printed again. Each problem gets the edit
that its own fix makes. Where stylelint throws (a unit that starts with a
dash, a function left open), the declaration is skipped.
"""

import re
from typing import Final

from kgcss._css_data import MATH_FUNCTIONS
from kgcss.linter import TextEdit
from kgcss.linter._csstools import (
    ComponentValue,
    ContainerNode,
    CSSToken,
    TokenNode,
    TokenType,
    ValueData,
    WalkEntry,
    WhitespaceNode,
    is_comment_node,
    is_function_node,
    is_simple_block_node,
    is_token_comma,
    is_token_delim,
    is_token_dimension,
    is_token_ident,
    is_token_node,
    is_token_numeric,
    is_token_open_curly,
    is_token_open_paren,
    is_token_semicolon,
    is_whitespace_node,
    mutate_ident,
    mutate_unit,
    parse_list_of_component_values,
    stringify_component_value_lists,
    tokenize,
    walk,
)
from kgcss.linter._engine import Context, narrow_edit
from kgcss.linter._postcss import Declaration, Root
from kgcss.linter._utils import declaration_value, declaration_value_index

OPERATORS: Final = frozenset({'+', '-'})
MULTIPLICATIVE: Final = frozenset({'*', '/'})
RE_FUNCTION_NAMES: Final = re.compile(f'^(?:{"|".join(sorted(MATH_FUNCTIONS))})$', re.IGNORECASE)
RE_MATH_FUNCTION: Final = re.compile(rf'\b(?:{"|".join(sorted(MATH_FUNCTIONS))})\(', re.IGNORECASE | re.ASCII)
RE_OPERATOR: Final = re.compile(r'[+-]')
RE_NEWLINE: Final = re.compile(r'\n|\r\n')
OPERAND_TOKEN_TYPES: Final = frozenset({TokenType.NUMBER, TokenType.DIMENSION, TokenType.PERCENTAGE, TokenType.IDENT})


def tokenize_value(value: str) -> list[ComponentValue]:
    """tokenizeDeclarationValue(): 1px-2px as 1px -2px, and SCSS #{$x} kept together."""
    tokens = tokenize(value)
    # Array.forEach() visits the indexes the array had when it started, while the code splices into it
    for i in range(len(tokens)):
        if i >= len(tokens) or not is_token_dimension(token := tokens[i]):
            continue
        unit = token.numeric_data.unit or ''
        if unit.startswith('--') or (dash := unit.find('-')) == -1:
            continue
        remainder = unit[dash:]
        if len(remainder) == 1:
            continue
        mutate_unit(token, unit[:dash])
        token.end = token.start + len(token.raw)  # one past the end, as in stylelint
        rest = tokenize(remainder)[:-1]
        for t in rest:
            t.start += token.end
            t.end += token.end
        tokens[i + 1 : i + 1] = rest
    for i in range(len(tokens)):
        if i >= len(tokens):
            continue
        token = tokens[i]
        if not is_token_delim(token) or token.value_data.value != '#':
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if not is_token_open_curly(nxt):
            continue
        nxt2 = tokens[i + 2] if i + 2 < len(tokens) else None
        if nxt2 is None or not is_token_delim(nxt2) or nxt2.value_data.value != '$':
            continue
        assert nxt is not None
        nxt.raw = '#{'
        del tokens[i]
    return parse_list_of_component_values(tokens)


def is_operand_node(node: ComponentValue | None) -> bool:
    if is_simple_block_node(node):
        return True
    if is_function_node(node):
        name = node.get_name().lower()
        return name in MATH_FUNCTIONS or name == 'var'
    return is_token_node(node) and node.value.type in OPERAND_TOKEN_TYPES


def is_whitespace_or_comment(node: ComponentValue | None) -> bool:
    return is_whitespace_node(node) or is_comment_node(node)


def new_whitespace(whitespace: str = ' ') -> WhitespaceNode:
    return WhitespaceNode([CSSToken(TokenType.WHITESPACE, whitespace, -1, -1)])


def index_of(nodes: list[ComponentValue], item: object) -> int:
    return next((i for i, n in enumerate(nodes) if n is item), -1)


class Operation:
    __slots__ = ('after', 'before', 'first', 'operator', 'second')

    def __init__(
        self,
        first: ComponentValue,
        before: list[ComponentValue],
        second: ComponentValue,
        after: list[ComponentValue],
        operator: TokenNode | None,
    ) -> None:
        self.first, self.before, self.second, self.after, self.operator = first, before, second, after, operator

    @property
    def operator_token(self) -> CSSToken:
        assert self.operator is not None
        assert is_token_delim(self.operator.value)
        return self.operator.value

    @property
    def operator_char(self) -> str:
        return self.operator_token.value_data.value

    @property
    def operator_position(self) -> int:
        return self.operator_token.start

    def insert_whitespace(self, node: ContainerNode, position: str) -> None:
        assert self.operator is not None
        i = index_of(node.value, self.operator) + (0 if position == 'before' else 1)
        node.value.insert(i, new_whitespace())

    def insert_operator_after_first(self, node: ContainerNode) -> None:
        assert self.operator is not None
        i = index_of(node.value, self.first) + 1
        node.value[i:i] = [*self.before, self.operator]

    def insert_operator_before_second(self, node: ContainerNode) -> None:
        assert self.operator is not None
        i = index_of(node.value, self.second)
        node.value[i:i] = [self.operator, *self.after]

    def complete_missing_operator(self, char: str, position: int, append: bool) -> None:
        token = CSSToken(TokenType.DELIM, char, position, position + len(char), ValueData(char))
        self.operator = TokenNode(token)
        if append:
            self.after = self.before
            self.before = [new_whitespace()]
        else:
            self.after = [new_whitespace()]


def parse_operation(container: list[ComponentValue], cursor: int) -> tuple[Operation | None, int]:
    def at(i: int) -> ComponentValue | None:
        return container[i] if 0 <= i < len(container) else None

    first = second = None
    before: list[ComponentValue] = []
    after: list[ComponentValue] = []
    operator: TokenNode | None = None
    current = at(cursor)
    while is_whitespace_or_comment(current):
        cursor += 1
        current = at(cursor)
    if is_operand_node(current):
        first = current
        cursor += 1
        current = at(cursor)
    while is_whitespace_or_comment(current):
        assert current is not None
        before.append(current)
        cursor += 1
        current = at(cursor)
    if is_token_node(current) and is_token_delim(current.value) and current.value.value_data.value in OPERATORS:
        operator = current
        cursor += 1
        current = at(cursor)
    while is_whitespace_or_comment(current):
        assert current is not None
        after.append(current)
        cursor += 1
        current = at(cursor)
    if is_operand_node(current):
        second = current
        cursor += 1
        current = at(cursor)
    while is_whitespace_or_comment(current):
        cursor += 1
        current = at(cursor)
    if first is None or second is None:
        while current is not None:
            if is_token_node(current):
                token = current.value
                if is_token_comma(token) or is_token_semicolon(token):
                    return None, cursor
                if is_token_delim(token) and token.value_data.value in MULTIPLICATIVE:
                    return None, cursor
            cursor += 1
            current = at(cursor)
        return None, len(container)
    return Operation(first, before, second, after, operator), cursor


class Complaint:
    __slots__ = ('index', 'message', 'operator')

    def __init__(self, message: str, operator: str, index: int) -> None:
        self.message, self.operator, self.index = message, operator, index


def analyze(value: str, apply: int | None = None, apply_all: bool = False) -> tuple[list[Complaint], str | None]:
    """The problems of a value, and the value fixed.

    With apply, only the fix of the problem at that index is applied. With
    apply_all, every fix is applied when its problem is found, as stylelint
    does when it fixes, so the problems found after it see the fixed value.
    """
    nodes = tokenize_value(value)
    complaints: list[Complaint] = []
    fixed: list[str] = []

    def complain(before: bool, operation: Operation, fix: object) -> None:
        char = operation.operator_char
        message = f'Expected single space {"before" if before else "after"} "{char}" operator'
        complaints.append(Complaint(message, char, operation.operator_position))
        if apply_all or (apply is not None and len(complaints) - 1 == apply):
            assert callable(fix)
            fix()
            fixed[:] = [stringify_component_value_lists([nodes])]

    def check_complete(node: ContainerNode, operation: Operation, before: bool) -> None:
        if any(is_whitespace_node(n) for n in (operation.before if before else operation.after)):
            return
        complain(before, operation, lambda: operation.insert_whitespace(node, 'before' if before else 'after'))

    def check_without_operator(node: ContainerNode, operation: Operation) -> None:
        if is_token_node(operation.first):
            token = operation.first.value

            def to_first_operand(char: str | None, mutate: object) -> bool:
                if not char or not RE_OPERATOR.search(char):
                    return False
                operation.complete_missing_operator(char, token.end, append=True)

                def fix() -> None:
                    operation.insert_operator_after_first(node)
                    assert callable(mutate)
                    mutate()

                complain(True, operation, fix)
                return True

            if is_token_dimension(token):
                unit = token.numeric_data.unit or ''
                if to_first_operand(unit[-1:] or None, lambda: mutate_unit(token, unit[:-1])):
                    return
            if is_token_ident(token):
                ident = token.value_data.value
                if to_first_operand(ident[-1:] or None, lambda: mutate_ident(token, ident[:-1])):
                    return
        if is_token_node(operation.second) and is_token_numeric(operation.second.value):
            token = operation.second.value
            char = token.numeric_data.sign_character
            if char and RE_OPERATOR.search(char):
                operation.complete_missing_operator(char, token.start, append=False)

                def fix() -> None:
                    operation.insert_operator_before_second(node)
                    token.numeric_data.sign_character = None
                    token.raw = token.raw[1:]

                complain(False, operation, fix)

    def check_whitespace(operation: Operation, before: bool) -> None:
        for node in operation.before if before else operation.after:
            if not isinstance(node, WhitespaceNode):
                continue
            whitespace = node.to_string()
            if whitespace == ' ':
                continue
            m = RE_NEWLINE.search(whitespace)
            first_newline = -1 if m is None else m.start()
            if first_newline == 0:
                continue

            def fix(node: WhitespaceNode = node, ws: str = whitespace, nl: int = first_newline) -> None:
                node.value = new_whitespace(' ' if nl == -1 else ws[nl:]).value

            complain(before, operation, fix)

    def visit(entry: WalkEntry[ComponentValue, object, dict[str, bool]], index: int | str) -> None:
        state, node = entry.state, entry.node
        if state is None:
            return
        if is_function_node(node):
            state['in_math'] = RE_FUNCTION_NAMES.match(node.get_name().lower()) is not None
        elif not is_simple_block_node(node) or not is_token_open_paren(node.start_token):
            state['in_math'] = False
            return
        if not state['in_math']:
            return
        cursor = 0
        while cursor != -1 and cursor < len(node.value):
            operation, cursor = parse_operation(node.value, cursor)
            if operation is None:
                cursor += 1
                continue
            if operation.operator is None:
                check_without_operator(node, operation)
            if operation.operator is not None:
                check_complete(node, operation, True)
                check_complete(node, operation, False)
                check_whitespace(operation, True)
                check_whitespace(operation, False)
            cursor = index_of(node.value, operation.second)

    walk(nodes, visit, {'in_math': False})
    return complaints, fixed[0] if fixed else None


def value_edit(css: str, decl: Declaration, old_value: str, new_value: str) -> TextEdit | None:
    """The edit that sets the value of decl, as stylelint computes edit info for it."""
    if decl.start is None or decl.end is None:
        return None
    start = decl.start + declaration_value_index(decl)
    text = css[decl.start : start] + new_value + css[start + len(old_value) : decl.end]
    return narrow_edit(css, decl.start, decl.end, text)


def check(root: Root, ctx: Context) -> None:
    for decl in root.walk_decls():
        value = declaration_value(decl)
        if not RE_OPERATOR.search(value) or not RE_MATH_FUNCTION.search(value):
            continue
        try:
            complaints, _ = analyze(value)
        except TypeError:  # stylelint throws: a unit that starts with -, or an unclosed function when fixing
            continue
        start = declaration_value_index(decl)
        # A fix can depend on an earlier one (an operator that it inserts), so, as stylelint gives edit
        # info for the first fix of a declaration only, the first problem gets the edit of its fix, and
        # the others the edit of all the fixes, applied as stylelint applies them when it fixes.
        first_fix = all_fixes = None
        try:
            if (fixed := analyze(value, apply=0)[1]) is not None:
                first_fix = value_edit(ctx.css, decl, value, fixed)
            if len(complaints) > 1 and (fixed := analyze(value, apply_all=True)[1]) is not None:
                all_fixes = value_edit(ctx.css, decl, value, fixed)
        except TypeError:  # stylelint throws on an unclosed nested function when it fixes
            pass
        for i, c in enumerate(complaints):
            fix = first_fix if i == 0 else all_fixes
            index = start + c.index
            ctx.report(decl, c.message, (c.operator,), index=index, end_index=index + len(c.operator), fix=fix)
