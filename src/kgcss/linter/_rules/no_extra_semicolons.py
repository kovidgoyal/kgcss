# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-extra-semicolons: lib/rules/no-extra-semicolons of @stylistic/stylelint-plugin.

PostCSS keeps the semicolons that close nothing in raws: before a node, after
the last node of a container (raws.after) and after a rule's closing brace
(raws.ownSemicolon). The rule finds them there, places each in the text by
counting semicolons back from the end of its raw, and fixes it by deleting
it. This is the plugin's rule for plain CSS: its parts for other syntaxes
are left out, and so is the warning it gives once for a @charset rule, which
asks for another rule to be turned on rather than reporting a problem.
"""

import re
from typing import Final

from kgcss.linter import TextEdit
from kgcss.linter._engine import Context
from kgcss.linter._messages import EXTRA_SEMICOLON
from kgcss.linter._postcss import Container, Node, Root, Rule, js_trim
from kgcss.linter._utils import has_interpolation, style_search

# The plugin's EVERY_ESCAPE_STRING_OR_COMMENT
RE_ESCAPE_STRING_OR_COMMENT: Final = re.compile(
    r'\\.|//|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'|/\*.*?\*/', re.DOTALL
)


def _blank(m: re.Match[str]) -> str:
    match = m.group()
    if match.startswith('\\') or match == '//':
        return match
    if match.startswith('/*'):
        return ' ' * len(match)
    return match[0] + ' ' * (len(match) - 2) + match[0]


def is_standard_syntax_selector(selector: str) -> bool:
    """The plugin's isStandardSyntaxSelector(), which differs from stylelint's."""
    code = RE_ESCAPE_STRING_OR_COMMENT.sub(_blank, selector)
    if has_interpolation(code):
        return False
    if code.endswith(')') and ':' not in code:  # a Less mixin definition
        return False
    return '<%' not in code and '%>' not in code and '//' not in code


def reads_the_raws_of(node: Node) -> bool:
    """readsTheRawsOf(): every node but a rule with a selector of another syntax."""
    if isinstance(node, Rule):
        return is_standard_syntax_selector(node.selector)
    return True


def semicolons(raw: str | None, *, nonblank: bool = True) -> list[int]:
    if raw is None or (nonblank and not js_trim(raw)):
        return []
    return list(style_search(raw, ';'))


def last_index_of(text: str, char: str, from_index: int) -> int:
    """JavaScript's text.lastIndexOf(char, from_index), which takes a negative from_index as 0."""
    return text.rfind(char, 0, max(from_index, 0) + 1)


def semicolon_offset(text: str, raw_end: int, raw: str, index: int) -> int | None:
    """semicolonOffset(): the semicolon of text with as many semicolons behind it before raw_end."""
    behind = raw.count(';', index + 1)
    offset = last_index_of(text, ';', raw_end - 1)
    while offset >= 0:
        if behind == 0:
            return offset
        behind -= 1
        offset = last_index_of(text, ';', offset - 1)
    return None


def unclosed_chain(node: Node) -> int:
    """How many closing braces PostCSS's stringifier adds that the text lacks, at the end of node."""
    count = 0
    while isinstance(node, Container) and node.nodes is not None:
        if node.end is None:
            count += 1
        last = node.last
        if last is None:
            break
        node = last
    return count


def end_offset(node: Container, text: str) -> int:
    """getEndOffsetByNode(): behind the closing brace or raws.ownSemicolon."""
    end = node.end
    if end is not None:
        expected = ';' if node.raws.own_semicolon else '}'
        if 0 < end <= len(text) and text[end - 1] == expected:
            return end
    # nodeString(node).length: the node runs to the end of the text, where its print adds the braces it lacks
    return len(text) + unclosed_chain(node)


def check(root: Root, ctx: Context) -> None:
    text = root.css

    def complain(index: int) -> None:
        ctx.report(root, EXTRA_SEMICOLON, index=index, end_index=index, fix=TextEdit(index, index + 1, ''))

    def take_out(raw: str | None, found: list[int], base: int) -> None:
        """Report the semicolons found in raw, which ends at base in the text (offsetOf)."""
        if not found or raw is None:
            return
        for semicolon in found:
            at = semicolon_offset(text, base, raw, semicolon)
            complain(base - len(raw) + semicolon if at is None else at)

    after = root.raws.after
    if after is not None and js_trim(after):
        for index in style_search(after, ';'):
            at = semicolon_offset(text, len(text), after, index)
            complain(len(text) - len(after) + index if at is None else at)

    for node in root.walk():
        if not reads_the_raws_of(node):
            continue
        start = node.start or 0
        before = node.raws.before
        take_out(before, semicolons(before), start)
        if not isinstance(node, Container):
            continue
        extra_after = semicolons(node.raws.after)
        own = node.raws.own_semicolon
        extra_own = semicolons(own, nonblank=False)
        end = end_offset(node, text) if extra_after or extra_own else 0
        own_length = len(own or '')
        take_out(node.raws.after, extra_after, end - own_length - 1)
        take_out(own, extra_own, end)
