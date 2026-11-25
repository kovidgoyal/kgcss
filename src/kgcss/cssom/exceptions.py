# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Exceptions raised by the CSSOM. Parsing whole sheets never raises, these
are for APIs that must produce one valid object."""


class CSSSyntaxError(ValueError):
    """Text assigned to the CSSOM, such as a css_text, is not valid CSS of the right kind."""


class InvalidValueError(ValueError):
    """A property value is not valid."""
