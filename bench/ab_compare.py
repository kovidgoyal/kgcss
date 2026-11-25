#!/usr/bin/env python3
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Compare the speed of the working tree with a git revision (A/B).

    taskset -c 0-2 uv run --group bench python bench/ab_compare.py --base HEAD

Both versions are built into temporary directories, then measured in
alternating rounds, so that drift in CPU frequency or load affects both
equally. Each round runs the workload in a fresh process and records its best
time. The report shows the median of those per-round bests and the change.
Run bench_parse.py prepare first, for the corpus.
"""

import argparse
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'bench' / '.cache' / 'corpus'

CHILD = """
import gc, sys, time
build, path, workload, repeat = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
import kgcss
assert kgcss.__file__.startswith(build), kgcss.__file__
from bench_parse import kgcss_workloads
fn = kgcss_workloads()[workload]
css = open(path, encoding='utf-8').read()
# Warm up: with the powersave governor the CPU needs time to reach full speed
start = time.perf_counter()
while time.perf_counter() - start < 0.5:
    fn(css)
best = float('inf')
for _ in range(repeat):
    gc.collect()
    start = time.perf_counter()
    fn(css)
    best = min(best, time.perf_counter() - start)
print(best)
"""


def build(source: Path, dest: Path) -> None:
    subprocess.run(
        [sys.executable, 'setup.py', '-q', 'build_ext', '--build-lib', str(dest), '--build-temp', str(dest / '.temp')],
        cwd=source,
        check=True,
    )
    shutil.copytree(
        source / 'src' / 'kgcss',
        dest / 'kgcss',
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('*.so', '*.pyd', '__pycache__'),
    )


def measure(build_dir: Path, path: Path, workload: str, repeat: int) -> float:
    env_path = f'{build_dir}:{ROOT / "bench"}'
    out = subprocess.run(
        [sys.executable, '-c', CHILD, str(build_dir), str(path), workload, str(repeat)],
        env={'PYTHONPATH': env_path, 'PATH': '/usr/bin:/bin'},
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', default='HEAD', help='git revision to compare against (default: %(default)s)')
    parser.add_argument('--rounds', type=int, default=9, help='alternating rounds (default: %(default)s)')
    parser.add_argument('--repeat', type=int, default=10, help='runs per round, best is kept (default: %(default)s)')
    parser.add_argument('--workload', action='append', choices=('tokenize', 'parse', 'full'))
    parser.add_argument('--corpus', action='append', default=[], help='corpus files whose name contains this')
    args = parser.parse_args()
    files = sorted(CORPUS.glob('*.css'))
    if args.corpus:
        files = [f for f in files if any(c in f.name for c in args.corpus)]
    if not files:
        raise SystemExit('No corpus files, run bench/bench_parse.py prepare')
    workloads = args.workload or ['tokenize', 'parse', 'full']
    with tempfile.TemporaryDirectory(prefix='kgcss-ab-') as tmp:
        base_src, base, head = Path(tmp) / 'base-src', Path(tmp) / 'base', Path(tmp) / 'head'
        subprocess.run(['git', 'worktree', 'add', '-q', '--detach', str(base_src), args.base], cwd=ROOT, check=True)
        try:
            build(base_src, base)
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(base_src)], cwd=ROOT, check=True)
        build(ROOT, head)
        print(f'| file | workload | {args.base} ms | working tree ms | change |')
        print('|---|---|---:|---:|---:|')
        for path in files:
            for workload in workloads:
                times: dict[Path, list[float]] = {base: [], head: []}
                for i in range(args.rounds):
                    order = (base, head) if i % 2 == 0 else (head, base)
                    for variant in order:
                        times[variant].append(measure(variant, path, workload, args.repeat))
                a, b = statistics.median(times[base]), statistics.median(times[head])
                print(f'| {path.name} | {workload} | {a * 1e3:.2f} | {b * 1e3:.2f} | {b / a - 1:+.1%} |')
                sys.stdout.flush()


if __name__ == '__main__':
    main()
