# kgcss

A fast and comprehensive CSS handling library for Python, with a C99 backend
for speed. It covers tokenizing, parsing, a mutable CSS object model,
serialization, URL rewriting, selector parsing and matching, media queries,
colors, fonts, and shorthand properties. It was written to replace
css_parser, tinycss and css_selectors in calibre: see
[requirements.md](requirements.md) for the scope and [porting.md](porting.md)
for the port.

The C extension supports both regular CPython and free-threaded (no-GIL)
CPython builds. Python 3.14 or later is required.

## A short tour

Parse, query, change and write stylesheets. Nothing raises on bad CSS:
problems are logged and collected in `sheet.errors`, and invalid parts are
dropped as browsers drop them.

```pycon
>>> import kgcss
>>> sheet = kgcss.parse_stylesheet('@media print { .note, p#x { color: #ff0000; margin: 0px } }')
>>> rule = sheet.rules[0].rules[0]
>>> rule.selector_text, [s.specificity for s in rule.selectors]
('.note, p#x', [(0, 1, 0), (1, 0, 1)])
>>> prop = rule.style.set('font-family', '"Helvetica Neue", serif')
>>> print(kgcss.serialize(sheet))
@media print {
  .note, p#x {
    color: #f00;
    margin: 0;
    font-family: "Helvetica Neue", serif;
  }
}
>>> list(kgcss.iter_urls(kgcss.parse_style('background: url(a.png)')))
['a.png']

```

Match selectors against lxml (or any ElementTree-style) trees, with
Selectors Level 4:

```pycon
>>> from lxml import etree
>>> root = etree.fromstring('<html xmlns="http://www.w3.org/1999/xhtml"><body><p class="note">a</p><p>b</p></body></html>')
>>> [e.text for e in kgcss.Select(root)('p:has(+ p), p:nth-child(2 of p)')]
['a', 'b']

```

Colors, media queries, fonts and shorthands:

```pycon
>>> kgcss.parse_rgba('rebeccapurple')
kgcss.RGBA(red=0.4, green=0.2, blue=0.6, alpha=1.0)
>>> kgcss.media_matches('screen and (min-width: 600px)', media_type={'screen'}.__contains__, feature=lambda f: True)
True
>>> kgcss.parse_font('bold 12pt/1.4 "A B", serif')
{'font-weight': 'bold', 'font-size': '12pt', 'line-height': '1.4', 'font-family': ['A B', 'serif']}
>>> kgcss.expand_shorthand('margin', '1em 2em')
{'margin-top': '1em', 'margin-right': '2em', 'margin-bottom': '1em', 'margin-left': '2em'}

```

The design of each part is in `docs/plan-*.md`:

| Module | What | Plan |
|---|---|---|
| `kgcss.tokenizer`, `kgcss.syntax`, `kgcss.decode` | tokens with positions, the CSS Syntax parse tree, decoding bytes | 01 |
| `kgcss` (`kgcss.cssom`) | the object model, serialization, URLs, `@import` | 02 |
| `kgcss.selectors`, `kgcss.matching` | selectors, specificity, renames, `Select` | 03 |
| `kgcss.colors`, `kgcss.media` | CSS Color 4, Media Queries 4 | 04 |
| `kgcss.fonts`, `kgcss.grammar`, `kgcss.shorthands` | font helpers, value grammar, shorthand expansion and condensation | 05 |

## Development

```sh
uv sync                   # create .venv, build the C extension, install dev tools
uv run pytest             # run the tests
uv run pytest -n auto     # run the tests in parallel (pytest-xdist)
uv run ruff check .       # lint (type annotations are mandatory)
uv run mypy               # type check
```

`uv sync` and `uv run` rebuild the extension automatically when files in
`csrc/` change (see `[tool.uv] cache-keys` in `pyproject.toml`). You can also
build in place with `python setup.py build_ext --inplace`.

Build knobs (environment variables): `KGCSS_DEBUG=1` (debug build, `-Og`
with asserts), `KGCSS_SANITIZE=1` (ASan + UBSan), `KGCSS_WERROR=1` (compiler
warnings are errors).

To test against free-threaded Python:

```sh
uv run --no-project -p 3.14t --isolated --with pytest --with pytest-xdist --with hypothesis --with . -- python -m pytest -n auto
```

Hardening tools:

```sh
uv run python tools/sanitize_tests.py -n auto    # tests against an ASan + UBSan build
uv run python tools/fuzz.py run --seconds 300    # libFuzzer on the C tokenizer (needs clang)
uv run python tools/fuzz.py run --target selectors --seconds 300   # and on the selector parser
uv run python tools/fuzz.py run --target colors --seconds 300      # and on the color parser
HYPOTHESIS_PROFILE=thorough uv run pytest -n auto  # many more property based examples
KGCSS_STRESS=10 uv run pytest tests/test_threads.py  # longer concurrency stress tests
```

Benchmarks (pin to fast cores, see `bench/`):

```sh
uv run --group bench python bench/bench_parse.py prepare   # download corpus, build calibre's tinycss
taskset -c 0-2 uv run --group bench python bench/bench_parse.py run
taskset -c 0-2 uv run --group bench python bench/ab_compare.py --base HEAD   # A/B against a revision
taskset -c 0-2 uv run --group bench python bench/bench_select.py   # Select against css_selectors
```

`bench/bench_color.py`, `bench_media.py` and `bench_fonts.py` compare colors,
media queries and the font helpers with calibre's tinycss.

## License

GPL-3.0. See [LICENSE](LICENSE).
