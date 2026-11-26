# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import os
import sys

import pytest
from hypothesis import settings

# Property based tests: a quick profile for everyday runs and a thorough one
# for CI and before releases. Select with HYPOTHESIS_PROFILE=thorough.
settings.register_profile('default', max_examples=150, deadline=None)
settings.register_profile('thorough', max_examples=3000, deadline=None)
HYPOTHESIS_PROFILE = os.environ.get('HYPOTHESIS_PROFILE', 'default')
settings.load_profile(HYPOTHESIS_PROFILE)


def gil_enabled() -> bool:
    return sys._is_gil_enabled()


def pytest_report_header(config: pytest.Config) -> list[str]:
    from kgcss import _kgcss

    return [
        f'kgcss C extension: {_kgcss.__file__}',
        f'free-threaded build: {bool(_kgcss.GIL_DISABLED_BUILD)}, GIL enabled at runtime: {gil_enabled()}',
        f'hypothesis profile: {HYPOTHESIS_PROFILE}',
    ]
