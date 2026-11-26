# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The test cases of the stylelint rules, extracted into tests/stylelint-rules.

See tools/extract_stylelint_tests.py for where they come from and what the
fields mean.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import NotRequired, TypedDict

from .stylelint_oracle import Config, EditInfo

FIXTURES = Path(__file__).resolve().parent / 'stylelint-rules'


class ExpectedWarning(TypedDict):
    message: NotRequired[str]  # ends with ' (rule-name)'
    line: NotRequired[int]
    column: NotRequired[int]
    endLine: NotRequired[int]
    endColumn: NotRequired[int]
    fix: NotRequired[EditInfo]


class AcceptCase(TypedDict):
    code: str
    description: NotRequired[str]
    fixed: NotRequired[str]


class RejectCase(TypedDict):
    code: str
    description: NotRequired[str]
    fixed: NotRequired[str]
    warnings: list[ExpectedWarning]


class Group(TypedDict):
    config: Config
    compute_edit_info: bool
    accept: list[AcceptCase]
    reject: list[RejectCase]


class Fixture(TypedDict):
    rule: str  # as stylelint names it: @stylistic/no-extra-semicolons for the plugin's rule
    source: str
    groups: list[Group]


@dataclass(frozen=True, slots=True)
class Case:
    """One test case, with the rule and config it is for."""

    id: str
    rule: str
    config: Config
    compute_edit_info: bool
    code: str
    fixed: str | None  # the expected result of fixing, or None if fixing is not checked
    warnings: tuple[ExpectedWarning, ...]  # empty for accept cases


@cache
def fixtures() -> tuple[Fixture, ...]:
    ans = []
    for path in sorted(FIXTURES.glob('*.json')):
        with path.open(encoding='utf-8') as f:
            ans.append(json.load(f))
    return tuple(ans)


def cases() -> Iterator[Case]:
    for fixture in fixtures():
        name = fixture['rule'].removeprefix('@stylistic/')
        for g, group in enumerate(fixture['groups']):
            for i, case in enumerate(group['accept']):
                yield Case(
                    f'{name}-{g}-accept-{i}', fixture['rule'], group['config'], group['compute_edit_info'],
                    case['code'], case.get('fixed'), (),
                )  # fmt: skip
            for i, case in enumerate(group['reject']):
                yield Case(
                    f'{name}-{g}-reject-{i}', fixture['rule'], group['config'], group['compute_edit_info'],
                    case['code'], case.get('fixed'), tuple(case['warnings']),
                )  # fmt: skip
