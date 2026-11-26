# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A port of the PostCSS parser, which the stylelint rules are written against.

stylelint's rules read the tree PostCSS builds, including its raws (the text
between the parts of a node), and stylelint reports PostCSS's errors as
CssSyntaxError. This is a faithful port of PostCSS 8.5's tokenizer
(lib/tokenize.js) and parser (lib/parser.js), and of postcss-safe-parser 7,
which recovers from every error, so that the rules see the same tree.
Quirks are kept on purpose, and so are the names of the raws.

Positions are offsets into the text, which are Python string indexes. A node
ends at an exclusive offset, or None when PostCSS gives it no end. JavaScript
string semantics that matter are spelt out: its whitespace (\\s, trim()) and
its ASCII-only \\w and \\d.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import ClassVar, Final, NamedTuple

# JavaScript's \s, which trim() also strips
JS_WHITESPACE: Final = (
    '\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
    '\u2028\u2029\u202f\u205f\u3000\ufeff'
)


def js_trim(text: str) -> str:
    return text.strip(JS_WHITESPACE)


class Token(NamedTuple):
    """A PostCSS token: [type, text, start, end] in PostCSS. Spaces have no position."""

    type: str  # space word string at-word comment brackets ( ) [ ] { } ; :
    text: str
    start: int | None = None
    end: int | None = None  # inclusive


class CssSyntaxError(Exception):
    """An error the PostCSS parser throws, at an offset, with an optional exclusive end."""

    def __init__(self, reason: str, offset: int, end: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.offset = offset
        self.end = end


RE_AT_END: Final = re.compile(r'[\t\n\f\r "#\'()/;\[\\\]{}]')
RE_WORD_END: Final = re.compile(r'[\t\n\f\r !"#\'():;@\[\\\]{}]|/(?=\*)')
RE_BAD_BRACKET: Final = re.compile('[^\n\r\u2028\u2029][\r\n"\'(/\\\\]')  # JavaScript's . is no line terminator
RE_HEX_ESCAPE: Final = re.compile(r'[0-9a-fA-F]')
RE_URL_NAME: Final = re.compile(r'(?:^|[\t\n\f\r "\'(),/:;\[\]{}])url$', re.IGNORECASE)
RE_QUOTED: Final = re.compile(r'[\t\n\f\r ]*["\']')
RE_ASCII_WORD: Final = re.compile(r'[A-Za-z0-9_]')
SPACES: Final = frozenset(' \n\t\r\f')
CONTROL: Final = frozenset('[]{}:;)')


class Tokenizer:
    """PostCSS's tokenizer. With ignore_errors, unclosed strings, comments and brackets do not raise."""

    __slots__ = ('css', 'ignore', 'last_bad_paren', 'length', 'pos', 'returned')

    def __init__(self, css: str, ignore_errors: bool = False) -> None:
        self.css = css
        self.ignore = ignore_errors
        self.length = len(css)
        self.pos = 0
        self.returned: list[Token] = []
        self.last_bad_paren = -1

    def position(self) -> int:
        return self.pos

    def end_of_file(self) -> bool:
        return not self.returned and self.pos >= self.length

    def back(self, token: Token) -> None:
        self.returned.append(token)

    def _char(self, index: int) -> str:
        return self.css[index] if 0 <= index < self.length else ''

    def _unclosed(self, what: str) -> CssSyntaxError:
        return CssSyntaxError('Unclosed ' + what, self.pos)

    def _escaped_at(self, index: int) -> bool:
        """Whether the character at index is escaped by an odd number of backslashes."""
        escaped = False
        while self._char(index - 1) == '\\':
            index -= 1
            escaped = not escaped
        return escaped

    def next_token(self, ignore_unclosed: bool = False) -> Token | None:
        if self.returned:
            return self.returned.pop()
        css, pos = self.css, self.pos
        if pos >= self.length:
            return None
        ignore = self.ignore or ignore_unclosed
        code = css[pos]
        if code in SPACES:
            nxt = pos + 1
            while nxt < self.length and css[nxt] in SPACES:
                nxt += 1
            token = Token('space', css[pos:nxt])
            pos = nxt - 1
        elif code in CONTROL:
            token = Token(code, code, pos)
        elif code == '(':
            if RE_URL_NAME.search(css[max(pos - 4, 0) : pos]) and not RE_QUOTED.match(css, pos + 1):
                nxt = pos
                while True:
                    nxt = css.find(')', nxt + 1)
                    if nxt == -1:
                        if ignore:
                            nxt = pos
                            break
                        raise self._unclosed('bracket')
                    if not self._escaped_at(nxt):
                        break
                token = Token('brackets', css[pos : nxt + 1], pos, nxt)
                pos = nxt
            elif pos <= self.last_bad_paren:
                token = Token('(', '(', pos)
            else:
                nxt = css.find(')', pos + 1)
                content = css[pos : nxt + 1] if nxt != -1 else css[pos:0]  # slice(pos, 0) is empty
                if nxt == -1 or RE_BAD_BRACKET.search(content):
                    self.last_bad_paren = self.length if nxt == -1 else nxt
                    token = Token('(', '(', pos)
                else:
                    token = Token('brackets', content, pos, nxt)
                    pos = nxt
        elif code in '\'"':
            nxt = pos
            while True:
                nxt = css.find(code, nxt + 1)
                if nxt == -1:
                    if ignore:
                        nxt = pos + 1
                        break
                    raise self._unclosed('string')
                if not self._escaped_at(nxt):
                    break
            token = Token('string', css[pos : nxt + 1], pos, nxt)
            pos = nxt
        elif code == '@':
            m = RE_AT_END.search(css, pos + 1)
            nxt = self.length - 1 if m is None else m.start() - 1
            token = Token('at-word', css[pos : nxt + 1], pos, nxt)
            pos = nxt
        elif code == '\\':
            nxt = pos
            escape = True
            while self._char(nxt + 1) == '\\':
                nxt += 1
                escape = not escape
            following = self._char(nxt + 1)
            if escape and following not in ('/', ' ', '\n', '\t', '\r', '\f'):
                # charCodeAt() past the end is NaN, which is none of these
                nxt += 1
                if RE_HEX_ESCAPE.match(self._char(nxt) or '-'):
                    while RE_HEX_ESCAPE.match(self._char(nxt + 1) or '-'):
                        nxt += 1
                    if self._char(nxt + 1) == ' ':
                        nxt += 1
            token = Token('word', css[pos : nxt + 1], pos, nxt)
            pos = nxt
        elif code == '/' and self._char(pos + 1) == '*':
            nxt = css.find('*/', pos + 2) + 1
            if nxt == 0:
                if not ignore:
                    raise self._unclosed('comment')
                nxt = self.length
            token = Token('comment', css[pos : nxt + 1], pos, nxt)
            pos = nxt
        else:
            m = RE_WORD_END.search(css, pos + 1)
            nxt = self.length - 1 if m is None else m.start() - 1
            token = Token('word', css[pos : nxt + 1], pos, nxt)
            pos = nxt
        self.pos = pos + 1
        return token


# Nodes


@dataclass(slots=True)
class RawValue:
    """The raw text of a selector, params or value that holds comments, and the value without them."""

    raw: str
    value: str


@dataclass(slots=True)
class Raws:
    """PostCSS's raws: the text around and between the parts of a node. None is undefined."""

    before: str | None = None
    after: str | None = None
    between: str | None = None
    after_name: str | None = None  # afterName
    semicolon: bool | None = None
    own_semicolon: str | None = None  # ownSemicolon
    important: str | None = None
    left: str | None = None
    right: str | None = None
    selector: RawValue | None = None
    params: RawValue | None = None
    value: RawValue | None = None


class Node:
    """A PostCSS node: start and end are offsets, end exclusive or None."""

    __slots__ = ('end', 'parent', 'raws', 'start')
    type: ClassVar[str] = ''

    def __init__(self) -> None:
        self.parent: Container | None = None
        self.raws = Raws()
        self.start: int | None = None
        self.end: int | None = None

    def root(self) -> Root:
        node: Node = self
        while node.parent is not None:
            node = node.parent
        assert isinstance(node, Root)
        return node

    def prev(self) -> Node | None:
        if self.parent is None or self.parent.nodes is None:
            return None
        i = self.parent.nodes.index(self)
        return self.parent.nodes[i - 1] if i > 0 else None

    def next(self) -> Node | None:
        if self.parent is None or self.parent.nodes is None:
            return None
        nodes = self.parent.nodes
        i = nodes.index(self)
        return nodes[i + 1] if i + 1 < len(nodes) else None


class Container(Node):
    __slots__ = ('nodes',)

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[Node] | None = None

    def push(self, node: Node) -> None:
        if self.nodes is None:
            self.nodes = []
        node.parent = self
        self.nodes.append(node)

    @property
    def first(self) -> Node | None:
        return self.nodes[0] if self.nodes else None

    @property
    def last(self) -> Node | None:
        return self.nodes[-1] if self.nodes else None

    def walk(self) -> Iterator[Node]:
        """Every descendant, depth first, in document order (PostCSS's walk())."""
        stack: list[Iterator[Node]] = [iter(self.nodes or ())]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                stack.pop()
                continue
            yield node
            if isinstance(node, Container) and node.nodes:
                stack.append(iter(node.nodes))

    def walk_decls(self) -> Iterator[Declaration]:
        return (n for n in self.walk() if isinstance(n, Declaration))

    def walk_rules(self) -> Iterator[Rule]:
        return (n for n in self.walk() if isinstance(n, Rule))

    def walk_at_rules(self) -> Iterator[AtRule]:
        return (n for n in self.walk() if isinstance(n, AtRule))

    def walk_comments(self) -> Iterator[Comment]:
        return (n for n in self.walk() if isinstance(n, Comment))


class Root(Container):
    __slots__ = ('css',)
    type: ClassVar[str] = 'root'

    def __init__(self, css: str) -> None:
        super().__init__()
        self.nodes = []
        self.css = css  # the text that was parsed: source.input.css


class AtRule(Container):
    __slots__ = ('name', 'params')
    type: ClassVar[str] = 'atrule'

    def __init__(self) -> None:
        super().__init__()
        self.name = ''
        self.params = ''


class Rule(Container):
    __slots__ = ('selector',)
    type: ClassVar[str] = 'rule'

    def __init__(self) -> None:
        super().__init__()
        self.nodes = []
        self.selector = ''


class Declaration(Node):
    __slots__ = ('important', 'prop', 'value')
    type: ClassVar[str] = 'decl'

    def __init__(self) -> None:
        super().__init__()
        self.prop = ''
        self.value = ''
        self.important = False


class Comment(Node):
    __slots__ = ('text',)
    type: ClassVar[str] = 'comment'

    def __init__(self) -> None:
        super().__init__()
        self.text = ''


# Parsing

SAFE_COMMENT_NEIGHBOR: Final = frozenset({'empty', 'space'})


def _tokens_to_string(tokens: list[Token], start: int, end: int) -> str:
    return ''.join(t.text for t in tokens[start:end])


def _find_last_with_position(tokens: list[Token]) -> int | None:
    for token in reversed(tokens):
        if pos := token.end or token.start:
            return pos
    return None


def _split_comment(text: str) -> tuple[str, str, str]:
    """text.match(/^(\\s*)([^]*\\S)(\\s*)$/) for text that is not all whitespace."""
    middle = text.strip(JS_WHITESPACE)
    left = text[: len(text) - len(text.lstrip(JS_WHITESPACE))]
    right = text[len(text.rstrip(JS_WHITESPACE)) :]
    return left, middle, right


class Parser:
    """PostCSS's parser. parse() raises CssSyntaxError at the first error."""

    def __init__(self, css: str) -> None:
        self.css = css
        self.root = Root(css)
        self.root.start = 0
        self.current: Container = self.root
        self.spaces = ''
        self.semicolon = False
        self.tokenizer = self.create_tokenizer()

    def create_tokenizer(self) -> Tokenizer:
        return Tokenizer(self.css)

    def parse(self) -> Root:
        tokenizer = self.tokenizer
        while not tokenizer.end_of_file():
            token = tokenizer.next_token()
            assert token is not None
            match token.type:
                case 'space':
                    self.spaces += token.text
                case ';':
                    self.free_semicolon(token)
                case '}':
                    self.end(token)
                case 'comment':
                    self.comment(token)
                case 'at-word':
                    self.atrule(token)
                case '{':
                    self.empty_rule(token)
                case _:
                    self.other(token)
        self.end_file()
        return self.root

    # The node types

    def atrule(self, token: Token) -> None:
        node = AtRule()
        node.name = token.text[1:]
        if node.name == '':
            self.unnamed_atrule(node, token)
        self.init(node, token.start)
        last = False
        is_open = False
        params: list[Token] = []
        brackets: list[str] = []
        tokenizer = self.tokenizer
        while not tokenizer.end_of_file():
            tok = tokenizer.next_token()
            assert tok is not None
            token = tok
            kind = token.type
            if kind in ('(', '['):
                brackets.append(')' if kind == '(' else ']')
            elif kind == '{' and brackets:
                brackets.append('}')
            elif brackets and kind == brackets[-1]:
                brackets.pop()
            if not brackets:
                if kind == ';':
                    assert token.start is not None
                    node.end = token.start + 1
                    self.semicolon = True
                    break
                if kind == '{':
                    is_open = True
                    break
                if kind == '}':
                    if params:
                        shift = len(params) - 1
                        prev: Token | None = params[shift]
                        while prev is not None and prev.type == 'space':
                            shift -= 1
                            prev = params[shift] if shift >= 0 else None
                        if prev is not None:
                            node.end = (prev.end or prev.start or 0) + 1
                    self.end(token)
                    break
                params.append(token)
            else:
                params.append(token)
            if tokenizer.end_of_file():
                last = True
                break
        node.raws.between = self.spaces_and_comments_from_end(params)
        if params:
            node.raws.after_name = self.spaces_and_comments_from_start(params)
            node.params = self.raw(node, 'params', params)
            if last:
                token = params[-1]
                node.end = (token.end or token.start or 0) + 1
                self.spaces = node.raws.between
                node.raws.between = ''
        else:
            node.raws.after_name = ''
            node.params = ''
        if is_open:
            node.nodes = []
            self.current = node

    def comment(self, token: Token) -> None:
        node = Comment()
        self.init(node, token.start)
        node.end = (token.end or token.start or 0) + 1
        text = token.text[2:-2]
        if not js_trim(text):
            node.text = ''
            node.raws.left = text
            node.raws.right = ''
        else:
            node.raws.left, node.text, node.raws.right = _split_comment(text)

    def decl(self, tokens: list[Token], custom_property: bool) -> None:
        node = Declaration()
        self.init(node, tokens[0].start)
        last = tokens[-1]
        if last.type == ';':
            self.semicolon = True
            tokens.pop()
        end = last.end or last.start or _find_last_with_position(tokens)
        node.end = None if end is None else end + 1

        start = 0
        while tokens[start].type != 'word':
            if start == len(tokens) - 1:
                self.unknown_word([tokens[start]])
            start += 1
        node.raws.before = (node.raws.before or '') + _tokens_to_string(tokens, 0, start)
        node.start = tokens[start].start

        prop_start = start
        while start < len(tokens):
            if tokens[start].type in (':', 'space', 'comment'):
                break
            start += 1
        node.prop = _tokens_to_string(tokens, prop_start, start)

        between_start = start
        while start < len(tokens):
            token = tokens[start]
            start += 1
            if token.type == ':':
                break
            if token.type == 'word' and RE_ASCII_WORD.search(token.text):
                self.unknown_word([token])
        node.raws.between = _tokens_to_string(tokens, between_start, start)

        if node.prop[:1] in ('_', '*'):
            node.raws.before += node.prop[0]
            node.prop = node.prop[1:]

        first_spaces_start = start
        while start < len(tokens):
            if tokens[start].type not in ('space', 'comment'):
                break
            start += 1
        first_spaces = tokens[first_spaces_start:start]
        tokens = tokens[start:]

        self.precheck_missed_semicolon(tokens)

        i = len(tokens) - 1
        while i >= 0:
            token = tokens[i]
            if token.text.lower() == '!important':
                node.important = True
                string = self.string_from(tokens, i)
                string = self.spaces_from_end(tokens) + string
                if string != ' !important':
                    node.raws.important = string
                break
            if token.text.lower() == 'important':
                cache = tokens[:]
                text = ''
                j = i
                while j > 0:
                    kind = cache[j].type
                    if js_trim(text).startswith('!') and kind != 'space':
                        break
                    text = cache.pop().text + text
                    j -= 1
                if js_trim(text).startswith('!'):
                    node.important = True
                    node.raws.important = text
                    tokens = cache
            if token.type not in ('space', 'comment'):
                break
            i -= 1

        if any(t.type not in ('space', 'comment') for t in tokens):
            node.raws.between += ''.join(t.text for t in first_spaces)
            first_spaces = []
        node.value = self.raw(node, 'value', first_spaces + tokens, custom_property)

        if ':' in node.value and not custom_property:
            self.check_missed_semicolon(tokens)

    def empty_rule(self, token: Token) -> None:
        node = Rule()
        self.init(node, token.start)
        node.selector = ''
        node.raws.between = ''
        self.current = node

    def end(self, token: Token) -> None:
        current = self.current
        if current.nodes:
            current.raws.semicolon = self.semicolon
        self.semicolon = False
        current.raws.after = (current.raws.after or '') + self.spaces
        self.spaces = ''
        if current.parent is not None:
            assert token.start is not None
            current.end = token.start + 1
            self.current = current.parent
        else:
            self.unexpected_close(token)

    def end_file(self) -> None:
        if self.current.parent is not None:
            self.unclosed_block()
        if self.current.nodes:
            self.current.raws.semicolon = self.semicolon
        self.current.raws.after = (self.current.raws.after or '') + self.spaces
        self.root.end = self.tokenizer.position()

    def free_semicolon(self, token: Token) -> None:
        self.spaces += token.text
        if self.current.nodes is not None:
            prev = self.current.nodes[-1] if self.current.nodes else None
            if isinstance(prev, Rule) and not prev.raws.own_semicolon:
                prev.raws.own_semicolon = self.spaces
                self.spaces = ''
                assert token.start is not None
                prev.end = token.start + 1

    def other(self, start: Token) -> None:
        end = False
        colon = False
        bracket: Token | None = None
        brackets: list[str] = []
        custom_property = start.text.startswith('--')
        tokens: list[Token] = []
        token: Token | None = start
        tokenizer = self.tokenizer
        while token is not None:
            kind = token.type
            tokens.append(token)
            if kind in ('(', '['):
                if bracket is None:
                    bracket = token
                brackets.append(')' if kind == '(' else ']')
            elif custom_property and colon and kind == '{':
                if bracket is None:
                    bracket = token
                brackets.append('}')
            elif not brackets:
                if kind == ';':
                    if colon:
                        self.decl(tokens, custom_property)
                        return
                    break
                if kind == '{':
                    self.rule(tokens)
                    return
                if kind == '}':
                    tokenizer.back(tokens.pop())
                    end = True
                    break
                if kind == ':':
                    colon = True
            elif kind == brackets[-1]:
                brackets.pop()
                if not brackets:
                    bracket = None
            token = tokenizer.next_token()

        if tokenizer.end_of_file():
            end = True
        if brackets:
            assert bracket is not None
            self.unclosed_bracket(bracket)

        if end and colon:
            if not custom_property:
                while tokens:
                    if tokens[-1].type not in ('space', 'comment'):
                        break
                    tokenizer.back(tokens.pop())
            self.decl(tokens, custom_property)
        else:
            self.unknown_word(tokens)

    def rule(self, tokens: list[Token]) -> None:
        tokens.pop()
        node = Rule()
        self.init(node, tokens[0].start)
        node.raws.between = self.spaces_and_comments_from_end(tokens)
        node.selector = self.raw(node, 'selector', tokens)
        self.current = node

    # Helpers

    def init(self, node: Node, offset: int | None) -> None:
        self.current.push(node)
        node.start = offset
        node.raws.before = self.spaces
        self.spaces = ''
        if not isinstance(node, Comment):
            self.semicolon = False

    def colon(self, tokens: list[Token]) -> int | None:
        brackets = 0
        prev: Token | None = None
        for i, token in enumerate(tokens):
            kind = token.type
            if kind == '(':
                brackets += 1
            if kind == ')':
                brackets -= 1
            if brackets == 0 and kind == ':':
                if prev is None:
                    self.double_colon(token)
                elif prev.type == 'word' and prev.text == 'progid':
                    continue  # without updating prev, as in PostCSS
                else:
                    return i
            prev = token
        return None

    def raw(self, node: Node, prop: str, tokens: list[Token], custom_property: bool = False) -> str:
        """Set node.raws[prop] if the text holds comments or trailing space, return the value."""
        value: list[str] = []
        last = ''
        clean = True
        length = len(tokens)
        for i, token in enumerate(tokens):
            kind = token.type
            if kind == 'space' and i == length - 1 and not custom_property:
                clean = False
            elif kind == 'comment':
                prev = tokens[i - 1].type if i > 0 else 'empty'
                nxt = tokens[i + 1].type if i + 1 < length else 'empty'
                if prev not in SAFE_COMMENT_NEIGHBOR and nxt not in SAFE_COMMENT_NEIGHBOR:
                    if last == ',':
                        clean = False
                    else:
                        value.append(token.text)
                        last = token.text[-1:]
                else:
                    clean = False
            else:
                value.append(token.text)
                last = token.text[-1:]
        text = ''.join(value)
        if not clean:
            raw = RawValue(''.join(t.text for t in tokens), text)
            if prop == 'selector':
                node.raws.selector = raw
            elif prop == 'params':
                node.raws.params = raw
            else:
                node.raws.value = raw
        return text

    @staticmethod
    def spaces_and_comments_from_end(tokens: list[Token]) -> str:
        spaces = ''
        while tokens and tokens[-1].type in ('space', 'comment'):
            spaces = tokens.pop().text + spaces
        return spaces

    @staticmethod
    def spaces_and_comments_from_start(tokens: list[Token]) -> str:
        n = 0
        while n < len(tokens) and tokens[n].type in ('space', 'comment'):
            n += 1
        spaces = ''.join(t.text for t in tokens[:n])
        del tokens[:n]
        return spaces

    @staticmethod
    def spaces_from_end(tokens: list[Token]) -> str:
        spaces = ''
        while tokens and tokens[-1].type == 'space':
            spaces = tokens.pop().text + spaces
        return spaces

    @staticmethod
    def string_from(tokens: list[Token], start: int) -> str:
        result = ''.join(t.text for t in tokens[start:])
        del tokens[start:]
        return result

    # Errors: the safe parser recovers from them instead

    def precheck_missed_semicolon(self, tokens: list[Token]) -> None:
        pass

    def check_missed_semicolon(self, tokens: list[Token]) -> None:
        colon = self.colon(tokens)
        if colon is None:
            return
        founded = 0
        token = tokens[0]
        for j in range(colon - 1, -1, -1):
            token = tokens[j]
            if token.type != 'space':
                founded += 1
                if founded == 2:
                    break
        offset = (token.end or 0) + 1 if token.type == 'word' else token.start
        raise CssSyntaxError('Missed semicolon', offset or 0)

    def double_colon(self, token: Token) -> None:
        assert token.start is not None
        raise CssSyntaxError('Double colon', token.start, token.start + len(token.text))

    def unclosed_block(self) -> None:
        raise CssSyntaxError('Unclosed block', self.current.start or 0)

    def unclosed_bracket(self, bracket: Token) -> None:
        assert bracket.start is not None
        raise CssSyntaxError('Unclosed bracket', bracket.start, bracket.start + 1)

    def unexpected_close(self, token: Token) -> None:
        assert token.start is not None
        raise CssSyntaxError('Unexpected }', token.start, token.start + 1)

    def unknown_word(self, tokens: list[Token]) -> None:
        first = tokens[0]
        start = first.start or 0  # spaces have no position: PostCSS reports offset undefined
        raise CssSyntaxError('Unknown word ' + first.text, start, start + len(first.text))

    def unnamed_atrule(self, node: AtRule, token: Token) -> None:
        assert token.start is not None
        raise CssSyntaxError('At-rule without name', token.start, token.start + len(token.text))


class SafeParser(Parser):
    """postcss-safe-parser: parses any text, as stylelint does when it fixes."""

    def create_tokenizer(self) -> Tokenizer:
        return Tokenizer(self.css, ignore_errors=True)

    def check_missed_semicolon(self, tokens: list[Token]) -> None:
        pass

    def comment(self, token: Token) -> None:
        node = Comment()
        self.init(node, token.start)
        node.end = (token.end or 0) + 1
        text = token.text[2:]
        if text.endswith('*/'):
            text = text[:-2]
        if not text.strip(JS_WHITESPACE):
            node.text = ''
            node.raws.left = text
            node.raws.right = ''
        else:
            node.raws.left, node.text, node.raws.right = _split_comment(text)

    def decl(self, tokens: list[Token], custom_property: bool) -> None:
        if len(tokens) > 1 and any(t.type == 'word' for t in tokens):
            super().decl(tokens, False)  # the safe parser drops customProperty

    def double_colon(self, token: Token) -> None:
        pass

    def end_file(self) -> None:
        if self.current.nodes:
            self.current.raws.semicolon = self.semicolon
        self.current.raws.after = (self.current.raws.after or '') + self.spaces
        while self.current.parent is not None:
            self.current = self.current.parent
            self.current.raws.after = ''
        self.root.end = self.tokenizer.position()

    def precheck_missed_semicolon(self, tokens: list[Token]) -> None:
        colon = self.colon(tokens)
        if colon is None:
            return
        next_start = colon - 1
        while next_start >= 0:
            if tokens[next_start].type == 'word':
                break
            next_start -= 1
        if next_start <= 0:
            return
        prev_end = next_start - 1
        while prev_end >= 0:
            if tokens[prev_end].type != 'space':
                prev_end += 1
                break
            prev_end -= 1
        other = tokens[next_start:]
        spaces = tokens[prev_end:next_start]
        del tokens[prev_end:]
        self.spaces = ''.join(t.text for t in spaces)
        self.decl(other, False)

    def unclosed_bracket(self, bracket: Token) -> None:
        pass

    def unexpected_close(self, token: Token) -> None:
        # JavaScript's undefined + '}' is 'undefined}', and PostCSS does just that to a root
        self.current.raws.after = ('undefined' if self.current.raws.after is None else self.current.raws.after) + '}'

    def unknown_word(self, tokens: list[Token]) -> None:
        self.spaces += ''.join(t.text for t in tokens)

    def unnamed_atrule(self, node: AtRule, token: Token) -> None:
        node.name = ''


def parse(css: str) -> tuple[Root, CssSyntaxError | None]:
    """Parse as stylelint does: the error PostCSS raises, if any, and the safe parser's tree."""
    try:
        return Parser(css).parse(), None
    except CssSyntaxError as err:
        return SafeParser(css).parse(), err
