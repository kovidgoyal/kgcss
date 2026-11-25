# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

# Type stubs for the C extension module built from csrc/. Keep in sync with
# the PyMethodDef table and constants in csrc/module.c.

from typing import Final

GIL_DISABLED_BUILD: Final[int]

def serialize_string(text: str, /) -> str: ...
def serialize_identifier(text: str, /) -> str: ...
