#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark color parsing: kgcss.parse_rgba and parse_color against calibre's C parser.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_color.py

bench_parse.py prepare downloads the corpus and calibre's compiled
calibre_extensions.tokenizer, whose parse_css4_color_string is what
tinycss.color4 uses. The workloads:

    single inputs   one call each, in nanoseconds, the inputs of
                    common colors, and text that is not a color
    color-values    the value of every declaration of the corpus whose
                    property name ends in color: mostly colors
    components      the text of every ident, hash and function in every
                    declaration of the corpus, as the editor's highlighter
                    sees them: mostly not colors

Times are the best of several runs. The counts of colors found show that
both did the same work.
"""

import argparse
import sys
import timeit
from collections.abc import Callable
from functools import cache

from bench_parse import CALIBRE, CORPUS, best_time

SINGLE = (
    'red', '#ff0000', 'rgb(255, 0, 0)', 'oklch(0.628 0.2577 29.23)', 'color(display-p3 0.9 0.2 0.1)', 'margin',
    'rgb(var(--x))', 'AliceBlue', 'rgba(0, 0, 0, .5)',
)  # fmt: skip

type Parser = Callable[[str], object]


@cache
def corpus_texts() -> tuple[tuple[str, ...], tuple[str, ...]]:
    import kgcss
    from kgcss.cssom.values import Function, Hash, Ident

    values: list[str] = []
    components: list[str] = []
    for path in sorted(CORPUS.glob('*.css')):
        sheet = kgcss.parse_stylesheet(path.read_text(encoding='utf-8'))
        for rule in sheet.iter_rules(kgcss.RuleType.STYLE):
            for prop in rule.style:  # type: ignore[attr-defined]
                if prop.name.endswith('color'):
                    values.append(prop.value)
                components.extend(c.css_text for c in prop.parsed_value.items if isinstance(c, (Ident, Hash, Function)))
    return tuple(values), tuple(components)


def parsers() -> dict[str, Parser]:
    import kgcss

    ans: dict[str, Parser] = {'kgcss': kgcss.parse_rgba, 'kgcss parse_color': kgcss.parse_color}
    sys.path.insert(0, str(CALIBRE))
    try:
        from calibre_extensions.tokenizer import parse_css4_color_string  # type: ignore[import-not-found]
    except ImportError:
        print('calibre_extensions is not in the benchmark cache, run bench/bench_parse.py prepare', file=sys.stderr)
    else:
        ans['calibre'] = parse_css4_color_string
    return ans


def single(parse: Parser, text: str) -> float:
    """Nanoseconds per call, without the overhead of a lambda."""
    timer = timeit.Timer('parse(text)', globals={'parse': parse, 'text': text})
    number, _ = timer.autorange()
    return min(timer.repeat(repeat=7, number=number)) / number * 1e9


def bulk(parse: Parser, texts: tuple[str, ...], budget: float) -> tuple[float, int]:
    def run(css: str) -> int:
        return sum(1 for t in texts if parse(t) is not None)

    return best_time(run, '', budget)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--budget', type=float, default=2, help='Seconds per bulk measurement')
    args = parser.parse_args()
    libs = parsers()
    print('| input | ' + ' | '.join(f'{name} ns' for name in libs) + ' |')
    print('|---|' + '---:|' * len(libs))
    for text in SINGLE:
        print(f'| `{text}` | ' + ' | '.join(f'{single(p, text):.0f}' for p in libs.values()) + ' |')
    print()
    values, components = corpus_texts()
    print('| workload | texts | ' + ' | '.join(f'{name} ms' for name in libs) + ' | colors |')
    print('|---|---:|' + '---:|' * (len(libs) + 1))
    for name, texts in (('color-values', values), ('components', components)):
        results = [bulk(p, texts, args.budget) for p in libs.values()]
        counts = '/'.join(sorted({str(n) for _, n in results}))
        print(f'| {name} | {len(texts)} | ' + ' | '.join(f'{t * 1e3:.2f}' for t, _ in results) + f' | {counts} |')


if __name__ == '__main__':
    main()
