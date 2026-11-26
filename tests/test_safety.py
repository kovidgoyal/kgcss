# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Memory safety and lifetime regressions, found by review.

Each test drives the C code into a state where it once used memory that Python
code had freed, or leaked a reference. On a normal build a use after free may
go unnoticed, so these run under the sanitizer build too (tools/sanitize_tests.py),
where it is reported.
"""

import dataclasses
import gc
import sys
from collections.abc import Iterator

import pytest

import kgcss
from kgcss import _kgcss


class Element:
    """A minimal ElementTree-style element whose attributes are plain Python, so tests can run code
    where the C code reads them."""

    def __init__(self, tag: str, children: list[Element] | None = None, text: str | None = None) -> None:
        self.tag = tag
        self.children = children or []
        self.text_value = text
        self.tail: str | None = None

    def items(self) -> list[tuple[str, str]]:
        return []

    def __iter__(self) -> Iterator[Element]:
        return iter(self.children)

    @property
    def text(self) -> str | None:
        return self.text_value


def test_match_keeps_its_selectors_alive() -> None:
    # Snapshot.match() used the selectors of the caller's list while Python code (reading .text for :empty)
    # could clear the list and free them
    selectors: list[_kgcss.CompiledSelector] = []

    class Clearing(Element):
        @property
        def text(self) -> str | None:
            selectors.clear()
            gc.collect()
            return None

    root = Clearing('html', [Clearing('body', [Clearing('p') for _ in range(10)])])
    snapshot = _kgcss.Snapshot(root)
    selectors.extend(_kgcss.parse_selectors('p:empty, body p:empty, html > body > p:empty', False))
    found = snapshot.match(selectors, False, 0, 0, -1, False)
    assert isinstance(found, list)
    assert len(found) == 10


def test_attributes_are_kept_alive_while_hashed() -> None:
    # Building a snapshot read attribute names and values borrowed from the list items() returned, while
    # hashing them could run Python code that emptied the list
    shared: list[tuple[str, str]] = []

    class Key(str):
        def __hash__(self) -> int:
            shared.clear()
            return str.__hash__(self)

        __eq__ = str.__eq__

    class El(Element):
        def items(self) -> list[tuple[str, str]]:
            shared[:] = [(Key('cl' + 'ass'), 'a b ' + 'c' * 50)]
            return shared

    select = kgcss.Select(El('p'))
    assert len(list(select('.a.b'))) == 1


def test_index_map_is_not_leaked() -> None:
    # A re-entrant index_of() while the element index was being built made the first map overwrite the
    # second without releasing it, leaking a reference to every element
    holder: list[_kgcss.Snapshot] = []
    reentered = False

    class El(Element):
        def __hash__(self) -> int:
            nonlocal reentered
            if holder and not reentered:
                reentered = True
                holder[0].index_of(self)
            return id(self) >> 4

    root = El('html', [El('p')])
    baseline = sys.getrefcount(root)
    holder.append(_kgcss.Snapshot(root))
    assert holder[0].index_of(root) == 0
    assert reentered
    holder.clear()
    gc.collect()
    assert sys.getrefcount(root) == baseline


def test_failed_content_read_is_retried() -> None:
    # The content of elements, for :empty, is published only when completely read: an exception part way
    # must not leave wrong values behind
    calls = 0

    class Failing(Element):
        @property
        def text(self) -> str | None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('text unavailable')
            return self.text_value

    root = Failing('html', [Failing('p', text='x'), Failing('p')])
    select = kgcss.Select(root)
    with pytest.raises(RuntimeError):
        list(select('p:empty'))
    assert [e.text_value for e in select('p:empty')] == [None]


def test_values_are_immutable() -> None:
    # Parsed values are shared, through the caches of media queries and the like: they cannot be changed
    value = kgcss.parse_value('1px solid')
    with pytest.raises(dataclasses.FrozenInstanceError):
        value[0].value = 2  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        value.css_text = 'x'  # type: ignore[misc]
    query = kgcss.parse_media_queries('(min-width: 600px)')[0]
    feature = next(query.features())
    assert feature.value is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        feature.value.css_text = '1px'  # type: ignore[misc]
    assert kgcss.parse_media_queries('(min-width: 600px)')[0].to_css() == '(min-width: 600px)'
    # They are still hashable and comparable
    assert value[0] == kgcss.parse_value('1px')[0]
    assert len({value[0], kgcss.parse_value('1px')[0]}) == 1
