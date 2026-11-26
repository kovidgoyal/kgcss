#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark the font helpers: kgcss.fonts against tinycss.fonts3.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_fonts.py

bench_parse.py prepare downloads the corpus and tinycss. The measurements:

    single inputs   one call each, in microseconds
    corpus          every font-family value and every font value of the corpus
"""

import argparse
import sys
import timeit
from collections.abc import Callable
from functools import partial
from types import ModuleType

from bench_parse import CALIBRE, CORPUS, best_time

FAMILY = '"Helvetica Neue", Arial, sans-serif'
FONT = 'bold italic 12pt/1.4 "Helvetica Neue", serif'
NAMES = ['Helvetica Neue', 'Arial', 'sans-serif']
FONT_DICT = {'font-weight': 'bold', 'font-size': '12pt', 'line-height': '1.4', 'font-family': NAMES}


def us(fn: Callable[[], object]) -> float:
    timer = timeit.Timer(fn)
    number, _ = timer.autorange()
    return min(timer.repeat(repeat=7, number=number)) / number * 1e6


def corpus_values() -> tuple[tuple[str, ...], tuple[str, ...]]:
    import kgcss

    families, fonts = [], []
    for path in sorted(CORPUS.glob('*.css')):
        sheet = kgcss.parse_stylesheet(path.read_text(encoding='utf-8'))
        for rule in sheet.iter_rules(kgcss.RuleType.STYLE, kgcss.RuleType.FONT_FACE):
            for prop in rule.style:  # type: ignore[attr-defined]
                if prop.name == 'font-family':
                    families.append(prop.value)
                elif prop.name == 'font':
                    fonts.append(prop.value)
    return tuple(families), tuple(fonts)


def tinycss() -> ModuleType | None:
    sys.path.insert(0, str(CALIBRE))
    try:
        import tinycss.fonts3  # type: ignore[import-not-found]
    except ImportError:
        print('tinycss is not in the benchmark cache, run bench/bench_parse.py prepare', file=sys.stderr)
        return None
    return tinycss.fonts3  # type: ignore[no-any-return]


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    import kgcss.fonts

    libs: dict[str, ModuleType] = {'kgcss': kgcss.fonts}
    if (t := tinycss()) is not None:
        libs['tinycss'] = t
    print('| call | ' + ' | '.join(f'{name} µs' for name in libs) + ' |')
    print('|---|' + '---:|' * len(libs))
    for call, arg in (
        ('parse_font_family', FAMILY),
        ('parse_font', FONT),
        ('serialize_font_family', NAMES),
        ('serialize_font', FONT_DICT),
    ):
        cells = [f'{us(partial(getattr(m, call), arg)):.2f}' for m in libs.values()]
        print(f'| `{call}` | ' + ' | '.join(cells) + ' |')
    print()
    families, fonts = corpus_values()
    print('| workload | values | ' + ' | '.join(f'{name} ms' for name in libs) + ' |')
    print('|---|---:|' + '---:|' * len(libs))
    for name, call, texts in (('font-family', 'parse_font_family', families), ('font', 'parse_font', fonts)):
        cells = []
        for m in libs.values():
            fn = getattr(m, call)

            def run(css: str, fn: Callable[[str], object] = fn, texts: tuple[str, ...] = texts) -> int:
                return sum(1 for t in texts if fn(t))

            cells.append(f'{best_time(run, "", 2)[0] * 1e3:.2f}')
        print(f'| {name} | {len(texts)} | ' + ' | '.join(cells) + ' |')


if __name__ == '__main__':
    main()
