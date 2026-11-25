# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Declaration blocks and properties (requirements §4)."""

from collections.abc import Iterable, Iterator, MutableSequence
from typing import TYPE_CHECKING, overload

from kgcss.cssom.exceptions import InvalidValueError
from kgcss.cssom.state import ErrorHandler, SheetState
from kgcss.escaping import serialize_identifier
from kgcss.syntax import Block, Declaration, Function, Token, parse_block_contents, parse_one_declaration
from kgcss.tokenizer import ParseError, TokenType, tokenize

if TYPE_CHECKING:
    from kgcss.cssom.rules import Comment, Rule

CSS_WHITESPACE = ' \t\n\r\f'
CLOSERS = frozenset((TokenType.RIGHT_CURLY, TokenType.RIGHT_PAREN, TokenType.RIGHT_SQUARE))


def normalize_name(name: str) -> str:
    """Property names are ASCII case-insensitive, except custom properties (--x), which are case-sensitive."""
    if not name:
        raise ValueError('A property name cannot be empty')
    if name.startswith('--') or not name.isascii():
        return name
    return name.lower()


def normalize_priority(priority: str) -> str:
    p = priority.strip(CSS_WHITESPACE).removeprefix('!').strip(CSS_WHITESPACE)
    if not p:
        return ''
    if p.isascii() and p.lower() == 'important':
        return 'important'
    raise ValueError(f'Invalid priority: {priority!r}, it must be "important" or ""')


def check_value(name: str, value: str, validate: bool = False) -> str:
    """Check that value can be the value of a declaration of name, returning it without surrounding whitespace.

    The structure is always checked, so that a value can never corrupt the
    text around it when serialized: no top level ; or closing bracket,
    nothing unclosed, and no !important (use the priority). With validate,
    the value must also be non-empty and free of bad strings, bad URLs and any
    other parse error.
    """
    text = f'{serialize_identifier(name)}:{value}'
    decl = parse_one_declaration(text)
    if not isinstance(decl, Declaration) or decl.end < len(text.rstrip(CSS_WHITESPACE)):
        raise InvalidValueError(f'Invalid value for {name}: {value!r}')
    if decl.important:
        raise InvalidValueError(f'The value for {name} must not contain !important, use the priority: {value!r}')
    for item in decl.value:
        if (isinstance(item, Block | Function) and not item.closed) or (
            isinstance(item, Token) and item.type in CLOSERS
        ):
            raise InvalidValueError(f'Unbalanced brackets in the value for {name}: {value!r}')
    for tok in decl.stream:
        if not tok.closed:
            raise InvalidValueError(f'Unclosed string, url() or comment in the value for {name}: {value!r}')
    if validate:
        if not decl.value.text:
            raise InvalidValueError(f'Empty value for {name}')
        if decl.stream.errors:
            raise InvalidValueError(f'Invalid value for {name}: {value!r}: {decl.stream.errors[0].message}')
    return decl.value.text


class Property:
    """A declaration: a name, a value and a priority.

    Properties are hashable and compare by identity. The value is text; it
    is checked when set, see check_value().
    """

    __slots__ = ('_literal_priority', '_name', '_node', '_priority', '_value', 'parent')

    def __init__(self, name: str, value: str, priority: str = '', *, validate: bool = False) -> None:
        self._name = normalize_name(name)
        self._value: str | None = check_value(self._name, value, validate)
        self._priority = normalize_priority(priority)
        self._literal_priority: str | None = None
        self._node: Declaration | None = None
        self.parent: DeclarationBlock | None = None

    @classmethod
    def _from_node(cls, node: Declaration) -> Property:
        """A property for a parsed declaration. The value is read from the node on first access."""
        p = cls.__new__(cls)
        name = node.name  # never empty for parsed declarations
        p._name = name if name.startswith('--') or not name.isascii() else name.lower()
        p._value = None
        p._priority = 'important' if node.important else ''
        p._literal_priority = None
        p._node = node
        p.parent = None
        return p

    def _changed(self) -> None:
        if self.parent is not None:
            self.parent._changed()

    @property
    def name(self) -> str:
        """The name, lowercased unless it is a custom property. Assigning renames the property in place."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        self._name = normalize_name(name)
        self._changed()

    @property
    def value(self) -> str:
        """The value as text, without surrounding whitespace. Assigning checks the new value, see check_value()."""
        value = self._value
        if value is None:
            assert self._node is not None
            self._value = value = self._node.value_text
        return value

    @value.setter
    def value(self, value: str) -> None:
        self._value = check_value(self._name, value)
        self._literal_priority = None
        self._changed()

    @property
    def priority(self) -> str:
        """'important' or ''."""
        return self._priority

    @priority.setter
    def priority(self, priority: str) -> None:
        self._priority = normalize_priority(priority)
        self._literal_priority = None
        self._changed()

    @property
    def important(self) -> bool:
        return self._priority == 'important'

    @property
    def literal_priority(self) -> str:
        """The priority as written in the source, such as '!IMPORTANT' or '! important', or '' if none."""
        literal = self._literal_priority
        if literal is None:
            literal = '!important' if self._priority else ''
            node = self._node
            if node is not None and self._priority:
                tail = node.stream.source[node.value.end : node.end]
                bang = None
                for tok in tokenize(tail):
                    if tok.is_delim('!'):
                        bang = tok.pos
                    elif tok.type is TokenType.IDENT and bang is not None:
                        literal = tail[bang : tok.end]
                        break
            self._literal_priority = literal
        return literal

    @property
    def line(self) -> int | None:
        """1-based line of this declaration in its source, None if it was not parsed from text."""
        return None if self._node is None else self._node.line

    @property
    def column(self) -> int | None:
        """1-based column of this declaration in its source, None if it was not parsed from text."""
        return None if self._node is None else self._node.column

    def copy(self) -> Property:
        """An unattached copy of this property."""
        p = Property.__new__(Property)
        p._name, p._value, p._priority = self._name, self.value, self._priority
        p._literal_priority, p._node, p.parent = self._literal_priority, self._node, None
        return p

    def __repr__(self) -> str:
        imp = ' !important' if self._priority else ''
        return f'<Property {self._name}: {self.value!r}{imp}>'


class DeclarationList(MutableSequence['Property | Comment']):
    """Every declaration and comment of a block in source order, duplicates included (the "full view").

    Inserting a property that belongs to another block raises ValueError:
    remove it from there first, or insert a copy().
    """

    __slots__ = ('_block', '_items')

    def __init__(self, block: DeclarationBlock, items: Iterable[Property | Comment] = ()) -> None:
        self._block = block
        self._items: list[Property | Comment] = []
        for item in items:
            self._adopt(item)
            self._items.append(item)

    def _adopt(self, item: Property | Comment) -> None:
        if isinstance(item, Property):
            if item.parent is not None and item.parent is not self._block:
                raise ValueError('The property belongs to another declaration block, insert a copy() instead')
            item.parent = self._block
        else:
            item.parent_rule = self._block.parent_rule

    def _release(self, item: Property | Comment) -> None:
        if isinstance(item, Property):
            item.parent = None
        else:
            item.parent_rule = None

    @overload
    def __getitem__(self, index: int) -> Property | Comment: ...
    @overload
    def __getitem__(self, index: slice) -> list[Property | Comment]: ...
    def __getitem__(self, index: int | slice) -> Property | Comment | list[Property | Comment]:
        return self._items[index]

    @overload
    def __setitem__(self, index: int, value: Property | Comment) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[Property | Comment]) -> None: ...
    def __setitem__(self, index: int | slice, value: Property | Comment | Iterable[Property | Comment]) -> None:
        if isinstance(index, slice):
            assert not isinstance(value, Property)
            assert isinstance(value, Iterable)
            new = list(value)
            old = self._items[index]
            for item in old:
                self._release(item)
            try:
                for item in new:
                    self._adopt(item)
            except ValueError:
                for item in old:
                    self._adopt(item)
                raise
            self._items[index] = new
        else:
            assert isinstance(value, Property) or not isinstance(value, Iterable)
            old_item = self._items[index]
            if value is not old_item:
                self._adopt(value)
                self._release(old_item)
                self._items[index] = value
        self._block._changed()

    def __delitem__(self, index: int | slice) -> None:
        items = self._items[index] if isinstance(index, slice) else [self._items[index]]
        for item in items:
            self._release(item)
        del self._items[index]
        self._block._changed()

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Property | Comment]:
        return iter(self._items)

    def insert(self, index: int, value: Property | Comment) -> None:
        self._adopt(value)
        self._items.insert(index, value)
        self._block._changed()

    def index(self, value: object, start: int = 0, stop: int | None = None) -> int:
        """The index of value, compared by identity."""
        for i in range(start, len(self._items) if stop is None else stop):
            if self._items[i] is value:
                return i
        raise ValueError('not in the list')

    def __repr__(self) -> str:
        return f'<DeclarationList of {len(self._items)} items>'


class DeclarationBlock:
    """The declarations of a style rule, @font-face, @page or a style="" attribute.

    Iteration, len(), `in`, indexing by name and keys() use the effective
    view: one property per name, where !important beats later normal
    declarations and otherwise the last one wins, in the order of each name's
    last occurrence. items is the full view, with every declaration and
    comment in source order.
    """

    __slots__ = ('_effective', '_items', 'errors', 'parent_rule')

    def __init__(self, items: Iterable[Property | Comment] = (), parent_rule: Rule | None = None) -> None:
        self.parent_rule = parent_rule
        self._effective: dict[str, Property] | None = None
        # Problems found when the block was parsed from text with parse_style()
        self.errors: list[ParseError] = []
        self._items = DeclarationList(self, items)

    @property
    def items(self) -> DeclarationList:
        """Every declaration and comment in source order, duplicates included."""
        return self._items

    def _changed(self) -> None:
        self._effective = None
        if self.parent_rule is not None:
            self.parent_rule._changed()

    def _effective_view(self) -> dict[str, Property]:
        effective = self._effective
        if effective is None:
            effective = {}
            # Hot: runs for every block that is read, so it uses the internals directly
            for item in self._items._items:
                if not isinstance(item, Property):
                    continue
                name = item._name
                current = effective.pop(name, None)
                if current is not None and current._priority and not item._priority:
                    effective[name] = current
                else:
                    effective[name] = item
            self._effective = effective
        return effective

    def __iter__(self) -> Iterator[Property]:
        return iter(list(self._effective_view().values()))

    def __len__(self) -> int:
        return len(self._effective_view())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and normalize_name(name) in self._effective_view()

    def __getitem__(self, name: str) -> str:
        """The effective value of the property name. Raises KeyError if it is not set."""
        return self._effective_view()[normalize_name(name)].value

    def keys(self) -> list[str]:
        """The names of the properties, in effective order."""
        return list(self._effective_view())

    def get_value(self, name: str, default: str = '') -> str:
        """The effective value of the property name, or default (normally '') if it is not set."""
        p = self._effective_view().get(normalize_name(name))
        return default if p is None else p.value

    def get_property(self, name: str) -> Property | None:
        """The effective property for name, or None."""
        return self._effective_view().get(normalize_name(name))

    def _remove_others(self, name: str, keep: Property | None) -> None:
        self._items[:] = [i for i in self._items if not (isinstance(i, Property) and i.name == name and i is not keep)]

    def set(self, name: str, value: str, priority: str = '') -> Property:
        """Set a property. An existing property of the same name is changed in place, and any other
        declarations of that name are removed. Returns the property."""
        name = normalize_name(name)
        current = self._effective_view().get(name)
        if current is None:
            prop = Property(name, value, priority)
            self._items.append(prop)
            return prop
        value = check_value(name, value)
        current._value, current._priority = value, normalize_priority(priority)
        current._literal_priority = None
        self._remove_others(name, current)
        self._changed()
        return current

    def set_property(self, prop: Property) -> Property:
        """Like set(), with a Property object. If it belongs to another block, a copy is used.
        Returns the property that is now in this block."""
        if prop.parent is not None and prop.parent is not self:
            prop = prop.copy()
        current = self._effective_view().get(prop.name)
        if current is None:
            if prop.parent is None:
                self._items.append(prop)
        elif current is not prop:
            if prop.parent is self:
                self._items.remove(prop)
            self._items[self._items.index(current)] = prop
        self._remove_others(prop.name, prop)
        return prop

    def remove(self, name: str) -> str:
        """Remove every declaration of name. Returns the old effective value, or '' if it was not set."""
        name = normalize_name(name)
        current = self._effective_view().get(name)
        if current is None:
            return ''
        old = current.value
        self._remove_others(name, None)
        return old

    def remove_declaration(self, prop: Property) -> None:
        """Remove one specific declaration, leaving others of the same name alone."""
        del self._items[self._items.index(prop)]

    def replace_declaration(self, prop: Property, replacements: Iterable[Property]) -> None:
        """Replace one specific declaration with several, at the same position (for shorthand expansion)."""
        i = self._items.index(prop)
        self._items[i : i + 1] = list(replacements)

    def copy(self) -> DeclarationBlock:
        """An unattached deep copy of this block."""
        from kgcss.cssom.rules import Comment

        items: list[Property | Comment] = []
        for item in self._items:
            items.append(item.copy() if isinstance(item, Property) else Comment(item.text))
        return DeclarationBlock(items)

    def __repr__(self) -> str:
        text = '; '.join(f'{p.name}: {p.value}' + (' !important' if p.important else '') for p in self)
        if len(text) > 60:
            text = text[:57] + '...'
        return f'<DeclarationBlock {text}>'


def block_from_nodes(nodes: Iterable[Declaration | Token], parent_rule: Rule | None) -> DeclarationBlock:
    """A declaration block for parsed declarations and comments."""
    from kgcss.cssom.rules import Comment

    block = DeclarationBlock(parent_rule=parent_rule)
    items = block._items._items
    from_node = Property._from_node
    for node in nodes:
        if isinstance(node, Declaration):
            prop = from_node(node)
            prop.parent = block
            items.append(prop)
        else:
            comment = Comment(str(node.value))
            comment._source = node
            comment.parent_rule = parent_rule
            items.append(comment)
    return block


def parse_declarations(
    source: str | Block, parent_rule: Rule | None, state: SheetState | None
) -> tuple[DeclarationBlock, list[ParseError]]:
    """Parse declarations and comments. Nested rules are not allowed and are dropped with an error."""
    nodes = parse_block_contents(source)
    errors = (
        list(nodes.errors) if isinstance(source, str) else [e for e in nodes.errors if e.kind.startswith('invalid')]
    )
    kept: list[Declaration | Token] = []
    for node in nodes:
        if isinstance(node, Declaration | Token):
            kept.append(node)
        else:
            line, column = node.line, node.column
            errors.append(
                ParseError('invalid-rule', 'Rules are not allowed in a declaration list', node.pos, line, column)
            )
    errors.sort(key=lambda e: e.offset)
    if state is not None:
        for e in errors:
            state.add(e)
    return block_from_nodes(kept, parent_rule), errors


def parse_style(text: str, *, log: ErrorHandler | None = None) -> DeclarationBlock:
    """Parse declarations, such as the contents of a style="" attribute (requirements P2).

    Never raises for bad CSS: invalid declarations and any rules are dropped.
    Problems are in the result's errors list and sent to log. A top level }
    ends the declarations, as in a block.
    """
    state = SheetState(log, None)
    block, errors = parse_declarations(text, None, state)
    block.errors = errors
    return block
