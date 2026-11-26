# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Exceptions raised by the CSSOM. Parsing whole sheets never raises, these
are for APIs that must produce one valid object."""


class CSSSyntaxError(ValueError):
    """Text assigned to the CSSOM, such as a css_text, is not valid CSS of the right kind."""


class InvalidValueError(ValueError):
    """A property value is not valid."""


class SelectorError(ValueError):
    """The base of the selector errors."""


class SelectorSyntaxError(SelectorError, CSSSyntaxError):
    """Text that is not a valid selector. pos is the offset of the problem in text."""

    def __init__(self, message: str, text: str = '', pos: int = 0) -> None:
        super().__init__(message)
        self.text = text
        self.pos = pos


class ExpressionError(SelectorError):
    """A valid selector that cannot be matched against a document, such as :hover."""
