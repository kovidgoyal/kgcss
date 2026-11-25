# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Declaration blocks and properties (requirements §4)."""

from collections.abc import Iterable, Iterator, MutableSequence
from typing import TYPE_CHECKING, cast, overload
from weakref import ReferenceType, ref

from kgcss._kgcss import PropertyData, build_properties
from kgcss.cssom.exceptions import InvalidValueError
from kgcss.cssom.state import ErrorHandler, SheetState
from kgcss.cssom.values import PropertyValue, parse_value, protected_end, value_from_components
from kgcss.escaping import serialize_identifier
from kgcss.syntax import (
    Block,
    Declaration,
    Function,
    NodeList,
    Token,
    parse_block_contents,
    parse_one_declaration,
)
from kgcss.tokenizer import ParseError, TokenType, tokenize

if TYPE_CHECKING:
    from kgcss.cssom.rules import Comment, Rule
    from kgcss.cssom.serializer import SerializeOptions

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


class Property(PropertyData):
    """A declaration: a name, a value and a priority.

    Properties are hashable and compare by identity. The value is text; it
    is checked when set, see check_value(). The fields are in the C base
    class, so that the parser can create properties quickly.
    """

    __slots__ = ()

    def __init__(self, name: str, value: str, priority: str = '', *, validate: bool = False) -> None:
        self._name = normalize_name(name)
        self._value: str | None = check_value(self._name, value, validate)
        self._priority = normalize_priority(priority)
        self._literal_priority: str | None = None
        self._node: Declaration | None = None
        # True while the value is still the one in the syntax node
        self._value_from_node = False
        self._parsed: PropertyValue | None = None
        # True once changed, so its source text is stale
        self._dirty = False
        self._parent: ReferenceType[DeclarationBlock] | None = None

    @property
    def parent(self) -> DeclarationBlock | None:
        """The block this property is in. A weak reference, so properties are never part of a
        reference cycle, which lets the parser exempt them from garbage collection."""
        ref = self._parent
        return None if ref is None else ref()

    @parent.setter
    def parent(self, block: DeclarationBlock | None) -> None:
        self._parent = None if block is None else ref(block)

    def _changed(self) -> None:
        self._dirty = True
        if (parent := self.parent) is not None:
            parent._changed()

    @property
    def name(self) -> str:
        """The name, lowercased unless it is a custom property. Assigning renames the property in place."""
        return self._name

    @name.setter
    def name(self, name: str) -> None:
        self._name = normalize_name(name)
        # How the value is parsed can depend on the name (unicode-range)
        self._parsed = None
        self._changed()

    @property
    def value(self) -> str:
        """The value as text, without surrounding whitespace. Assigning checks the new value, see check_value()."""
        value = self._value
        if value is None:
            node = self._node
            assert node is not None
            value = node.value_text
            if value.endswith('\\'):
                # A trailing lone backslash needs its newline, see protected_end()
                values = node.value
                end = protected_end(node.stream.source, values.end, values[len(values) - 1])
                value = node.stream.source[values.pos : end]
            self._value = value
        return value

    @value.setter
    def value(self, value: str) -> None:
        self._value = check_value(self._name, value)
        self._value_from_node = False
        self._parsed = None
        self._literal_priority = None
        self._changed()

    def _replace_value(self, value: str) -> None:
        """Set the value without checking it, for changes that cannot alter its structure, such as
        replacing a URL (its value may come from a source that was left open by EOF)."""
        self._value = value
        self._value_from_node = False
        self._parsed = None
        self._changed()

    @property
    def parsed_value(self) -> PropertyValue:
        """The value as a sequence of components, parsed on first access."""
        parsed = self._parsed
        if parsed is None:
            node = self._node
            if node is not None and self._value_from_node:
                parsed = value_from_components(node.value, self._name)
            else:
                parsed = parse_value(self.value, property_name=self._name)
            self._parsed = parsed
        return parsed

    def remove_component(self, index: int) -> None:
        """Remove the component at index of parsed_value, with one adjacent separator (requirements V6)."""
        self.value = self.parsed_value.text_without(index)

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
        p._literal_priority, p._node, p._parent = self._literal_priority, self._node, None
        p._value_from_node, p._parsed, p._dirty = self._value_from_node, self._parsed, self._dirty
        return p

    @property
    def css_text(self) -> str:
        """name: value, with !important if set, in the pretty format. Assigning parses one declaration
        and takes its name, value and priority, raising CSSSyntaxError if it is not one."""
        from kgcss.cssom.serializer import DEFAULT_OPTIONS, property_text

        return property_text(self, DEFAULT_OPTIONS)

    @css_text.setter
    def css_text(self, text: str) -> None:
        from kgcss.cssom.exceptions import CSSSyntaxError

        decl = parse_one_declaration(text)
        if not isinstance(decl, Declaration) or decl.end < len(
            text.rstrip(CSS_WHITESPACE).rstrip(';').rstrip(CSS_WHITESPACE)
        ):
            raise CSSSyntaxError(f'Not a single declaration: {text!r}')
        name = normalize_name(decl.name)
        value = check_value(name, decl.value_text)
        self._name, self._value = name, value
        self._priority = 'important' if decl.important else ''
        self._literal_priority = None
        self._value_from_node = False
        self._parsed = None
        self._changed()

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

    __slots__ = ('__weakref__', '_effective', '_errors', '_items', 'parent_rule')

    def __init__(self, items: Iterable[Property | Comment] = (), parent_rule: Rule | None = None) -> None:
        self.parent_rule = parent_rule
        self._effective: dict[str, Property] | None = None
        # Created on first use, to save allocating it for the many blocks that never need it
        self._errors: list[ParseError] | None = None
        self._items = DeclarationList(self, items)

    @property
    def errors(self) -> list[ParseError]:
        """Problems found when the block was parsed from text, with parse_style() or by assigning text."""
        errors = self._errors
        if errors is None:
            self._errors = errors = []
        return errors

    @errors.setter
    def errors(self, errors: list[ParseError]) -> None:
        self._errors = errors

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

    def get_parsed_value(self, name: str) -> PropertyValue | None:
        """The parsed effective value of the property name, or None if it is not set."""
        p = self._effective_view().get(normalize_name(name))
        return None if p is None else p.parsed_value

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
        current._value_from_node = False
        current._parsed = None
        current._dirty = True
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

    @property
    def css_text(self) -> str:
        """The declarations in the pretty format: one per line, each ending with a semicolon."""
        return self.to_css()

    @css_text.setter
    def css_text(self, text: str) -> None:
        block, errors = parse_declarations(text, self.parent_rule, None)
        self._items[:] = []
        items = list(block._items)
        block._items[:] = []
        self._items[:] = items
        self.errors = errors

    def to_css(self, options: SerializeOptions | None = None, *, single_line: bool = False) -> str:
        """Serialize the declarations. With single_line, they are on one line separated by '; ' and the
        text is safe inside a double-quoted HTML attribute, as for style="" (comments are left out)."""
        from kgcss.cssom.serializer import DEFAULT_OPTIONS, declaration_lines, declarations_single_line

        options = options or DEFAULT_OPTIONS
        if single_line:
            return declarations_single_line(self, options)
        return '\n'.join(declaration_lines(self, options))

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


def block_from_nodes(nodes: NodeList | Iterable[Declaration | Token], parent_rule: Rule | None) -> DeclarationBlock:
    """A declaration block for parsed declarations and comments."""
    from kgcss.cssom.rules import Comment

    # Hot: runs for every block that is read, so objects are built directly rather than through
    # their constructors. Property values are read from their nodes on first access.
    block = DeclarationBlock.__new__(DeclarationBlock)
    block.parent_rule = parent_rule
    block._effective = None
    block._errors = None
    full = DeclarationList.__new__(DeclarationList)
    full._block = block
    block._items = full
    # The properties are created in C (csrc/properties.c), which also exempts them from garbage
    # collection: they cannot be part of a reference cycle, see Property.parent.
    if not isinstance(nodes, NodeList | list):
        nodes = cast('list[Declaration | Token]', list(nodes))
    built, has_comments = build_properties(nodes, Property, ref(block))
    items = cast('list[Property | Comment]', built)
    if has_comments:
        for i, node in enumerate(built):
            if type(node) is Token:
                comment = Comment(str(node.value))
                comment._source = node
                comment.parent_rule = parent_rule
                items[i] = comment
    full._items = items
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
