# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Loading and inlining @import rules."""

import posixpath
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit, urlunsplit

from kgcss.syntax import AtRule, QualifiedRule, parse_block_contents
from kgcss.tokenizer import ParseError

if TYPE_CHECKING:
    from kgcss.cssom.rules import GroupingRule, ImportRule, Rule
    from kgcss.cssom.stylesheet import Stylesheet

# Called with the URL of an imported sheet, resolved against the importing sheet's href.
# Returns (encoding, text), where text is str or bytes, or None (or a None text) if the
# sheet does not exist. An empty text is an empty sheet.
# Deep enough that resolving ../ in relative hrefs does not reach its top
CSS_WHITESPACE = ' \t\n\r\f'
PLACEHOLDER_ROOT = 'http://kgcss.invalid/' + 'd/' * 64

type ImportFetcher = Callable[[str], tuple[str | None, str | bytes | None] | None]


def resolved_url(rule: ImportRule) -> str:
    """The href of an @import rule, resolved against the href of its sheet."""
    sheet = rule.parent_style_sheet
    base = None if sheet is None else sheet.href
    return urljoin(base, rule.href) if base else rule.href


def import_chain(sheet: Stylesheet) -> frozenset[str]:
    """The URLs of sheet and of the sheets that import it, directly or not."""
    return sheet._import_chain | {sheet.href} if sheet.href else sheet._import_chain


def is_relative_reference(href: str) -> bool:
    parts = urlsplit(href)
    return not parts.scheme and not parts.netloc and not href.startswith('/')


def rebased_url(url: str, from_href: str, to_href: str | None) -> str:
    """A URL in a sheet at from_href, changed to mean the same in a sheet at to_href: relative if both
    are on the same host, otherwise absolute. Absolute, data: and #fragment URLs are unchanged."""
    if not url or url.startswith('#') or urlsplit(url).scheme:
        return url
    joined = urljoin(from_href, url)
    if to_href is None:
        return joined
    if is_relative_reference(from_href) and is_relative_reference(to_href):
        # Hrefs relative to the same unknown place, such as paths in a book: resolve them against a common root
        from_href, to_href = urljoin(PLACEHOLDER_ROOT, from_href), urljoin(PLACEHOLDER_ROOT, to_href)
    target = urlsplit(urljoin(from_href, url))
    base = urlsplit(to_href)
    if (target.scheme, target.netloc) != (base.scheme, base.netloc) or not target.path.startswith('/'):
        return joined
    path = posixpath.relpath(target.path, posixpath.dirname(base.path) or '/')
    if target.path.endswith('/') and not path.endswith('/'):
        path += '/'
    if path == '.':
        path = './'
    # A first segment with a colon would read as a scheme
    if ':' in path.partition('/')[0]:
        path = './' + path
    return urlunsplit(('', '', path, target.query, target.fragment))


def report(sheet: Stylesheet, rule: Rule, kind: str, message: str) -> None:
    source = rule._source
    if source is None:
        error = ParseError(kind, message)
    else:
        error = ParseError(kind, message, source.pos, source.line, source.column)
    sheet._state.add(error)


def load_import(rule: ImportRule) -> Stylesheet | None:
    """Fetch and parse the sheet imported by rule, using its sheet's fetcher. Problems are reported to the
    importing sheet's errors and log handler, and give None."""
    from kgcss.cssom.stylesheet import parse_stylesheet

    sheet = rule.parent_style_sheet
    if sheet is None or sheet.fetcher is None:
        return None
    url = resolved_url(rule)
    chain = import_chain(sheet)
    if url in chain:
        report(sheet, rule, 'import-loop', f'@import of {url} ignored, it is part of an import loop')
        return None
    try:
        result = sheet.fetcher(url)
    except Exception as e:
        report(sheet, rule, 'import-failed', f'Failed to fetch the sheet imported from {url}: {e}')
        return None
    encoding, text = (None, None) if result is None else result
    if text is None:
        report(sheet, rule, 'import-not-found', f'The sheet imported from {url} was not found')
        return None
    if isinstance(text, str):
        child = parse_stylesheet(text, href=url, log=sheet._state.log, fetcher=sheet.fetcher)
    else:
        child = parse_stylesheet(
            text,
            href=url,
            log=sheet._state.log,
            fetcher=sheet.fetcher,
            protocol_encoding=encoding,
            environment_encoding=sheet.encoding,
        )
    child._import_chain = chain
    child.owner_rule = rule
    if sheet._eager_imports:
        child._eager_imports = True
        load_all(child)
    return child


def load_all(sheet: Stylesheet) -> None:
    """Load every @import of a sheet now, rather than on first access."""
    from kgcss.cssom.rules import ImportRule

    for rule in sheet.rules:
        if isinstance(rule, ImportRule):
            _ = rule.style_sheet


def resolve_imports(sheet: Stylesheet) -> Stylesheet:
    """A copy of sheet with its @import rules replaced by the rules of the imported sheets, as far as
    they can be loaded with the sheet's fetcher.

    Inlined rules are wrapped in @layer, @media and @supports rules for the
    conditions of their @import (in that order, @supports outermost), and
    marked with ``/* START @import url */`` and ``/* END @import url */`` comments.
    Rules that would not be valid nested in those, which browsers ignore, are
    dropped. Relative URLs in imported rules are rewritten to mean the same
    in sheet: relative to its href if on the same host, else absolute.

    The @charset and @namespace rules of imported sheets are dropped, as they
    cannot be nested. Those of sheet are moved before the inlined rules, the
    only place they are valid, so they apply to them too.

    Imports that are part of a loop are dropped. Other imports that cannot
    be loaded stay as @import rules, with their URLs rebased the same way,
    before the first rule of sheet other than @charset, @layer statements and
    comments, the only place they are valid. For an unloadable import inside
    an imported sheet, that loses the conditions of the @import rules that
    led to it.

    The errors of the result are those of sheet and of the imported sheets,
    with positions in their own sheets, then those of resolving.
    """
    from kgcss.cssom.rules import CharsetRule, Comment, ImportRule, LayerStatementRule, NamespaceRule, StyleRule
    from kgcss.cssom.serializer import SerializeOptions, serialize
    from kgcss.cssom.stylesheet import Stylesheet
    from kgcss.cssom.urls import replace_urls

    out = Stylesheet(href=sheet.href, fetcher=sheet.fetcher)
    out.encoding = sheet.encoding
    out._state.log = sheet._state.log
    unresolved: list[ImportRule] = []
    inlined: list[Stylesheet] = []

    def inline(src: Stylesheet, top: bool) -> list[Rule]:
        def copy[R: Rule](rule: R) -> R:
            ans = rule.copy()
            if not top and src.href:
                base = src.href
                replace_urls(ans, lambda u: rebased_url(u, base, sheet.href))
            return ans

        rules: list[Rule] = []
        for rule in src.rules:
            if not isinstance(rule, ImportRule):
                if top or not isinstance(rule, CharsetRule | NamespaceRule):
                    rules.append(copy(rule))
                continue
            url = resolved_url(rule)
            child = rule.style_sheet
            if child is None:
                if url not in import_chain(src):  # a loop imports nothing
                    unresolved.append(copy(rule))
                continue
            inlined.append(child)
            body = inline(child, False)
            preludes = wrapper_preludes(rule)
            if preludes:
                body = nestable(body, url)
            for prelude in preludes:
                wrapper = parse_wrapper(prelude)
                for r in body:
                    wrapper.rules.append(r)
                body = [wrapper]
            label = url.replace('*/', '*%2F')
            rules.append(Comment(f' START @import {label} '))
            rules.extend(body)
            rules.append(Comment(f' END @import {label} '))
        return rules

    def nestable(rules: list[Rule], url: str) -> list[Rule]:
        # Rules parsed at the top level, such as a style rule with ; or } in its selector, can parse
        # as something else once nested. Browsers ignore such rules, so they are dropped.
        ans: list[Rule] = []
        for rule in rules:
            if isinstance(rule, Comment):
                ans.append(rule)
                continue
            text = serialize(rule, SerializeOptions(style='preserve', drop_empty_rules=False))
            nodes = list(parse_block_contents(text))
            whole = len(nodes) == 1 and nodes[0].source_text.strip(CSS_WHITESPACE) == text.strip(CSS_WHITESPACE)
            if whole and isinstance(nodes[0], AtRule if not isinstance(rule, StyleRule) else QualifiedRule):
                ans.append(rule)
            else:
                message = f'{rule.type.value} rule from {url} ignored, it is not valid when nested: {text!r}'
                out._state.add(ParseError('invalid-rule', message))
        return ans

    # The leading rules that may come before @import are copied one to one, so they keep their indices
    head = next(
        (i for i, r in enumerate(sheet.rules) if not isinstance(r, CharsetRule | LayerStatementRule | Comment)),
        len(sheet.rules),
    )
    body = inline(sheet, True)
    # @namespace rules must come before the inlined rules
    namespaces = [r for r in body if isinstance(r, NamespaceRule)]
    rest = [r for r in body[head:] if not isinstance(r, NamespaceRule)]
    for rule in [*body[:head], *unresolved, *namespaces, *rest]:
        out.rules.append(rule)
    # Problems of sheet, including loading its imports, then of the imported sheets, then of resolving
    out._state.errors[:0] = [e for src in (sheet, *inlined) for e in src.errors]
    out._dirty = True
    return out


def wrapper_preludes(rule: ImportRule) -> list[str]:
    """The preludes of the rules to wrap the contents of an imported sheet in, innermost first."""
    preludes = []
    if rule.layer is not None:
        preludes.append(f'@layer {rule.layer}' if rule.layer else '@layer')
    if media := rule.media.media_text:
        preludes.append(f'@media {media}')
    if rule.supports is not None:
        # supports(display: grid) holds a declaration or a condition, both valid in parentheses
        preludes.append(f'@supports ({rule.supports})')
    return preludes


def parse_wrapper(text: str) -> GroupingRule:
    from kgcss.cssom.build import Context, parse_rule_text
    from kgcss.cssom.rules import GroupingRule

    rule = parse_rule_text(text + ' {}', Context.TOP)
    assert isinstance(rule, GroupingRule)
    return rule
