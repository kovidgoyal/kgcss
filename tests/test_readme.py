# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The example in README.md must work. The examples of the documentation are doctests, run by the
docs job of CI (sphinx-build -b doctest)."""

import doctest
from pathlib import Path


def test_readme_examples() -> None:
    readme = Path(__file__).parent.parent / 'README.md'
    result = doctest.testfile(str(readme), module_relative=False)
    assert result.attempted >= 2
    assert result.failed == 0
