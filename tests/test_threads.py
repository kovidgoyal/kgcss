# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Concurrency and stress tests.

These matter most on free-threaded builds, where the C code really runs in
parallel, but also exercise the GIL release in tokenize() on regular builds.
Set KGCSS_STRESS to a number to multiply the amount of work.
"""

import os
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import kgcss
import kgcss.grammar
import kgcss.media
import kgcss.selectors
import kgcss.syntax
import kgcss.tokenizer
from kgcss import _kgcss

from .conftest import gil_enabled

THREADS = 8
STRESS = max(1, int(os.environ.get('KGCSS_STRESS', '1')))


def run_concurrently[R](fn: Callable[[int], R], tasks: int = THREADS) -> list[R]:
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        return list(pool.map(fn, range(tasks)))


def check_gil() -> None:
    if _kgcss.GIL_DISABLED_BUILD:
        assert not gil_enabled()


def test_concurrent_use() -> None:
    inputs = [f'a"b\\c\x00\x01-{i}' * 50 for i in range(64)]
    expected = [(kgcss.serialize_string(s), kgcss.serialize_identifier(s)) for s in inputs]

    def work(idx: int) -> tuple[str, str]:
        s = inputs[idx % len(inputs)]
        return kgcss.serialize_string(s), kgcss.serialize_identifier(s)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(len(inputs) * 50)))
    for i, r in enumerate(results):
        assert r == expected[i % len(inputs)]


def test_concurrent_tokenize_and_shared_stream() -> None:
    css = 'a { color: red; background: url(x\\)y.png) }\n/* é */ .\\31 x { margin: 1.5e1px }\n' * 3000
    shared = kgcss.tokenizer.tokenize(css)
    n = len(shared)
    barrier = threading.Barrier(8)

    def work(idx: int) -> tuple[int, int, int]:
        barrier.wait()
        # Every thread races to build the shared stream's lazy line table
        last = shared[n - 1 - idx]
        stream = kgcss.tokenizer.tokenize(css)
        return last.line, len(stream), len(stream.errors)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(8)))
    assert {r[1:] for r in results} == {(n, 0)}
    assert all(r[0] in (6000, 6001) for r in results)


def test_concurrent_walk_of_shared_values() -> None:
    css = 'a { b: rgb(1, calc(2 + (3 * 4))) [x] } ' * 2000
    shared = kgcss.syntax.parse_component_value_list(css)
    barrier = threading.Barrier(8)

    def walk(_: int) -> int:
        barrier.wait()
        count = 0
        stack = [shared]
        while stack:
            for item in stack.pop():
                count += 1
                if isinstance(item, kgcss.syntax.Block):
                    stack.append(item.content)
                elif isinstance(item, kgcss.syntax.Function):
                    stack.append(item.arguments)
        return count

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(walk, range(8)))
    assert len(set(results)) == 1
    assert results[0] > len(shared)


def test_concurrent_parse_and_shared_tree() -> None:
    css = 'a { b: c !important; &:hover { d: e } } @media x { f { g: h } } /* i */ ' * 1000
    shared = kgcss.syntax.parse_stylesheet(css)
    barrier = threading.Barrier(8)

    def work(_: int) -> tuple[int, int]:
        barrier.wait()
        own = kgcss.syntax.parse_stylesheet(css)
        decls = 0
        for node in shared:
            block = getattr(node, 'block', None)
            if block is not None:
                for child in kgcss.syntax.parse_block_contents(block):
                    decls += isinstance(child, kgcss.syntax.Declaration)
        return len(own), decls

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(8)))
    assert set(results) == {(len(shared), 1000)}


def summarize(stream: kgcss.tokenizer.TokenStream) -> list[tuple[object, ...]]:
    return [(t.type, t.value, t.pos, t.end) for t in stream]


def test_lazy_line_table_race() -> None:
    # Each round creates a fresh stream whose line table is built on first
    # use, and every thread races to trigger that
    css = ''.join(f'a{i} {{ b: c }}\r\n/* {i} */\f' if i % 3 else f'd{i}\re\n' for i in range(200))
    reference = kgcss.tokenizer.tokenize(css)
    expected = [(t.line, t.column) for t in reference]
    barrier = threading.Barrier(THREADS)
    for _ in range(50 * STRESS):
        stream = kgcss.tokenizer.tokenize(css)

        def work(idx: int, stream: kgcss.tokenizer.TokenStream = stream) -> list[tuple[int, int]]:
            barrier.wait()
            tokens = list(stream)
            # Different threads start at different ends of the stream
            order = tokens if idx % 2 else tokens[::-1]
            got = {t.pos: (t.line, t.column) for t in order}
            return [got[t.pos] for t in tokens]

        assert all(r == expected for r in run_concurrently(work))
    check_gil()


def test_concurrent_large_inputs() -> None:
    # Large inputs are tokenized with the GIL released
    inputs = [
        f'.c{i} {{ color: #{i:03x}; background: url(img{i}.png) }} /* {"é" * i} */\n' * 1500 for i in range(THREADS)
    ]
    assert all(len(css) > 64 * 1024 for css in inputs)
    expected = [summarize(kgcss.tokenizer.tokenize(css)) for css in inputs]
    for _ in range(2 * STRESS):
        results = run_concurrently(lambda i: summarize(kgcss.tokenizer.tokenize(inputs[i])))
        assert results == expected
    check_gil()


def test_shared_block_parsed_concurrently() -> None:
    body = ' '.join(f'p{i}: v{i} !important; @media x {{ q{i} {{ r: s }} }} n{i} {{ t: u }}' for i in range(300))
    (rule,) = kgcss.syntax.parse_stylesheet(f'a {{ {body} }}')
    assert isinstance(rule, kgcss.syntax.QualifiedRule)
    block = rule.block

    def summary(nodes: kgcss.syntax.NodeList) -> list[str]:
        return [n.source_text for n in nodes]

    expected = (summary(kgcss.syntax.parse_block_contents(block)), summary(kgcss.syntax.parse_rule_list(block)))
    barrier = threading.Barrier(THREADS)

    def work(_: int) -> bool:
        barrier.wait()
        ok = True
        for _ in range(5 * STRESS):
            ok &= (
                summary(kgcss.syntax.parse_block_contents(block)),
                summary(kgcss.syntax.parse_rule_list(block)),
            ) == expected
        return ok

    assert all(run_concurrently(work))
    check_gil()


def test_cssom_lazy_blocks_race() -> None:
    # Many threads materialize the same unparsed blocks at once: all must see
    # the same objects, and each lazily found error must be reported once
    css = ''.join(f'.r{i} {{ a: b; bad bad; &:hover {{ c: d }} @media x {{ e {{}} }} }}\n' for i in range(200))
    barrier = threading.Barrier(THREADS)
    for _ in range(5 * STRESS):
        sheet = kgcss.parse_stylesheet(css)

        def work(idx: int, sheet: kgcss.Stylesheet = sheet) -> list[int]:
            barrier.wait()
            rules = list(sheet.rules)
            if idx % 2:
                rules.reverse()
            ids = {}
            for rule in rules:
                assert isinstance(rule, kgcss.StyleRule)
                ids[rule.selector_text] = id(rule.rules)
            return [ids[f'.r{i}'] for i in range(200)]

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
        assert len(sheet.errors) == 200
        assert all(e.kind == 'invalid-rule' for e in sheet.errors)
    check_gil()


def test_cssom_lazy_style_race() -> None:
    css = ''.join(f'.r{i} {{ a: {i}; b: x !important; a: y }}\n' for i in range(300))
    barrier = threading.Barrier(THREADS)
    for _ in range(5 * STRESS):
        sheet = kgcss.parse_stylesheet(css)

        def work(idx: int, sheet: kgcss.Stylesheet = sheet) -> list[tuple[int, str]]:
            barrier.wait()
            rules = list(sheet.rules)
            if idx % 2:
                rules.reverse()
            out = {}
            for rule in rules:
                assert isinstance(rule, kgcss.StyleRule)
                out[rule.selector_text] = (id(rule.style), rule.style.get_value('a'))
            return [out[f'.r{i}'] for i in range(300)]

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
        assert {v for _, v in results[0]} == {'y'}
    check_gil()


def test_cssom_lazy_selectors_race() -> None:
    css = ''.join(f'.r{i}, .s{i} > p {{ a: b }}\n' for i in range(300))
    barrier = threading.Barrier(THREADS)
    for _ in range(3 * STRESS):
        sheet = kgcss.parse_stylesheet(css)

        def work(_: int, sheet: kgcss.Stylesheet = sheet) -> list[int]:
            barrier.wait()
            out = []
            for rule in sheet.rules:
                assert isinstance(rule, kgcss.StyleRule)
                out.append(id(rule.selectors))
            return out

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
    check_gil()


def test_cssom_import_fetched_once() -> None:
    css = ''.join(f'@import "s{i}.css";\n' for i in range(50))
    barrier = threading.Barrier(THREADS)
    for _ in range(3 * STRESS):
        calls: list[str] = []
        lock = threading.Lock()

        def fetch(url: str, calls: list[str] = calls, lock: threading.Lock = lock) -> tuple[str | None, str]:
            with lock:
                calls.append(url)
            return None, f'a {{ b: {url} }}'

        sheet = kgcss.parse_stylesheet(css, href='http://x/', fetcher=fetch)

        def work(idx: int, sheet: kgcss.Stylesheet = sheet) -> list[int]:
            barrier.wait()
            rules = list(sheet.rules)
            if idx % 2:
                rules.reverse()
            out = {}
            for rule in rules:
                assert isinstance(rule, kgcss.ImportRule)
                out[rule.href] = id(rule.style_sheet)
            return [out[f's{i}.css'] for i in range(50)]

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
        assert sorted(calls) == sorted(f'http://x/s{i}.css' for i in range(50))
    check_gil()


def test_cssom_serialize_while_modifying() -> None:
    # Serializing reads properties in C while other threads assign their values
    css = ''.join(f'.r{i} {{ a: {i}px; b: url(x{i}.png); --c: {{{i}}} }}\n' for i in range(200))
    barrier = threading.Barrier(THREADS)
    for _ in range(3 * STRESS):
        sheet = kgcss.parse_stylesheet(css)
        rules = [r for r in sheet.rules if isinstance(r, kgcss.StyleRule)]

        def work(idx: int, sheet: kgcss.Stylesheet = sheet, rules: list[kgcss.StyleRule] = rules) -> int:
            barrier.wait()
            if idx % 2:
                for _ in range(5):
                    text = kgcss.serialize(sheet)
                    assert text.count(' {\n') == 200
                return 0
            for n, rule in enumerate(rules):
                prop = rule.style.get_property('a')
                assert prop is not None
                prop.value = f'{n + idx}em  /* x */'
            return len(rules)

        run_concurrently(work)
        text = kgcss.serialize(sheet)
        assert 'px' not in text.replace('url(', '')
    check_gil()


def test_selectors_shared() -> None:
    # Selector lists, compiled selectors and their caches are created lazily by whichever
    # thread gets there first. All threads must see the same lists and the same results.
    css = ''.join(f'.r{i} > p:nth-child(2n+{i % 5} of .x), #i{i}:is(a, b)::before {{ a: b }}\n' for i in range(300))
    barrier = threading.Barrier(THREADS)
    for _ in range(3 * STRESS):
        kgcss.selectors.compile_selectors.cache_clear()
        sheet = kgcss.parse_stylesheet(css)

        def work(idx: int, sheet: kgcss.Stylesheet = sheet) -> list[tuple[int, tuple[int, int, int], str | None]]:
            barrier.wait()
            rules = list(sheet.rules)
            if idx % 2:
                rules.reverse()
            out = {}
            for rule in rules:
                assert isinstance(rule, kgcss.StyleRule)
                for s in rule.selectors:
                    pe = s.pseudo_element
                    out[s.text] = (id(rule.selectors), s.specificity, None if pe is None else pe.name)
            return [out[k] for k in sorted(out)]

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
        assert results[0][0][1:] == ((1, 0, 2), 'before')  # #i0:is(a, b)::before
    check_gil()


def test_select_shared() -> None:
    # One Select used by many threads: matching runs without the GIL, and the data for :empty and
    # the map from elements to indices are filled lazily by whichever thread needs them first.
    # The standard library's ElementTree, as lxml enables the GIL on free-threaded builds.
    import xml.etree.ElementTree as ET

    parts = ['<root lang="en">']
    for i in range(200):
        lang = ' lang="fr-CH"' if i % 5 == 0 else ''
        parts.append(f'<div class="d{i % 7}"{lang}><p id="p{i}" class="x{i % 3}">t</p><p/><span/></div>')
    parts.append('</root>')
    document = ET.fromstring(''.join(parts))
    barrier = threading.Barrier(THREADS)
    for _ in range(3 * STRESS):
        select = kgcss.Select(document)
        roots = [div for div in document if div.get('class') == 'd3']

        def work(idx: int, select: kgcss.Select[ET.Element] = select, roots: list[ET.Element] = roots) -> list[object]:
            barrier.wait()
            out: list[object] = []
            out.append([e.get('id') for e in select('div.d1 > p.x2, p:empty + span, :not(div, p, span)')])
            # Of-type positions and languages are filled lazily, nth-of and :has() results per call
            out.append(len(list(select('p:nth-of-type(2), p:lang(fr) ~ span, div:nth-child(3n of .d2)'))))
            out.append(len(list(select('div:has(> p.x1 + p:last-of-type)'))))
            out.append(sum(1 for r in roots for _ in select('p', root=r)))
            out.append(select.matches(roots[idx % len(roots)], 'div:first-child, .d3'))
            return out

        results = run_concurrently(work)
        assert all(r == results[0] for r in results)
        assert results[0][3] == 2 * len(roots)
    check_gil()


def test_colors_concurrently() -> None:
    # The parser keeps no state; the escape fallback allocates, so mix in escaped input
    inputs = ['red', 'rgb(1 2 3 / 50%)', 'oklch(0.7 0.1 200)', 'r\\65 d', 'red /* é */', 'currentColor', 'x', '#abc']
    expected = [(kgcss.parse_rgba(s), kgcss.parse_color(s)) for s in inputs]

    def work(idx: int) -> bool:
        check_gil()
        return all(
            [(kgcss.parse_rgba(s), kgcss.parse_color(s)) for s in inputs] == expected for _ in range(1000 * STRESS)
        )

    assert all(run_concurrently(work))


def test_media_queries_concurrently() -> None:
    # The cache is shared: threads parse the same texts at once, and new ones that evict others
    texts = [
        f'screen and (min-width: {i}px), not print and ((color) or (hover)), (400px < width < {i}em)' for i in range(64)
    ]
    expected = [[q.to_css() for q in kgcss.parse_media_queries(t)] for t in texts]
    kgcss.media.parse_media_queries.cache_clear()

    def work(idx: int) -> bool:
        check_gil()
        for k in range(200 * STRESS):
            i = (idx + k) % len(texts)
            if [q.to_css() for q in kgcss.parse_media_queries(texts[i])] != expected[i]:
                return False
            kgcss.parse_media_queries(f'(x: {idx}-{k})')
        return True

    assert all(run_concurrently(work))


def test_grammar_concurrently() -> None:
    values = [f'{i}px' for i in range(50)] + ['solid', 'red', 'rgb(1 2 3)', 'inherit', 'var(--x)', 'bold']
    names = sorted(kgcss.grammar.PROPERTIES)
    expected = {(n, v): kgcss.matches(n, v) for n in names for v in values}
    kgcss.grammar.matches_text.cache_clear()

    def work(idx: int) -> bool:
        check_gil()
        for k in range(20 * STRESS):
            for n in names[idx % 3 :: 3]:
                for v in values:
                    if kgcss.matches(n, v) is not expected[n, v]:
                        return False
            kgcss.matches('font-size', f'{idx}.{k}em')  # new entries, which evict others
        return True

    assert all(run_concurrently(work))


def test_shorthands_concurrently() -> None:
    # Each thread condenses its own blocks; the expansion caches are shared
    css = '; '.join(f'border-{e}-{p}: {v}' for e in ('top', 'right', 'bottom', 'left')
                    for p, v in (('color', 'red'), ('style', 'solid'), ('width', '5px')))  # fmt: skip
    css += '; margin-top: 1px; margin-right: 2px; margin-bottom: 1px; margin-left: 2px'

    def work(idx: int) -> bool:
        check_gil()
        for k in range(100 * STRESS):
            padding = f'{idx + 1}px {k + 1}px'  # not 0px, which the serializer writes as 0
            block = kgcss.parse_style(f'{css}; padding: {padding}')
            kgcss.condense_shorthands(block)
            if block.to_css(single_line=True) != f'border: red solid 5px; margin: 1px 2px; padding: {padding}':
                return False
            if kgcss.expand_shorthand('padding', padding) is None:
                return False
        return True

    assert all(run_concurrently(work))


class SlowElement:
    """An element whose text and tail are Python properties that sleep: reading them lets other threads run,
    and suspends the critical sections of free-threaded Python."""

    def __init__(self, tag: object, children: list[SlowElement] | None = None, text: str | None = None) -> None:
        self.tag = tag
        self.children = children or []
        self._text = text
        self._tail: str | None = None

    def items(self) -> list[tuple[str, str]]:
        return []

    def __iter__(self) -> Iterator[SlowElement]:
        return iter(self.children)

    @property
    def text(self) -> str | None:
        time.sleep(0.0002)
        return self._text

    @property
    def tail(self) -> str | None:
        time.sleep(0.0002)
        return self._tail


def test_empty_while_content_is_read_concurrently() -> None:
    # The content of elements, for :empty, is read from the tree on first use. Two threads reading it at once
    # once let one match against the other's half-written content: a p whose only text is the tail of a
    # comment matched :empty.
    def comment() -> SlowElement:
        c = SlowElement(object())  # a tag that is not a str: not an element
        c._tail = 'tail text'
        return c

    bad = 0
    for _ in range(4 * STRESS):
        root = SlowElement('html', [SlowElement('body', [SlowElement('p', [comment()]) for _ in range(30)])])
        select = kgcss.Select(root)
        barrier = threading.Barrier(THREADS)

        def work(idx: int, select: kgcss.Select[SlowElement] = select, barrier: threading.Barrier = barrier) -> int:
            barrier.wait()
            return len(list(select('p:empty')))

        bad += sum(run_concurrently(work))
    assert bad == 0


def test_constructed_rules_concurrently() -> None:
    # Rules made by the API, not parsed, have no sheet lock: their parts were once created by each racing
    # thread, so that the threads got different objects, and changes made through all but one were lost
    for _ in range(50 * STRESS):
        sheet = kgcss.Stylesheet()
        rule = sheet.insert_rule(kgcss.StyleRule('p.a, div'))
        assert isinstance(rule, kgcss.StyleRule)
        barrier = threading.Barrier(THREADS)

        def work(idx: int, rule: kgcss.StyleRule = rule, barrier: threading.Barrier = barrier) -> tuple[int, ...]:
            barrier.wait()
            return id(rule.style), id(rule.selectors), id(rule.rules)

        assert len(set(run_concurrently(work))) == 1


def test_fetcher_runs_outside_the_sheet_lock() -> None:
    # The fetcher is user code: it once ran under the sheet lock, so a fetcher that waits for another thread
    # that reads the same sheet deadlocked
    finished: list[bool] = []
    sheet: kgcss.Stylesheet

    def read_other_rule() -> None:
        finished.append(sheet.rules[1].style['color'] == 'red')  # ty: ignore[missing-attribute]

    def fetcher(url: str) -> tuple[str | None, str]:
        helper = threading.Thread(target=read_other_rule, daemon=True)
        helper.start()
        helper.join(timeout=10)
        return None, '.imported { color: blue }'

    sheet = kgcss.parse_stylesheet('@import "a.css"; p { color: red }', href='http://x/', fetcher=fetcher)
    imported = sheet.rules[0].style_sheet  # ty: ignore[missing-attribute]
    assert imported is not None
    assert finished == [True]


def test_log_handler_runs_outside_the_sheet_lock() -> None:
    # Errors found while parsing blocks lazily are logged once the sheet lock is released, so that a handler
    # that waits for another thread using the same sheet cannot deadlock
    finished: list[bool] = []
    sheet: kgcss.Stylesheet

    def read_other_rule() -> None:
        finished.append(sheet.rules[1].style['color'] == 'red')  # ty: ignore[missing-attribute]

    def log(error: kgcss.ParseError) -> None:
        helper = threading.Thread(target=read_other_rule, daemon=True)
        helper.start()
        helper.join(timeout=10)

    sheet = kgcss.parse_stylesheet('a { color: red; margin 0 } p { color: red }', log=log)
    assert sheet.rules[0].style['color'] == 'red'  # ty: ignore[missing-attribute]
    assert finished == [True]
    assert [e.message for e in sheet.errors] == ['Invalid rule ignored']


def test_concurrent_lint() -> None:
    # Each thread lints its own sheet and a shared one, with fixes, while the rule modules load lazily
    sheets = [
        f'@foo{{}} a{{colr:#ff;;}} .c{i}{{color:red;color:blue}} .c{i}{{}} b{{width:calc(1px+2px)}} '
        f'@media (widht:1px){{x:hovr{{margin:1pix}}}} p{{font-family:Arial}}'
        for i in range(THREADS)
    ]
    expected = [(kgcss.lint(css), kgcss.lint(css, fix=True)) for css in sheets]
    barrier = threading.Barrier(THREADS)

    def work(idx: int) -> bool:
        barrier.wait()
        ok = True
        for _ in range(3 * STRESS):
            css = sheets[idx]
            ok = ok and kgcss.lint(css) == expected[idx][0] and kgcss.lint(css, fix=True) == expected[idx][1]
            ok = ok and kgcss.lint(sheets[0]) == expected[0][0]
        return ok

    assert all(run_concurrently(work))
    check_gil()
