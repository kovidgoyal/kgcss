# Instructions for coding agents working on kgcss

kgcss is a fast and comprehensive CSS library for Python. Performance-critical
parts are written in C99. The scope is in `requirements.md`. Read the relevant
section before you implement a feature.

## Layout

| Path | Contents |
|---|---|
| `src/kgcss/` | The Python package. Public API is re-exported from `__init__.py` |
| `src/kgcss/_kgcss.pyi` | Type stubs for the C extension. **Must** match `csrc/` exactly |
| `csrc/` | C99 sources of the `kgcss._kgcss` extension module |
| `csrc/kgcss.h` | Shared header: included first by every `.c` file, module state, helpers |
| `csrc/module.c` | Module definition, method table, module state lifecycle |
| `csrc/tokens.h` | Token record, token type and error enums, character classes |
| `csrc/tokenizer_impl.h` | Tokenizer template body, compiled once per PEP 393 kind by `tokenizer.c` |
| `csrc/types.c` | Python heap types `TokenStream`, `Token`, `ParseError` and `tokenize()` |
| `src/kgcss/enums.py` | Enums shared with C (imported by the extension at init, so it must not import kgcss) |
| `tests/css-parsing-tests/` | Vendored CC0 conformance suite. Do not edit; record differences in the runners |
| `tests/` | pytest test suite |
| `setup.py` | Builds the extension only; all metadata lives in `pyproject.toml` |
| `docs/plan-*.md` | Implementation plans. Read the relevant one before starting work on a feature |

## Workflow

```sh
uv sync                                  # build + install into .venv
uv sync --reinstall-package kgcss        # rebuild after changing C code
uv run pytest -n auto                    # tests, in parallel
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Before you report work as done, all of the following must pass:

1. `uv run ruff check .` with no errors. The `ANN` rules are on, so every
   function, method and argument needs a type annotation, including tests and
   `setup.py`. Do not add `# noqa` to get around `ANN` rules. Do not use `Any`
   unless nothing narrower is correct.
2. `uv run ruff format --check .` (single quotes, 120 columns).
3. `uv run mypy` in strict mode, with no new `type: ignore` unless a test is
   deliberately passing a wrong type. In that case use the narrowest error
   code, e.g. `# type: ignore[arg-type]`.
4. `uv run pytest -n auto` on a regular CPython build.
5. For any C change: the tests on a free-threaded build as well (see below),
   and a build with `KGCSS_WERROR=1` that has no warnings.

## Python rules

- Supported Python: 3.14 and later only. Use modern syntax and stdlib
  freely, and do not add compatibility code for older versions. Do not use
  `from __future__ import annotations`: annotations are already evaluated
  lazily in 3.14 (PEP 649/749).
- Any new public function or class must be fully annotated and have a
  docstring. It must also be exported from `kgcss/__init__.py` and listed in
  `__all__`.
- Prefer immutable data, `__slots__`, and `typing.Final` constants.
- Do not use module-level mutable state such as caches, global loggers or
  global settings. Pass options and loggers/callbacks per call
  (requirements.md §13, L1). Any cache must be thread safe, because the
  library must work without the GIL.
- Hot paths (tokenizing, color parsing, serialization of large sheets,
  selector matching) belong in C. Python is for the object model and API
  surface.

## C rules

- Use strict C99, compiled with `-std=c99 -Wall -Wextra -Wshadow
  -Wstrict-prototypes`. Do not use compiler extensions beyond what `kgcss.h`
  already wraps (`KG_LIKELY`, ...). Keep all code warning-free.
- Every `.c` file includes `"kgcss.h"` first. Declare cross-file functions
  in `kgcss.h`, and make everything else `static`. The build uses
  `-fvisibility=hidden`, so only `PyInit__kgcss` is exported.
- When you add a function to the method table in `module.c`, also update
  `src/kgcss/_kgcss.pyi` and add tests.
- New `.c` files in `csrc/` are picked up by `setup.py` automatically.
- Use only heap types (`PyType_FromModuleAndSpec`), and store them in
  `kg_module_state`. Do not use static `PyTypeObject`s. Cached objects
  (interned strings, exception types) also go in the module state, and you
  must update `module_traverse` and `module_clear` for them.
- Check every Python C-API return value and propagate errors. Every
  reference you own must be released on every path, including error paths.
  Use `goto` cleanup labels.
- Py_ssize_t is for lengths and indices. Work with `PyUnicode_KIND` /
  `PyUnicode_DATA` / `PyUnicode_READ` directly instead of converting to
  UTF-8, so that source offsets stay in code points and match Python string
  indices (requirements.md T2, T7).
- Parsers must never read past the end of input, and must handle any input,
  including invalid UTF-16 surrogates, NUL and huge inputs, without crashing.

### Free-threading (no GIL)

The extension declares `Py_mod_gil = Py_MOD_GIL_NOT_USED`, so on
free-threaded Python several threads can run inside it at once. It must stay
correct in that case:

- **No mutable `static` or global variables.** Lookup tables that are only
  read (`static const`) are fine.
- `str`, `bytes` and `tuple` objects are immutable, so reading them without
  locking is safe.
- Do not use borrowed-reference APIs on objects that other threads can
  mutate. Use `PyList_GetItemRef` and `PyDict_GetItemRef` instead of `PyList_GET_ITEM` and
  `PyDict_GetItem`. Or hold `Py_BEGIN_CRITICAL_SECTION(obj)` /
  `Py_END_CRITICAL_SECTION()` around the access.
- Our own mutable objects must protect their internal state with
  `Py_BEGIN_CRITICAL_SECTION(self)` in every method that reads or writes it.
- If a long-running pure-C section touches no Python objects, release the
  GIL around it with `Py_BEGIN_ALLOW_THREADS` (this is harmless on
  free-threaded builds).
- Add a concurrency test (see `tests/test_native.py::test_concurrent_use`)
  for any new C entry point that has state.

Test free-threaded builds with:

```sh
uv run --no-project -p 3.14t --isolated --with pytest --with pytest-xdist --with . -- python -m pytest -n auto
```

`test_importing_does_not_enable_gil` fails if the module ever re-enables the
GIL.

For memory errors, build with `KGCSS_SANITIZE=1 KGCSS_DEBUG=1`. Then run
pytest with `LD_PRELOAD=$(gcc -print-file-name=libasan.so)` and without `-n`.

## Tests

- Use pytest with plain `assert`s. Use `pytest.mark.parametrize` for tables
  of inputs and outputs. Tests must be fully annotated like the rest of the
  code (`-> None`).
- Tests must not depend on order or shared state, because they run in
  parallel under pytest-xdist. Use `tmp_path` for files, and do not write
  into the source tree.
- Mark slow tests with `@pytest.mark.slow`.
- Warnings are errors (`filterwarnings = error`).
- When you port behaviour from the calibre tests listed in requirements.md
  §16, keep the original inputs and expected outputs. If a quirk from §18 is
  being fixed, comment why the expected value differs.

## Performance

- This machine's cores differ in performance. Pin benchmarks to the fast
  cores: `taskset -c 0-2 uv run python bench/...`.
- Back performance claims with measurements, before and after.
- Parsing must stay roughly linear in input size. Do not use quadratic
  string building in Python or C.

## Commits

- Commit to `master`. Write a short, imperative summary line. Do not add
  Co-authored-by trailers.
