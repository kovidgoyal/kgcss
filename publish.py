#!/usr/bin/env python
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Build the kgcss source distribution, tag the release and upload it to PyPI."""

import ast
import os
import shlex
import shutil
import subprocess
from pathlib import Path

NAME = 'kgcss'
ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'


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
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def twine() -> list[str]:
    exe = shutil.which('twine')
    return [exe] if exe else ['uvx', 'twine']


def check_repo() -> None:
    if output('git', 'status', '--porcelain', '--untracked-files=no').strip():
        raise SystemExit(red('The working tree has uncommitted changes'))
    if output('git', 'tag', '--list', f'v{VERSION}').strip():
        raise SystemExit(red(f'The tag v{VERSION} already exists, bump the version in src/{NAME}/_version.py'))


def build_release() -> list[Path]:
    for rem in (DIST, ROOT / 'build'):
        shutil.rmtree(rem, ignore_errors=True)
    # Only an sdist: the extension is compiled on the user's machine
    run('uv', 'build', '--sdist', '--out-dir', str(DIST))
    files = sorted(p for p in DIST.iterdir() if not p.name.startswith('.'))  # uv adds a .gitignore
    expected = [DIST / f'{NAME}-{VERSION}.tar.gz']
    if files != expected:
        raise SystemExit(red(f'Expected the build to produce {expected}, got {files}'))
    run(*twine(), 'check', '--strict', *map(str, files))
    return files


def tag_release() -> None:
    run('git', 'push')
    run('git', 'tag', '-s', f'v{VERSION}', '-m', f'version-{VERSION}')
    run('git', 'push', 'origin', f'v{VERSION}')


def upload_release(files: list[Path]) -> None:
    cmd = [*twine(), 'upload', '--repository', 'pypi']
    if penv := os.environ.get('PENV'):
        cmd += ['--config-file', os.path.join(penv, 'pypi')]
    run(*cmd, *map(str, files))


def main() -> None:
    check_repo()
    if input(f'Publish {NAME} version {red(VERSION)} [y/n]? ') != 'y':
        raise SystemExit(1)
    files = build_release()
    if input(red('Upload') + f' {", ".join(f.name for f in files)} to PyPI [y/n]? ') != 'y':
        raise SystemExit(1)
    tag_release()
    upload_release(files)


if __name__ == '__main__':
    main()
