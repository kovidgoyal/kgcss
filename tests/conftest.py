# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

from __future__ import annotations

import sys

import pytest


def gil_enabled() -> bool:
    is_enabled = getattr(sys, '_is_gil_enabled', None)
    return True if is_enabled is None else bool(is_enabled())


def pytest_report_header(config: pytest.Config) -> list[str]:
    from kgcss import _kgcss

    return [
        f'kgcss C extension: {_kgcss.__file__}',
        f'free-threaded build: {bool(_kgcss.GIL_DISABLED_BUILD)}, GIL enabled at runtime: {gil_enabled()}',
    ]
