#!/usr/bin/env python3
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Build and run the libFuzzer harness for the C tokenizer (fuzz/fuzz_tokenizer.c).

    python tools/fuzz.py build
    python tools/fuzz.py run --seconds 300 [-- extra libFuzzer options]

Needs clang with libFuzzer. The corpus lives in build/fuzz/corpus and is
seeded from the conformance suite inputs the first time. Crashing inputs are
written to build/fuzz/ as crash-*; reproduce one with
build/fuzz/fuzz_tokenizer build/fuzz/crash-<hash>.
"""

import argparse
import json
import os
import subprocess
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'build' / 'fuzz'
BINARY = OUT / 'fuzz_tokenizer'
CORPUS = OUT / 'corpus'
SOURCES = (ROOT / 'fuzz' / 'fuzz_tokenizer.c', ROOT / 'csrc' / 'tokenizer.c')


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    libdir = sysconfig.get_config_var('LIBDIR')
    cmd = [
        os.environ.get('CLANG', 'clang'),
        '-std=c99', '-g', '-O1', '-UNDEBUG',
        '-fsanitize=fuzzer,address,undefined', '-fno-sanitize-recover=undefined',
        '-Wall', '-Wextra', '-Werror', '-Wno-missing-field-initializers',
        f'-I{ROOT / "csrc"}', f'-I{sysconfig.get_path("include")}',
        *map(str, SOURCES),
        f'-L{libdir}', f'-Wl,-rpath,{libdir}', f'-lpython{sysconfig.get_config_var("LDVERSION")}',
        '-o', str(BINARY),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def seed_corpus() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    if any(CORPUS.iterdir()):
        return
    suite = ROOT / 'tests' / 'css-parsing-tests'
    inputs: set[str] = set()
    for name in ('component_value_list.json', 'stylesheet.json', 'blocks_contents.json', 'one_declaration.json'):
        data = json.loads((suite / name).read_text(encoding='utf-8'))
        inputs.update(x for x in data[::2] if isinstance(x, str))
    for i, text in enumerate(sorted(inputs)):
        raw = text.encode('utf-8', 'surrogatepass')
        (CORPUS / f'seed-{i}-utf8').write_bytes(raw)
        # Seeds for the 2 byte kind, which reinterprets the input as UTF-16 code units
        (CORPUS / f'seed-{i}-utf16').write_bytes(text.encode('utf-16-le', 'surrogatepass'))


def run(seconds: int, extra: list[str]) -> int:
    if not BINARY.exists():
        build()
    seed_corpus()
    env = os.environ | {'PYTHONMALLOC': 'malloc'}
    cmd = [str(BINARY), str(CORPUS), f'-max_total_time={seconds}', '-max_len=4096', '-timeout=10', *extra]
    return subprocess.run(cmd, cwd=OUT, env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('build', help='Build the fuzzer')
    r = sub.add_parser('run', help='Build if needed, then fuzz')
    r.add_argument('--seconds', type=int, default=60, help='How long to fuzz for (default: %(default)s)')
    r.add_argument('extra', nargs='*', help='Extra libFuzzer options, after --')
    args = parser.parse_args()
    if args.command == 'build':
        build()
        return 0
    return run(args.seconds, args.extra)


if __name__ == '__main__':
    raise SystemExit(main())
