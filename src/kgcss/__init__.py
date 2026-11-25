# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""kgcss: fast and comprehensive CSS handling with a C99 backend."""

from kgcss._version import __version__
from kgcss.serialize import serialize_identifier, serialize_string

__all__ = [
    '__version__',
    'serialize_identifier',
    'serialize_string',
]
