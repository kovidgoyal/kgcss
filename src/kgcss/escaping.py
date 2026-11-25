# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""CSSOM serialization primitives.

See https://drafts.csswg.org/cssom/#common-serializing-idioms
"""

from kgcss import _kgcss


def serialize_string(text: str) -> str:
    """Serialize ``text`` as a double quoted CSS string."""
    return _kgcss.serialize_string(text)


def serialize_identifier(text: str) -> str:
    """Serialize ``text`` as a CSS identifier, escaping characters as needed."""
    return _kgcss.serialize_identifier(text)
