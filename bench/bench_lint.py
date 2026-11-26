#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark kgcss.lint against stylelint 18 (in Node), with all the rules of kgcss.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_lint.py

bench_parse.py prepare downloads the corpus and installs stylelint (which
needs npm). For each corpus file, the best time of several runs of:

    kgcss         kgcss.lint(css)
    kgcss fix     kgcss.lint(css, fix=True)
    stylelint     stylelint.lint() in one Node process (startup not counted)

The problem counts show that both did comparable work.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from bench_parse import CORPUS, STYLELINT, best_time

SCRIPT = Path(__file__).resolve().parent / 'stylelint' / 'bench_lint.mjs'


def stylelint_times(files: list[Path], budget: float) -> dict[str, tuple[float, int]]:
    from kgcss import LINT_RULES

    node = shutil.which('node')
    if node is None or not (STYLELINT / 'node_modules' / 'stylelint').exists():
        print('stylelint is not installed: run bench/bench_parse.py prepare (needs npm)', file=sys.stderr)
        return {}
    rules = json.dumps({info.stylelint_name: True for info in LINT_RULES.values()})
    out = subprocess.run(
        [node, str(SCRIPT), str(STYLELINT), str(budget), rules, *map(str, files)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    ans = {}
    for line in out.splitlines():
        r = json.loads(line)
        ans[Path(r['file']).name] = r['ms'] / 1000, r['warnings']
    return ans


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--budget', type=float, default=3, help='Seconds to spend on each measurement')
    parser.add_argument('--corpus', nargs='*', help='Names of corpus files (default: all)')
    args = parser.parse_args()
    import kgcss

    files = sorted(CORPUS.glob('*.css'))
    if args.corpus:
        files = [f for f in files if f.name in args.corpus]
    if not files:
        raise SystemExit('No corpus: run bench/bench_parse.py prepare')
    theirs = stylelint_times(files, args.budget)
    print('| file | KiB | kgcss ms | kgcss fix ms | stylelint ms | kgcss problems | stylelint warnings |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    for path in files:
        css = path.read_text(encoding='utf-8')
        lint, problems = best_time(lambda css: len(kgcss.lint(css).problems), css, args.budget)
        fix, _ = best_time(lambda css: len(kgcss.lint(css, fix=True).problems), css, args.budget)
        their_time, warnings = theirs.get(path.name, (float('nan'), -1))
        print(
            f'| {path.name} | {len(css) / 1024:.0f} | {lint * 1e3:.0f} | {fix * 1e3:.0f} | {their_time * 1e3:.0f} '
            f'| {problems} | {warnings} |'
        )


if __name__ == '__main__':
    main()
