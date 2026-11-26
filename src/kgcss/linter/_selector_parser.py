# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A port of postcss-selector-parser 7.2.1, which stylelint's rules parse selectors with.

This is a faithful port of its tokenizer (tokenize.js), parser (parser.js),
node classes (selectors/*.js) and the escaping helpers (util/unesc.js,
util/cssesc.js), quirks included: the same input gives the same tree, prints
the same, and raises the same errors with the same messages (where the
JavaScript fails with a TypeError on some malformed input, so does this, as a
SelectorError named 'TypeError' with V8's message). Only the default parse
options are supported (lossless, no length limit, a nesting limit of 256).

Names are in Python style: sourceIndex is source_index, quoteMark is
quote_mark, walkPseudos() is walk_pseudos() and so on. Positions are Python
string indexes (code points), where the JavaScript counts UTF-16 code units.
Lines and columns are counted as the tokenizer counts them, quirks included.

Where JavaScript has both undefined and null or '', this port uses None for
undefined, except for the value of a Root or a Selector, which is '' (it is
undefined for those the parser makes). Raws and spaces are always present:
JavaScript leaves raws undefined on some nodes, which no code can observe
except by looking for the object itself.

Walk and each callbacks get only the node, and stop the walk by returning
False, as in the JavaScript. Containers have no len(), as an empty container
must not be false: use the length property, as in the JavaScript.
"""

import re
from collections.abc import Callable
from enum import Enum
from typing import ClassVar, Final, Literal, cast

MAX_NESTING_DEPTH: Final = 256

# Token types: a character code for the single character tokens
AMPERSAND: Final = 38
ASTERISK: Final = 42
COMMA: Final = 44
COLON: Final = 58
SEMICOLON: Final = 59
OPEN_PARENTHESIS: Final = 40
CLOSE_PARENTHESIS: Final = 41
OPEN_SQUARE: Final = 91
CLOSE_SQUARE: Final = 93
DOLLAR: Final = 36
TILDE: Final = 126
CARET: Final = 94
PLUS: Final = 43
EQUALS: Final = 61
PIPE: Final = 124
GREATER_THAN: Final = 62
SPACE: Final = 32
SINGLE_QUOTE: Final = 39
DOUBLE_QUOTE: Final = 34
SLASH: Final = 47
BANG: Final = 33
BACKSLASH: Final = 92
CR: Final = 13
FEED: Final = 12
NEWLINE: Final = 10
TAB: Final = 9
STR: Final = SINGLE_QUOTE
COMMENT: Final = -1
WORD: Final = -2
COMBINATOR: Final = -3

# Fields of a token
TYPE: Final = 0
START_LINE: Final = 1
START_COL: Final = 2
END_LINE: Final = 3
END_COL: Final = 4
START_POS: Final = 5
END_POS: Final = 6

type Token = tuple[int, int, int, int, int, int, int]

UNESCAPABLE: Final = frozenset((TAB, NEWLINE, CR, FEED))
WORD_DELIMITERS: Final = frozenset(
    {
        SPACE,
        TAB,
        NEWLINE,
        CR,
        FEED,
        AMPERSAND,
        ASTERISK,
        BANG,
        COMMA,
        COLON,
        SEMICOLON,
        OPEN_PARENTHESIS,
        CLOSE_PARENTHESIS,
        OPEN_SQUARE,
        CLOSE_SQUARE,
        SINGLE_QUOTE,
        DOUBLE_QUOTE,
        PLUS,
        PIPE,
        TILDE,
        GREATER_THAN,
        EQUALS,
        DOLLAR,
        CARET,
        SLASH,
    }
)
HEX: Final = frozenset(map(ord, '0123456789abcdefABCDEF'))
SPACE_CODES: Final = frozenset((SPACE, TAB, NEWLINE, CR, FEED))
COMBINATOR_CODES: Final = frozenset((PLUS, GREATER_THAN, TILDE, PIPE))
SINGLE_TOKENS: Final = frozenset(
    {
        ASTERISK,
        AMPERSAND,
        BANG,
        COMMA,
        EQUALS,
        DOLLAR,
        CARET,
        OPEN_SQUARE,
        CLOSE_SQUARE,
        COLON,
        SEMICOLON,
        OPEN_PARENTHESIS,
        CLOSE_PARENTHESIS,
    }
)
WHITESPACE_TOKENS: Final = frozenset((SPACE, CR, FEED, NEWLINE, TAB))
WHITESPACE_EQUIV_TOKENS: Final = WHITESPACE_TOKENS | {COMMENT}


class SelectorError(Exception):
    """An error postcss-selector-parser throws: name is 'Error', or 'TypeError' where the JavaScript fails.

    str() of it is what String() of the JavaScript error is ('Error: message'),
    which is what stylelint puts in its 'Cannot parse selector' warning.
    """

    def __init__(self, message: str, name: str = 'Error') -> None:
        super().__init__(message)
        self.name = name
        self.message = message

    def __str__(self) -> str:
        return f'{self.name}: {self.message}'


def _type_error(prop: str) -> SelectorError:
    """The TypeError V8 throws when reading a property of undefined."""
    return SelectorError(f"Cannot read properties of undefined (reading '{prop}')", 'TypeError')


def _js(value: object) -> str:
    """String(value) in JavaScript, for the values that occur here."""
    if value is None:
        return 'undefined'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    return str(value)


class _Undefined(Enum):
    UNDEFINED = 0


UNDEFINED: Final = _Undefined.UNDEFINED


# Escaping {{{


def _is_hex_char(c: str) -> bool:
    return c in '0123456789abcdefABCDEF' and c != ''


def unesc(text: str) -> str:
    """Remove CSS escapes, as util/unesc.js."""
    if '\\' not in text:
        return text
    ret: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '\\':
            # gobbleHex(str.slice(i + 1, i + 9))
            j = i + 1
            end = min(i + 7, n)
            while j < end and _is_hex_char(text[j]):
                j += 1
            hex_len = j - i - 1
            if hex_len:
                consumed = hex_len
                nxt = text[i + 1 + consumed] if i + 1 + consumed < n and consumed < 8 else ''
                if nxt in (' ', '\t', '\n', '\r', '\f'):
                    after = i + 2 + consumed
                    consumed += 2 if nxt == '\r' and after < n and after < i + 9 and text[after] == '\n' else 1
                cp = int(text[i + 1 : j], 16)
                ret.append('\ufffd' if 0xD800 <= cp <= 0xDFFF or cp == 0 or cp > 0x10FFFF else chr(cp))
                i += consumed + 1
                continue
            if i + 1 < n and text[i + 1] == '\\':
                ret.append('\\')
                i += 2
                continue
            if n == i + 1:
                ret.append(c)
            i += 1
            continue
        ret.append(c)
        i += 1
    return ''.join(ret)


def _single_escape(c: str, any_single: bool = False) -> bool:
    """/[ -,./:-@[\\]^`{-~]/ (or regexAnySingleEscape, which also has \\ and _ is not in either)."""
    o = ord(c)
    return 0x20 <= o <= 0x2C or c in './[]^`' or 0x3A <= o <= 0x40 or 0x7B <= o <= 0x7E or (any_single and c == '\\')


def _is_upper_hex(o: int) -> bool:
    return 0x30 <= o <= 0x39 or 0x41 <= o <= 0x46


def _is_hex_or_space(o: int) -> bool:
    return _is_upper_hex(o) or 0x61 <= o <= 0x66 or o == SPACE


def _remove_excessive_spaces(output: str) -> str:
    length = len(output)
    result: list[str] = []
    copied = 0
    i = 0

    def code(k: int) -> int:
        return ord(output[k]) if k < length else -1

    while i < length:
        if output[i] != '\\':
            i += 1
            continue
        run_end = i + 1
        while run_end < length and output[run_end] == '\\':
            run_end += 1
        hex_end = run_end
        while hex_end < length and hex_end - run_end < 7 and _is_upper_hex(ord(output[hex_end])):
            hex_end += 1
        hex_length = hex_end - run_end
        if 1 <= hex_length <= 6 and code(hex_end) == SPACE and not _is_hex_or_space(code(hex_end + 1)):
            if (run_end - i - 1) % 2 == 0:
                result.append(output[copied:hex_end])
                copied = hex_end + 1
            i = hex_end + 1
        else:
            i = run_end
    return output if copied == 0 else ''.join(result) + output[copied:]


def cssesc(text: str, *, is_identifier: bool = False, quotes: str = 'single', wrap: bool = False) -> str:
    """Escape text for CSS, as util/cssesc.js (cssesc 3 with a linear space removal)."""
    quote = '"' if quotes == 'double' else "'"
    first = text[:1]
    out: list[str] = []
    for c in text:
        o = ord(c)
        if o < 0x20 or o > 0x7E:
            out.append(f'\\{o:X} ')
        elif (
            c == '\\'
            or (not is_identifier and ((c == '"' and quote == c) or (c == "'" and quote == c)))
            or (is_identifier and _single_escape(c))
        ):
            out.append('\\' + c)
        else:
            out.append(c)
    output = ''.join(out)
    if is_identifier:
        if len(output) > 1 and output[0] == '-' and (output[1] == '-' or '0' <= output[1] <= '9'):
            output = '\\-' + output[1:]
        elif '0' <= first <= '9' and first:
            output = '\\3' + first + ' ' + output[1:]
    output = _remove_excessive_spaces(output)
    if not is_identifier and wrap:
        return quote + output + quote
    return output


def _cssesc_for_quote(text: str, quote_mark: str | _Undefined | None) -> str:
    """cssesc(text, CSSESC_QUOTE_OPTIONS[quoteMark])."""
    if quote_mark == "'":
        return cssesc(text, quotes='single', wrap=True)
    if quote_mark == '"':
        return cssesc(text, quotes='double', wrap=True)
    if quote_mark is None:
        return cssesc(text, is_identifier=True)
    return cssesc(text)  # CSSESC_QUOTE_OPTIONS[undefined] is undefined: the defaults


# }}}

# Node data {{{


class Position:
    """A line and column, both 1-based as postcss-selector-parser counts them."""

    __slots__ = ('column', 'line')

    def __init__(self, line: int, column: int) -> None:
        self.line = line
        self.column = column

    def __repr__(self) -> str:
        return f'Position({self.line}, {self.column})'


class Source:
    """The start and end of a node, either of which may be missing."""

    __slots__ = ('end', 'start')

    def __init__(self, start: Position | None = None, end: Position | None = None) -> None:
        self.start = start
        self.end = end

    def __repr__(self) -> str:
        return f'Source({self.start}, {self.end})'


class PartSpaces:
    """The spaces around a part of an attribute selector. None is a missing key."""

    __slots__ = ('after', 'before')

    def __init__(self, before: str | None = None, after: str | None = None) -> None:
        self.before = before
        self.after = after


class Spaces:
    """node.spaces: before and after, and for attributes the spaces around their parts."""

    __slots__ = ('after', 'attribute', 'before', 'insensitive', 'operator', 'value')

    def __init__(self, before: str = '', after: str = '') -> None:
        self.before = before
        self.after = after
        self.attribute: PartSpaces | None = None
        self.operator: PartSpaces | None = None
        self.value: PartSpaces | None = None
        self.insensitive: PartSpaces | None = None


class RawSpaces:
    """node.raws.spaces: the spaces with comments. None is a missing key."""

    __slots__ = ('after', 'attribute', 'before', 'insensitive', 'namespace', 'operator', 'value')

    def __init__(self, before: str | None = None, after: str | None = None) -> None:
        self.before = before
        self.after = after
        self.attribute: PartSpaces | None = None
        self.operator: PartSpaces | None = None
        self.value: PartSpaces | None = None
        self.insensitive: PartSpaces | None = None
        self.namespace: PartSpaces | None = None


class Raws:
    """node.raws: the source text of properties that differ from their value. None is a missing key."""

    __slots__ = ('attribute', 'insensitive_flag', 'namespace', 'operator', 'sensitive_flag', 'spaces', 'value')

    def __init__(self, value: str | None = None) -> None:
        self.value = value
        self.namespace: str | None = None
        self.attribute: str | None = None
        self.operator: str | None = None
        self.insensitive_flag: str | None = None
        self.sensitive_flag: str | None = None
        self.spaces: RawSpaces | None = None


def _copy_part(p: PartSpaces | None) -> PartSpaces | None:
    return None if p is None else PartSpaces(p.before, p.after)


def _copy_source(s: Source | None) -> Source | None:
    if s is None:
        return None
    return Source(
        None if s.start is None else Position(s.start.line, s.start.column),
        None if s.end is None else Position(s.end.line, s.end.column),
    )


def _copy_spaces(s: Spaces) -> Spaces:
    ans = Spaces(s.before, s.after)
    if s.attribute is not None or s.operator is not None or s.value is not None or s.insensitive is not None:
        ans.attribute = _copy_part(s.attribute)
        ans.operator = _copy_part(s.operator)
        ans.value = _copy_part(s.value)
        ans.insensitive = _copy_part(s.insensitive)
    return ans


def _copy_raws(r: Raws) -> Raws:
    ans = Raws(r.value)
    ans.namespace = r.namespace
    ans.attribute = r.attribute
    ans.operator = r.operator
    ans.insensitive_flag = r.insensitive_flag
    ans.sensitive_flag = r.sensitive_flag
    if r.spaces is not None:
        s = ans.spaces = RawSpaces(r.spaces.before, r.spaces.after)
        for k in ('attribute', 'operator', 'value', 'insensitive', 'namespace'):
            setattr(s, k, _copy_part(getattr(r.spaces, k)))
    return ans


def _clone_depth(node: Node) -> int:
    """How deep below a node cloneNode() goes for its own data (not its children)."""
    depth = 1  # its primitive properties
    s = node.source
    if s is not None:
        depth = 3 if s.start is not None or s.end is not None else 1
    if node.raws.spaces is not None:
        rs = node.raws.spaces
        parts = (rs.attribute, rs.operator, rs.value, rs.insensitive, rs.namespace)
        depth = max(depth, 4 if any(p is not None and (p.before, p.after) != (None, None) for p in parts) else 3)
    sp = node.spaces
    parts2 = (sp.attribute, sp.operator, sp.value, sp.insensitive)
    depth = max(depth, 3 if any(p is not None and (p.before, p.after) != (None, None) for p in parts2) else 2)
    return depth


# }}}

# Nodes {{{


class Node:
    """The base of the selector nodes."""

    __slots__ = ('_value', 'parent', 'raws', 'source', 'source_index', 'spaces')
    type: ClassVar[str] = ''

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        self.parent: Container | None = None
        self._value = value
        self.source = source
        self.source_index = source_index
        self.spaces = Spaces() if spaces is None else spaces
        self.raws = Raws() if raws is None else raws

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, v: str) -> None:
        self._set_value(v)

    def _set_value(self, v: str) -> None:
        self._value = v

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.to_string()!r})'

    def __str__(self) -> str:
        return self.to_string()

    def remove(self) -> Node:
        if self.parent is not None:
            self.parent.remove_child(self)
        self.parent = None
        return self

    def replace_with(self, *nodes: Node) -> Node:
        if self.parent is not None:
            for node in nodes:
                self.parent.insert_before(self, node)
            self.remove()
        return self

    def next(self) -> Node | None:
        """The next sibling. As in the JavaScript, it is an error to call this on a node without parent."""
        if self.parent is None:
            raise _type_error('at')
        return self.parent.at(self.parent.index(self) + 1)

    def prev(self) -> Node | None:
        """The previous sibling. As in the JavaScript, it is an error to call this on a node without parent."""
        if self.parent is None:
            raise _type_error('at')
        return self.parent.at(self.parent.index(self) - 1)

    def clone(self, **overrides: object) -> Node:
        """A deep copy without parent, with the overrides (such as nodes=[]) assigned to it."""
        cloned = self._clone(None, 0)
        for name, value in overrides.items():
            setattr(cloned, name, value)
        return cloned

    def _check_clone_depth(self, depth: int) -> None:
        # _clone_depth() is at most 4: it only needs computing near the limit
        if depth + 4 > MAX_NESTING_DEPTH and depth + _clone_depth(self) > MAX_NESTING_DEPTH:
            raise SelectorError(f'Cannot clone selector: nesting depth exceeds the maximum of {MAX_NESTING_DEPTH}.')

    def _clone(self, parent: Container | None, depth: int) -> Node:
        self._check_clone_depth(depth)
        cloned = object.__new__(type(self))
        self._copy_into(cloned)
        cloned.parent = parent
        return cloned

    def _copy_into(self, cloned: Node) -> None:
        cloned._value = self._value
        cloned.source = _copy_source(self.source)
        cloned.source_index = self.source_index
        cloned.spaces = _copy_spaces(self.spaces)
        cloned.raws = _copy_raws(self.raws)

    def append_to_property_and_escape(self, name: str, value: str, value_escaped: str) -> None:
        original_value = getattr(self, name)
        original_escaped = getattr(self.raws, name, None)
        setattr(self, name, _js(original_value) + value)  # may trigger a setter that updates raws
        if original_escaped or value_escaped != value:
            setattr(self.raws, name, (original_escaped or _js(original_value)) + value_escaped)
        else:
            setattr(self.raws, name, None)

    def set_property_and_escape(self, name: str, value: object, value_escaped: str) -> None:
        setattr(self, name, value)
        setattr(self.raws, name, value_escaped)

    def set_property_without_escape(self, name: str, value: object) -> None:
        setattr(self, name, value)
        setattr(self.raws, name, None)

    def is_at_position(self, line: int, column: int) -> bool | None:
        s = self.source
        if s is not None and s.start is not None and s.end is not None:
            if s.start.line > line or s.end.line < line:
                return False
            if s.start.line == line and s.start.column > column:
                return False
            return not (s.end.line == line and s.end.column < column)
        return None

    def stringify_property(self, name: str) -> object:
        """(raws[name] || this[name]): the escaped value of a property when there is one."""
        return getattr(self.raws, name, None) or getattr(self, name)

    @property
    def raw_space_before(self) -> str:
        raw = self.raws.spaces.before if self.raws.spaces is not None else None
        if raw is None:
            raw = self.spaces.before
        return raw or ''

    @raw_space_before.setter
    def raw_space_before(self, raw: str | None) -> None:
        if self.raws.spaces is None:
            self.raws.spaces = RawSpaces()
        self.raws.spaces.before = raw

    @property
    def raw_space_after(self) -> str:
        raw = self.raws.spaces.after if self.raws.spaces is not None else None
        if raw is None:
            raw = self.spaces.after
        return raw or ''

    @raw_space_after.setter
    def raw_space_after(self, raw: str | None) -> None:
        if self.raws.spaces is None:
            self.raws.spaces = RawSpaces()
        self.raws.spaces.after = raw

    def value_to_string(self) -> str:
        # stringify_property('value'), without looking the names up
        raw = self.raws.value
        if raw:
            return raw
        value = self.value
        return value if type(value) is str else _js(value)

    def to_string(self) -> str:
        """What the node prints as, as toString() in the JavaScript."""
        return self.raw_space_before + self.value_to_string() + self.raw_space_after

    def _stringify(self, depth: int) -> str:
        return self.to_string()


class Container(Node):
    """A node with children: Root, Selector and Pseudo."""

    __slots__ = ('indexes', 'last_each', 'nodes')

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
        nodes: list[Node] | None = None,
    ) -> None:
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
        # As in the JavaScript, the nodes given keep their parent
        self.nodes: list[Node] = [] if nodes is None else nodes
        self.last_each = 0
        self.indexes: dict[int, int] = {}

    def _clone(self, parent: Container | None, depth: int) -> Node:
        self._check_clone_depth(depth)
        cloned = cast(Container, object.__new__(type(self)))
        self._copy_into(cloned)
        cloned.parent = parent
        cloned.last_each = self.last_each
        cloned.indexes = dict(self.indexes)
        cloned.nodes = [child._clone(cloned, depth + 1) for child in self.nodes]
        return cloned

    def clone(self, **overrides: object) -> Container:
        return cast(Container, super().clone(**overrides))

    def append(self, node: Node) -> Container:
        node.parent = self
        self.nodes.append(node)
        return self

    def prepend(self, node: Node) -> Container:
        node.parent = self
        self.nodes.insert(0, node)
        for k in self.indexes:
            self.indexes[k] += 1
        return self

    def at(self, index: int) -> Node | None:
        """nodes[index], None when out of range (negative indexes do not count from the end)."""
        return self.nodes[index] if 0 <= index < len(self.nodes) else None

    def index(self, child: Node | int) -> int:
        if isinstance(child, int):
            return child
        for i, node in enumerate(self.nodes):
            if node is child:
                return i
        return -1

    @property
    def first(self) -> Node | None:
        return self.at(0)

    @property
    def last(self) -> Node | None:
        return self.at(len(self.nodes) - 1)

    @property
    def length(self) -> int:
        return len(self.nodes)

    def remove_child(self, child: Node | int) -> Container:
        idx = self.index(child)
        node = self.at(idx)
        if node is None:
            raise SelectorError("Cannot set properties of undefined (setting 'parent')", 'TypeError')
        node.parent = None
        del self.nodes[idx]
        for k, v in self.indexes.items():
            if v >= idx:
                self.indexes[k] = v - 1
        return self

    def remove_all(self) -> Container:
        for node in self.nodes:
            node.parent = None
        self.nodes = []
        return self

    def empty(self) -> Container:
        return self.remove_all()

    def _insert(self, at: int, old_index: int, after: bool, new_nodes: tuple[Node, ...]) -> None:
        for n in new_nodes:
            n.parent = self
        if at < 0:
            at = max(len(self.nodes) + at, 0)
        self.nodes[at:at] = new_nodes
        for k, v in self.indexes.items():
            if (old_index < v) if after else (v >= old_index):
                self.indexes[k] = v + len(new_nodes)

    def insert_after(self, old_node: Node | int, new_node: Node, *more: Node) -> Container:
        old_index = self.index(old_node)
        self._insert(old_index + 1, old_index, True, (new_node, *more))
        return self

    def insert_before(self, old_node: Node | int, new_node: Node, *more: Node) -> Container:
        old_index = self.index(old_node)
        self._insert(old_index, old_index, False, (new_node, *more))
        return self

    def at_position(self, line: int, col: int) -> Node | None:
        if not self.is_at_position(line, col):
            return None
        found: list[Node] = []

        def check(node: Node) -> bool | None:
            if isinstance(node, Container):
                child = node.at_position(line, col)
                if child is not None:
                    found.append(child)
                    return False
            elif node.is_at_position(line, col):
                found.append(node)
                return False
            return None

        self.each(check)
        return found[0] if found else self

    def _infer_end_position(self) -> None:
        last = self.last
        if last is not None and last.source is not None and last.source.end is not None:
            if self.source is None:
                self.source = Source()
            if self.source.end is None:
                self.source.end = Position(0, 0)
            self.source.end.line = last.source.end.line
            self.source.end.column = last.source.end.column

    def each(self, callback: Callable[[Node], object]) -> Literal[False] | None:
        """Call callback with each child, safely against changes of the children, until it returns False."""
        self.last_each += 1
        id_ = self.last_each
        self.indexes[id_] = 0
        if not self.nodes:
            return None
        result: object = None
        while self.indexes[id_] < len(self.nodes):
            index = self.indexes[id_]
            result = callback(self.nodes[index])
            if result is False:
                break
            self.indexes[id_] += 1
        del self.indexes[id_]
        return False if result is False else None

    def walk(self, callback: Callable[[Node], object], depth: int = 0) -> Literal[False] | None:
        """Call callback with each descendant, depth first, until it returns False."""
        if depth > MAX_NESTING_DEPTH:
            raise SelectorError(f'Cannot walk selector: nesting depth exceeds the maximum of {MAX_NESTING_DEPTH}.')
        self.last_each += 1
        id_ = self.last_each
        self.indexes[id_] = 0
        if not self.nodes:
            return None
        result: object = None
        while self.indexes[id_] < len(self.nodes):
            node = self.nodes[self.indexes[id_]]
            result = callback(node)
            if result is not False and isinstance(node, Container) and node.nodes:
                result = node.walk(callback, depth + 1)
            if result is False:
                break
            self.indexes[id_] += 1
        del self.indexes[id_]
        return False if result is False else None

    def _walk_type[T: Node](self, cls: type[T], callback: Callable[[T], object]) -> Literal[False] | None:
        return self.walk(lambda node: callback(node) if isinstance(node, cls) else None)

    def walk_attributes(self, callback: Callable[[Attribute], object]) -> Literal[False] | None:
        return self._walk_type(Attribute, callback)

    def walk_classes(self, callback: Callable[[ClassName], object]) -> Literal[False] | None:
        return self._walk_type(ClassName, callback)

    def walk_combinators(self, callback: Callable[[Combinator], object]) -> Literal[False] | None:
        return self._walk_type(Combinator, callback)

    def walk_comments(self, callback: Callable[[Comment], object]) -> Literal[False] | None:
        return self._walk_type(Comment, callback)

    def walk_ids(self, callback: Callable[[ID], object]) -> Literal[False] | None:
        return self._walk_type(ID, callback)

    def walk_nesting(self, callback: Callable[[Nesting], object]) -> Literal[False] | None:
        return self._walk_type(Nesting, callback)

    def walk_pseudos(self, callback: Callable[[Pseudo], object]) -> Literal[False] | None:
        return self._walk_type(Pseudo, callback)

    def walk_tags(self, callback: Callable[[Tag], object]) -> Literal[False] | None:
        return self._walk_type(Tag, callback)

    def walk_universals(self, callback: Callable[[Universal], object]) -> Literal[False] | None:
        return self._walk_type(Universal, callback)

    def split(self, callback: Callable[[Node], object]) -> list[list[Node]]:
        """The children in groups, each ending after a child for which callback is true, as split()."""
        ans: list[list[Node]] = []
        current: list[Node] = []
        n = len(self.nodes)
        for i, node in enumerate(list(self.nodes)):
            split = callback(node)
            current.append(node)
            if split:
                ans.append(current)
                current = []
            elif i == n - 1:
                ans.append(current)
        return ans

    def to_string(self) -> str:
        return self._stringify(0)

    def _stringify(self, depth: int) -> str:
        # A list, not a generator, so that each level of nesting takes one Python frame
        return self.raw_space_before + ''.join([c._stringify(depth) for c in self.nodes]) + self.raw_space_after


class Root(Container):
    """The selector list."""

    __slots__ = ('trailing_comma',)
    type: ClassVar[str] = 'root'

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
        nodes: list[Node] | None = None,
    ) -> None:
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws, nodes=nodes)
        self.trailing_comma = False

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        cast(Root, cloned).trailing_comma = self.trailing_comma

    def _stringify(self, depth: int) -> str:
        s = ','.join([c._stringify(depth) for c in self.nodes])
        if self.trailing_comma:
            s += ','
        return self.raw_space_before + s + self.raw_space_after


class Selector(Container):
    """A complex selector: an item of a selector list."""

    __slots__ = ()
    type: ClassVar[str] = 'selector'


class Pseudo(Container):
    """A pseudo-class or pseudo-element: value is its name with its colons, nodes the selectors of its arguments."""

    __slots__ = ()
    type: ClassVar[str] = 'pseudo'

    def _stringify(self, depth: int) -> str:
        if depth >= MAX_NESTING_DEPTH:
            raise SelectorError(f'Cannot serialize selector: nesting depth exceeds the maximum of {MAX_NESTING_DEPTH}.')
        params = '(' + ','.join([c._stringify(depth + 1) for c in self.nodes]) + ')' if self.nodes else ''
        value = self.stringify_property('value')
        return self.raw_space_before + ('' if value is None else str(value)) + params + self.raw_space_after


class ClassName(Node):
    """A class selector: value is unescaped, raws.value has the source text when it differs."""

    __slots__ = ('_constructed', 'namespace')
    type: ClassVar[str] = 'class'

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        self._constructed = False
        # The parser sets this plain property when a namespace prefixes a class: ns|.a
        self.namespace: str | bool | None = None
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
        self._constructed = True

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        cast(ClassName, cloned)._constructed = True
        cast(ClassName, cloned).namespace = self.namespace

    def _set_value(self, v: str) -> None:
        if self._constructed:
            escaped = cssesc(v, is_identifier=True)
            self.raws.value = escaped if escaped != v else None
        self._value = v

    def value_to_string(self) -> str:
        return '.' + super().value_to_string()


class ID(Node):
    """An id selector: value is unescaped, raws.value has the source text when it differs."""

    __slots__ = ('namespace',)
    type: ClassVar[str] = 'id'

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        # The parser sets this plain property when a namespace prefixes an id: ns|#a
        self.namespace: str | bool | None = None
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        cast(ID, cloned).namespace = self.namespace

    def value_to_string(self) -> str:
        return '#' + super().value_to_string()


class Combinator(Node):
    """A combinator: value is ' ' for a descendant combinator, or such as '>', '+', '~', '||' or '/deep/'."""

    __slots__ = ('_constructed',)
    type: ClassVar[str] = 'combinator'

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        self._constructed = False
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
        self._constructed = True

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        cast(Combinator, cloned)._constructed = True

    def _set_value(self, v: str) -> None:
        if self._constructed:
            self.raws.value = None
        self._value = v


class Comment(Node):
    __slots__ = ()
    type: ClassVar[str] = 'comment'


class String(Node):
    """A string, or text the parser keeps as it is (such as parenthesized text without a pseudo before it)."""

    __slots__ = ()
    type: ClassVar[str] = 'string'


class Nesting(Node):
    __slots__ = ()
    type: ClassVar[str] = 'nesting'

    def __init__(
        self,
        *,
        value: str = '&',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
        self._value = '&'


class Namespace(Node):
    """The base of the nodes that can have a namespace: Tag, Universal and Attribute.

    namespace is None for none, True for an empty namespace (|a), or a
    string ('*', '&' or a name, unescaped).
    """

    __slots__ = ('_namespace',)

    def __init__(
        self,
        *,
        value: str = '',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        self._namespace: str | bool | None = None
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        cast(Namespace, cloned)._namespace = self._namespace

    def _has_raws(self) -> bool:
        return True

    @property
    def namespace(self) -> str | bool | None:
        return self._namespace

    @namespace.setter
    def namespace(self, namespace: str | bool | None) -> None:
        if namespace is True or namespace == '*' or namespace == '&':
            self._namespace = namespace
            if self._has_raws():
                self.raws.namespace = None
            return
        escaped = cssesc(_js(namespace), is_identifier=True)
        self._namespace = namespace
        if escaped != namespace:
            self._ensure_raws()
            self.raws.namespace = escaped
        elif self._has_raws():
            self.raws.namespace = None

    def _ensure_raws(self) -> None:
        pass

    @property
    def ns(self) -> str | bool | None:
        return self._namespace

    @ns.setter
    def ns(self, namespace: str | bool | None) -> None:
        self.namespace = namespace

    @property
    def namespace_string(self) -> str:
        if self.namespace:
            ns = self.stringify_property('namespace')
            return '' if ns is True else _js(ns)
        return ''

    def qualified_name(self, value: str) -> str:
        return f'{self.namespace_string}|{value}' if self.namespace else value

    def value_to_string(self) -> str:
        return self.qualified_name(super().value_to_string())


class Tag(Namespace):
    """A type selector, value unescaped. The parser also makes tags of words it does not otherwise know."""

    __slots__ = ()
    type: ClassVar[str] = 'tag'


class Universal(Namespace):
    __slots__ = ()
    type: ClassVar[str] = 'universal'

    def __init__(
        self,
        *,
        value: str = '*',
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
    ) -> None:
        super().__init__(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
        self._value = '*'


WRAPPED_IN_QUOTES: Final = re.compile(r'^([\'"])(.*)\1$', re.DOTALL)


def unescape_value(value: str) -> tuple[bool, str, str | None]:
    """unescapeValue() of attribute.js: (deprecated_usage, unescaped, quote_mark)."""
    quote_mark: str | None = None
    unescaped = value
    if m := WRAPPED_IN_QUOTES.match(unescaped):
        quote_mark = m.group(1)
        unescaped = m.group(2)
    unescaped = unesc(unescaped)
    return unescaped != value, unescaped, quote_mark


class Attribute(Namespace):
    """An attribute selector.

    attribute and value are unescaped (raws.attribute and raws.value have the
    source text when it differs). value is None without an operator, and
    quote_mark is the quote of the value or None (also before a value is set).
    """

    __slots__ = (
        '_attr_value',
        '_attribute',
        '_constructed',
        '_insensitive',
        '_quote_mark',
        '_raws_pending',
        '_sensitive',
        'operator',
    )
    type: ClassVar[str] = 'attribute'
    NO_QUOTE: ClassVar[None] = None
    SINGLE_QUOTE: ClassVar[str] = "'"
    DOUBLE_QUOTE: ClassVar[str] = '"'

    def __init__(
        self,
        *,
        source: Source | None = None,
        source_index: int | None = None,
        spaces: Spaces | None = None,
        raws: Raws | None = None,
        namespace: str | bool | None = None,
        attribute: str | None = None,
        operator: str | None = None,
        value: str | None = None,
        quote_mark: str | _Undefined | None = UNDEFINED,
        insensitive: bool | None = None,
        sensitive: bool | None = None,
    ) -> None:
        opts: list[tuple[str, object]] = [('source', source), ('source_index', source_index)]
        if spaces is not None:
            opts.append(('spaces', spaces))
        if raws is not None:
            opts.append(('raws', raws))
        for name, v in (
            ('namespace', namespace),
            ('attribute', attribute),
            ('operator', operator),
            ('value', value),
            ('insensitive', insensitive),
            ('sensitive', sensitive),
        ):
            if v is not None:
                opts.append((name, v))
        if quote_mark is not UNDEFINED:
            opts.append(('quote_mark', quote_mark))
        self._construct(opts)

    @classmethod
    def _from_opts(cls, opts: list[tuple[str, object]]) -> Attribute:
        self = object.__new__(cls)
        self._construct(opts)
        return self

    def _construct(self, opts: list[tuple[str, object]]) -> None:
        # handleDeprecatedContructorOpts()
        names = [k for k, _ in opts]
        if 'quote_mark' not in names and 'value' in names:
            value = cast(str, dict(opts)['value'])
            _, unescaped, quote_mark = unescape_value(value)
            if 'raws' not in names:
                opts.append(('raws', Raws()))
            raws = cast(Raws, dict(opts)['raws'])
            if raws.value is None:
                raws.value = value
            opts = [(k, unescaped if k == 'value' else v) for k, v in opts]
            opts.append(('quote_mark', quote_mark))
        # Node(): Object.assign(this, opts), with the setters of a node not yet constructed
        self._constructed = False
        self._raws_pending = True
        self.raws = Raws()
        self.parent = None
        self._value = ''
        self._attr_value: str | None = None
        self.source = None
        self.source_index = None
        self.spaces = Spaces()
        self._namespace = None
        self._attribute: str | None = None
        self.operator: str | None = None
        self._quote_mark: str | _Undefined | None = UNDEFINED
        self._insensitive: bool | None = None
        self._sensitive: bool | None = None
        for name, v in opts:
            if name == 'raws':
                self.raws = cast(Raws, v)
                self._raws_pending = False
            elif name == 'value':
                self._set_attr_value(cast(str | None, v))
            else:
                setattr(self, name, v)
        self._raws_pending = False
        self._constructed = True

    def _copy_into(self, cloned: Node) -> None:
        super()._copy_into(cloned)
        c = cast(Attribute, cloned)
        c._attr_value = self._attr_value
        c._attribute = self._attribute
        c.operator = self.operator
        c._quote_mark = self._quote_mark
        c._insensitive = self._insensitive
        c._sensitive = self._sensitive
        c._raws_pending = False
        c._constructed = True

    def _has_raws(self) -> bool:
        return not self._raws_pending

    def _ensure_raws(self) -> None:
        self._raws_pending = False

    def get_quoted_value(
        self,
        *,
        quote_mark: str | _Undefined | None = UNDEFINED,
        prefer_current_quote_mark: bool = False,
        smart: bool = False,
    ) -> str:
        qm = self._determine_quote_mark(quote_mark, prefer_current_quote_mark, smart)
        return _cssesc_for_quote(_js(self._attr_value), qm)

    def _determine_quote_mark(
        self, quote_mark: str | _Undefined | None, prefer_current: bool, smart: bool
    ) -> str | _Undefined | None:
        if smart:
            return self.smart_quote_mark(quote_mark=quote_mark, prefer_current_quote_mark=prefer_current)
        return self.preferred_quote_mark(quote_mark=quote_mark, prefer_current_quote_mark=prefer_current)

    def set_value(
        self,
        value: str,
        *,
        quote_mark: str | _Undefined | None = UNDEFINED,
        prefer_current_quote_mark: bool = False,
        smart: bool = False,
    ) -> None:
        """Set the unescaped value, with the quoting the options choose."""
        self._attr_value = value
        self._quote_mark = self._determine_quote_mark(quote_mark, prefer_current_quote_mark, smart)
        self._sync_raw_value()

    def smart_quote_mark(
        self, *, quote_mark: str | _Undefined | None = UNDEFINED, prefer_current_quote_mark: bool = False
    ) -> str | _Undefined | None:
        v = _js(self.value)
        singles, doubles = v.count("'"), v.count('"')
        if singles + doubles == 0:
            escaped = cssesc(v, is_identifier=True)
            if escaped == v:
                return None
            pref = self.preferred_quote_mark(quote_mark=quote_mark, prefer_current_quote_mark=prefer_current_quote_mark)
            if pref is None:
                quote = self.quote_mark or (quote_mark if isinstance(quote_mark, str) else None) or '"'
                if len(_cssesc_for_quote(v, quote)) < len(escaped):
                    return quote
            return pref
        if doubles == singles:
            return self.preferred_quote_mark(quote_mark=quote_mark, prefer_current_quote_mark=prefer_current_quote_mark)
        return '"' if doubles < singles else "'"

    def preferred_quote_mark(
        self, *, quote_mark: str | _Undefined | None = UNDEFINED, prefer_current_quote_mark: bool = False
    ) -> str | _Undefined | None:
        qm = self._quote_mark if prefer_current_quote_mark else quote_mark
        if qm is UNDEFINED:
            qm = quote_mark if prefer_current_quote_mark else self._quote_mark
        if qm is UNDEFINED:
            qm = '"'
        return qm

    @property
    def quoted(self) -> bool:
        return self.quote_mark in ("'", '"')

    @property
    def quote_mark(self) -> str | None:
        """The quote of the value, or None if it is not quoted (or its quoting is unknown)."""
        return None if self._quote_mark is UNDEFINED else self._quote_mark

    @quote_mark.setter
    def quote_mark(self, quote_mark: str | None) -> None:
        if not self._constructed:
            self._quote_mark = quote_mark
            return
        if self._quote_mark != quote_mark:
            self._quote_mark = quote_mark
            self._sync_raw_value()

    def _sync_raw_value(self) -> None:
        raw = _cssesc_for_quote(_js(self._attr_value), self._quote_mark)
        self.raws.value = None if raw == self._attr_value else raw

    @property
    def qualified_attribute(self) -> str:
        return self.qualified_name(self.raws.attribute or _js(self.attribute))

    @property
    def insensitive_flag(self) -> str:
        return 'i' if self.insensitive else ''

    @property
    def sensitive_flag(self) -> str:
        return 's' if self.sensitive else ''

    @property
    def value(self) -> str:
        """The unescaped value, '' when there is none: value_or_none tells these apart."""
        return self._attr_value or ''

    @value.setter
    def value(self, v: str) -> None:
        self._set_attr_value(v)

    @property
    def value_or_none(self) -> str | None:
        """The unescaped value, None when there is none (when the JavaScript has undefined)."""
        return self._attr_value

    def _set_attr_value(self, v: str | None) -> None:
        if self._constructed:
            _, unescaped, quote_mark = unescape_value(_js(v))
            if unescaped == self._attr_value and quote_mark == self._quote_mark:
                return
            self._attr_value = unescaped
            self._quote_mark = quote_mark
            self._sync_raw_value()
        else:
            self._attr_value = v

    @property
    def insensitive(self) -> bool | None:
        return self._insensitive

    @insensitive.setter
    def insensitive(self, insensitive: bool | None) -> None:
        if insensitive:
            self.sensitive = False
        elif self._has_raws() and self.raws.insensitive_flag in ('I', 'i'):
            self.raws.insensitive_flag = None
        self._insensitive = insensitive

    @property
    def sensitive(self) -> bool | None:
        return self._sensitive

    @sensitive.setter
    def sensitive(self, sensitive: bool | None) -> None:
        if sensitive:
            self.insensitive = False
        elif self._has_raws() and self.raws.sensitive_flag in ('S', 's'):
            self.raws.sensitive_flag = None
        self._sensitive = sensitive

    @property
    def attribute(self) -> str | None:
        return self._attribute

    @attribute.setter
    def attribute(self, name: str | None) -> None:
        if self._constructed:
            escaped = cssesc(_js(name), is_identifier=True)
            self.raws.attribute = escaped if escaped != name else None
        self._attribute = name

    def _spaces_for(self, name: str) -> tuple[str, str]:
        before, after = '', ''
        for part in (getattr(self.spaces, name, None), getattr(self.raws.spaces, name, None)):
            if part is not None:
                if part.before is not None:
                    before = part.before
                if part.after is not None:
                    after = part.after
        return before, after

    def _string_for(self, name: str, space_name: str | None = None) -> str:
        before, after = self._spaces_for(space_name or name)
        return before + _js(self.stringify_property(name)) + after

    def offset_of(self, name: str) -> int:
        """The offset of a part (ns, namespace, attribute, attributeNS, operator, value, insensitive, sensitive)."""
        count = 1
        attr_before, attr_after = self._spaces_for('attribute')
        count += len(attr_before)
        if name in ('namespace', 'ns'):
            return count if self.namespace else -1
        if name == 'attributeNS':
            return count
        count += len(self.namespace_string)
        if self.namespace:
            count += 1
        if name == 'attribute':
            return count
        count += len(_js(self.stringify_property('attribute')))
        count += len(attr_after)
        op_before, op_after = self._spaces_for('operator')
        count += len(op_before)
        operator = self.raws.operator or self.operator
        if name == 'operator':
            return count if operator else -1
        if operator is None:
            raise _type_error('length')
        count += len(operator)
        count += len(op_after)
        value_before, value_after = self._spaces_for('value')
        count += len(value_before)
        value = self.raws.value or self._attr_value
        if name == 'value':
            return count if value else -1
        if value is None:
            raise _type_error('length')
        count += len(value)
        count += len(value_after)
        count += len(self._spaces_for('insensitive')[0])
        if name == 'insensitive':
            return count if self.insensitive else -1
        if name == 'sensitive':
            return count if self.sensitive else -1
        return -1

    def to_string(self) -> str:
        parts = [self.raw_space_before, '[', self._string_for('qualified_attribute', 'attribute')]
        if self.operator and self._attr_value is not None:
            parts.append(self._string_for('operator'))
            parts.append(self._string_for('value'))
            flag = _js(self.stringify_property('sensitive_flag' if self.sensitive else 'insensitive_flag'))
            before, after = self._spaces_for('insensitive')
            if flag and not self.quoted and not before and not (self.spaces.value and self.spaces.value.after):
                before = ' '
            parts.append(before + flag + after)
        parts.append(']')
        parts.append(self.raw_space_after)
        return ''.join(parts)


# }}}

# Guards {{{


def is_pseudo_element(node: object) -> bool:
    """isPseudoElement(): a pseudo starting with :: or one of the four level 2 pseudo-elements."""
    if not isinstance(node, Pseudo) or not node.value:
        return False
    v = node.value
    return v.startswith('::') or v.lower() in (':before', ':after', ':first-letter', ':first-line')


def is_pseudo_class(node: object) -> bool:
    return isinstance(node, Pseudo) and not is_pseudo_element(node)


# }}}

# Tokenizer {{{


def _consume_escape(css: str, start: int) -> int:
    n = len(css)
    nxt = start
    code = ord(css[nxt + 1]) if nxt + 1 < n else -1
    if code in UNESCAPABLE:
        pass
    elif code in HEX:
        hex_digits = 0
        while True:
            nxt += 1
            hex_digits += 1
            code = ord(css[nxt + 1]) if nxt + 1 < n else -1
            if not (code in HEX and hex_digits < 6):
                break
        if hex_digits < 6 and code == SPACE:
            nxt += 1
    else:
        nxt += 1
    return nxt


def _consume_word(css: str, start: int) -> int:
    n = len(css)
    nxt = start
    while True:
        code = ord(css[nxt]) if nxt < n else -1
        if code in WORD_DELIMITERS:
            return nxt - 1
        if code == BACKSLASH:
            nxt = _consume_escape(css, nxt) + 1
        else:
            nxt += 1
        if nxt >= n:
            return nxt - 1


def tokenize(css: str) -> list[Token]:
    """The tokens of a selector, as tokenize.js: (type, start line, start column, end line, end column, start, end)."""
    tokens: list[Token] = []
    length = len(css)
    offset = -1
    line = 1
    start = 0
    next_offset: int | None = None

    def code_at(i: int) -> int:
        return ord(css[i]) if 0 <= i < length else -1

    while start < length:
        code = ord(css[start])
        if code == NEWLINE:
            offset = start
            line += 1
        if code in SPACE_CODES:
            nxt = start
            while True:
                nxt += 1
                code = code_at(nxt)
                if code == NEWLINE:
                    offset = nxt
                    line += 1
                if code not in SPACE_CODES:
                    break
            token_type = SPACE
            end_line = line
            end_column = nxt - offset - 1
            end = nxt
        elif code in COMBINATOR_CODES:
            nxt = start
            while True:
                nxt += 1
                code = code_at(nxt)
                if code not in COMBINATOR_CODES:
                    break
            token_type = COMBINATOR
            end_line = line
            end_column = start - offset
            end = nxt
        elif code in SINGLE_TOKENS:
            token_type = code
            end_line = line
            end_column = start - offset
            end = start + 1
        elif code in (SINGLE_QUOTE, DOUBLE_QUOTE):
            quote = "'" if code == SINGLE_QUOTE else '"'
            nxt = start
            while True:
                escaped = False
                nxt = css.find(quote, nxt + 1)
                if nxt == -1:
                    raise SelectorError('Unclosed quote')
                escape_pos = nxt
                while code_at(escape_pos - 1) == BACKSLASH:
                    escape_pos -= 1
                    escaped = not escaped
                if not escaped:
                    break
            token_type = STR
            end_line = line
            end_column = start - offset
            end = nxt + 1
        else:
            if code == SLASH and code_at(start + 1) == ASTERISK:
                nxt = css.find('*/', start + 2) + 1
                if nxt == 0:
                    raise SelectorError('Unclosed comment')
                content = css[start : nxt + 1]
                newlines = content.count('\n')
                if newlines:
                    next_line = line + newlines
                    next_offset = nxt - (len(content) - content.rfind('\n') - 1)
                else:
                    next_line = line
                    next_offset = offset
                token_type = COMMENT
                line = next_line
                end_line = next_line
                end_column = nxt - next_offset
            elif code == SLASH:
                nxt = start
                token_type = code
                end_line = line
                end_column = start - offset
            else:
                nxt = _consume_word(css, start)
                token_type = WORD
                end_line = line
                end_column = nxt - offset
            end = nxt + 1
        tokens.append((token_type, line, start - offset, end_line, end_column, start, end))
        if next_offset:  # 0 is false in JavaScript too
            offset = next_offset
            next_offset = None
        start = end
    return tokens


# }}}

# Parser {{{


def _token_start(token: Token | None) -> Position:
    if token is None:
        raise _type_error('1')
    return Position(token[START_LINE], token[START_COL])


def _token_end(token: Token) -> Position:
    return Position(token[END_LINE], token[END_COL])


def _get_source(start_line: int, start_column: int, end_line: int, end_column: int) -> Source:
    return Source(Position(start_line, start_column), Position(end_line, end_column))


def _token_source(token: Token) -> Source:
    return _get_source(token[START_LINE], token[START_COL], token[END_LINE], token[END_COL])


def _token_source_span(start: Token | None, end: Token) -> Source | None:
    if start is None:
        return None
    return _get_source(start[START_LINE], start[START_COL], end[END_LINE], end[END_COL])


def _indexes_of(text: str, item: str) -> list[int]:
    ans = []
    i = text.find(item)
    while i != -1:
        ans.append(i)
        i = text.find(item, i + 1)
    return ans


def _is_escaped(word: str, index: int) -> bool:
    start = index
    while start > 0 and word[start - 1] == '\\':
        start -= 1
    return (index - start) % 2 == 1


def _inside_interpolation(text: str | None) -> bool:
    if not text:
        return False
    open_ = text.rfind('{')
    if open_ < 1 or text.find('}', open_) != -1:
        return False
    return text[open_ - 1] in '#@'


RE_KEYFRAMES_PERCENT: Final = re.compile(r'[0-9]+\.[0-9]+%')


class _AttrOpts:
    """The plain object the parser builds an Attribute from, remembering the order its keys were added in."""

    __slots__ = (
        'attribute',
        'insensitive',
        'namespace',
        'operator',
        'order',
        'quote_mark',
        'raws',
        'sensitive',
        'source',
        'source_index',
        'spaces',
        'value',
    )

    def __init__(self, source: Source, source_index: int) -> None:
        object.__setattr__(self, 'order', [])
        self.source = source
        self.source_index = source_index
        self.raws: Raws | None = None
        self.spaces: Spaces | None = None
        self.operator: str | None = None
        self.namespace: str | bool | None = None
        self.attribute: str | None = None
        self.value: str | None = None
        self.quote_mark: str | _Undefined | None = UNDEFINED
        self.insensitive: bool | None = None
        self.sensitive: bool | None = None
        self.order.clear()
        self.order.extend(('source', 'source_index'))

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self.order:
            self.order.append(name)
        object.__setattr__(self, name, value)

    def ensure_raws(self) -> Raws:
        if self.raws is None:
            self.raws = Raws()
        return self.raws

    def ensure_spaces(self, part: str) -> PartSpaces:
        if self.spaces is None:
            self.spaces = Spaces()
        p = getattr(self.spaces, part)
        if p is None:
            p = PartSpaces()
            setattr(self.spaces, part, p)
        return p

    def ensure_raw_spaces(self, part: str) -> PartSpaces:
        raws = self.ensure_raws()
        if raws.spaces is None:
            raws.spaces = RawSpaces()
        p = getattr(raws.spaces, part)
        if p is None:
            p = PartSpaces()
            setattr(raws.spaces, part, p)
        return p

    def get(self, name: str) -> object:
        return getattr(self, name)

    def raw(self, name: str) -> str | None:
        """getProp(node, 'raws', name)."""
        return None if self.raws is None else getattr(self.raws, name) or None

    def raw_part_after(self, part: str) -> str | None:
        """getProp(node, 'raws', 'spaces', part, 'after')."""
        if self.raws is None or self.raws.spaces is None:
            return None
        p = getattr(self.raws.spaces, part, None)
        return None if p is None else p.after or None

    def part_after(self, part: str) -> str | None:
        """getProp(node, 'spaces', part, 'after')."""
        if self.spaces is None:
            return None
        p = getattr(self.spaces, part, None)
        return None if p is None else p.after or None

    def to_list(self) -> list[tuple[str, object]]:
        ans: list[tuple[str, object]] = []
        for name in self.order:
            v = getattr(self, name)
            if name == 'raws' and v is None:
                continue
            ans.append((name, v))
        return ans


class _Parser:
    __slots__ = ('css', 'current', 'max_nesting_depth', 'nesting_depth', 'position', 'root', 'spaces', 'tokens')

    def __init__(self, css: str) -> None:
        self.css = css
        self.position = 0
        self.nesting_depth = 0
        self.max_nesting_depth = MAX_NESTING_DEPTH
        self.spaces = ''
        self.tokens = tokenize(css)
        self.root = Root(source=_token_source_span(self.tokens[0], self.tokens[-1]) if self.tokens else None)
        selector = Selector(source=Source(Position(1, 1)), source_index=0)
        self.root.append(selector)
        self.current: Container = selector
        self.loop()

    # Helpers

    @property
    def curr_token(self) -> Token | None:
        return self.tokens[self.position] if 0 <= self.position < len(self.tokens) else None

    @property
    def next_token(self) -> Token | None:
        p = self.position + 1
        return self.tokens[p] if 0 <= p < len(self.tokens) else None

    @property
    def prev_token(self) -> Token | None:
        p = self.position - 1
        return self.tokens[p] if 0 <= p < len(self.tokens) else None

    @property
    def tok(self) -> Token:
        """currToken, where the JavaScript reads it knowing it is there."""
        t = self.curr_token
        if t is None:
            raise _type_error('0')
        return t

    def content(self, token: Token | None = None) -> str:
        if token is None:
            token = self.tok
        return self.css[token[START_POS] : token[END_POS]]

    def error(self, message: str) -> SelectorError:
        return SelectorError(message)

    def expected(self, description: str | list[str], found: str | None = None) -> SelectorError:
        if isinstance(description, list):
            description = ', '.join(description[:-1]) + ' or ' + description[-1]
        an = 'an' if description[:1] in ('a', 'e', 'i', 'o', 'u') else 'a'
        if not found:
            return self.error(f'Expected {an} {description}.')
        return self.error(f'Expected {an} {description}, found "{found}" instead.')

    def unexpected(self) -> SelectorError:
        return self.error(f"Unexpected '{self.content()}'. Escaping special characters with \\ may help.")

    def unexpected_pipe(self) -> SelectorError:
        return self.error("Unexpected '|'.")

    def new_node(self, node: Node, namespace: str | bool | None = None) -> None:
        if namespace:
            if isinstance(namespace, str) and namespace.strip(' ') == '' and namespace:
                self.spaces = self.spaces + namespace
                namespace = True
            assert isinstance(node, Namespace | ClassName | ID)
            node.namespace = namespace
            _unescape_namespace(node)
        if self.spaces:
            node.spaces.before = self.spaces
            self.spaces = ''
        self.current.append(node)

    def locate_next_meaningful_token(self, start: int) -> int:
        pos = start
        while pos < len(self.tokens):
            if self.tokens[pos][TYPE] in WHITESPACE_EQUIV_TOKENS:
                pos += 1
                continue
            return pos
        return -1

    # Parsing

    def loop(self) -> Root:
        while self.position < len(self.tokens):
            self.parse(True)
        self.flush_pending_spaces()
        self.current._infer_end_position()
        return self.root

    def parse(self, throw_on_parenthesis: bool = False) -> None:
        t = self.tok[TYPE]
        if t == SPACE:
            self.space()
        elif t == COMMENT:
            self.comment()
        elif t == OPEN_PARENTHESIS:
            self.parentheses()
        elif t == CLOSE_PARENTHESIS:
            if throw_on_parenthesis:
                raise self.expected('opening parenthesis')
        elif t == OPEN_SQUARE:
            self.attribute()
        elif t in (DOLLAR, CARET, EQUALS, WORD):
            self.word()
        elif t == COLON:
            self.pseudo()
        elif t == COMMA:
            self.comma()
        elif t == ASTERISK:
            self.universal()
        elif t == AMPERSAND:
            self.nesting()
        elif t in (SLASH, COMBINATOR):
            self.combinator()
        elif t == STR:
            self.string()
        elif t == CLOSE_SQUARE:
            raise self.expected('opening square bracket')
        elif t == SEMICOLON:
            raise self.error('Expected a backslash preceding the semicolon.')
        else:
            raise self.unexpected()

    def namespace_separator_follows(self, attr: list[Token], pos: int) -> bool:
        n = len(attr)
        nxt = pos
        while nxt < n and attr[nxt][TYPE] == SPACE:
            nxt += 1
        if nxt >= n or self.content(attr[nxt]) != '|':
            return False
        nxt += 1
        if nxt < n and attr[nxt][TYPE] == EQUALS:
            return False
        while nxt < n and attr[nxt][TYPE] == SPACE:
            nxt += 1
        return nxt < n

    def attribute(self) -> None:
        attr: list[Token] = []
        starting = self.tok
        self.position += 1
        while self.position < len(self.tokens) and self.tok[TYPE] != CLOSE_SQUARE:
            attr.append(self.tok)
            self.position += 1
        if self.curr_token is None:
            raise self.expected('closing square bracket')
        close = self.tok
        length = len(attr)
        node = _AttrOpts(
            _get_source(starting[START_LINE], starting[START_COL], close[END_LINE], close[END_COL]), starting[START_POS]
        )
        if length == 1 and attr[0][TYPE] != WORD:
            raise self.expected('attribute')
        pos = 0
        space_before = ''
        comment_before = ''
        last_added: str | None = None
        space_after_meaningful = False
        namespace_spaces: str | None = None
        while pos < length:
            token = attr[pos]
            content = self.content(token)
            nxt = attr[pos + 1] if pos + 1 < length else None
            ttype = token[TYPE]
            if ttype == SPACE:
                space_after_meaningful = True
                if namespace_spaces is not None:
                    namespace_spaces += content
                elif last_added == 'namespace':
                    raws = node.ensure_raws()
                    raws.namespace = _js(raws.namespace or node.namespace) + content
                elif last_added:
                    part = node.ensure_spaces(last_added)
                    part.after = (part.after or '') + content
                    existing = node.raw_part_after(last_added)
                    if existing:
                        node.ensure_raw_spaces(last_added).after = existing + content
                else:
                    space_before += content
                    comment_before += content
            elif ttype == ASTERISK:
                if nxt is not None and nxt[TYPE] == EQUALS:
                    node.operator = content
                    last_added = 'operator'
                elif (not node.namespace or (last_added == 'namespace' and not space_after_meaningful)) and nxt:
                    node.namespace = _js(node.namespace or '') + content
                    if node.raw('namespace'):
                        raws = node.ensure_raws()
                        raws.namespace = _js(raws.namespace) + content
                    if space_before:
                        node.ensure_spaces('attribute').before = space_before
                        space_before = ''
                    if comment_before:
                        node.ensure_raw_spaces('attribute').before = comment_before
                        comment_before = ''
                    last_added = 'namespace'
                space_after_meaningful = False
            elif ttype in (DOLLAR, CARET):
                handled = False
                if ttype == DOLLAR:
                    if last_added == 'value':
                        old_raw = node.raw('value')
                        node.value = _js(node.value) + '$'
                        if old_raw:
                            node.ensure_raws().value = old_raw + '$'
                        handled = True
                    elif (
                        last_added == 'attribute'
                        and _inside_interpolation(node.attribute)
                        and not (nxt is not None and nxt[TYPE] == EQUALS)
                    ):
                        old_raw = node.raw('attribute')
                        node.attribute = (node.attribute or '') + '$'
                        if old_raw:
                            node.ensure_raws().attribute = old_raw + '$'
                        last_added = 'attribute'
                        space_after_meaningful = False
                        handled = True
                if not handled:
                    if nxt is not None and nxt[TYPE] == EQUALS:
                        node.operator = content
                        last_added = 'operator'
                    space_after_meaningful = False
            elif ttype == COMBINATOR:
                if content == '~' and nxt is not None and nxt[TYPE] == EQUALS:
                    node.operator = content
                    last_added = 'operator'
                if content != '|':
                    space_after_meaningful = False
                else:
                    if nxt is not None and nxt[TYPE] == EQUALS:
                        node.operator = content
                        last_added = 'operator'
                    elif not node.operator:
                        if not node.namespace and not node.attribute:
                            node.namespace = True
                        namespace_spaces = ''
                    space_after_meaningful = False
            elif ttype == WORD:
                if self.namespace_separator_follows(attr, pos + 1) and not node.operator and not node.namespace:
                    node.namespace = content
                    last_added = 'namespace'
                elif not node.attribute or (last_added == 'attribute' and not space_after_meaningful):
                    if space_before:
                        node.ensure_spaces('attribute').before = space_before
                        space_before = ''
                    if comment_before:
                        node.ensure_raw_spaces('attribute').before = comment_before
                        comment_before = ''
                    if namespace_spaces:
                        raws = node.ensure_raws()
                        raws.attribute = namespace_spaces + (raws.attribute or node.attribute or '')
                    namespace_spaces = None
                    node.attribute = (node.attribute or '') + content
                    if node.raw('attribute'):
                        raws = node.ensure_raws()
                        raws.attribute = _js(raws.attribute) + content
                    last_added = 'attribute'
                elif node.value is None or (last_added == 'value' and not (space_after_meaningful or node.quote_mark)):
                    unescaped = unesc(content)
                    old_raw = node.raw('value') or ''
                    old_value = node.value or ''
                    node.value = old_value + unescaped
                    node.quote_mark = None
                    if unescaped != content or old_raw:
                        node.ensure_raws().value = (old_raw or old_value) + content
                    last_added = 'value'
                else:
                    insensitive = content in ('i', 'I')
                    sensitive = content in ('s', 'S')
                    if node.value is not None and (node.quote_mark or space_after_meaningful):
                        node.insensitive = insensitive
                        node.sensitive = sensitive
                        if sensitive:
                            if content == 'S':
                                node.ensure_raws().sensitive_flag = content
                        elif not insensitive or content == 'I':
                            node.ensure_raws().insensitive_flag = content
                        last_added = 'insensitive'
                        if space_before:
                            node.ensure_spaces('insensitive').before = space_before
                            space_before = ''
                        if comment_before:
                            node.ensure_raw_spaces('insensitive').before = comment_before
                            comment_before = ''
                    elif node.value is not None:
                        last_added = 'value'
                        node.value += content
                        if node.raws is None:
                            raise _type_error('value')
                        if node.raws.value:
                            node.raws.value += content
                space_after_meaningful = False
            elif ttype == STR:
                if not node.attribute or not node.operator:
                    raise self.error('Expected an attribute followed by an operator preceding the string.')
                _, unescaped, quote_mark = unescape_value(content)
                node.value = unescaped
                node.quote_mark = quote_mark
                last_added = 'value'
                node.ensure_raws().value = content
                space_after_meaningful = False
            elif ttype == EQUALS:
                if not node.attribute:
                    raise self.expected('attribute', content)
                if node.value:
                    raise self.error('Unexpected "=" found; an operator was already defined.')
                node.operator = node.operator + content if node.operator else content
                last_added = 'operator'
                space_after_meaningful = False
            elif ttype == COMMENT:
                if last_added:
                    if (
                        space_after_meaningful
                        or (nxt is not None and nxt[TYPE] == SPACE)
                        or last_added == 'insensitive'
                    ):
                        last_comment = node.part_after(last_added) or ''
                        raw_last = node.raw_part_after(last_added) or last_comment
                        node.ensure_raw_spaces(last_added).after = raw_last + content
                    else:
                        last_value = node.get(last_added) or ''
                        raw_last_value = node.raw(last_added) or last_value
                        raws = node.ensure_raws()
                        setattr(raws, last_added, _js(raw_last_value) + content)
                else:
                    comment_before += content
            else:
                raise self.error(f'Unexpected "{content}" found.')
            pos += 1
        if not node.attribute:
            raise self.expected('attribute')
        # unescapeProp(node, 'attribute'), unescapeProp(node, 'namespace')
        for prop in ('attribute', 'namespace'):
            v = node.get(prop)
            if isinstance(v, str) and '\\' in v:
                raws = node.ensure_raws()
                setattr(node, prop, unesc(v))
                if getattr(raws, prop) is None:
                    setattr(raws, prop, v)
        self.new_node(Attribute._from_opts(node.to_list()))
        self.position += 1

    def parse_whitespace_equivalent_tokens(self, stop: int) -> list[Node]:
        if stop < 0:
            stop = len(self.tokens)
        start = self.position
        nodes: list[Node] = []
        space = ''
        last_comment: Comment | None = None
        while True:
            tok = self.tok
            if tok[TYPE] in WHITESPACE_TOKENS:
                space += self.content()
            elif tok[TYPE] == COMMENT:
                spaces = Spaces()
                if space:
                    spaces.before = space
                    space = ''
                last_comment = Comment(
                    value=self.content(), source=_token_source(tok), source_index=tok[START_POS], spaces=spaces
                )
                nodes.append(last_comment)
            self.position += 1
            if self.position >= stop:
                break
        if space:
            if last_comment is not None:
                last_comment.spaces.after = space
            else:
                first, last = self.tokens[start], self.tokens[self.position - 1]
                nodes.append(
                    String(
                        value='',
                        source=_get_source(first[START_LINE], first[START_COL], last[END_LINE], last[END_COL]),
                        source_index=first[START_POS],
                        spaces=Spaces(space, ''),
                    )
                )
        return nodes

    @staticmethod
    def convert_whitespace_nodes_to_space(nodes: list[Node]) -> tuple[str, str | None]:
        space = raw_space = ''
        for n in nodes:
            before = n.spaces.before
            space += before + n.spaces.after
            raw_space += before + n.value + n.raw_space_after
        return space, (None if raw_space == space else raw_space)

    def is_named_combinator(self) -> bool:
        p = self.position
        t = self.tokens
        return p + 2 < len(t) and t[p][TYPE] == SLASH and t[p + 1][TYPE] == WORD and t[p + 2][TYPE] == SLASH

    def named_combinator(self) -> Combinator:
        tok = self.tok
        name_raw = self.content(self.tokens[self.position + 1])
        name = unesc(name_raw).lower()
        raws = Raws()
        if name != name_raw:
            raws.value = f'/{name_raw}/'
        end = self.tokens[self.position + 2]
        node = Combinator(
            value=f'/{name}/',
            source=_get_source(tok[START_LINE], tok[START_COL], end[END_LINE], end[END_COL]),
            source_index=tok[START_POS],
            raws=raws,
        )
        self.position += 3
        return node

    def combinator(self) -> None:
        if self.content() == '|':
            return self.namespace()
        next_sig = self.locate_next_meaningful_token(self.position)
        if next_sig < 0 or self.tokens[next_sig][TYPE] in (COMMA, CLOSE_PARENTHESIS):
            nodes = self.parse_whitespace_equivalent_tokens(next_sig)
            if nodes:
                last = self.current.last
                if last is not None:
                    space, raw_space = self.convert_whitespace_nodes_to_space(nodes)
                    if raw_space is not None:
                        last.raw_space_after = last.raw_space_after + raw_space
                    last.spaces.after += space
                else:
                    for n in nodes:
                        self.new_node(n)
            return None
        first = self.tok
        ws_nodes: list[Node] | None = None
        if next_sig > self.position:
            ws_nodes = self.parse_whitespace_equivalent_tokens(next_sig)
        node: Combinator | None = None
        if self.is_named_combinator():
            node = self.named_combinator()
        elif self.tok[TYPE] == COMBINATOR:
            tok = self.tok
            node = Combinator(value=self.content(), source=_token_source(tok), source_index=tok[START_POS])
            self.position += 1
        elif self.tok[TYPE] in WHITESPACE_TOKENS:
            pass
        elif ws_nodes is None:
            raise self.unexpected()
        if node is not None:
            if ws_nodes is not None:
                space, raw_space = self.convert_whitespace_nodes_to_space(ws_nodes)
                node.spaces.before = space
                node.raw_space_before = raw_space
        else:
            if ws_nodes is None:
                raise _type_error('forEach')
            space, raw = self.convert_whitespace_nodes_to_space(ws_nodes)
            raw_space = raw or space
            spaces = Spaces()
            raws = Raws()
            raws.spaces = RawSpaces()
            if space.endswith(' ') and raw_space.endswith(' '):
                spaces.before = space[:-1]
                raws.spaces.before = raw_space[:-1]
            elif space[:1] == ' ' and raw_space[:1] == ' ':
                spaces.after = space[1:]
                raws.spaces.after = raw_space[1:]
            else:
                raws.value = raw_space
            node = Combinator(
                value=' ',
                source=_token_source_span(first, self.tokens[self.position - 1]),
                source_index=first[START_POS],
                spaces=spaces,
                raws=raws,
            )
        if self.curr_token is not None and self.tok[TYPE] == SPACE:
            node.spaces.after = self.content()
            self.position += 1
        self.new_node(node)
        return None

    def flush_pending_spaces(self) -> None:
        if not self.spaces:
            return
        last = self.current.last
        if last is not None:
            last.spaces.after += self.spaces
            self.spaces = ''
            return
        token = self.curr_token or self.prev_token
        self.new_node(
            String(
                value='',
                source=_token_source(token) if token else None,
                source_index=token[START_POS] if token else 0,
            )
        )

    def comma(self) -> None:
        self.flush_pending_spaces()
        if self.position == len(self.tokens) - 1:
            self.root.trailing_comma = True
            self.position += 1
            return
        self.current._infer_end_position()
        nxt = self.tokens[self.position + 1]
        selector = Selector(source=Source(_token_start(nxt)), source_index=nxt[START_POS])
        parent = self.current.parent
        assert parent is not None
        parent.append(selector)
        self.current = selector
        self.position += 1

    def comment(self) -> None:
        tok = self.tok
        self.new_node(Comment(value=self.content(), source=_token_source(tok), source_index=tok[START_POS]))
        self.position += 1

    def namespace(self) -> None:
        prev = self.prev_token
        before: str | bool = (
            self.content(prev) if prev is not None and prev[TYPE] in (WORD, ASTERISK, AMPERSAND) else True
        )
        nxt = self.next_token
        if nxt is None:
            raise self.unexpected_pipe()
        if nxt[TYPE] == WORD:
            self.position += 1
            return self.word(before)
        if nxt[TYPE] == ASTERISK:
            self.position += 1
            return self.universal(before)
        raise self.unexpected_pipe()

    def nesting(self) -> None:
        nxt = self.next_token
        if nxt is not None and self.content(nxt) == '|':
            self.position += 1
            return
        tok = self.tok
        self.new_node(Nesting(value=self.content(), source=_token_source(tok), source_index=tok[START_POS]))
        self.position += 1

    def parentheses(self) -> None:
        last = self.current.last
        unbalanced = 1
        self.position += 1
        if isinstance(last, Pseudo):
            first = self.curr_token
            selector = Selector(source=Source(_token_start(first)), source_index=cast(Token, first)[START_POS])
            cache = self.current
            last.append(selector)
            self.current = selector
            self.nesting_depth += 1
            try:
                if self.nesting_depth > self.max_nesting_depth:
                    raise self.error(
                        f'Cannot parse selector: nesting depth exceeds the maximum of {self.max_nesting_depth}.'
                    )
                while self.position < len(self.tokens) and unbalanced:
                    tok = self.tok
                    if tok[TYPE] == OPEN_PARENTHESIS:
                        unbalanced += 1
                    if tok[TYPE] == CLOSE_PARENTHESIS:
                        unbalanced -= 1
                    if unbalanced:
                        self.parse()
                    else:
                        self.flush_pending_spaces()
                        assert self.current.source is not None
                        self.current.source.end = _token_end(tok)
                        parent = self.current.parent
                        assert parent is not None
                        assert parent.source is not None
                        parent.source.end = _token_end(tok)
                        self.position += 1
            finally:
                self.nesting_depth -= 1
            self.current = cache
        else:
            paren_start = self.curr_token  # the token after the parenthesis, as in the JavaScript
            paren_value = '('
            paren_end: Token | None = None
            while True:
                while self.position < len(self.tokens) and unbalanced:
                    tok = self.tok
                    if tok[TYPE] == OPEN_PARENTHESIS:
                        unbalanced += 1
                    if tok[TYPE] == CLOSE_PARENTHESIS:
                        unbalanced -= 1
                    paren_end = tok
                    paren_value += self.content(tok)
                    self.position += 1
                if unbalanced or last is None or self.curr_token is None or self.tok[TYPE] != OPEN_PARENTHESIS:
                    break
                paren_value += '('
                unbalanced = 1
                self.position += 1
            if last is not None:
                last.append_to_property_and_escape('value', paren_value, paren_value)
            else:
                if paren_start is None:
                    raise _type_error('1')
                assert paren_end is not None
                self.new_node(
                    String(
                        value=paren_value,
                        source=_get_source(
                            paren_start[START_LINE], paren_start[START_COL], paren_end[END_LINE], paren_end[END_COL]
                        ),
                        source_index=paren_start[START_POS],
                    )
                )
        if unbalanced:
            raise self.expected('closing parenthesis')

    def pseudo(self) -> None:
        pseudo_str = ''
        starting = self.tok
        while self.curr_token is not None and self.tok[TYPE] == COLON:
            pseudo_str += self.content()
            self.position += 1
        if self.curr_token is None:
            raise self.expected(['pseudo-class', 'pseudo-element'])
        if self.tok[TYPE] != WORD:
            raise self.expected(['pseudo-class', 'pseudo-element'])

        def first(value: str, length: int) -> None:
            self.new_node(
                Pseudo(
                    value=pseudo_str + value,
                    source=_token_source_span(starting, self.tok),
                    source_index=starting[START_POS],
                )
            )
            nxt = self.next_token
            if length > 1 and nxt is not None and nxt[TYPE] == OPEN_PARENTHESIS:
                raise self.error('Misplaced parenthesis.')

        self.split_word(None, first)

    def current_has_only_comments(self) -> bool:
        return all(isinstance(n, Comment) for n in self.current.nodes)

    def space(self) -> None:
        content = self.content()
        prev = self.prev_token
        if (
            self.position == 0
            or (prev is not None and prev[TYPE] in (COMMA, OPEN_PARENTHESIS))
            or self.current_has_only_comments()
        ):
            self.spaces = content
            self.position += 1
        elif self.position == len(self.tokens) - 1 or cast(Token, self.next_token)[TYPE] in (
            COMMA,
            CLOSE_PARENTHESIS,
        ):
            last = self.current.last
            assert last is not None
            last.spaces.after = content
            self.position += 1
        else:
            self.combinator()

    def string(self) -> None:
        tok = self.tok
        self.new_node(String(value=self.content(), source=_token_source(tok), source_index=tok[START_POS]))
        self.position += 1

    def universal(self, namespace: str | bool | None = None) -> None:
        nxt = self.next_token
        if nxt is not None and self.content(nxt) == '|':
            self.position += 1
            return self.namespace()
        tok = self.tok
        self.new_node(
            Universal(value=self.content(), source=_token_source(tok), source_index=tok[START_POS]), namespace
        )
        self.position += 1
        return None

    def split_word(
        self, namespace: str | bool | None, first_callback: Callable[[str, int], None] | None = None
    ) -> None:
        nxt = self.next_token
        word = self.content()
        while nxt is not None and nxt[TYPE] in (DOLLAR, CARET, EQUALS, WORD):
            self.position += 1
            current = self.content()
            word += current
            if current.rfind('\\') == len(current) - 1:
                n2 = self.next_token
                if n2 is not None and n2[TYPE] == SPACE:
                    word += self.content(n2)
                    self.position += 1
            nxt = self.next_token
        is_keyframes_percent = RE_KEYFRAMES_PERCENT.fullmatch(word) is not None
        has_class = [] if is_keyframes_percent else [i for i in _indexes_of(word, '.') if not _is_escaped(word, i)]
        has_id = [i for i in _indexes_of(word, '#') if not _is_escaped(word, i)]
        interpolations = set(_indexes_of(word, '#{'))
        if interpolations:
            has_id = [i for i in has_id if i not in interpolations]
        class_indexes = set(has_class)
        id_indexes = set(has_id)
        indices = sorted(dict.fromkeys([0, *has_class, *has_id]))
        for i, ind in enumerate(indices):
            index = (indices[i + 1] if i + 1 < len(indices) else 0) or len(word)
            value = word[ind:index]
            if i == 0 and first_callback is not None:
                first_callback(value, len(indices))
                continue
            current_tok = self.tok
            source_index = current_tok[START_POS] + indices[i]
            source = _get_source(current_tok[1], current_tok[2] + ind, current_tok[3], current_tok[2] + (index - 1))
            node: Node
            if ind in class_indexes:
                v = value[1:]
                if '\\' in v:
                    node = ClassName(value=unesc(v), source=source, source_index=source_index, raws=Raws(v))
                else:
                    node = ClassName(value=v, source=source, source_index=source_index)
            elif ind in id_indexes:
                v = value[1:]
                if '\\' in v:
                    node = ID(value=unesc(v), source=source, source_index=source_index, raws=Raws(v))
                else:
                    node = ID(value=v, source=source, source_index=source_index)
            elif '\\' in value:
                node = Tag(value=unesc(value), source=source, source_index=source_index, raws=Raws(value))
            else:
                node = Tag(value=value, source=source, source_index=source_index)
            self.new_node(node, namespace)
            namespace = None
        self.position += 1

    def word(self, namespace: str | bool | None = None) -> None:
        nxt = self.next_token
        if nxt is not None and self.content(nxt) == '|':
            self.position += 1
            return self.namespace()
        return self.split_word(namespace)


def _unescape_namespace(node: Namespace | ClassName | ID) -> None:
    """unescapeProp(node, 'namespace') on a constructed node."""
    value = node.namespace
    if isinstance(value, str) and '\\' in value:
        node.namespace = unesc(value)
        if node.raws.namespace is None:
            node.raws.namespace = value


def parse_selector(selector: str) -> Root:
    """The tree of a selector list, as parser().astSync(selector). Raises SelectorError."""
    return _Parser(selector).root


# }}}
