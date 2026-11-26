# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Tests for the C extension module itself. Threading tests are in test_threads.py."""

import sysconfig

import pytest

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
