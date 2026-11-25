# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Tests for the C extension module itself: loading, GIL and threading."""

import sysconfig
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import kgcss
from kgcss import _kgcss

from .conftest import gil_enabled


def test_extension_is_compiled() -> None:
    assert _kgcss.__file__ is not None
    assert _kgcss.__file__.endswith(tuple(sysconfig.get_config_var('EXT_SUFFIX').split()))


def test_free_threading_flag_matches_interpreter() -> None:
    assert bool(_kgcss.GIL_DISABLED_BUILD) == bool(sysconfig.get_config_var('Py_GIL_DISABLED'))


@pytest.mark.skipif(not sysconfig.get_config_var('Py_GIL_DISABLED'), reason='requires a free-threaded Python')
def test_importing_does_not_enable_gil() -> None:
    # Importing an extension that does not declare Py_mod_gil re-enables the GIL.
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
    shared = kgcss.tokenize(css)
    n = len(shared)
    barrier = threading.Barrier(8)

    def work(idx: int) -> tuple[int, int, int]:
        barrier.wait()
        # Every thread races to build the shared stream's lazy line table
        last = shared[n - 1 - idx]
        stream = kgcss.tokenize(css)
        return last.line, len(stream), len(stream.errors)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(8)))
    assert {r[1:] for r in results} == {(n, 0)}
    assert all(r[0] in (6000, 6001) for r in results)


def test_concurrent_walk_of_shared_values() -> None:
    css = 'a { b: rgb(1, calc(2 + (3 * 4))) [x] } ' * 2000
    shared = kgcss.parse_component_value_list(css)
    barrier = threading.Barrier(8)

    def walk(_: int) -> int:
        barrier.wait()
        count = 0
        stack = [shared]
        while stack:
            for item in stack.pop():
                count += 1
                if isinstance(item, kgcss.Block):
                    stack.append(item.content)
                elif isinstance(item, kgcss.Function):
                    stack.append(item.arguments)
        return count

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(walk, range(8)))
    assert len(set(results)) == 1
    assert results[0] > len(shared)
