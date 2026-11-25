# kgcss

A fast and comprehensive CSS handling library for Python. It has a C99 backend
for speed. It aims to cover tokenizing, parsing, a mutable CSS object model,
serialization, URL rewriting, media queries, colors, fonts, and selector
parsing and matching. See [requirements.md](requirements.md) for the scope.

The C extension supports both regular CPython and free-threaded (no-GIL)
CPython builds.

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
HYPOTHESIS_PROFILE=thorough uv run pytest -n auto  # many more property based examples
KGCSS_STRESS=10 uv run pytest tests/test_threads.py  # longer concurrency stress tests
```

## License

GPL-3.0. See [LICENSE](LICENSE).
