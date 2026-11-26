#!/usr/bin/env python
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Release kgcss: push master and a signed v<version> tag.

The tag starts .github/workflows/release.yml, which builds the sdist and the wheels and uploads
them to PyPI with Trusted Publishing.
"""

import ast
import shlex
import subprocess
from pathlib import Path

NAME = 'kgcss'
BRANCH = 'master'
ROOT = Path(__file__).resolve().parent
ACTIONS_URL = 'https://github.com/kovidgoyal/kgcss/actions/workflows/release.yml'


def read_version() -> str:
    """Read __version__ from src/kgcss/_version.py without importing anything from the package."""
    path = ROOT / 'src' / NAME / '_version.py'
    for node in ast.parse(path.read_text(encoding='utf-8'), str(path)).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise SystemExit(f'No __version__ string found in {path}')


VERSION = read_version()
TAG = f'v{VERSION}'


def red(text: str) -> str:
    return '\033[91;1m' + text + '\033[39;22m'


def green(text: str) -> str:
    return '\033[92;1m' + text + '\033[39;22m'


def run(*cmd: str) -> None:
    print(green(shlex.join(cmd)), flush=True)
    ret = subprocess.run(cmd, cwd=ROOT).returncode
    if ret != 0:
        raise SystemExit(ret)


def output(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def check_repo() -> None:
    if (branch := output('git', 'branch', '--show-current')) != BRANCH:
        raise SystemExit(red(f'Releases are made from {BRANCH}, not {branch or "a detached HEAD"}'))
    if output('git', 'status', '--porcelain', '--untracked-files=no'):
        raise SystemExit(red('The working tree has uncommitted changes'))
    if output('git', 'tag', '--list', TAG) or output('git', 'ls-remote', '--tags', 'origin', f'refs/tags/{TAG}'):
        raise SystemExit(red(f'The tag {TAG} already exists, bump the version in src/{NAME}/_version.py'))


def main() -> None:
    check_repo()
    if input(f'Publish {NAME} version {red(VERSION)} [y/n]? ') != 'y':
        raise SystemExit(1)
    run('git', 'push', 'origin', BRANCH)
    run('git', 'tag', '-s', TAG, '-m', f'version-{VERSION}')
    run('git', 'push', 'origin', TAG)
    print(f'The release workflow now builds and uploads {TAG}, follow it at:\n{ACTIONS_URL}')


if __name__ == '__main__':
    main()
