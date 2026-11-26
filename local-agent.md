# Instructions for coding agents working on kgcss

kgcss is a fast and comprehensive CSS library for Python. Performance-critical
parts are written in C99. It replaces what calibre used css_parser, tinycss,
css_selectors and (in Check Book) stylelint for: parsing, the object model,
serialization, URL rewriting, selector matching, media queries, colors,
fonts, shorthands and linting. calibre is the main user, so its needs decide
behaviour where CSS leaves room.

## Layout

| Path | Contents |
|---|---|
| `src/kgcss/` | The Python package. The top level (`__init__.py`) exports the CSSOM API. The lower layers are only available as `kgcss.tokenizer` and `kgcss.syntax` |
| `src/kgcss/cssom/` | The CSS object model: `rules.py` (rule classes), `build.py` (syntax tree to CSSOM), `stylesheet.py`, `serializer.py`, `state.py` (per sheet lock, errors, sources), `declarations.py`, `values.py`, `selector_list.py`, `urls.py` (finding and rewriting URLs), `imports.py` (loading and inlining @import) |
| `src/kgcss/selectors.py` | Parsed selectors: the selector tree, `compile_selectors()` (the cached parse) and `build_tree()` |
| `src/kgcss/matching.py` | `Select`: matching selectors against ElementTree-style trees |
| `src/kgcss/escaping.py` | `serialize_string()` and `serialize_identifier()`, the CSSOM escaping idioms |
| `src/kgcss/colors.py` | `parse_rgba()`, `parse_color()` and `Color`, on the C parser in `csrc/color.c` |
| `src/kgcss/media.py` | Media queries: the parser, the model and three-valued evaluation |
| `src/kgcss/fonts.py` | The font helpers that replace tinycss.fonts3 |
| `src/kgcss/grammar.py` | `matches()` and `is_color()`: value grammar for the longhands of the shorthands |
| `src/kgcss/shorthands.py` | Shorthand expansion and condensation |
| `src/kgcss/compat.py` | The css_parser-style facade: live camelCase views of kgcss objects, `wrap()` and `unwrap()` |
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
| `csrc/color.c` | The CSS Color 4 parser, ported from calibre's tinycss, and `RGBA` |
| `fuzz/` | libFuzzer harnesses, run with `tools/fuzz.py` |
| `src/kgcss/decode.py` | Decoding stylesheet bytes: BOM, protocol/`@charset`/environment encodings, WHATWG decoders |
| `src/kgcss/_encodings.py` | **Generated** by `tools/generate_encodings.py` from WHATWG data. Do not edit by hand. Any new generated file must also be marked `linguist-generated` in `.gitattributes` |
| `tools/` | Developer scripts: code generators, `sanitize_tests.py`, `fuzz.py`. Typed and linted like the rest of the code |
| `fuzz/` | libFuzzer harnesses for the C code, built and run by `tools/fuzz.py` |
| `src/kgcss/enums.py` | Enums shared with C (imported by the extension at init, so it must not import kgcss) |
| `tests/css-parsing-tests/` | Vendored CC0 conformance suite. Do not edit; record differences in the runners |
| `tests/` | pytest test suite |
| `setup.py` | Builds the extension only; all metadata lives in `pyproject.toml` |
| `src/kgcss/linter/` | The linter, `kgcss.lint()`: stylelint's rules on ports of the JavaScript libraries they use. See "The linter" below |
| `src/kgcss/linter/__init__.py` | The public API (`lint()`, `LintRule`, `LintProblem`, `TextEdit`, `SyntaxProblem`, `LINT_RULES`) and the registry of rules |
| `src/kgcss/linter/_engine.py` | Running the rules: parsing, positions as stylelint computes them (`rangeBy`), options, disable comments, edits and the fix passes |
| `src/kgcss/linter/_rules/` | One module per rule, named after it, with `check(root, ctx)`, and `OPTIONS`/`OPTION_VALUES` for the secondary options it supports. Imported when first used |
| `src/kgcss/linter/_postcss.py` | Port of PostCSS 8.5 (tokenizer, parser, `toString()`) and postcss-safe-parser 7 |
| `src/kgcss/linter/_value_parser.py` | Port of postcss-value-parser 4.2 |
| `src/kgcss/linter/_selector_parser.py`, `_selector_tools.py` | Ports of postcss-selector-parser 7.2 and of @csstools/selector-specificity and selector-resolve-nested |
| `src/kgcss/linter/_csstools.py`, `_media_query.py` | @csstools/css-tokenizer tokens (made from kgcss's C tokenizer), and ports of @csstools/css-parser-algorithms and media-query-list-parser |
| `src/kgcss/linter/_utils.py`, `_selector_helpers.py`, `_fonts.py`, `_disables.py` | Ports of the helpers in stylelint's `lib/utils` that rules share, and of its disable comments |
| `src/kgcss/_css_data.py` | **Generated** by `tools/generate_css_data.py` from the pinned stylelint and css-tree: the names the rules know. Do not edit by hand |
| `tests/stylelint-rules/` | **Generated** by `tools/extract_stylelint_tests.py`: the test cases of stylelint's own rules (MIT). Do not edit; record differences in the runners |
| `bench/stylelint/` | `package.json` and lock file pinning stylelint 18.1.0 and @stylistic/stylelint-plugin 6.0.1, the oracle of the linter tests |
| `docs/*.rst`, `docs/guide/` | The user documentation (Sphinx, built by Read the Docs from `.readthedocs.yaml`). Update it with any change to the public API: the guide's examples are doctests, run by `sphinx-build -b doctest`, and the API reference lists members explicitly in `docs/api.rst` |

## Workflow

```sh
uv sync                                  # build + install into .venv
uv run pytest -n auto                    # tests, in parallel
uv run ruff check . && uv run ruff format --check .
uv run ty check
```

Before you report work as done, all of the following must pass:

1. `uv run ruff check .` with no errors. The `ANN` rules are on, so every
   function, method and argument needs a type annotation, including tests and
   `setup.py`. Do not add `# noqa` to get around `ANN` rules. Do not use `Any`
   unless nothing narrower is correct.
2. `uv run ruff format --check .` (single quotes, 120 columns).
3. `uv run ty check` with no errors (configured in `pyproject.toml`), and no new
   `ty: ignore` unless a test is deliberately passing a wrong type. In that case
   use the narrowest rule, e.g. `# ty: ignore[invalid-argument-type]`.
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
  global settings. Pass options and loggers/callbacks per call: calibre
  creates parsers with different settings in the same process. Any cache
  must be thread safe, because the library must work without the GIL.
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
  indices: callers slice the source with token offsets to rewrite it.
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

## The linter

`kgcss.lint()` runs stylelint's rules, and must report what stylelint 18.1.0
reports (with @stylistic/stylelint-plugin 6.0.1 for `no-extra-semicolons`):
the same problems, messages, lines and columns, and the same fixed text. So
the linter is a port: of each rule, and of the JavaScript libraries the
rules use (see the layout above). The ports are faithful, quirks included,
and each names what it ports. Read the JavaScript before you change a port
or add a rule: stylelint is installed in `bench/.cache/stylelint` by
`uv run --group bench python bench/bench_parse.py prepare` (needs npm).

- Keep JavaScript semantics where Python differs: JavaScript's `\s` and
  `trim()` whitespace (`JS_WHITESPACE` in `_postcss.py`), its ASCII-only `\w`,
  `\d` and `\b`, `x || y` (0 and '' are false), `charCodeAt()` past the end
  (NaN), `lastIndexOf()` with a negative index, `Array.forEach()` over a list
  that the callback changes, and `localeCompare()` sorts (any total order is
  fine where the order only serves comparisons). Write non-ASCII characters
  in source as `\u` escapes.
- Positions are offsets into the text (Python indexes, so code points) and
  stylelint's lines and columns. stylelint counts columns in UTF-16 code
  units; kgcss in code points. The tests convert.
- A rule is a module in `_rules/` with `check(root, ctx)`, registered in
  `LintRule`, `_DESCRIPTIONS` (and `_FIXABLE`, `_STYLISTIC`) in
  `linter/__init__.py`. Report with `ctx.report()` as stylelint's `report()`
  is called (`index`/`end_index`, `word`, or `start`/`end`). Messages are
  stylelint's without the " (rule-name)" suffix, and `args` are its
  `messageArgs` as strings. Support the secondary options the extracted test
  cases use, under stylelint's names, listed in `OPTIONS` and, for keyword
  lists, `OPTION_VALUES`; anything else raises ValueError.
- Fixes are `TextEdit`s of the original text, computed as stylelint computes
  its edit info: print the changed node and narrow the difference
  (`narrow_edit()`, `removal_edit()`). Where fixes depend on each other,
  give later problems the edit of all the fixes (see
  `function_calc_no_unspaced_operator.py`). `lint(fix=True)` applies the
  edits that do not overlap and lints again, until nothing changes.
- Rule modules, and `_css_data.py`, are imported when first used:
  `tests/test_linter.py` checks that `import kgcss` loads none of them.
- `ctx.cache` lives for one `lint()` call and is shared by its rules. Use it
  for results that depend only on their key, as stylelint caches them
  (parsed selectors, resolved nesting, normalized selectors). Never add
  module-level caches.
- Where stylelint crashes on some input, skip that part rather than raise:
  `lint()` must not raise for any text.

Tests (run them with `HYPOTHESIS_PROFILE=thorough` after any change here):

- `tests/test_linter_stylelint_cases.py` runs stylelint's own test cases
  (`tests/stylelint-rules`) for every rule and supported config. Record a
  deliberate difference in `KNOWN_DIFFERENCES` (or `IGNORED_MESSAGES`) with
  the reason, never by changing the expected values.
- `tests/test_linter_vs_stylelint.py` compares `lint()` with stylelint,
  warnings and fixed text, on random sheets (`lint_css` and `css_text` in
  `tests/strategies.py`). Add fragments to `LINT_FRAGMENTS` for new rules and
  check that every rule fires on them. The `assume()`s and comments there
  list the differences that are known and deliberate.
- The ports are compared with the originals on random input:
  `test_linter_postcss.py` (trees, raws, errors, `toString()`),
  `test_linter_value_parser.py`, `test_linter_selector_parser.py` and
  `test_linter_csstools.py`. The oracles are `tests/stylelint_oracle.py` and
  `tests/csstools_oracle.py` (Node processes); the tests skip themselves when
  stylelint is not installed, so install it.
- `tests/test_stylelint_fixtures.py` checks the extracted test cases against
  stylelint itself, and `tests/test_css_data.py` that `_css_data.py` is up to
  date.
- To move to a new stylelint: change the versions in
  `bench/stylelint/package.json` (then `npm install` there for the lock file)
  and in `tools/extract_stylelint_tests.py`, run `prepare`, regenerate
  `tests/stylelint-rules` and `_css_data.py` (see the docstrings of the two
  tools), and fix what the tests find.

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
  about: generate a few thousand examples and count them by kind, as
  the property tests of `tests/test_colors.py` and `tests/test_shorthands.py` were checked.
- Warnings are errors (`filterwarnings = error`).
- When you port behaviour from calibre's tests (of css_parser, tinycss,
  css_selectors and the code using them), keep the original inputs and
  expected outputs. Where a quirk of those libraries is fixed instead,
  comment why the expected value differs.

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
- `bench/bench_fonts.py` measures the font helpers against tinycss.fonts3.
- `bench/bench_lint.py` measures `kgcss.lint` (and fixing) against stylelint
  in Node, which `prepare` installs if npm is there. `bench_parse.py` has a
  `lint` workload for `ab_compare.py`.
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
