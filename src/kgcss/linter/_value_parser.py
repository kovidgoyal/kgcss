# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A port of postcss-value-parser 4.2, which stylelint's rules parse values with.

Nodes are words, strings, divisions (, / :), spaces, comments, functions and
unicode ranges, with offsets into the parsed text (source_index and
source_end_index). As in the JavaScript, an unclosed string or url() gets its
missing quote or parenthesis added to the text, and a calc() reads / and *
as words.
"""

import re
from collections.abc import Callable, Iterable
from typing import Final

from kgcss._kgcss import ValueNode as _ValueNode
from kgcss._kgcss import value_parse

RE_UNICODE_RANGE: Final = re.compile(r'^[a-fA-F0-9?-]+$')


# A node: what postcss-value-parser gives as a plain object, in C (see csrc/value_parser.c). Its type is one
# of word string div space comment function unicode-range
ValueNode = _ValueNode


def _code(value: str, i: int) -> int:
    """charCodeAt(): -1 for NaN, past the end, which compares false with everything we compare."""
    return ord(value[i]) if 0 <= i < len(value) else -1


def _le32(code: int) -> bool:
    return 0 <= code <= 32


def parse(value: str) -> list[ValueNode]:
    """The nodes of a value, as postcss-value-parser's parse() (in C, python_parse() is the reference)."""
    return value_parse(value)


def python_parse(value: str) -> list[ValueNode]:
    """The nodes of a value, as postcss-value-parser's parse()."""
    tokens: list[ValueNode] = []
    root = ValueNode('root', 0)
    root.nodes = tokens
    stack: list[ValueNode] = [root]
    pos = 0
    code = _code(value, pos)
    max_ = len(value)
    balanced = 0
    parent: ValueNode | None = None
    name = before = after = ''

    def in_calc() -> bool:
        return parent is not None and parent.type == 'function' and parent.value == 'calc'

    while pos < max_:
        if _le32(code):  # whitespace
            nxt = pos
            while True:
                nxt += 1
                code = _code(value, nxt)
                if not _le32(code):
                    break
            token = value[pos:nxt]
            prev = tokens[-1] if tokens else None
            if code == 0x29 and balanced:
                after = token
            elif prev is not None and prev.type == 'div':
                prev.after = token
                prev.source_end_index += len(token)
            elif code in (0x2C, 0x3A) or (
                code == 0x2F
                and _code(value, nxt + 1) != 0x2A
                and (parent is None or (parent.type == 'function' and parent.value != 'calc'))
            ):
                before = token
            else:
                node = ValueNode('space', pos, token)
                node.source_end_index = nxt
                tokens.append(node)
            pos = nxt
        elif code in (0x27, 0x22):  # quotes
            nxt = pos
            quote = chr(code)
            node = ValueNode('string', pos)
            node.quote = quote
            while True:
                escape = False
                nxt = value.find(quote, nxt + 1)
                if nxt != -1:
                    escape_pos = nxt
                    while _code(value, escape_pos - 1) == 0x5C:
                        escape_pos -= 1
                        escape = not escape
                else:
                    value += quote
                    nxt = len(value) - 1
                    node.unclosed = True
                if not escape:
                    break
            node.value = value[pos + 1 : nxt]
            node.source_end_index = nxt if node.unclosed else nxt + 1
            tokens.append(node)
            pos = nxt + 1
            code = _code(value, pos)
        elif code == 0x2F and _code(value, pos + 1) == 0x2A:  # comments
            nxt = value.find('*/', pos)
            node = ValueNode('comment', pos)
            node.source_end_index = nxt + 2
            if nxt == -1:
                node.unclosed = True
                nxt = len(value)
                node.source_end_index = nxt
            node.value = value[pos + 2 : nxt]
            tokens.append(node)
            pos = nxt + 2
            code = _code(value, pos)
        elif code in (0x2F, 0x2A) and in_calc():  # an operation within calc()
            node = ValueNode('word', pos - len(before), value[pos])
            node.source_end_index = pos + 1
            tokens.append(node)
            pos += 1
            code = _code(value, pos)
        elif code in (0x2F, 0x2C, 0x3A):  # dividers
            node = ValueNode('div', pos - len(before), value[pos])
            node.source_end_index = pos + 1
            node.before = before
            tokens.append(node)
            before = ''
            pos += 1
            code = _code(value, pos)
        elif code == 0x28:  # open parenthesis
            nxt = pos
            while True:
                nxt += 1
                code = _code(value, nxt)
                if not _le32(code):
                    break
            open_pos = pos
            node = ValueNode('function', pos - len(name), name)
            node.before = value[open_pos + 1 : nxt]
            pos = nxt
            if name == 'url' and code not in (0x27, 0x22):
                nxt -= 1
                while True:
                    escape = False
                    nxt = value.find(')', nxt + 1)
                    if nxt != -1:
                        escape_pos = nxt
                        while _code(value, escape_pos - 1) == 0x5C:
                            escape_pos -= 1
                            escape = not escape
                    else:
                        value += ')'
                        nxt = len(value) - 1
                        node.unclosed = True
                    if not escape:
                        break
                ws_pos = nxt
                while True:
                    ws_pos -= 1
                    code = _code(value, ws_pos)
                    if not _le32(code):
                        break
                if open_pos < ws_pos:
                    if pos != ws_pos + 1:
                        word = ValueNode('word', pos, value[pos : ws_pos + 1])
                        word.source_end_index = ws_pos + 1
                        node.nodes = [word]
                    else:
                        node.nodes = []
                    if node.unclosed and ws_pos + 1 != nxt:
                        node.after = ''
                        space = ValueNode('space', ws_pos + 1, value[ws_pos + 1 : nxt])
                        space.source_end_index = nxt
                        node.nodes.append(space)
                    else:
                        node.after = value[ws_pos + 1 : nxt]
                        node.source_end_index = nxt
                else:
                    node.after = ''
                    node.nodes = []
                pos = nxt + 1
                node.source_end_index = nxt if node.unclosed else pos
                code = _code(value, pos)
                tokens.append(node)
            else:
                balanced += 1
                node.after = ''
                node.source_end_index = pos + 1
                tokens.append(node)
                stack.append(node)
                tokens = node.nodes = []
                parent = node
            name = ''
        elif code == 0x29 and balanced:  # close parenthesis
            pos += 1
            code = _code(value, pos)
            assert parent is not None
            parent.after = after
            parent.source_end_index += len(after)
            after = ''
            balanced -= 1
            stack[-1].source_end_index = pos
            stack.pop()
            parent = stack[balanced]
            assert parent.nodes is not None
            tokens = parent.nodes
        else:  # words
            nxt = pos
            while True:
                if code == 0x5C:
                    nxt += 1
                nxt += 1
                code = _code(value, nxt)
                if not (nxt < max_) or (
                    _le32(code)
                    or code in (0x27, 0x22, 0x2C, 0x3A, 0x2F, 0x28)
                    or (code == 0x2A and in_calc())
                    or (code == 0x29 and balanced)
                ):
                    break
            token = value[pos:nxt]
            if code == 0x28:
                name = token
            elif token[:1] in ('u', 'U') and token[1:2] == '+' and RE_UNICODE_RANGE.match(token[2:]):
                node = ValueNode('unicode-range', pos, token)
                node.source_end_index = nxt
                tokens.append(node)
            else:
                node = ValueNode('word', pos, token)
                node.source_end_index = nxt
                tokens.append(node)
            pos = nxt
    for node in stack[:0:-1]:
        node.unclosed = True
        node.source_end_index = len(value)
    assert root.nodes is not None
    return root.nodes


def walk(nodes: Iterable[ValueNode], callback: Callable[[ValueNode], bool | None], bubble: bool = False) -> None:
    """walk(): call callback on every node, depth first; returning False skips a function's nodes."""
    for node in nodes:
        result = None if bubble else callback(node)
        if result is not False and node.type == 'function' and node.nodes is not None:
            walk(node.nodes, callback, bubble)
        if bubble:
            callback(node)


def iter_nodes(nodes: Iterable[ValueNode]) -> Iterable[ValueNode]:
    """Every node, depth first, as walk() visits them."""
    for node in nodes:
        yield node
        if node.type == 'function' and node.nodes is not None:
            yield from iter_nodes(node.nodes)


def stringify(nodes: ValueNode | Iterable[ValueNode]) -> str:
    """stringify(): the text of nodes."""
    if isinstance(nodes, ValueNode):
        return _stringify_node(nodes)
    return ''.join(_stringify_node(n) for n in nodes)


def _stringify_node(node: ValueNode) -> str:
    t = node.type
    if t in ('word', 'space'):
        return node.value
    if t == 'string':
        return node.quote + node.value + ('' if node.unclosed else node.quote)
    if t == 'comment':
        return '/*' + node.value + ('' if node.unclosed else '*/')
    if t == 'div':
        return node.before + node.value + node.after
    if node.nodes is not None:
        buf = stringify(node.nodes)
        if t != 'function':
            return buf
        return node.value + '(' + node.before + buf + node.after + ('' if node.unclosed else ')')
    return node.value


def _digit(code: int) -> bool:
    return 48 <= code <= 57


def _like_number(value: str) -> bool:
    code = _code(value, 0)
    if code in (0x2B, 0x2D):
        nxt = _code(value, 1)
        if _digit(nxt):
            return True
        return nxt == 0x2E and _digit(_code(value, 2))
    if code == 0x2E:
        return _digit(_code(value, 1))
    return _digit(code)


def unit(value: str) -> tuple[str, str] | None:
    """unit(): the number and the unit of a dimension, or None (false) if it is no number."""
    if not value or not _like_number(value):
        return None
    pos = 0
    length = len(value)
    if _code(value, 0) in (0x2B, 0x2D):
        pos += 1
    while pos < length and _digit(_code(value, pos)):
        pos += 1
    if _code(value, pos) == 0x2E and _digit(_code(value, pos + 1)):
        pos += 2
        while pos < length and _digit(_code(value, pos)):
            pos += 1
    code, nxt, nxt2 = _code(value, pos), _code(value, pos + 1), _code(value, pos + 2)
    if code in (0x65, 0x45) and (_digit(nxt) or (nxt in (0x2B, 0x2D) and _digit(nxt2))):
        pos += 3 if nxt in (0x2B, 0x2D) else 2
        while pos < length and _digit(_code(value, pos)):
            pos += 1
    return value[:pos], value[pos:]
