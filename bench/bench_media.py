#!/usr/bin/env python3
# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark media queries: kgcss.parse_media_queries against tinycss's media3 parser.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_media.py

bench_parse.py prepare downloads the corpus and tinycss. The measurements:

    single queries   one call each, in microseconds: parsing without the cache,
                     with it, and media_matches() with calibre's policy, against
                     tinycss parsing (it has no cache) and calibre's media_ok()
    corpus           every @media and @import media list of the corpus,
                     parsed without the cache
"""

import argparse
import sys
import timeit
from collections.abc import Callable
from functools import partial

from bench_parse import CALIBRE, CORPUS, best_time

SINGLE = (
    'screen',
    'only screen and (min-width: 600px) and (max-width: 900px)',
    '(width >= 600px) or ((color) and not (hover))',
    'amzn-kf8',
)
ALLOWED = frozenset({'screen', 'all', 'aural', 'amzn-kf8'})
IGNORED = frozenset(('width', 'min-width', 'max-width', 'height', 'min-height', 'max-height', 'color'))


def us(fn: Callable[[], object]) -> float:
    timer = timeit.Timer(fn)
    number, _ = timer.autorange()
    return min(timer.repeat(repeat=7, number=number)) / number * 1e6


def corpus_media() -> tuple[str, ...]:
    import kgcss

    texts = []
    for path in sorted(CORPUS.glob('*.css')):
        sheet = kgcss.parse_stylesheet(path.read_text(encoding='utf-8'))
        for rule in sheet.iter_rules(kgcss.RuleType.MEDIA, kgcss.RuleType.IMPORT):
            texts.append(rule.media.media_text)  # type: ignore[attr-defined]
    return tuple(texts)


def tinycss() -> Callable[[str], list[object]] | None:
    sys.path.insert(0, str(CALIBRE))
    try:
        from tinycss.media3 import CSSMedia3Parser  # type: ignore[import-not-found]
        from tinycss.tokenizer import tokenize_grouped  # type: ignore[import-not-found]
    except ImportError:
        print('tinycss is not in the benchmark cache, run bench/bench_parse.py prepare', file=sys.stderr)
        return None
    parser = CSSMedia3Parser()

    def parse(text: str) -> list[object]:
        return parser.parse_media(list(tokenize_grouped(text)), [])  # type: ignore[no-any-return]

    return parse


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    from kgcss.media import MediaFeature, media_matches, parse_media_queries

    uncached = parse_media_queries.__wrapped__
    tiny = tinycss()

    def feature(f: MediaFeature) -> bool | None:
        return False if f.name in IGNORED else None

    print('| query | kgcss parse µs | kgcss cached µs | kgcss media_matches µs | tinycss parse µs |')
    print('|---|---:|---:|---:|---:|')
    for text in SINGLE:
        parse_media_queries(text)
        cells = [
            us(partial(uncached, text)),
            us(partial(parse_media_queries, text)),
            us(partial(media_matches, text, media_type=ALLOWED.__contains__, feature=feature)),
        ]
        tiny_cell = f'{us(partial(tiny, text)):.2f}' if tiny is not None else '-'
        print(f'| `{text}` | ' + ' | '.join(f'{c:.2f}' for c in cells) + f' | {tiny_cell} |')
    print()
    texts = corpus_media()

    def run_kgcss(css: str) -> int:
        return sum(len(uncached(t)) for t in texts)

    print('| workload | lists | kgcss ms | tinycss ms | queries |')
    print('|---|---:|---:|---:|---:|')
    t, n = best_time(run_kgcss, '', 2)
    tiny_cell = '-'
    if tiny is not None:

        def run_tiny(css: str) -> int:
            return sum(len(tiny(t)) for t in texts)

        tt, tn = best_time(run_tiny, '', 2)
        tiny_cell = f'{tt * 1e3:.2f}'
        n_text = f'{n}/{tn}'
    else:
        n_text = str(n)
    print(f'| corpus | {len(texts)} | {t * 1e3:.2f} | {tiny_cell} | {n_text} |')


if __name__ == '__main__':
    main()
