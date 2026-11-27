#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Build and run the libFuzzer harnesses in fuzz/.

    python tools/fuzz.py build [--target tokenizer|selectors|colors|linter]
    python tools/fuzz.py run --seconds 300 [--target tokenizer|selectors|colors|linter] [-- extra libFuzzer options]

Targets:

    tokenizer  the C tokenizer (fuzz/fuzz_tokenizer.c), the default
    selectors  the selector parser (fuzz/fuzz_selectors.c), with the whole
               extension compiled in and the Python package from src/
    colors     the color parser (fuzz/fuzz_colors.c), likewise
    linter     the C code of the linter (fuzz/fuzz_linter.c), likewise, compared with
               the Python code it ports by tests/linter_c_checks.py

Needs clang with libFuzzer. The corpus lives in build/fuzz/<target>/corpus
and is seeded the first time: from the conformance suite inputs for the
tokenizer, from the selector test tables for the selectors, and from the
color tests and the color files of the suite for the colors, and from the
selectors, values and rules of the corpus and the tests of the C code for
the linter. Crashing
inputs are written to build/fuzz/<target>/ as crash-*; reproduce one with
build/fuzz/<target>/fuzz_<target> build/fuzz/<target>/crash-<hash>.
"""

import argparse
import json
import os
import subprocess
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ('tokenizer', 'selectors', 'colors', 'linter')


def out_dir(target: str) -> Path:
    return ROOT / 'build' / 'fuzz' / target


def binary(target: str) -> Path:
    return out_dir(target) / f'fuzz_{target}'


def sources(target: str) -> list[Path]:
    harness = ROOT / 'fuzz' / f'fuzz_{target}.c'
    if target == 'tokenizer':
        return [harness, ROOT / 'csrc' / 'tokenizer.c']
    return [harness, *sorted((ROOT / 'csrc').glob('*.c'))]


def build(target: str) -> None:
    out_dir(target).mkdir(parents=True, exist_ok=True)
    libdir = sysconfig.get_config_var('LIBDIR')
    cmd = [
        os.environ.get('CLANG', 'clang'),
        '-std=c99', '-g', '-O1', '-UNDEBUG',
        '-fsanitize=fuzzer,address,undefined', '-fno-sanitize-recover=undefined',
        '-Wall', '-Wextra', '-Werror', '-Wno-missing-field-initializers',
        f'-I{ROOT / "csrc"}', f'-I{sysconfig.get_path("include")}',
        *map(str, sources(target)),
        f'-L{libdir}', f'-Wl,-rpath,{libdir}', f'-lpython{sysconfig.get_config_var("LDVERSION")}', '-lm',
        '-o', str(binary(target)),
    ]  # fmt: skip
    subprocess.run(cmd, check=True)


def seed_corpus(target: str) -> Path:
    corpus = out_dir(target) / 'corpus'
    corpus.mkdir(parents=True, exist_ok=True)
    if any(corpus.iterdir()):
        return corpus
    if target == 'linter':
        import ast

        texts: set[str] = set()
        for name in ('postcss', 'selector', 'value_parser', 'utils'):
            tree = ast.parse((ROOT / 'tests' / f'test_linter_{name}_c.py').read_text(encoding='utf-8'))
            texts.update(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        # Rules, selectors and values of real sheets
        for path in sorted((ROOT / 'bench' / '.cache' / 'corpus').glob('*.css'))[:2]:
            css = path.read_text(encoding='utf-8')
            for start in range(0, min(len(css), 200_000), 1000):
                texts.add(css[start : start + 300])
        for i, text in enumerate(sorted(texts)):
            (corpus / f'seed-{i}').write_bytes(text.encode('utf-8', 'surrogatepass'))
        return corpus
    if target in ('selectors', 'colors'):
        import ast

        # The strings in the tables of the tests
        tree = ast.parse((ROOT / 'tests' / f'test_{target}.py').read_text(encoding='utf-8'))
        texts = {
            node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        if target == 'colors':
            for path in (ROOT / 'tests' / 'css-parsing-tests').glob('color_*.json'):
                texts.update(x for x in json.loads(path.read_text(encoding='utf-8'))[::2] if isinstance(x, str))
        for i, text in enumerate(sorted(texts)):
            (corpus / f'seed-{i}').write_bytes(text.encode('utf-8', 'surrogatepass'))
        return corpus
    suite = ROOT / 'tests' / 'css-parsing-tests'
    inputs: set[str] = set()
    for name in ('component_value_list.json', 'stylesheet.json', 'blocks_contents.json', 'one_declaration.json'):
        data = json.loads((suite / name).read_text(encoding='utf-8'))
        inputs.update(x for x in data[::2] if isinstance(x, str))
    for i, text in enumerate(sorted(inputs)):
        raw = text.encode('utf-8', 'surrogatepass')
        (corpus / f'seed-{i}-utf8').write_bytes(raw)
        # Seeds for the 2 byte kind, which reinterprets the input as UTF-16 code units
        (corpus / f'seed-{i}-utf16').write_bytes(text.encode('utf-16-le', 'surrogatepass'))
    return corpus


def run(target: str, seconds: int, extra: list[str]) -> int:
    if not binary(target).exists():
        build(target)
    corpus = seed_corpus(target)
    path = [str(ROOT / 'src')] + ([str(ROOT)] if target == 'linter' else [])  # tests.linter_c_checks
    env = os.environ | {'PYTHONMALLOC': 'malloc', 'PYTHONPATH': os.pathsep.join(path)}
    # Python's own allocations at exit are not leaks of ours
    env.setdefault('ASAN_OPTIONS', 'detect_leaks=0')
    cmd = [str(binary(target)), str(corpus), f'-max_total_time={seconds}', '-max_len=4096', '-timeout=10', *extra]
    return subprocess.run(cmd, cwd=out_dir(target), env=env, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='Build the fuzzer')
    b.add_argument('--target', choices=TARGETS, default='tokenizer')
    r = sub.add_parser('run', help='Build if needed, then fuzz')
    r.add_argument('--target', choices=TARGETS, default='tokenizer')
    r.add_argument('--seconds', type=int, default=60, help='How long to fuzz for (default: %(default)s)')
    r.add_argument('extra', nargs='*', help='Extra libFuzzer options, after --')
    args = parser.parse_args()
    if args.command == 'build':
        build(args.target)
        return 0
    return run(args.target, args.seconds, args.extra)


if __name__ == '__main__':
    raise SystemExit(main())
