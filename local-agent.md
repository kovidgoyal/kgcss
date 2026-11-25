# Instructions for coding agents working on kgcss

kgcss is a fast and comprehensive CSS library for Python. Performance-critical
parts are written in C99. The scope is in `requirements.md`. Read the relevant
section before you implement a feature.

## Layout

| Path | Contents |
|---|---|
| `src/kgcss/` | The Python package. The top level (`__init__.py`) exports the CSSOM API. The lower layers are only available as `kgcss.tokenizer` and `kgcss.syntax` |
| `src/kgcss/cssom/` | The CSS object model (plan 02): `rules.py` (rule classes), `build.py` (syntax tree to CSSOM), `stylesheet.py`, `serializer.py`, `state.py` (per sheet lock, errors, sources), `declarations.py`, `values.py`, `selector_list.py`, `urls.py` (finding and rewriting URLs), `imports.py` (loading and inlining @import) |
| `src/kgcss/selectors.py` | Parsed selectors (plan 03): the selector tree, `compile_selectors()` (the cached parse) and `build_tree()` |
| `src/kgcss/matching.py` | `Select`: matching selectors against ElementTree-style trees (plan 03) |
| `src/kgcss/escaping.py` | `serialize_string()` and `serialize_identifier()`, the CSSOM escaping idioms |
| `src/kgcss/_kgcss.pyi` | Type stubs for the C extension. **Must** match `csrc/` exactly |
| `csrc/` | C99 sources of the `kgcss._kgcss` extension module |
| `csrc/kgcss.h` | Shared header: included first by every `.c` file, module state, helpers |
| `csrc/module.c` | Module definition, method table, module state lifecycle |
| `csrc/tokens.h` | Token record, token type and error enums, character classes |
| `csrc/tokenizer_impl.h` | Tokenizer template body, compiled once per PEP 393 kind by `tokenizer.c` |
| `csrc/objects.h` | Object layouts and functions shared between the `.c` files |
| `csrc/types.c` | Python heap types `TokenStream`, `Token`, `ParseError` and `tokenize()` |
| `csrc/values.c` | `Block`, `Function`, `ComponentValueList` and the component value parse functions |
| `csrc/syntax.c` | CSS Syntax parse algorithms: `AtRule`, `QualifiedRule`, `Declaration`, `NodeList` |
| `csrc/writer.c` | C port of the CSSOM token writer (`TokenWriter`, `write_values()`) and `write_declarations()` |
| `csrc/urls.c` | C port of `find_urls()` in `kgcss/cssom/urls.py` |
| `csrc/properties.c` | `PropertyData`, the C base of `Property` holding its fields, and `build_properties()` |
| `csrc/selectors.c` | The selector parser, `CompiledSelector` (a flat array of nodes) and specificity. `selectors.h` has the node layout |
| `csrc/select.c` | `Snapshot`, the matcher's view of a tree, and matching |
| `fuzz/` | libFuzzer harnesses, run with `tools/fuzz.py` |
| `src/kgcss/decode.py` | Decoding stylesheet bytes: BOM, protocol/`@charset`/environment encodings, WHATWG decoders |
| `src/kgcss/_encodings.py` | **Generated** by `tools/generate_encodings.py` from WHATWG data. Do not edit by hand. Any new generated file must also be marked `linguist-generated` in `.gitattributes` |
| `tools/` | Developer scripts: code generators, `sanitize_tests.py`, `fuzz.py`. Typed and linted like the rest of the code |
| `fuzz/` | libFuzzer harnesses for the C code, built and run by `tools/fuzz.py` |
| `src/kgcss/enums.py` | Enums shared with C (imported by the extension at init, so it must not import kgcss) |
| `tests/css-parsing-tests/` | Vendored CC0 conformance suite. Do not edit; record differences in the runners |
| `tests/` | pytest test suite |
| `setup.py` | Builds the extension only; all metadata lives in `pyproject.toml` |
| `docs/plan-*.md` | Implementation plans. Read the relevant one before starting work on a feature |

## Workflow

```sh
uv sync                                  # build + install into .venv
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
- The public API follows Pythonic conventions, not DOM/CSSOM ones:
  `snake_case` for functions, methods, attributes and keyword arguments
  (`css_text`, `selector_text`, `get_value()`), `CapWords` classes and
  `UPPER_CASE` constants. Use enums rather than integer constants, Python
  container protocols, and keyword-only options. Any camelCase
  compatibility belongs only in a separate facade module.
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
- Do not use `strtod()` or other locale-dependent functions: calibre's GUI
  sets the locale from the environment, and in a locale with a decimal
  comma `strtod("0.5")` stops at the dot. Use `PyOS_string_to_double()`.

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
- Add a concurrency test to `tests/test_threads.py` for any new C entry
  point. Use a `threading.Barrier` so that the threads really overlap, and
  scale the work with `STRESS`.

Test free-threaded builds with:

```sh
uv run --no-project -p 3.14t --isolated --with pytest --with pytest-xdist --with hypothesis --with . -- python -m pytest -n auto
```

`test_importing_does_not_enable_gil` fails if the module ever re-enables the
GIL. lxml re-enables it on free-threaded builds, so tests that need lxml skip
themselves when it is not installed, as in the command above, and thread
tests use the standard library's ElementTree.

### Sanitizers and fuzzing

For any C change, also run the suite against an ASan + UBSan build:

```sh
uv run python tools/sanitize_tests.py -n auto
```

This builds into a temporary directory and leaves the normal build alone. It
runs with `PYTHONMALLOC=malloc`, so ASan also sees Python object
allocations. For changes to the tokenizer (`csrc/tokenizer*`), the
selector parser (`csrc/selectors.c`) or the color parser (`csrc/color.c`),
fuzz for a few minutes too:

```sh
uv run python tools/fuzz.py run --seconds 300
uv run python tools/fuzz.py run --target selectors --seconds 300
uv run python tools/fuzz.py run --target colors --seconds 300
```

If the fuzzer finds a crash, add the input as a regression test in
`tests/test_tokenizer.py` before fixing it.

### C ports of Python code

Some hot CSSOM code exists twice. The Python version is the reference, and
the C version must give exactly the same results:

| Python reference | C version |
|---|---|
| `TokenWriter` and `python_write_values()` in `cssom/serializer.py` | `write_values()` in `csrc/writer.c` |
| `declaration_lines()` in `cssom/serializer.py` | `write_declarations()` in `csrc/writer.c` |
| `python_find_urls()` in `cssom/urls.py` | `find_urls()` in `csrc/urls.c` |

- Change both together. `tests/test_writer.py` compares them on random input
  and must pass, also with `HYPOTHESIS_PROFILE=thorough`. When a comparison
  fails, check which side is wrong: the Python version has had bugs too.
- The C versions return `NotImplemented` for rare cases that they leave to
  Python, for example a comment dropped from a selector, where deciding on a
  separator needs the tokenizer.
- The C matcher (`csrc/select.c`) has a reference too, used only by the
  tests: the naive matcher in `tests/selector_oracle.py`. Change both when
  the rules of matching change, and keep the oracle obviously right rather
  than fast. `tests/test_select_vs_css_selectors.py` also compares the
  matcher with calibre's css_selectors. It is skipped unless
  `uv run --group bench python bench/bench_parse.py prepare` has downloaded
  css_selectors into the bench cache, so run that once.
- Properties created by `build_properties()` are not tracked by the garbage
  collector. That is only safe while a `Property` cannot be part of a
  reference cycle: `Property.parent` is a weak reference, and nothing else a
  property refers to refers back to it. Keep it that way, or track them
  again.

## Tests

- Use pytest with plain `assert`s. Use `pytest.mark.parametrize` for tables
  of inputs and outputs. Tests must be fully annotated like the rest of the
  code (`-> None`).
- Tests must not depend on order or shared state, because they run in
  parallel under pytest-xdist. Use `tmp_path` for files, and do not write
  into the source tree.
- Mark slow tests with `@pytest.mark.slow`.
- Add hypothesis property tests (`tests/test_properties.py`, strategies in
  `tests/strategies.py`) for invariants that should hold for all input:
  round trips, positions, and agreement with reference implementations. For
  important changes, run them with `HYPOTHESIS_PROFILE=thorough`.
  Check that a new strategy actually produces the structures a property is
  about. Count them, as the milestone 5 notes in
  `docs/plan-01-tokenizer-syntax.md` describe.
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
- `bench/bench_parse.py` compares kgcss with calibre's tinycss, tinycss2
  and css_parser on a real and synthetic corpus. Run `prepare` once, then
  `run`. It needs `uv run --group bench`.
- Use `bench/ab_compare.py --base HEAD` to judge an optimization. It builds
  both versions and alternates rounds between them. Timings on this machine
  are noisy (other load, SMT siblings, and the `powersave` governor, which
  needs a warm-up). Comparing numbers from separate runs is not reliable:
  differences of 20-30% in either direction have come from noise alone.
  Use more `--rounds` and `--repeat` for files that parse in under a
  millisecond.
- `bench_parse.py run --suite cssom` measures the object model (parse, read
  everything, rewrite URLs, serialize) against css_parser.
- `bench/bench_select.py` measures selector matching (`Select`
  construction, whole sheets against a document as calibre's stylizer
  does, and level 4 selectors) against calibre's css_selectors. Its
  workloads also work with `ab_compare.py --workload`.
- `bench/bench_color.py` measures `parse_rgba` against calibre's C color
  parser (`calibre_extensions.tokenizer.parse_css4_color_string`, which
  `prepare` downloads): single inputs in nanoseconds, and every color
  value and every ident, hash and function of the corpus.
- `bench/bench_media.py` measures `parse_media_queries` (with and without
  its cache) and `media_matches` against tinycss's media3 parser.
- Garbage collection is a large part of the cost of creating many Python
  objects, as every collection walks the tracked objects. In hot paths,
  avoid allocating container objects you do not keep. Note that
  `cast(list[X], y)` builds the type at run time: quote it, as
  `cast('list[X]', y)`.
- `perf stat -e instructions` counts are a steadier signal than time.
  `perf record -g` with `python -X perf` attributes time to C and Python
  frames.

## Commits

- Commit to `master`. Write a short, imperative summary line. Do not add
  Co-authored-by trailers.
