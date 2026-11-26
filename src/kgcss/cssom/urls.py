# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Finding and rewriting URLs.

URLs are found at token level: url(x) and url("x") anywhere, at any depth
of functions (image-set(), cross-fade()), the strings of @import rules, and
strings directly inside image-set(), which are URLs too. @namespace URIs are
not links and are left alone. Rewriting splices new text in at the offsets
of those tokens, so everything else stays exactly as it was.
"""

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from kgcss._kgcss import find_urls as c_find_urls
from kgcss.cssom.declarations import DeclarationBlock, Property
from kgcss.cssom.rules import (
    GroupingRule,
    ImportRule,
    LazyBlockRule,
    MediaRule,
    NestedDeclarationsRule,
    Rule,
    UnknownAtRule,
)
from kgcss.cssom.serializer import format_url
from kgcss.cssom.stylesheet import Stylesheet
from kgcss.escaping import serialize_string
from kgcss.syntax import Block, ComponentValueList, Function, Token, parse_component_value_list
from kgcss.tokenizer import TokenType

type URLMapper = Callable[[str], str]
IMAGE_SET = frozenset(('image-set', '-webkit-image-set'))
IGNORED = frozenset((TokenType.WHITESPACE, TokenType.COMMENT))


@dataclass(frozen=True, slots=True)
class URLSpan:
    """Where a URL is in a text: [start, end) is replaced when it is rewritten."""

    start: int
    end: int
    url: str
    # 'url' for url(...), whether quoted or not, 'string' for a bare string
    kind: Literal['url', 'string']

    def replacement(self, url: str) -> str:
        return format_url(url, False) if self.kind == 'url' else serialize_string(url)


def _lower(name: str) -> str:
    return name.lower() if name.isascii() else name


def find_urls(values: ComponentValueList, *, top_level_rules: bool = False) -> list[URLSpan]:
    """The URLs in component values, in order. With top_level_rules the values are a whole stylesheet,
    and the strings of @import rules count as URLs while @namespace rules are skipped.

    This uses the C version (csrc/urls.c) of python_find_urls(), the reference implementation.
    """
    return [URLSpan(*span) for span in c_find_urls(values, top_level_rules)]


def python_find_urls(values: ComponentValueList, *, top_level_rules: bool = False) -> list[URLSpan]:
    """find_urls() in Python, the reference for the C version."""
    spans: list[URLSpan] = []
    # Each level: an iterator over items, and the lowercased function name if the level is a function
    stack: list[tuple[Iterator[Token | Block | Function], str]] = [(iter(values), '')]
    # At the top level of a sheet: 'import' after @import, until its URL; 'namespace' until the ;
    pending_rule = ''
    while stack:
        it, function = stack[-1]
        item = next(it, None)
        if item is None:
            stack.pop()
            continue
        at_top = top_level_rules and len(stack) == 1
        if isinstance(item, Function):
            name = _lower(item.name)
            if pending_rule == 'namespace' and at_top:
                continue
            args = [a for a in item.arguments if not (isinstance(a, Token) and a.type in IGNORED)]
            if name == 'url' and len(args) == 1 and isinstance(args[0], Token) and args[0].type is TokenType.STRING:
                spans.append(URLSpan(item.pos, item.end, str(args[0].value), 'url'))
                if at_top:
                    pending_rule = ''
                continue
            stack.append((iter(item.arguments), name))
            continue
        if isinstance(item, Block):
            if at_top:
                pending_rule = ''
            stack.append((iter(item.content), ''))
            continue
        t = item.type
        if at_top:
            if t is TokenType.AT_KEYWORD:
                name = _lower(str(item.value))
                pending_rule = name if name in ('import', 'namespace') else ''
                continue
            if t is TokenType.SEMICOLON:
                pending_rule = ''
                continue
            if pending_rule == 'namespace':
                continue
            if pending_rule == 'import' and t is TokenType.STRING:
                spans.append(URLSpan(item.pos, item.end, str(item.value), 'string'))
                pending_rule = ''
                continue
            if pending_rule == 'import' and t not in IGNORED and t is not TokenType.URL:
                pending_rule = ''
        if t is TokenType.URL:
            spans.append(URLSpan(item.pos, item.end, str(item.value), 'url'))
            if at_top:
                pending_rule = ''
        elif t is TokenType.STRING and function in IMAGE_SET:
            spans.append(URLSpan(item.pos, item.end, str(item.value), 'string'))
    return spans


def might_have_urls(text: str) -> bool:
    """A quick check that skips the tokenizer for text that cannot contain a URL."""
    lower = text.lower()
    return 'url(' in lower or 'image-set' in lower or '@import' in lower or '\\' in text


def splice(text: str, spans: Iterable[URLSpan], fn: URLMapper) -> str:
    """text with the URLs in spans replaced by fn(url). The same object if nothing changed."""
    parts: list[str] = []
    pos = 0
    for span in spans:
        new = fn(span.url)
        if new == span.url:
            continue
        parts.append(text[pos : span.start])
        parts.append(span.replacement(new))
        pos = span.end
    if not parts:
        return text
    parts.append(text[pos:])
    return ''.join(parts)


def replace_urls_in_text(css: str, fn: URLMapper, *, style_attribute: bool = False) -> str:
    """Rewrite the URLs in CSS text, leaving everything else exactly as it was.

    fn is called with each URL, unquoted and unescaped, and returns its
    replacement; quotes are added if the new URL needs them. With
    style_attribute the text is a declaration list (a style="" attribute)
    rather than a stylesheet. Returns css itself if nothing changed.
    """
    if not might_have_urls(css):
        return css
    return splice(css, find_urls(parse_component_value_list(css), top_level_rules=not style_attribute), fn)


def _text_urls(text: str) -> list[URLSpan]:
    return find_urls(parse_component_value_list(text)) if might_have_urls(text) else []


def _properties(block: DeclarationBlock) -> Iterator[Property]:
    for item in block.items:
        if isinstance(item, Property):
            yield item


def _blocks_and_rules(obj: Stylesheet | Rule | DeclarationBlock) -> Iterator[Rule | DeclarationBlock]:
    """The rules in obj, at every depth, and their declaration blocks, in order, leaving out rules
    that certainly have no URL, without parsing their blocks."""
    if isinstance(obj, DeclarationBlock):
        yield obj
        return
    stack: list[Iterator[Rule]] = [iter(obj.rules if isinstance(obj, Stylesheet) else (obj,))]
    while stack:
        rule = next(stack[-1], None)
        if rule is None:
            stack.pop()
            continue
        if _has_no_urls(rule):
            continue
        yield rule
        if isinstance(rule, LazyBlockRule | NestedDeclarationsRule):
            yield rule.style
        if children := rule._children():
            stack.append(iter(children))


def _has_no_urls(rule: Rule) -> bool:
    """Whether a rule and the rules in it certainly have no URLs: it is unchanged and its source text,
    which covers its block and the rules in it, has nothing like a URL. Most rules of a sheet are
    like that, and skipping them saves parsing their blocks."""
    source = rule._source
    return not rule._dirty and source is not None and not might_have_urls(source.source_text)


def _rule_texts(rule: Rule) -> list[str]:
    """Texts of a rule, other than its declarations, that can contain URLs."""
    if isinstance(rule, UnknownAtRule):
        return [rule.prelude_text] + ([rule.block_text] if rule.block_text is not None else [])
    if isinstance(rule, GroupingRule) and not isinstance(rule, MediaRule):
        return [rule.prelude_text]  # @supports (background: url(x)) and the like
    return []


def iter_urls(obj: Stylesheet | Rule | DeclarationBlock, *, ignore_imports: bool = False) -> Iterator[str]:
    """Every URL in a sheet, rule or declaration block, in order.

    This covers @import, all declarations (including in @page, margin rules,
    @font-face and nested rules, at any depth), the conditions of @supports
    and similar rules, unknown at-rules, and URLs inside functions such as
    image-set(). data: URLs are included.
    """
    for item in _blocks_and_rules(obj):
        if isinstance(item, DeclarationBlock):
            for prop in _properties(item):
                for span in _text_urls(prop.value):
                    yield span.url
        elif isinstance(item, ImportRule):
            if not ignore_imports:
                yield item.href
        else:
            for text in _rule_texts(item):
                for span in _text_urls(text):
                    yield span.url


def replace_urls(obj: Stylesheet | Rule | DeclarationBlock, fn: URLMapper, *, ignore_imports: bool = False) -> bool:
    """Replace every URL in a sheet, rule or declaration block with fn(url).

    fn is called with each URL, unquoted and unescaped, and returns its
    replacement; quotes are added if the new URL needs them. Only the
    properties and attributes that contain a changed URL are modified, so in
    preserve serialization the other rules and declarations keep their exact
    text. To change nothing but the URLs in a text, use
    replace_urls_in_text(). Returns True if anything changed.
    """
    changed = False
    for item in _blocks_and_rules(obj):
        if isinstance(item, DeclarationBlock):
            for prop in _properties(item):
                text = prop.value
                new = splice(text, _text_urls(text), fn)
                if new is not text:
                    # Only url() and string tokens were replaced, so the structure is unchanged
                    prop._replace_value(new)
                    changed = True
        elif isinstance(item, ImportRule):
            if not ignore_imports and (href := fn(item.href)) != item.href:
                item.href = href
                changed = True
        elif isinstance(item, UnknownAtRule):
            prelude = splice(item.prelude_text, _text_urls(item.prelude_text), fn)
            if prelude is not item.prelude_text:
                item.prelude_text = prelude
                changed = True
            if (block := item.block_text) is not None and (
                new_block := splice(block, _text_urls(block), fn)
            ) is not block:
                item.block_text = new_block
                changed = True
        elif isinstance(item, GroupingRule) and not isinstance(item, MediaRule):
            prelude = splice(item.prelude_text, _text_urls(item.prelude_text), fn)
            if prelude is not item.prelude_text:
                item.prelude_text = prelude
                changed = True
    return changed


def remove_url_values(block: DeclarationBlock, predicate: Callable[[str], bool]) -> bool:
    """Remove the parts of values that contain a URL for which predicate is true, and declarations
    left with nothing. A part is a top level component of the value, such as
    url(x) or image-set(...). Returns True if anything changed."""
    changed = False
    for prop in list(_properties(block)):
        if not might_have_urls(prop.value):
            continue
        value = prop.parsed_value
        doomed = [
            i for i, component in enumerate(value) if any(predicate(s.url) for s in _text_urls(component.css_text))
        ]
        if not doomed:
            continue
        changed = True
        if len(doomed) == len(value):
            block.remove_declaration(prop)
            continue
        for i in reversed(doomed):
            prop.remove_component(i)
    return changed
