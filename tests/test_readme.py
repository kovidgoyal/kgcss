# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The examples in README.md must work."""

import doctest
from pathlib import Path

import pytest

pytest.importorskip('lxml')


def test_readme_examples() -> None:
    readme = Path(__file__).parent.parent / 'README.md'
    result = doctest.testfile(str(readme), module_relative=False, optionflags=doctest.ELLIPSIS)
    assert result.attempted > 5
    assert result.failed == 0
