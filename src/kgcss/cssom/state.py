# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""State shared by the objects of one parsed stylesheet."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self

from kgcss.tokenizer import ParseError, TokenStream

type ErrorHandler = Callable[[ParseError], None] | logging.Logger

# Errors produced by the syntax parser itself, as opposed to the tokenizer.
# Tokenizer errors are reported once for the whole sheet, so only these are
# reported when blocks are parsed later.
SYNTAX_ERROR_KINDS = frozenset(('invalid-rule', 'invalid-declaration'))


class SupportsRichComparison(Protocol):
    """A sort key: a value that can be compared with others of its kind. Like typeshed's protocol of the
    same name, which only exists for type checkers, so it cannot be used in annotations evaluated at run time."""

    def __lt__(self, other: Self, /) -> bool: ...


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


class SheetLock:
    """The lock of a sheet, re-entrant. Errors reported by the thread that holds it are sent to the log handler
    only when that thread releases it, so that handlers, which are user code, never run under the lock: a
    handler that waits for another thread using the same sheet would deadlock."""

    __slots__ = ('_depth', '_lock', '_owner', '_pending', '_state')

    def __init__(self, state: SheetState) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._owner = 0
        self._pending: list[ParseError] = []
        self._state = state

    def __enter__(self) -> None:
        self._lock.acquire()
        if self._depth == 0:
            self._owner = threading.get_ident()
        self._depth += 1

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._depth -= 1
        pending = None
        if self._depth == 0:
            self._owner = 0
            if self._pending:
                pending, self._pending = self._pending, []
        self._lock.release()
        if pending:
            for error in pending:
                self._state.log_now(error)

    def defer(self, error: ParseError) -> bool:
        """Queue error to be logged when the lock is released, if the current thread holds it."""
        if self._depth and self._owner == threading.get_ident():
            self._pending.append(error)
            return True
        return False


class SheetState:
    """Shared by every object of a parsed stylesheet.

    The lock guards lazy materialization, so that concurrent readers always
    see the same objects. Errors found later, while parsing blocks lazily, are
    added to the sheet's error list and sent to its handler, once the lock is
    released.
    """

    __slots__ = ('batch', 'errors', 'href', 'lock', 'log')

    def __init__(self, log: ErrorHandler | None, href: str | None) -> None:
        self.lock = SheetLock(self)
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
        if self.log is not None and not self.lock.defer(error):
            self.log_now(error)

    def log_now(self, error: ParseError) -> None:
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
