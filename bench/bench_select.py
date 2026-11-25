#!/usr/bin/env python3
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark selector matching: kgcss.Select against calibre's css_selectors.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_select.py

bench_parse.py prepare downloads css_selectors (pinned) and the CSS corpus.
The workloads:

    construct-1k, -10k, -50k  Select(root) alone on synthetic book chapters of
                              about that many elements; css_selectors builds
                              its indexes lazily, so it is not compared here
    stylizer-calibre          a fresh Select, then each of the first 2,000
                              selectors of the calibre-style sheet, against a
                              chapter that uses its classes, as calibre's
                              stylizer does for every document
    stylizer-bootstrap        the same with bootstrap's 3,000 selectors against
                              the Shakespeare document of the css_selectors
                              tests, ignoring inappropriate pseudo-classes and
                              skipping selectors that raise, as calibre does
    level4                    :is(), :has(), nth-child(of S) and long
                              descendant chains on the Shakespeare document

Times are the best of several runs. The match counts show that both did the
same work.
"""

import argparse
import os
import random
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from bench_parse import CALIBRE, CORPUS, Workload, best_time

ROOT = Path(__file__).resolve().parent.parent
SHAKESPEARE = ROOT / 'tests' / 'data' / 'css_selectors_shakespeare.html'
XHTML = 'http://www.w3.org/1999/xhtml'
LEVEL4 = (
    'div:has(> div.dialog)', ':is(div.character, div.dialog) div', 'div:nth-child(2n+1 of .dialog)',
    'div div div div div', 'div:not(.dialog) > div:has(+ div)', 'div:where(.scene, .dialog) ~ div',
    'body :has(.direction)', 'div:nth-last-child(odd of :not(.character))', 'div:has(~ div.direction) + div',
)  # fmt: skip
# An lxml element
type Element = Any
CONSTRUCT_SIZES = {'construct-1k': 330, 'construct-10k': 3300, 'construct-50k': 16500}


@cache
def chapter(paragraphs: int) -> Element:
    """A synthetic chapter in the style of calibre's output: about 3 elements per paragraph."""
    from lxml import etree

    rng = random.Random(paragraphs)
    parts = [f'<html xmlns="{XHTML}"><head><title>t</title></head><body class="calibre">']
    for i in range(paragraphs):
        if rng.random() < 0.05:
            parts.append(f'<h2 class="calibre{rng.randrange(400)}" id="h{i}">Heading {i}</h2>')
        parts.append(
            f'<p class="calibre{rng.randrange(400)}">Text <span class="calibre{rng.randrange(400)}">emph</span> '
            f'and <a href="#n{i}" class="calibre{rng.randrange(400)}">link</a></p>'
        )
        if rng.random() < 0.02:
            parts.append(f'<div class="calibre{rng.randrange(400)}"><img src="i.png" alt=""/></div>')
    parts.append('</body></html>')
    return etree.fromstring(''.join(parts).encode())


@cache
def shakespeare() -> Element:
    from lxml import html

    return html.document_fromstring(SHAKESPEARE.read_text(encoding='utf-8'))


@cache
def sheet_selectors(name: str, limit: int | None = None) -> tuple[str, ...]:
    import kgcss

    sheet = kgcss.parse_stylesheet((CORPUS / name).read_text(encoding='utf-8'))
    texts = [s.text for r in sheet.iter_rules(kgcss.RuleType.STYLE) for s in r.selectors]  # type: ignore[attr-defined]
    return tuple(texts[:limit])


def run_selectors(
    select: Callable[[str], Any], selectors: tuple[str, ...], errors: tuple[type[BaseException], ...]
) -> int:
    count = 0
    for selector in selectors:
        try:
            for _ in select(selector):
                count += 1
        except errors:
            pass
    return count


def kgcss_select_workloads() -> dict[str, Workload]:
    import kgcss

    errors = (kgcss.SelectorError,)

    def stylizer(document: Element, selectors: tuple[str, ...]) -> Workload:
        def run(css: str) -> int:
            return run_selectors(kgcss.Select(document, True, quirks_mode=True), selectors, errors)

        return run

    def construct(paragraphs: int) -> Workload:
        def run(css: str) -> int:
            return kgcss.Select(chapter(paragraphs))._snapshot.count

        return run

    return {
        **{name: construct(n) for name, n in CONSTRUCT_SIZES.items()},
        'stylizer-calibre': stylizer(chapter(1500), sheet_selectors('calibre-style.css', 2000)),
        'stylizer-bootstrap': stylizer(shakespeare(), sheet_selectors('bootstrap-5.3.3.css')),
        'level4': stylizer(shakespeare(), LEVEL4),
    }


def css_selectors_select_workloads() -> dict[str, Workload]:
    if str(CALIBRE) not in sys.path:
        sys.path.insert(0, str(CALIBRE))
    import css_selectors  # type: ignore[import-not-found]

    errors = (css_selectors.SelectorError,)

    def stylizer(document: Element, selectors: tuple[str, ...]) -> Workload:
        def run(css: str) -> int:
            select = css_selectors.Select(document, ignore_inappropriate_pseudo_classes=True)
            return run_selectors(select, selectors, errors)

        return run

    return {
        'stylizer-calibre': stylizer(chapter(1500), sheet_selectors('calibre-style.css', 2000)),
        'stylizer-bootstrap': stylizer(shakespeare(), sheet_selectors('bootstrap-5.3.3.css')),
        'level4': stylizer(shakespeare(), LEVEL4),
    }


LIBRARIES: dict[str, Callable[[], dict[str, Workload]]] = {
    'kgcss': kgcss_select_workloads,
    'css_selectors': css_selectors_select_workloads,
}
WORKLOADS = (*CONSTRUCT_SIZES, 'stylizer-calibre', 'stylizer-bootstrap', 'level4')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lib', action='append', choices=list(LIBRARIES), help='Libraries to run (default: all)')
    parser.add_argument('--workload', action='append', choices=WORKLOADS, help='Workloads to run (default: all)')
    parser.add_argument(
        '--budget', type=float, default=float(os.environ.get('BENCH_BUDGET', '2')), help='Seconds per measurement'
    )
    args = parser.parse_args()
    if not (CALIBRE / 'css_selectors').exists():
        raise SystemExit('Run bench/bench_parse.py prepare first')
    libraries = {name: LIBRARIES[name]() for name in args.lib or list(LIBRARIES)}
    print('| workload | ' + ' | '.join(f'{lib} ms' for lib in libraries) + ' | matches |')
    print('|---|' + '---:|' * (len(libraries) + 1))
    for workload in args.workload or WORKLOADS:
        cells, counts = [], set()
        for workloads in libraries.values():
            fn = workloads.get(workload)
            if fn is None:
                cells.append('-')
                continue
            t, n = best_time(fn, '', args.budget)
            cells.append(f'{t * 1e3:.2f}')
            counts.add(n)
        print(f'| {workload} | ' + ' | '.join(cells) + f' | {"/".join(map(str, sorted(counts)))} |')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
