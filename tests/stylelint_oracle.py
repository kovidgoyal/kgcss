# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Lint with the real stylelint, to compare kgcss.lint with it.

stylelint and @stylistic/stylelint-plugin, at the versions pinned in
bench/stylelint/package-lock.json, are installed into the benchmark cache by
`uv run --group bench python bench/bench_parse.py prepare`, which needs npm.
Tests that use the oracle skip themselves when it or Node is missing.

One Node process (tests/stylelint_oracle.mjs) serves all the requests of a
Stylelint object, as starting stylelint takes a large part of a second.
"""

import json
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import NotRequired, Self, TypedDict

import pytest

INSTALL_DIR = Path(__file__).resolve().parent.parent / 'bench' / '.cache' / 'stylelint'
SCRIPT = Path(__file__).resolve().parent / 'stylelint_oracle.mjs'

# A rule config as JSON, with {"$regexp": [source, flags]} for regular expressions
type Config = bool | int | float | str | list[Config] | dict[str, Config] | None


class EditInfo(TypedDict):
    range: list[int]  # [start, end): offsets in UTF-16 code units
    text: str


class StylelintWarning(TypedDict):
    rule: str
    severity: str
    text: str  # the message, followed by ' (rule-name)'
    line: int
    column: int  # 1-based, in UTF-16 code units
    endLine: NotRequired[int]
    endColumn: NotRequired[int]  # exclusive
    fix: NotRequired[EditInfo]


class LintResult(TypedDict):
    warnings: list[StylelintWarning]
    parseErrors: list[str]
    invalidOptionWarnings: list[str]
    code: str | None  # the fixed code, when fix was requested


def skip_reason() -> str | None:
    """Why the oracle cannot run, or None if it can."""
    if shutil.which('node') is None:
        return 'node is not installed'
    if not (INSTALL_DIR / 'node_modules' / 'stylelint' / 'package.json').exists():
        return 'stylelint is not in the benchmark cache (run bench/bench_parse.py prepare)'
    return None


def require_oracle() -> None:
    """Skip the calling test module if the oracle cannot run."""
    if (reason := skip_reason()) is not None:
        pytest.skip(reason, allow_module_level=True)


class Stylelint:
    """A running stylelint. Use it as a context manager, or call close()."""

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

    def lint(
        self, code: str, rules: Mapping[str, Config], *, fix: bool = False, compute_edit_info: bool = False
    ) -> LintResult:
        """Lint code with the rules, configured as in a stylelint config."""
        stdin, stdout = self.process.stdin, self.process.stdout
        assert stdin is not None
        assert stdout is not None
        request = {'code': code, 'rules': rules, 'fix': fix, 'computeEditInfo': compute_edit_info}
        stdin.write(json.dumps(request) + '\n')  # ASCII, so lone surrogates are escaped
        stdin.flush()
        line = stdout.readline()
        if not line:
            raise RuntimeError(f'The stylelint oracle exited with code {self.process.wait()}')
        response = json.loads(line)
        if 'error' in response:
            raise RuntimeError(f'stylelint failed on {code!r}: {response["error"]}')
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
