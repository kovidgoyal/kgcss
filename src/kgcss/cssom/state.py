# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""State shared by the objects of one parsed stylesheet."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from kgcss.tokenizer import ParseError, TokenStream

type ErrorHandler = Callable[[ParseError], None] | logging.Logger

# Errors produced by the syntax parser itself, as opposed to the tokenizer.
# Tokenizer errors are reported once for the whole sheet, so only these are
# reported when blocks are parsed later.
SYNTAX_ERROR_KINDS = frozenset(('invalid-rule', 'invalid-declaration'))


class SourceLike(Protocol):
    """Where an object came from. Syntax nodes and tokens satisfy this, so
    parsed rules simply keep their syntax node."""

    @property
    def stream(self) -> TokenStream: ...
    @property
    def pos(self) -> int: ...
    @property
    def end(self) -> int: ...
    @property
    def source_text(self) -> str: ...
    @property
    def line(self) -> int: ...
    @property
    def column(self) -> int: ...


@dataclass(slots=True)
class Source:
    """A range of a TokenStream's source, for objects that do not correspond to one syntax node."""

    stream: TokenStream
    pos: int
    end: int

    @property
    def source_text(self) -> str:
        return self.stream.source[self.pos : self.end]

    @property
    def line(self) -> int:
        return self.stream.line_col(self.pos)[0]

    @property
    def column(self) -> int:
        return self.stream.line_col(self.pos)[1]


class SheetState:
    """Shared by every object of a parsed stylesheet.

    The lock guards lazy materialization, so that concurrent readers always
    see the same objects. Errors found later, while parsing blocks lazily, are
    added to the sheet's error list and sent to its handler.
    """

    __slots__ = ('batch', 'errors', 'href', 'lock', 'log')

    def __init__(self, log: ErrorHandler | None, href: str | None) -> None:
        self.lock = threading.RLock()
        self.log = log
        self.href = href
        self.errors: list[ParseError] = []
        self.batch: list[ParseError] | None = None

    def begin_batch(self) -> None:
        """Collect errors without reporting them, until end_batch() reports them in source order."""
        self.batch = []

    def end_batch(self) -> None:
        batch, self.batch = self.batch or [], None
        for error in sorted(batch, key=lambda e: e.offset):
            self.add(error)

    def add(self, error: ParseError) -> None:
        if self.batch is not None:
            self.batch.append(error)
            return
        self.errors.append(error)
        match self.log:
            case None:
                pass
            case logging.Logger():
                self.log.warning('%s:%d:%d: %s', self.href or '<stylesheet>', error.line, error.column, error.message)
            case _:
                self.log(error)

    def report(self, kind: str, message: str, stream: TokenStream, offset: int) -> None:
        line, column = stream.line_col(offset)
        self.add(ParseError(kind, message, offset, line, column))
