# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The object model: @import."""

import logging
from collections.abc import Mapping

import pytest

from kgcss import (
    ImportRule,
    ParseError,
    RuleType,
    SerializeOptions,
    StyleRule,
    Stylesheet,
    parse_stylesheet,
    replace_urls,
    resolve_imports,
    serialize,
)
from kgcss.cssom.imports import rebased_url

PRESERVE = SerializeOptions(style='preserve')


class Fetcher:
    """Serves sheets from a dict, recording the URLs asked for. An exception value is raised."""

    def __init__(self, files: Mapping[str, str | bytes | Exception | None], encoding: str | None = None) -> None:
        self.files = files
        self.encoding = encoding
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[str | None, str | bytes | None] | None:
        self.calls.append(url)
        if url not in self.files:
            return None
        text = self.files[url]
        if isinstance(text, Exception):
            raise text
        return self.encoding, text


def imports(sheet: Stylesheet) -> list[ImportRule]:
    return [r for r in sheet.rules if isinstance(r, ImportRule)]


def selectors(sheet: Stylesheet | None) -> list[str]:
    assert sheet is not None
    return [r.selector_text for r in sheet.iter_rules() if isinstance(r, StyleRule)]


def test_lazy_loading() -> None:
    fetch = Fetcher({'http://x/css/a.css': 'a { b: c }', 'http://x/b.css': 'b { c: d }'})
    css = '@import "a.css"; @import url(../b.css) print;'
    sheet = parse_stylesheet(css, href='http://x/css/main.css', fetcher=fetch)
    assert fetch.calls == []
    a, b = imports(sheet)
    child = a.style_sheet
    assert fetch.calls == ['http://x/css/a.css']
    assert child is not None
    assert child is a.style_sheet
    assert fetch.calls == ['http://x/css/a.css']  # fetched once
    assert child.href == 'http://x/css/a.css'
    assert child.owner_rule is a
    assert child.fetcher is fetch
    assert selectors(child) == ['a']
    assert selectors(b.style_sheet) == ['b']
    assert fetch.calls == ['http://x/css/a.css', 'http://x/b.css']
    assert sheet.owner_rule is None
    assert sheet.errors == []


def test_eager_loading_is_recursive() -> None:
    fetch = Fetcher({'http://x/a.css': '@import "sub/b.css";', 'http://x/sub/b.css': '@import "c.css"; b {}'})
    fetch.files = {**fetch.files, 'http://x/sub/c.css': 'c {}'}
    sheet = parse_stylesheet('@import "a.css";', href='http://x/', fetcher=fetch, eager_imports=True)
    assert fetch.calls == ['http://x/a.css', 'http://x/sub/b.css', 'http://x/sub/c.css']
    # nested imports resolve against the importing sheet
    a = imports(sheet)[0].style_sheet
    assert a is not None
    b = imports(a)[0].style_sheet
    assert b is not None
    assert selectors(imports(b)[0].style_sheet) == ['c']
    assert len(fetch.calls) == 3


def test_without_fetcher_or_href() -> None:
    sheet = parse_stylesheet('@import "a.css";')
    assert imports(sheet)[0].style_sheet is None
    assert sheet.errors == []
    fetch = Fetcher({'a.css': 'a {}'})
    sheet = parse_stylesheet('@import "a.css";', fetcher=fetch)
    assert selectors(imports(sheet)[0].style_sheet) == ['a']
    assert fetch.calls == ['a.css']  # no href: the URL as written
    # a rule that is not in a sheet has nothing to load with
    assert ImportRule('a.css').style_sheet is None


def test_missing_and_empty() -> None:
    fetch = Fetcher({'http://x/empty.css': '', 'http://x/none.css': None})
    css = '@import "empty.css";\n  @import "none.css";\n@import "gone.css";'
    sheet = parse_stylesheet(css, href='http://x/', fetcher=fetch)
    empty, none, gone = imports(sheet)
    child = empty.style_sheet
    assert child is not None
    assert len(child.rules) == 0
    assert none.style_sheet is None
    assert gone.style_sheet is None
    errors = [(e.kind, e.line, e.column) for e in sheet.errors]
    assert errors == [('import-not-found', 2, 3), ('import-not-found', 3, 1)]
    assert 'http://x/gone.css' in sheet.errors[1].message
    # a missing sheet is not fetched again, and the rule is kept
    assert none.style_sheet is None
    assert fetch.calls.count('http://x/none.css') == 1
    assert serialize(sheet, PRESERVE) == css


def test_fetcher_returning_none() -> None:
    sheet = parse_stylesheet('@import "a.css";', fetcher=lambda url: None)
    assert imports(sheet)[0].style_sheet is None
    assert [e.kind for e in sheet.errors] == ['import-not-found']


def test_bytes_and_encodings() -> None:
    files: dict[str, str | bytes | Exception | None] = {
        'http://x/a.css': 'a::after { content: "\xe9" }'.encode('latin-1'),
        'http://x/b.css': '@charset "iso-8859-7"; b::after { content: "\xe1" }'.encode('latin-1'),
    }
    # the encoding from the fetcher is the protocol encoding
    sheet = parse_stylesheet('@import "a.css";', href='http://x/', fetcher=Fetcher(files, 'iso-8859-1'))
    child = imports(sheet)[0].style_sheet
    assert child is not None
    assert child.encoding == 'windows-1252'
    assert 'content: "\xe9"' in child.css_text
    # without one, @charset, then the importing sheet's encoding (the environment encoding)
    sheet = parse_stylesheet(b'@import "a.css"; @import "b.css";', href='http://x/', fetcher=Fetcher(files))
    a, b = (r.style_sheet for r in imports(sheet))
    assert a is not None
    assert a.encoding == 'utf-8'
    assert b is not None
    assert b.encoding == 'iso-8859-7'
    assert 'content: "\u03b1"' in b.css_text
    sheet = parse_stylesheet(
        '@import "a.css";'.encode('latin-1'), href='http://x/', protocol_encoding='iso-8859-1', fetcher=Fetcher(files)
    )
    a = imports(sheet)[0].style_sheet
    assert a is not None
    assert a.encoding == 'windows-1252'
    assert 'content: "\xe9"' in a.css_text


def test_import_loops() -> None:
    fetch = Fetcher(
        {
            'http://x/main.css': '@import "a.css";',
            'http://x/a.css': '@import "b.css"; a {}',
            'http://x/b.css': '@import "a.css"; @import "main.css"; @import "b.css"; b {}',
        }
    )
    sheet = parse_stylesheet('@import "a.css"; @import "main.css";', href='http://x/main.css', fetcher=fetch)
    a_rule, self_rule = imports(sheet)
    assert self_rule.style_sheet is None
    assert [e.kind for e in sheet.errors] == ['import-loop']
    a = a_rule.style_sheet
    assert a is not None
    b = imports(a)[0].style_sheet
    assert b is not None
    assert [r.style_sheet for r in imports(b)] == [None, None, None]
    assert fetch.calls == ['http://x/a.css', 'http://x/b.css']
    assert [e.kind for e in b.errors] == ['import-loop'] * 3
    # each sheet is only fetched once, so even eager loading terminates
    sheet = parse_stylesheet('@import "a.css";', href='http://x/main.css', fetcher=fetch, eager_imports=True)
    assert len(list(sheet.iter_rules(follow_imports=True))) == 7


def test_fetcher_exception_is_reported() -> None:
    fetch = Fetcher({'boom.css': OSError('network is down')})
    sheet = parse_stylesheet('/* x */\n@import "boom.css";', fetcher=fetch, eager_imports=True)
    assert [e.kind for e in sheet.errors] == ['import-failed']
    error = sheet.errors[0]
    assert (error.line, error.column) == (2, 1)
    assert 'network is down' in error.message


def test_logging() -> None:
    # requirements L1: errors of imported sheets go to the handler of the parse that loaded them
    seen: list[ParseError] = []
    fetch = Fetcher({'a.css': 'a { color: red; ; : x }', 'b.css': None})
    sheet = parse_stylesheet('@import "a.css"; @import "b.css";', fetcher=fetch, log=seen.append)
    assert seen == []
    a, b = imports(sheet)
    child = a.style_sheet
    assert child is not None
    _ = [r.style for r in child.rules if isinstance(r, StyleRule)]  # parsed lazily
    assert b.style_sheet is None
    assert [e.kind for e in seen] == ['invalid-rule', 'import-not-found']
    assert seen[0] in child.errors
    assert seen[1] in sheet.errors


def test_logging_to_logger(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger('kgcss-test-imports')
    sheet = parse_stylesheet('\n@import "a.css";', href='http://x/main.css', fetcher=Fetcher({}), log=log)
    with caplog.at_level(logging.WARNING, logger='kgcss-test-imports'):
        assert imports(sheet)[0].style_sheet is None
    assert caplog.messages == ['http://x/main.css:2:1: The sheet imported from http://x/a.css was not found']


def test_reload_on_change() -> None:
    fetch = Fetcher({'http://x/a.css': 'a {}', 'http://x/b.css': 'b {}'})
    sheet = parse_stylesheet('@import "a.css";', href='http://x/', fetcher=fetch)
    rule = imports(sheet)[0]
    assert selectors(rule.style_sheet) == ['a']
    rule.href = 'b.css'
    assert selectors(rule.style_sheet) == ['b']
    rule.css_text = '@import "a.css";'
    assert selectors(rule.style_sheet) == ['a']
    assert fetch.calls == ['http://x/a.css', 'http://x/b.css', 'http://x/a.css']
    # added to another sheet, the rule is copied and loads with that sheet's fetcher and href
    other = Stylesheet(href='http://y/', fetcher=Fetcher({'http://y/a.css': 'y {}'}))
    added = other.add(rule)
    assert isinstance(added, ImportRule)
    assert selectors(added.style_sheet) == ['y']
    # removed from its sheet, a rule has no imported sheet
    sheet.delete_rule(rule)
    assert rule.style_sheet is None


def test_iter_rules_follow_imports() -> None:
    fetch = Fetcher({'a.css': '@import "b.css"; @media print { a {} }', 'b.css': 'b {}'})
    sheet = parse_stylesheet('@import "a.css"; @import "c.css"; c {}', fetcher=fetch)
    assert [r.type for r in sheet.iter_rules()] == [RuleType.IMPORT, RuleType.IMPORT, RuleType.STYLE]
    assert fetch.calls == []
    rules = list(sheet.iter_rules(follow_imports=True))
    assert [r.type.value for r in rules] == ['import', 'import', 'style', 'media', 'style', 'import', 'style']
    assert [r.selector_text for r in rules if isinstance(r, StyleRule)] == ['b', 'a', 'c']
    b = rules[2].parent_style_sheet
    assert b is not None
    assert b.href == 'b.css'
    styles = sheet.iter_rules(RuleType.STYLE, follow_imports=True, recursive=False)
    assert [r.selector_text for r in styles if isinstance(r, StyleRule)] == ['b', 'c']
    assert fetch.calls == ['a.css', 'b.css', 'c.css']


def test_copy_and_reparse_keep_fetcher() -> None:
    fetch = Fetcher({'http://x/a.css': 'a {}'})
    sheet = parse_stylesheet('@import "a.css";', href='http://x/', fetcher=fetch)
    copy = sheet.copy()
    assert copy.fetcher is fetch
    assert selectors(imports(copy)[0].style_sheet) == ['a']
    sheet.css_text = '@import "a.css"; b {}'
    assert sheet.fetcher is fetch
    assert selectors(imports(sheet)[0].style_sheet) == ['a']
    eager = parse_stylesheet('@import "a.css";', href='http://x/', fetcher=fetch, eager_imports=True)
    fetch.calls.clear()
    eager.copy()
    eager.css_text = '@import "a.css";'
    assert fetch.calls == ['http://x/a.css', 'http://x/a.css']


FILES: dict[str, str | bytes | Exception | None] = {
    'http://x/css/a.css': '@charset "utf-8"; @import "sub/b.css" print; @import "nope.css"; '
    '@namespace svg url(http://www.w3.org/2000/svg); a { background: url(img/a.png) }',
    'http://x/css/sub/b.css': 'b { background: url(../b.png), url(data:x), url(#f), url(http://y/z.png) }',
    'http://x/c.css': '@font-face { src: url(fonts/c.otf) } c { d: e }',
}


def test_resolve_imports() -> None:
    css = (
        '@charset "utf-8"; @layer base; /* hi */ @import url(css/a.css) layer(base) supports(display: grid) screen;'
        ' @import "c.css" layer; @import "missing.css"; @namespace url(http://www.w3.org/1999/xhtml); main { a: b }'
    )
    sheet = parse_stylesheet(css, href='http://x/main.css', fetcher=Fetcher(FILES))
    out = resolve_imports(sheet)
    assert out is not sheet
    assert out.href == sheet.href
    assert out.fetcher is sheet.fetcher
    assert (
        out.css_text
        == """\
@charset "utf-8";
@layer base;
/* hi */
@import "css/nope.css";
@import "missing.css";
@namespace url(http://www.w3.org/1999/xhtml);
/* START @import http://x/css/a.css */
@supports (display: grid) {
  @media screen {
    @layer base {
      /* START @import http://x/css/sub/b.css */
      @media print {
        b {
          background: url(css/b.png), url(data:x), url(#f), url(http://y/z.png);
        }
      }
      /* END @import http://x/css/sub/b.css */
      a {
        background: url(css/img/a.png);
      }
    }
  }
}
/* END @import http://x/css/a.css */
/* START @import http://x/c.css */
@layer {
  @font-face {
    src: url(fonts/c.otf);
  }
  c {
    d: e;
  }
}
/* END @import http://x/c.css */
main {
  a: b;
}"""
    )
    assert [e.kind for e in out.errors] == ['import-not-found', 'import-not-found']
    # the result is a valid sheet: reparsing drops nothing
    again = parse_stylesheet(out.css_text)
    assert again.errors == []
    assert again.css_text == out.css_text
    # the original is unchanged
    assert serialize(sheet, PRESERVE) == css
    assert parse_stylesheet(css, href='http://x/main.css', fetcher=Fetcher(FILES), resolve_imports=True).css_text == (
        out.css_text
    )


def test_resolve_imports_drops_loops() -> None:
    files: dict[str, str | bytes | Exception | None] = {
        'http://x/a.css': '@import "main.css"; @import "a.css"; a { b: c }'
    }
    sheet = parse_stylesheet('@import "a.css"; m { n: o }', href='http://x/main.css', fetcher=Fetcher(files))
    out = resolve_imports(sheet)
    expected = '/* START @import http://x/a.css */\na {\n  b: c;\n}\n/* END @import http://x/a.css */\nm {\n  n: o;\n}'
    assert out.css_text == expected


def test_resolve_imports_comment_safe_url() -> None:
    sheet = parse_stylesheet('@import "a*/b.css";', fetcher=Fetcher({'a*/b.css': 'p {}'}))
    out = resolve_imports(sheet)
    assert out.css_text.splitlines()[0] == '/* START @import a*%2Fb.css */'
    assert parse_stylesheet(out.css_text).errors == []


def test_two_phase_flow() -> None:
    # requirements P11: parse without resolving, rewrite the hrefs, serialize, then reparse resolving them
    files: dict[str, str | bytes | Exception | None] = {'/book/styles/a.css': 'a { b: c }'}
    sheet = parse_stylesheet('@import "old/a.css"; p {}', href='/book/main.css')
    assert replace_urls(sheet, lambda url: url.replace('old/', 'styles/'))
    text = serialize(sheet, PRESERVE)
    assert text == '@import "styles/a.css"; p {}'
    out = parse_stylesheet(text, href='/book/main.css', fetcher=Fetcher(files), resolve_imports=True)
    assert selectors(out) == ['a', 'p']


@pytest.mark.parametrize(
    ('url', 'from_href', 'to_href', 'expected'),
    [
        ('a.png', 'http://x/css/s.css', 'http://x/main.css', 'css/a.png'),
        ('../a.png', 'http://x/css/s.css', 'http://x/main.css', 'a.png'),
        ('a.png', 'http://x/s.css', 'http://x/css/main.css', '../a.png'),
        ('a.png?q=1#f', 'http://x/css/s.css', 'http://x/main.css', 'css/a.png?q=1#f'),
        ('dir/', 'http://x/css/s.css', 'http://x/main.css', 'css/dir/'),
        ('/abs.png', 'http://x/css/s.css', 'http://x/main.css', 'abs.png'),
        ('a.png', 'http://y/s.css', 'http://x/main.css', 'http://y/a.png'),
        ('a.png', 'http://x/s.css', None, 'http://x/a.png'),
        ('a.png', 'css/s.css', 'main.css', 'css/a.png'),
        ('a.png', 'https://x/s.css', 'http://x/main.css', 'https://x/a.png'),
        ('a:b.png', 'http://x/s.css', 'http://x/main.css', 'a:b.png'),  # has a scheme
        ('./a:b.png', 'http://x/s.css', 'http://x/main.css', './a:b.png'),
        ('http://z/a.png', 'http://x/css/s.css', 'http://x/main.css', 'http://z/a.png'),
        ('data:x', 'http://x/css/s.css', 'http://x/main.css', 'data:x'),
        ('#frag', 'http://x/css/s.css', 'http://x/main.css', '#frag'),
        ('', 'http://x/css/s.css', 'http://x/main.css', ''),
        ('/book/css/a.png', '/book/s.css', '/book/main.css', 'css/a.png'),
        ('a.png', 'css/s.css', 'text/main.css', '../css/a.png'),
        ('a.png', '../s.css', 'main.css', '../a.png'),
        ('../a.png', 'css/s.css', 'main.css', 'a.png'),
        ('./', 'css/s.css', 'main.css', 'css/'),
        ('../', 'css/s.css', 'main.css', './'),
    ],
)
def test_rebased_url(url: str, from_href: str, to_href: str | None, expected: str) -> None:
    assert rebased_url(url, from_href, to_href) == expected


def test_resolve_imports_drops_rules_invalid_when_nested() -> None:
    # at the top level a;@media x {} is one (invalid) style rule, nested it would be something else
    files: dict[str, str | bytes | Exception | None] = {'a.css': 'p { q: r } a;@media x { b {} } s:{t}'}
    out = parse_stylesheet('@import "a.css" print;', fetcher=Fetcher(files), resolve_imports=True)
    assert out.css_text == (
        '/* START @import a.css */\n@media print {\n  p {\n    q: r;\n  }\n}\n/* END @import a.css */'
    )
    assert [e.kind for e in out.errors] == ['invalid-rule', 'invalid-rule']
    # without a wrapper the rules stay at the top level, and are kept
    out = parse_stylesheet('@import "a.css";', fetcher=Fetcher(files), resolve_imports=True)
    assert len(out.rules) == 5
    assert out.errors == []
