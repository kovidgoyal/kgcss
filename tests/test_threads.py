# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Concurrency and stress tests.

These matter most on free-threaded builds, where the C code really runs in
parallel, but also exercise the GIL release in tokenize() on regular builds.
Set KGCSS_STRESS to a number to multiply the amount of work.
"""

import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import kgcss
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
