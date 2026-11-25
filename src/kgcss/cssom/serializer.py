# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Serializing CSSOM objects to text (requirements §6).

The pretty format is the one calibre writes: each rule starts on its own line,
one declaration per line, every declaration ends with a semicolon, closing
braces at the rule's own indent, nested rules indented one more level.

All text goes through one token serializer. It always produces well-formed
output: unclosed brackets, strings and comments are closed, and bad strings
keep the newline that ends them. So a value can never swallow what follows
it, and serializing is idempotent. With normalization it also applies the
calibre normalizations (S3). Escapes are always kept as written.
"""

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from kgcss.cssom.values import HEX_ESCAPE_AT_END
from kgcss.decode import PYTHON_CODECS, lookup_encoding
from kgcss.escaping import serialize_identifier, serialize_string
from kgcss.syntax import Block, Function, Token, parse_component_value_list
from kgcss.tokenizer import TokenType

if TYPE_CHECKING:
    from kgcss.cssom.declarations import DeclarationBlock, Property
    from kgcss.cssom.rules import Rule
    from kgcss.cssom.stylesheet import Stylesheet


@dataclass(frozen=True, slots=True, kw_only=True)
class SerializeOptions:
    """Options for serialize(). They apply to one call only, nothing is global."""

    # 'pretty' writes the normalised calibre format. 'preserve' writes
    # unmodified rules and declarations exactly as they were in the source.
    style: Literal['pretty', 'preserve'] = 'pretty'
    # The indent for each nesting level, for example the editor's tab width (S1)
    indent: str = '  '
    # The S3 normalisations: quotes, url(), numbers, colors, function names, whitespace
    normalize: bool = True
    # Leave out rules without declarations or child rules (calibre's behaviour)
    drop_empty_rules: bool = True
    keep_comments: bool = True


DEFAULT_OPTIONS = SerializeOptions()
CSS_WHITESPACE = ' \t\n\r\f'
# Units of <length>, whose zero values can be written without a unit
LENGTH_UNITS = frozenset((
    'px', 'em', 'rem', 'ex', 'rex', 'ch', 'rch', 'cap', 'rcap', 'ic', 'ric', 'lh', 'rlh', 'pt', 'pc', 'in', 'cm',
    'mm', 'q', 'vw', 'vh', 'vi', 'vb', 'vmin', 'vmax', 'svw', 'svh', 'lvw', 'lvh', 'dvw', 'dvh',
))  # fmt: skip
URL_UNSAFE = frozenset(' \t\n\r\f"\'()\\') | {chr(i) for i in range(0x20)} | {'\x7f'}
COMBINATORS = frozenset('>+~')
# Tokens that can end with an escape
ESCAPABLE = frozenset((TokenType.IDENT, TokenType.AT_KEYWORD, TokenType.HASH, TokenType.DIMENSION))


class _Unsafe(Exception):
    """Raised in attribute-safe mode for values that cannot be written on one line."""


def serialize_string_single_quoted(text: str) -> str:
    """Like serialize_string(), with single quotes, and with " escaped, for use inside double-quoted HTML attributes."""
    out = ["'"]
    for c in text:
        if c == '\0':
            out.append('�')
        elif c < ' ' or c == '\x7f':
            out.append(f'\\{ord(c):x} ')
        elif c in "'\\":
            out.append('\\' + c)
        elif c == '"':
            out.append('\\22 ')
        else:
            out.append(c)
    out.append("'")
    return ''.join(out)


def format_url(url: str, attribute_safe: bool) -> str:
    if url and not any(c in URL_UNSAFE for c in url):
        return f'url({url})'
    return f'url({serialize_string_single_quoted(url) if attribute_safe else serialize_string(url)})'


def format_number(tok: Token, top_level: bool) -> str:
    """Normalise a NUMBER, PERCENTAGE or DIMENSION: .5 -> 0.5, 1.0 -> 1, 010 -> 10, -0.0 -> 0, 0px -> 0, +1px kept."""
    rep = tok.representation or ''
    v = tok.value
    assert isinstance(v, int | float)
    if isinstance(v, float) and (v != v or v in (float('inf'), float('-inf'))):
        s = rep
    elif v == 0:
        s = '0'
    elif isinstance(v, int) or (v.is_integer() and abs(v) < 1e16):
        s = str(int(v))
    else:
        s = repr(v)
        if 'e' in s:
            s = rep
    if s != rep and rep.startswith('+') and v > 0:
        s = '+' + s
    match tok.type:
        case TokenType.PERCENTAGE:
            return s + '%'
        case TokenType.DIMENSION:
            unit_text = tok.source_text[len(rep) :]
            unit = str(tok.unit)
            if v == 0 and top_level and unit.isascii() and unit.lower() in LENGTH_UNITS:
                return '0'
            return s + unit_text
    return s


def shorten_color(tok: Token) -> str:
    text = tok.source_text
    value = str(tok.value)
    if text == '#' + value and len(value) in (6, 8) and all(c in '0123456789abcdefABCDEF' for c in value):
        pairs = [value[i : i + 2] for i in range(0, len(value), 2)]
        if all(p[0] == p[1] for p in pairs):
            return '#' + ''.join(p[0] for p in pairs)
    return text


def reparses_as_declaration(selector: str) -> bool:
    """Whether a nested rule with this selector would be parsed as a declaration: an ident and a colon."""
    significant = [
        v
        for v in parse_component_value_list(selector)
        if not (isinstance(v, Token) and v.type in (TokenType.WHITESPACE, TokenType.COMMENT))
    ]
    return (
        len(significant) == 2
        and isinstance(significant[0], Token)
        and significant[0].type is TokenType.IDENT
        and isinstance(significant[1], Token)
        and significant[1].type is TokenType.COLON
    )


def could_merge(c: str, after: bool = False) -> bool:
    """Whether a character next to a rewritten number could become part of it, or the reverse."""
    if not c:
        return False
    return c.isalnum() or ord(c) >= 0x80 or c in '-_\\.+' or (after and c == '%')


def is_lone_backslash(tok: Token) -> bool:
    return tok.type is TokenType.DELIM and tok.source_text == '\\'


class TokenWriter:
    """Serializes component values. Iterative, so deep nesting cannot overflow the stack."""

    def __init__(
        self,
        normalize: bool,
        *,
        mode: Literal['value', 'selector', 'verbatim'] = 'value',
        attribute_safe: bool = False,
        keep_comments: bool = True,
    ) -> None:
        self.normalize = normalize and mode != 'verbatim'
        self.mode = mode
        self.attribute_safe = attribute_safe
        self.keep_comments = keep_comments and not attribute_safe
        # True if the last significant output is a bad string or lone backslash, which must be
        # followed by a newline
        self.ends_needing_newline = False
        # Where the significant output starts and ends: whitespace outside them came from
        # whitespace tokens and can be trimmed, unlike whitespace inside a token (\\ )
        self.significant_start = self.significant_end = 0

    def write(self, values: Iterable[Token | Block | Function]) -> str:
        out: list[str] = []
        # Each level: (iterator over items, closer to write when done, the kind of block)
        stack: list[tuple[Iterator[Token | Block | Function], str, str]] = [(iter(values), '', '')]
        pending_space = False
        at_start = True  # right after the start, an opener or a comma: no space needed
        # A bad string or lone backslash was just written: it is ended by a newline, which
        # must come before whatever is written next
        need_newline = False
        length = 0
        first_significant: int | None = None
        last_needs_newline = False

        def emit(text: str, significant: bool = True) -> None:
            nonlocal need_newline, pending_space, length, first_significant, last_needs_newline
            if need_newline:
                need_newline = False
                if not text.startswith(('\n', '\r', '\f')):
                    out.append('\n')
                    length += 1
                    pending_space = False
            if significant and first_significant is None:
                first_significant = length
            out.append(text)
            length += len(text)
            if significant:
                self.significant_end = length
                last_needs_newline = False

        def space() -> None:
            nonlocal length
            out.append(' ')
            length += 1

        while stack:
            it, closer, kind = stack[-1]
            item = next(it, None)
            if item is None:
                stack.pop()
                if closer:
                    emit(closer)
                    pending_space, at_start = False, False
                continue
            top_level = len(stack) == 1
            if isinstance(item, Token):
                t = item.type
                if t is TokenType.WHITESPACE:
                    if self.normalize:
                        if need_newline:
                            emit('\n', False)
                        else:
                            pending_space = True
                    elif self.attribute_safe:
                        # One line in attribute-safe mode, where nothing needs a newline
                        if not (out and out[-1].endswith(' ')):
                            emit(' ', False)
                    else:
                        emit(item.source_text, False)
                    continue
                if t is TokenType.COMMENT:
                    if not self.keep_comments:
                        continue
                    text = f'/*{item.value}*/'
                elif self.normalize:
                    # Rewriting a number changes its first and last characters, which could merge with
                    # neighbours that touch it, as in \\ -.5em: only do it when that cannot happen
                    left_ok = at_start or pending_space or not out or not could_merge(out[-1][-1:])
                    right = item.stream.source[item.end : item.end + 1]
                    text = self.normalized_token(item, top_level, left_ok and not could_merge(right, True))
                else:
                    text = self.verbatim_token(item)
                if self.normalize:
                    if t is TokenType.COMMA:
                        emit(',')
                        # A space after commas, except inside attribute selectors
                        pending_space = self.mode == 'value' or kind != '['
                        at_start = False
                        continue
                    if self.mode == 'selector' and t is TokenType.DELIM and text in COMBINATORS and kind != '[':
                        if not at_start:
                            space()
                        emit(text)
                        pending_space, at_start = True, False
                        continue
                    in_attribute_operator = self.mode == 'selector' and kind == '[' and t is TokenType.DELIM
                    if pending_space and not at_start and not in_attribute_operator and not need_newline:
                        space()
                    if in_attribute_operator:
                        # No spaces around the operators of attribute selectors
                        emit(text)
                        pending_space, at_start = False, True
                        continue
                emit(text)
                pending_space, at_start = False, False
                need_newline = last_needs_newline = t is TokenType.BAD_STRING or is_lone_backslash(item)
                continue
            # A function or block
            if self.normalize and pending_space and not at_start and not need_newline:
                space()
            if isinstance(item, Function):
                url = self.url_function(item)
                if url is not None:
                    emit(url)
                    pending_space, at_start = False, False
                    continue
                name_text = item.source_text[: item.arguments.pos - item.pos]
                if self.normalize:
                    name_text = name_text.rstrip(CSS_WHITESPACE)  # url( with whitespace is handled above
                    if item.name.isascii() and not item.name.startswith('--') and name_text == item.name + '(':
                        name_text = item.name.lower() + '('
                emit(name_text)
                stack.append((iter(item.arguments), ')', '('))
            else:
                emit(item.opener)
                stack.append((iter(item.content), {'{': '}', '[': ']', '(': ')'}[item.opener], item.opener))
            pending_space, at_start = False, True
        self.ends_needing_newline = last_needs_newline
        self.significant_start = length if first_significant is None else first_significant
        return ''.join(out)

    def url_function(self, item: Function) -> str | None:
        name = item.name
        if not (name.isascii() and name.lower() == 'url'):
            return None
        args = [
            a
            for a in item.arguments
            if not (isinstance(a, Token) and a.type in (TokenType.WHITESPACE, TokenType.COMMENT))
        ]
        if len(args) != 1 or not isinstance(args[0], Token) or args[0].type is not TokenType.STRING:
            return None
        if self.normalize:
            return format_url(str(args[0].value), self.attribute_safe)
        if not item.closed or not args[0].closed:
            return f'url({serialize_string(str(args[0].value))})'
        return None

    def verbatim_token(self, tok: Token) -> str:
        """The source text of a token, closing it if it was ended by EOF."""
        text = self.verbatim_text(tok)
        if self.attribute_safe and (
            '"' in text
            or tok.type is TokenType.BAD_STRING
            or is_lone_backslash(tok)
            or any(c in text for c in '\n\r\f')  # a bad url can contain newlines
        ):
            raise _Unsafe
        return text

    def verbatim_text(self, tok: Token) -> str:
        text = tok.source_text
        if not tok.closed:
            match tok.type:
                case TokenType.STRING:
                    return serialize_string(str(tok.value))
                case TokenType.URL:
                    return format_url(str(tok.value), False)
                case TokenType.COMMENT:
                    return text + '*/'
                case TokenType.BAD_URL:
                    return text + ')'
        if tok.type in ESCAPABLE and text.endswith('\\') and (len(text) - len(text.rstrip('\\'))) % 2:
            # A backslash at the end of the input is an escape for U+FFFD. Anything written
            # after it would be escaped instead, so use the explicit form.
            text = text[:-1] + '\\fffd '
        return text

    def normalized_token(self, tok: Token, top_level: bool, isolated: bool) -> str:
        t = tok.type
        safe = self.attribute_safe
        if safe and (t is TokenType.BAD_STRING or is_lone_backslash(tok)):
            raise _Unsafe
        match t:
            case TokenType.STRING:
                value = str(tok.value)
                return serialize_string_single_quoted(value) if safe else serialize_string(value)
            case TokenType.URL:
                return format_url(str(tok.value), safe)
            case TokenType.NUMBER | TokenType.PERCENTAGE | TokenType.DIMENSION if self.mode == 'value' and isolated:
                return format_number(tok, top_level)
            case TokenType.HASH if self.mode == 'value':
                return shorten_color(tok)
        text = self.verbatim_text(tok)
        if safe:
            # A " can only be an escape here (strings were handled above): use the hex form
            text = text.replace('\\"', '\\22 ')
            if '"' in text or any(c in text for c in '\n\r\f'):
                raise _Unsafe
        return text


def write_values(
    values: Iterable[Token | Block | Function],
    options: SerializeOptions,
    *,
    mode: Literal['value', 'selector', 'verbatim'] = 'value',
    attribute_safe: bool = False,
) -> str:
    """Serialize component values, stripped of surrounding whitespace."""
    w = TokenWriter(options.normalize, mode=mode, attribute_safe=attribute_safe, keep_comments=options.keep_comments)
    text = w.write(values)
    # Only whitespace from whitespace tokens is trimmed, never the space ending an escape
    text = text[w.significant_start : max(w.significant_start, w.significant_end)]
    if HEX_ESCAPE_AT_END.search(text):
        # The canonical form ends a final hex escape with its space, otherwise whatever is
        # written next (' {', ' !important') would be read back as part of the escape
        text += ' '
    if w.ends_needing_newline:
        # A bad string or lone backslash is ended by a newline, which must stay
        text += '\n'
    return text


def normalize_selector(text: str, options: SerializeOptions = DEFAULT_OPTIONS) -> str:
    """Normalise the whitespace of a selector list (requirements 3.3), keeping escapes, case and comments."""
    return write_values(parse_component_value_list(text), options, mode='selector')


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------


def property_value_text(prop: Property, options: SerializeOptions, attribute_safe: bool = False) -> str:
    node = prop._node
    if node is not None and prop._value_from_node:
        values: Iterable[Token | Block | Function] = node.value
    else:
        values = parse_component_value_list(prop.value)
    if prop.name.startswith('--'):
        # Custom property values are written exactly, apart from closing what is unclosed
        return write_values(values, options, mode='verbatim', attribute_safe=attribute_safe)
    return write_values(values, options, attribute_safe=attribute_safe)


def property_text(prop: Property, options: SerializeOptions, attribute_safe: bool = False) -> str:
    """name: value, with !important if set."""
    if options.style == 'preserve' and not prop._dirty and prop._node is not None and not attribute_safe:
        return prop._node.source_text
    value = property_value_text(prop, options, attribute_safe)
    # Names are stored unescaped, so they are escaped as needed: \31 x is stored as 1x
    name = serialize_identifier(prop.name)
    sep = ': ' if value else ':'
    if options.normalize or prop._node is None:
        priority = ' !important' if prop.important else ''
    else:
        priority = (' ' + prop.literal_priority) if prop.important else ''
    return f'{name}{sep}{value}{priority}'


def declaration_lines(block: DeclarationBlock, options: SerializeOptions) -> list[str]:
    """One line per declaration (ending with ;) or comment, without indentation."""
    from kgcss.cssom.declarations import Property

    lines: list[str] = []
    for item in block.items:
        if isinstance(item, Property):
            lines.append(property_text(item, options) + ';')
        elif options.keep_comments:
            lines.append(f'/*{item.text}*/')
    return lines


def declarations_single_line(block: DeclarationBlock, options: SerializeOptions) -> str:
    """Declarations on one line, safe inside a double-quoted HTML attribute."""
    from kgcss.cssom.declarations import Property

    parts: list[str] = []
    for item in block.items:
        if isinstance(item, Property):
            try:
                parts.append(property_text(item, options, attribute_safe=True))
            except _Unsafe:
                continue
    return '; '.join(parts)


def has_declarations(block: DeclarationBlock) -> bool:
    from kgcss.cssom.declarations import Property

    return any(isinstance(i, Property) for i in block.items)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class Writer:
    def __init__(self, options: SerializeOptions) -> None:
        self.options = options
        self.preserve = options.style == 'preserve'

    def indent(self, depth: int) -> str:
        return self.options.indent * depth

    def unchanged_source(self, rule: Rule) -> str | None:
        if self.preserve and not rule._dirty and rule._source is not None:
            return rule._source.source_text
        return None

    def block(self, header: str, body: list[str], depth: int, empty: bool) -> list[str]:
        if empty and self.options.drop_empty_rules:
            return []
        ind = self.indent(depth)
        return [ind + header + ' {', *body, ind + '}']

    def decl_lines(self, block: DeclarationBlock, depth: int) -> list[str]:
        ind = self.indent(depth)
        return [ind + line for line in declaration_lines(block, self.options)]

    def rule_lines(self, rule: Rule, depth: int) -> list[str]:
        from kgcss.cssom import rules as r

        if (source := self.unchanged_source(rule)) is not None:
            return [self.indent(depth) + source]
        ind = self.indent(depth)
        opts = self.options
        match rule:
            case r.StyleRule():
                if rule.parent_rule is not None and reparses_as_declaration(rule.selector_text):
                    # A nested rule with the selector "a:" would be read back as a declaration whose
                    # value is a {} block. Such a selector is invalid, so the rule is left out.
                    return []
                body = self.decl_lines(rule.style, depth + 1)
                children, has_content = self.rules_with_content(rule.rules, depth + 1)
                empty = not has_declarations(rule.style) and not has_content
                return self.block(rule.selector_text, body + children, depth, empty)
            case r.NestedDeclarationsRule():
                if opts.drop_empty_rules and not has_declarations(rule.style):
                    return []
                return self.decl_lines(rule.style, depth)
            case r.MediaRule():
                return self.grouping(rule, '@media', rule.media.media_text, depth)
            case r.GroupingRule():
                return self.grouping(rule, '@' + rule.at_keyword_name, rule.prelude_text, depth)
            case r.FontFaceRule():
                return self.block(
                    '@font-face', self.decl_lines(rule.style, depth + 1), depth, not has_declarations(rule.style)
                )
            case r.PageRule():
                body = self.decl_lines(rule.style, depth + 1)
                margins = self.rules_lines(rule.rules, depth + 1)
                header = '@page' + (' ' + rule.selector_text if rule.selector_text else '')
                return self.block(header, body + margins, depth, not has_declarations(rule.style) and not margins)
            case r.MarginRule():
                return self.block(
                    '@' + rule.name, self.decl_lines(rule.style, depth + 1), depth, not has_declarations(rule.style)
                )
            case r.CharsetRule():
                return [f'{ind}@charset {serialize_string(rule.encoding)};']
            case r.ImportRule():
                return [ind + import_text(rule, opts)]
            case r.NamespaceRule():
                prefix = f'{rule.prefix} ' if rule.prefix else ''
                return [f'{ind}@namespace {prefix}{format_url(rule.namespace_uri, False)};']
            case r.LayerStatementRule():
                return [f'{ind}@layer {", ".join(rule.names)};']
            case r.Comment():
                return [f'{ind}/*{rule.text}*/'] if opts.keep_comments else []
            case r.UnknownAtRule():
                return [ind + unknown_at_rule_text(rule, opts)]
        raise TypeError(f'Cannot serialize {rule!r}')

    def grouping(self, rule: Rule, keyword: str, prelude: str, depth: int) -> list[str]:
        children = rule.child_rules()
        assert children is not None
        lines, has_content = self.rules_with_content(children, depth + 1)
        header = keyword + (' ' + prelude if prelude else '')
        return self.block(header, lines, depth, not has_content)

    def rules_lines(self, rules: Iterable[Rule], depth: int) -> list[str]:
        lines, _ = self.rules_with_content(rules, depth)
        return lines

    def rules_with_content(self, rules: Iterable[Rule], depth: int) -> tuple[list[str], bool]:
        """The lines for rules, and whether any of them is more than a comment: a rule containing only
        comments counts as empty, wherever the comments are."""
        from kgcss.cssom.rules import Comment

        lines: list[str] = []
        has_content = False
        for rule in rules:
            rule_lines = self.rule_lines(rule, depth)
            if rule_lines and not isinstance(rule, Comment):
                has_content = True
            lines.extend(rule_lines)
        return lines, has_content

    def sheet(self, sheet: Stylesheet) -> str:
        if self.preserve and not sheet._dirty and sheet._source is not None:
            return sheet._source.source_text
        if not self.preserve:
            return '\n'.join(self.rules_lines(sheet.rules, 0))
        # Preserve: keep the original text between rules that are unchanged and were adjacent
        parts: list[str] = []
        previous: Rule | None = None
        for rule in sheet.rules:
            lines = self.rule_lines(rule, 0)
            if not lines:
                continue
            if parts:
                parts.append(gap_between(previous, rule) or '\n')
            parts.append('\n'.join(lines))
            previous = rule
        return ''.join(parts)


def gap_between(previous: Rule | None, rule: Rule) -> str | None:
    """The original whitespace between two rules, if they are still in source order with only whitespace
    between them. The gap belongs to neither rule, so it is kept even if they were changed."""
    if previous is None or previous._source is None or rule._source is None:
        return None
    a, b = previous._source, rule._source
    if a.stream is not b.stream or a.end > b.pos:
        return None
    gap = a.stream.source[a.end : b.pos]
    return gap if gap and not gap.strip(CSS_WHITESPACE) else None


def import_text(rule: Rule, options: SerializeOptions) -> str:
    from kgcss.cssom.rules import ImportRule

    assert isinstance(rule, ImportRule)
    href = serialize_string(rule.href) if rule._href_is_string else format_url(rule.href, False)
    parts = ['@import', href]
    if rule.layer is not None:
        parts.append(f'layer({rule.layer})' if rule.layer else 'layer')
    if rule.supports is not None:
        parts.append(f'supports({rule.supports})')
    if rule.media.media_text:
        parts.append(rule.media.media_text)
    return ' '.join(parts) + ';'


def unknown_at_rule_text(rule: Rule, options: SerializeOptions) -> str:
    """Unknown at-rules are written exactly as they were, closing anything left open at the end of the input."""
    from kgcss.cssom.rules import UnknownAtRule

    assert isinstance(rule, UnknownAtRule)
    verbatim = SerializeOptions(normalize=False, keep_comments=True)
    if rule._source is not None and not rule._dirty:
        text = write_values(parse_component_value_list(rule._source.source_text), verbatim, mode='verbatim')
        if not text.endswith((';', '}')):
            text += ';'
        return text
    prelude = write_values(parse_component_value_list(rule.prelude_text), verbatim, mode='verbatim')
    head = '@' + rule.name + (' ' + prelude if prelude else '')
    if rule.block_text is None:
        return head + ';'
    block = TokenWriter(False, mode='verbatim').write(parse_component_value_list(rule.block_text))
    return head + ' {' + block + '}'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def serialize(obj: Stylesheet | Rule, options: SerializeOptions = DEFAULT_OPTIONS) -> str:
    """Serialize a stylesheet or a rule to text."""
    from kgcss.cssom.stylesheet import Stylesheet

    writer = Writer(options)
    if isinstance(obj, Stylesheet):
        return writer.sheet(obj)
    return '\n'.join(writer.rule_lines(obj, 0))


def _css_escape(error: UnicodeError) -> tuple[str, int]:
    assert isinstance(error, UnicodeEncodeError)
    bad = error.object[error.start : error.end]
    return ''.join(f'\\{ord(c):x} ' for c in bad), error.end


codecs.register_error('kgcss-css-escape', _css_escape)


def python_codec(encoding: str) -> str:
    """The Python codec to encode with for a WHATWG encoding name."""
    if (name := PYTHON_CODECS.get(encoding)) is not None:
        return name
    if encoding in ('x-user-defined', 'replacement'):
        return 'utf-8'
    try:
        return codecs.lookup(encoding.replace('iso-8859-8-i', 'iso-8859-8')).name
    except LookupError:
        return 'utf-8'


def encode_text(text: str, charset: str | None) -> bytes:
    """Encode text in the encoding named by an @charset rule, or UTF-8. Characters
    that encoding cannot represent are written as CSS escapes (S4)."""
    encoding = lookup_encoding(charset) if charset else None
    codec = python_codec(encoding) if encoding else 'utf-8'
    return text.encode(codec, 'kgcss-css-escape')
