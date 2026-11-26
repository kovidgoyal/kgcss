#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Run the test suite against a build of the extension with AddressSanitizer
and UndefinedBehaviorSanitizer.

The extension is built (with -Og and asserts enabled) into a temporary
directory, so the normal development build is not touched. Any arguments are
passed on to pytest, for example:

    uv run python tools/sanitize_tests.py -x tests/test_tokenizer.py

Linux only, and the compiler Python was built with must be GCC (its sanitizer
runtimes are preloaded into the interpreter).
"""

import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def runtime_library(compiler: str, name: str) -> str:
    path = subprocess.check_output([compiler, f'-print-file-name={name}'], text=True).strip()
    if not os.path.isabs(path) or not os.path.exists(path):
        raise SystemExit(f'Could not find {name} for {compiler}, is it GCC with sanitizer support installed?')
    return path


def main() -> int:
    compiler = (sysconfig.get_config_var('CC') or 'gcc').split()[0]
    preload = ':'.join(runtime_library(compiler, lib) for lib in ('libasan.so', 'libubsan.so'))
    with tempfile.TemporaryDirectory(prefix='kgcss-sanitize-') as tmp:
        build_lib = Path(tmp) / 'lib'
        build_env = os.environ | {'KGCSS_SANITIZE': '1', 'KGCSS_DEBUG': '1', 'KGCSS_WERROR': '1'}
        subprocess.run(
            [sys.executable, 'setup.py', '-q', 'build_ext', '--force',
             '--build-lib', str(build_lib), '--build-temp', str(Path(tmp) / 'temp')],
            cwd=ROOT, env=build_env, check=True,
        )  # fmt: skip
        # Put the pure Python modules next to the sanitized extension
        shutil.copytree(
            ROOT / 'src' / 'kgcss',
            build_lib / 'kgcss',
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns('*.so', '*.pyd', '__pycache__'),
        )
        test_env = os.environ | {
            'PYTHONPATH': str(build_lib),
            'LD_PRELOAD': preload,
            # Python's own allocator hides heap errors in Python objects from ASan
            'PYTHONMALLOC': 'malloc',
            # The interpreter itself does not free everything at exit
            'ASAN_OPTIONS': 'detect_leaks=0',
            'UBSAN_OPTIONS': 'halt_on_error=1:print_stacktrace=1',
        }
        cmd = [sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', *sys.argv[1:]]
        result = subprocess.run(cmd, cwd=ROOT, env=test_env, check=False)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
