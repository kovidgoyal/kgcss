# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Run the @csstools parsing libraries that stylelint uses, to compare the ports in kgcss.linter with them.

@csstools/css-tokenizer, @csstools/css-parser-algorithms and
@csstools/media-query-list-parser are installed with stylelint into the
benchmark cache (see tests/stylelint_oracle.py). One Node process
(tests/csstools_oracle.mjs) serves all the requests of a CSSTools object.
Positions in the responses are in UTF-16 code units, see utf16_offsets().
"""

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Self

from .stylelint_oracle import INSTALL_DIR, require_oracle, skip_reason, utf16_offsets

SCRIPT = Path(__file__).resolve().parent / 'csstools_oracle.mjs'

__all__ = ['CSSTools', 'require_oracle', 'skip_reason', 'utf16_offsets']


class CSSTools:
    """A running oracle. Use it as a context manager, or call close()."""

    def __init__(self) -> None:
        node = shutil.which('node')
        assert node is not None
        self.process = subprocess.Popen(
            [node, str(SCRIPT), str(INSTALL_DIR)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='surrogatepass',
        )

    def tokenize(self, css: str) -> dict[str, object]:
        """{'tokens', 'errors', 'stringify'}."""
        return self.request({'op': 'tokenize', 'css': css})

    def parse_list(
        self, css: str, *, comma: bool = False, mutate: bool = False, state: bool = True
    ) -> dict[str, object]:
        """The component value trees of css, and the visits of a walk of them (that mutates them if mutate)."""
        return self.request({'op': 'list', 'css': css, 'comma': comma, 'mutate': mutate, 'noState': not state})

    def mutate(self, css: str, unit: str, ident: str) -> dict[str, object]:
        """The tokens of css after mutateUnit() of every dimension and mutateIdent() of every ident."""
        return self.request({'op': 'mutate', 'css': css, 'unit': unit, 'ident': ident})

    def media(self, css: str, *, preserve: bool = True, state: bool = True) -> dict[str, object]:
        """The media queries that parse() gives, with the visits of their walks."""
        return self.request({'op': 'media', 'css': css, 'preserve': preserve, 'noState': not state})

    def custom_media(self, css: str, *, preserve: bool = True) -> dict[str, object]:
        """What parseCustomMedia() gives."""
        return self.request({'op': 'customMedia', 'css': css, 'preserve': preserve})

    def ratio(self, css: str) -> dict[str, object]:
        """[matchesRatio(), matchesRatioExactly()] of the component values of css."""
        return self.request({'op': 'ratio', 'css': css})

    def request[T](self, request: Mapping[str, object]) -> T:
        stdin, stdout = self.process.stdin, self.process.stdout
        assert stdin is not None
        assert stdout is not None
        stdin.write(json.dumps(request) + '\n')  # ASCII, so lone surrogates are escaped
        stdin.flush()
        line = stdout.readline()
        if not line:
            raise RuntimeError(f'The csstools oracle exited with code {self.process.wait()}')
        response = json.loads(line)
        if 'failure' in response:
            raise RuntimeError(f'The csstools oracle failed on {request!r}: {response["failure"]}')
        return response

    def close(self) -> None:
        """Stop the Node process."""
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        if self.process.stdout is not None:
            self.process.stdout.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()
