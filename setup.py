"""Build configuration for the kgcss C extension.

All project metadata lives in pyproject.toml, this file only describes the
native extension module, which cannot be fully expressed declaratively.
"""

import os
import sys
from pathlib import Path

from setuptools import Extension, setup

CSRC = Path('csrc')


def c_sources() -> list[str]:
    return sorted(str(p) for p in CSRC.glob('*.c'))


def c_headers() -> list[str]:
    return sorted(str(p) for p in CSRC.glob('*.h'))


def compile_args() -> list[str]:
    if sys.platform == 'win32':
        return ['/W4']
    args = [
        '-std=c99',
        '-Wall',
        '-Wextra',
        '-Wno-missing-field-initializers',
        '-Wstrict-prototypes',
        '-Wshadow',
        '-fvisibility=hidden',
    ]
    if os.environ.get('KGCSS_DEBUG') == '1':
        args += ['-Og', '-g3', '-UNDEBUG', '-fno-omit-frame-pointer']
    else:
        args += ['-O3']
    if os.environ.get('KGCSS_SANITIZE') == '1':
        args += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    if os.environ.get('KGCSS_WERROR') == '1':
        args += ['-Werror']
    return args


def link_args() -> list[str]:
    if sys.platform != 'win32' and os.environ.get('KGCSS_SANITIZE') == '1':
        return ['-fsanitize=address,undefined']
    return []


def define_macros() -> list[tuple[str, str | None]]:
    macros: list[tuple[str, str | None]] = []
    if os.environ.get('KGCSS_DEBUG') == '1':
        macros.append(('KGCSS_DEBUG', '1'))
    return macros


setup(
    ext_modules=[
        Extension(
            'kgcss._kgcss',
            sources=c_sources(),
            depends=c_headers(),
            include_dirs=[str(CSRC)],
            define_macros=define_macros(),
            extra_compile_args=compile_args(),
            extra_link_args=link_args(),
        ),
    ],
)
