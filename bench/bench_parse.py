#!/usr/bin/env python3
# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Benchmark kgcss against calibre's tinycss (with its C tokenizer), tinycss2 and css_parser.

    uv run --group bench python bench/bench_parse.py prepare
    taskset -c 0-2 uv run --group bench python bench/bench_parse.py run
    taskset -c 0-2 uv run --group bench python bench/bench_parse.py run --suite cssom

prepare downloads the corpus and calibre's tinycss (pinned, checksummed) into
bench/.cache, builds calibre's C tokenizer, and generates the synthetic
sheets. run measures, for every library and corpus file, the syntax suite:

    tokenize  produce all tokens (tinycss2 has no flat tokenizer, so this is
              its component value parser, which is also how its parsers work)
    parse     parse the stylesheet into rules
    full      parse, then read every declaration's name, value text and
              priority, at every nesting depth (the typical calibre workload)

or the cssom suite, comparing the object models of kgcss and css_parser:

    cssom-parse      parse into the object model
    cssom-full       parse, then read every selector and every declaration's
                     name, value and priority, at every depth
    cssom-urls       parse, then rewrite every URL
    cssom-serialize  serialize a parsed sheet, all of whose declarations were
                     read, in the default (pretty) format

Times are the best of several runs. The declaration counts show that each
library did comparable work.
"""

import argparse
import gc
import hashlib
import importlib
import os
import random
import subprocess
import sys
import sysconfig
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / '.cache'
CORPUS = CACHE / 'corpus'
CALIBRE = CACHE / 'calibre'

DOWNLOADS = {
    'bootstrap-5.3.3.css': (
        'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.css',
        '18a105d7cb38e01e5ed0ca255c092992a2e211b39594a7fa57262bfc6fc4ea9c',
    ),
    'tailwind-2.2.19.css': (
        'https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.css',
        'de305d923dde1f8559e7734bb1f01970af3a76efc844b94ca3c8488fea8da3f7',
    ),
}
CALIBRE_COMMIT = '01ad1f246bcd206ea38ed5de1c8878011dbabcad'
TINYCSS_FILES = (
    '__init__.py', 'css21.py', 'decoding.py', 'parsing.py', 'token_data.py', 'tokenizer.py', 'version.py',
    'color3.py', 'color4.py', 'fonts3.py', 'media3.py', 'page3.py', 'tokenizer.c', 'color.c', 'color.h',
)  # fmt: skip
# Stand ins for the parts of calibre that tinycss imports
POLYGLOT_SHIMS = {
    'binary.py': 'def from_hex_bytes(x):\n    return bytes.fromhex(x.decode("ascii") if isinstance(x, bytes) else x)\n',
    'builtins.py': 'def error_message(e):\n    return str(e)\n',
}


def download(url: str, dest: Path, sha256: str | None = None) -> None:
    if dest.exists():
        return
    print('Downloading', url)
    with urllib.request.urlopen(url, timeout=60) as f:
        data = f.read()
    if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f'Checksum mismatch for {url}')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


# ---------------------------------------------------------------------------
# Synthetic sheets
# ---------------------------------------------------------------------------


def calibre_style_sheet(rng: random.Random, rules: int, fonts: list[str], texts: list[str]) -> str:
    """A sheet in the style of calibre conversion output: many .calibreN class rules."""
    props = [
        ('display', ['block', 'inline', 'inline-block', 'none']),
        ('font-size', ['1em', '0.83333em', '1.125em', '75%', 'small', '12pt']),
        ('font-weight', ['bold', 'normal', '700']),
        ('font-style', ['italic', 'normal']),
        ('font-family', [f'"{f}", serif' for f in fonts]),
        ('line-height', ['1.2', 'normal', '1.5em']),
        ('margin', ['0', '0 5pt', '0.83em 0', '1em 0 0.5em 0']),
        ('margin-left', ['0', '2em', '-1.5em']),
        ('padding-left', ['0', '5pt']),
        ('text-align', ['justify', 'center', 'left']),
        ('text-indent', ['0', '1.5em', '-1em']),
        ('color', ['#000', '#333333', 'rgb(51, 51, 51)', 'black']),
        ('background', ['url(images/bg.png) no-repeat', 'transparent', '#fff']),
        ('page-break-before', ['always', 'auto']),
        ('content', [f'"{t}"' for t in texts]),
    ]
    out = ['@charset "utf-8";\n', '@page { margin-bottom: 5pt; margin-top: 5pt }\n']
    for i, font in enumerate(fonts):
        out.append(f'@font-face {{ font-family: "{font}"; src: url(fonts/font{i}.ttf) }}\n')
    for i in range(rules):
        decls = '; '.join(f'{name}: {rng.choice(values)}' for name, values in rng.sample(props, rng.randint(1, 6)))
        if rng.random() < 0.1:
            decls += ' !important'
        selector = f'.calibre{i}' if rng.random() < 0.8 else f'.calibre{i} > p, span.calibre{i}'
        out.append(f'{selector} {{\n    {decls}\n    }}\n')
        if rng.random() < 0.02:
            out.append(f'@media screen and (max-width: 600px) {{ .calibre{i} {{ display: none }} }}\n')
        if rng.random() < 0.05:
            out.append(f'/* {rng.choice(texts)} */\n')
    return ''.join(out)


def generate_synthetic() -> None:
    sheets = {
        # ASCII only: kind 1
        'calibre-style.css': (['Liberation Serif', 'Georgia', 'Charis SIL'], ['*', '-', 'Chapter']),
        # CJK text: kind 2
        'calibre-style-cjk.css': (['宋体', 'Noto Serif CJK JP', '明朝'], ['第一章', '注釈', '目次']),
        # Emoji: kind 4
        'calibre-style-emoji.css': (['Noto Color Emoji', 'Georgia'], ['😀', '📖 Chapter', '★']),
    }
    for name, (fonts, texts) in sheets.items():
        text = calibre_style_sheet(random.Random(name), 15000, fonts, texts)
        (CORPUS / name).write_text(text, encoding='utf-8')


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def prepare() -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for name, (url, sha256) in DOWNLOADS.items():
        download(url, CORPUS / name, sha256)
    generate_synthetic()
    base = f'https://raw.githubusercontent.com/kovidgoyal/calibre/{CALIBRE_COMMIT}/src/tinycss/'
    for name in TINYCSS_FILES:
        download(base + name, CALIBRE / 'tinycss' / name)
    for name, src in POLYGLOT_SHIMS.items():
        dest = CALIBRE / 'polyglot' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src)
    (CALIBRE / 'polyglot' / '__init__.py').touch()
    ext_dir = CALIBRE / 'calibre_extensions'
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / '__init__.py').touch()
    target = ext_dir / f'tokenizer{sysconfig.get_config_var("EXT_SUFFIX")}'
    if not target.exists():
        print('Building calibre tinycss C tokenizer')
        cc = (sysconfig.get_config_var('CC') or 'cc').split()
        subprocess.run(
            [*cc, '-shared', '-fPIC', '-O3', '-DCALIBRE_MODINIT_FUNC=PyMODINIT_FUNC',
             f'-I{sysconfig.get_path("include")}',
             str(CALIBRE / 'tinycss' / 'tokenizer.c'), str(CALIBRE / 'tinycss' / 'color.c'), '-o', str(target)],
            check=True,
        )  # fmt: skip
    print('Corpus:', ', '.join(sorted(p.name for p in CORPUS.glob('*.css'))))


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------

type Workload = Callable[[str], int]


def kgcss_workloads() -> dict[str, Workload]:
    import kgcss
    import kgcss.syntax
    import kgcss.tokenizer

    def tokenize(css: str) -> int:
        return len(kgcss.tokenizer.tokenize(css))

    def parse(css: str) -> int:
        return len(kgcss.syntax.parse_stylesheet(css))

    def full(css: str) -> int:
        count = 0
        stack = [kgcss.syntax.parse_stylesheet(css)]
        while stack:
            for node in stack.pop():
                if isinstance(node, kgcss.syntax.Declaration):
                    count += 1
                    _ = (node.name, node.value.text, node.important)
                elif isinstance(node, kgcss.syntax.AtRule | kgcss.syntax.QualifiedRule) and node.block is not None:
                    _ = node.prelude.text
                    stack.append(kgcss.syntax.parse_block_contents(node.block))
        return count

    return {'tokenize': tokenize, 'parse': parse, 'full': full, **kgcss_cssom_workloads()}


def rewrite_url(url: str) -> str:
    return 'images/' + url


def kgcss_cssom_workloads() -> dict[str, Workload]:
    import kgcss

    def read_all(sheet: kgcss.Stylesheet) -> int:
        count = 0
        for rule in sheet.iter_rules():
            if isinstance(rule, kgcss.StyleRule):
                _ = rule.selector_text
            if (style := getattr(rule, 'style', None)) is not None:
                for prop in style.items:
                    if isinstance(prop, kgcss.Property):
                        count += 1
                        _ = (prop.name, prop.value, prop.priority)
        return count

    def parse(css: str) -> int:
        return len(kgcss.parse_stylesheet(css).rules)

    def full(css: str) -> int:
        return read_all(kgcss.parse_stylesheet(css))

    def urls(css: str) -> int:
        sheet = kgcss.parse_stylesheet(css)
        kgcss.replace_urls(sheet, rewrite_url)
        return len(sheet.rules)

    parsed: dict[str, kgcss.Stylesheet] = {}

    def serialize(css: str) -> int:
        if (sheet := parsed.get(css)) is None:
            parsed[css] = sheet = kgcss.parse_stylesheet(css)
            read_all(sheet)
        return len(sheet.css_text)

    return {'cssom-parse': parse, 'cssom-full': full, 'cssom-urls': urls, 'cssom-serialize': serialize}


def import_calibre_tinycss() -> ModuleType:
    if str(CALIBRE) not in sys.path:
        sys.path.insert(0, str(CALIBRE))
    tokenizer = importlib.import_module('tinycss.tokenizer')
    if tokenizer.c_tokenize_flat is None:
        raise SystemExit('calibre tinycss C tokenizer did not load, run prepare')
    return importlib.import_module('tinycss.css21')


def tinycss_workloads() -> dict[str, Workload]:
    css21 = import_calibre_tinycss()
    tokenizer = importlib.import_module('tinycss.tokenizer')

    def tokenize(css: str) -> int:
        return len(tokenizer.tokenize_flat(css, ignore_comments=False))

    def parse(css: str) -> int:
        return len(css21.CSS21Parser().parse_stylesheet(css).rules)

    def iter_declarations(rules: list[object]) -> Iterator[object]:
        for rule in rules:
            yield from getattr(rule, 'declarations', None) or ()
            yield from iter_declarations(getattr(rule, 'rules', None) or [])

    def full(css: str) -> int:
        count = 0
        for decl in iter_declarations(css21.CSS21Parser().parse_stylesheet(css).rules):
            count += 1
            _ = (decl.name, decl.value.as_css(), decl.priority)  # type: ignore[attr-defined]
        return count

    return {'tokenize': tokenize, 'parse': parse, 'full': full}


def tinycss2_workloads() -> dict[str, Workload]:
    tinycss2 = importlib.import_module('tinycss2')

    def tokenize(css: str) -> int:
        return len(tinycss2.parse_component_value_list(css))

    def parse(css: str) -> int:
        return len(tinycss2.parse_stylesheet(css))

    def full(css: str) -> int:
        count = 0
        stack = [tinycss2.parse_stylesheet(css)]
        while stack:
            for node in stack.pop():
                if node.type == 'declaration':
                    count += 1
                    _ = (node.name, tinycss2.serialize(node.value), node.important)
                elif node.type in ('qualified-rule', 'at-rule') and node.content is not None:
                    tinycss2.serialize(node.prelude)
                    stack.append(tinycss2.parse_blocks_contents(node.content))
        return count

    return {'tokenize': tokenize, 'parse': parse, 'full': full}


def css_parser_workloads() -> dict[str, Workload]:
    import logging

    css_parser = importlib.import_module('css_parser')
    tokenize2 = importlib.import_module('css_parser.tokenize2')
    parser = css_parser.CSSParser(loglevel=logging.CRITICAL, validate=False)

    def tokenize(css: str) -> int:
        return sum(1 for _ in tokenize2.Tokenizer().tokenize(css))

    def parse(css: str) -> int:
        return len(parser.parseString(css).cssRules)

    def read_all(sheet: object, selectors: bool = False) -> int:
        count = 0
        stack = [sheet.cssRules]  # type: ignore[attr-defined]
        while stack:
            for rule in stack.pop():
                if selectors and (text := getattr(rule, 'selectorText', None)) is not None:
                    _ = text
                if (style := getattr(rule, 'style', None)) is not None:
                    for prop in style.getProperties(all=True):
                        count += 1
                        _ = (prop.name, prop.value, prop.priority)
                if (children := getattr(rule, 'cssRules', None)) is not None:
                    stack.append(children)
        return count

    def full(css: str) -> int:
        return read_all(parser.parseString(css))

    def cssom_full(css: str) -> int:
        return read_all(parser.parseString(css), selectors=True)

    def urls(css: str) -> int:
        sheet = parser.parseString(css)
        css_parser.replaceUrls(sheet, rewrite_url, ignoreImportRules=False)
        return len(sheet.cssRules)

    parsed: dict[str, object] = {}

    def serialize(css: str) -> int:
        if (sheet := parsed.get(css)) is None:
            parsed[css] = sheet = parser.parseString(css)
            read_all(sheet, selectors=True)
        return len(sheet.cssText)  # type: ignore[attr-defined]

    return {
        'tokenize': tokenize, 'parse': parse, 'full': full,
        'cssom-parse': parse, 'cssom-full': cssom_full, 'cssom-urls': urls, 'cssom-serialize': serialize,
    }  # fmt: skip


LIBRARIES: dict[str, Callable[[], dict[str, Workload]]] = {
    'kgcss': kgcss_workloads,
    'tinycss (calibre, C)': tinycss_workloads,
    'tinycss2': tinycss2_workloads,
    'css_parser': css_parser_workloads,
}


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def best_time(fn: Workload, css: str, budget: float) -> tuple[float, int]:
    """Best of several runs, at least 3, until the time budget is used up."""
    times: list[float] = []
    result = 0
    # Warm up: with the powersave governor the CPU needs time to reach full speed
    start = time.perf_counter()
    while time.perf_counter() - start < min(0.5, budget / 4):
        fn(css)
    while len(times) < 3 or sum(times) < budget:
        gc.collect()
        start = time.perf_counter()
        result = fn(css)
        times.append(time.perf_counter() - start)
        if times[0] > budget or len(times) >= 50:
            break
    return min(times), result


SUITES = {
    'syntax': ('tokenize', 'parse', 'full'),
    'cssom': ('cssom-parse', 'cssom-full', 'cssom-urls', 'cssom-serialize'),
}


def run(libraries: list[str], corpus: list[str], budget: float, suite: str) -> None:
    files = sorted(CORPUS.glob('*.css'))
    if not files:
        raise SystemExit('No corpus, run prepare first')
    if corpus:
        files = [f for f in files if any(c in f.name for c in corpus)]
    names = SUITES[suite]
    all_workloads = {name: LIBRARIES[name]() for name in libraries}
    workloads = {name: w for name, w in all_workloads.items() if all(n in w for n in names)}
    full = names[2] if suite == 'syntax' else names[1]
    for path in files:
        css = path.read_text(encoding='utf-8')
        mb = len(css.encode('utf-8')) / 1e6
        top = max(map(ord, css), default=0)
        kind = 'ucs1' if top < 0x100 else 'ucs2' if top < 0x10000 else 'ucs4'
        print(f'\n### {path.name}: {mb:.2f} MB, {kind}\n')
        print('| library | ' + ' | '.join(f'{n} ms' for n in names) + f' | {full} MB/s | declarations |')
        print('|---|' + '---:|' * (len(names) + 2))
        for name, w in workloads.items():
            row = []
            decls = 0
            for workload in names:
                t, result = best_time(w[workload], css, budget)
                row.append(t)
                if workload == full:
                    decls = result
            cells = [name, *(f'{t * 1e3:.1f}' for t in row), f'{mb / row[names.index(full)]:.1f}', str(decls)]
            print('| ' + ' | '.join(cells) + ' |')
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('prepare', help='Download and build the corpus and competitors')
    r = sub.add_parser('run', help='Run the benchmarks')
    r.add_argument('--lib', action='append', choices=list(LIBRARIES), help='Libraries to run (default: all)')
    r.add_argument('--corpus', action='append', default=[], help='Only corpus files whose name contains this')
    r.add_argument('--suite', choices=list(SUITES), default='syntax', help='Workloads to run (default: %(default)s)')
    r.add_argument(
        '--budget', type=float, default=float(os.environ.get('BENCH_BUDGET', '2')), help='Seconds per measurement'
    )
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare()
    else:
        run(args.lib or list(LIBRARIES), args.corpus, args.budget, args.suite)


if __name__ == '__main__':
    main()
