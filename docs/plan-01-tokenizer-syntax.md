# Plan 01: Tokenizer and syntax tree

This plan covers requirements.md §1 (T1–T7), plus the parts of §2 that belong
to the generic syntax layer: P1 (str and bytes input), P5 (error recovery), P12
(positions) and the syntax side of P6, P7 and P15. It also covers L3 (collected
errors with positions).

The CSSOM (rules with types, mutable declaration blocks, serialization) is
**not** part of this plan. It is the next layer and is built on the syntax tree
defined here.

Spec target: the current CSS Syntax Level 3 Editor's Draft, including the
nesting-aware "consume a block's contents" algorithm.

---

## 1. Key decision: what the C code works on

### Options

**A. Work directly on Python `str` (PEP 393 storage), templated on kind.**
CPython stores each `str` as 1, 2 or 4 bytes per code point. That is Latin-1,
UCS-2 or UCS-4, chosen by the highest code point in the string. The tokenizer
is written once as a template header. It is compiled three times, with
`CHAR_T` set to `uint8_t`, `uint16_t` and `uint32_t`, and dispatched on
`PyUnicode_KIND`. CPython's own `stringlib` works the same way.

**B. Convert to UTF-8 and tokenize bytes.**
Call `PyUnicode_AsUTF8AndSize`, which costs nothing for pure-ASCII strings.
For any other string it encodes and caches a UTF-8 copy on the str object.

### Recommendation: A

| Concern | str / kind (A) | UTF-8 (B) |
|---|---|---|
| **Offsets** (T2, T7, P12) | Code point offsets **are** Python indices. `src[tok.pos:next.pos]` works as is, which is what `rename_ids_in_selector` needs. | Byte offsets have to be mapped to code point offsets for every token exposed to Python. That needs a side table or a rescan. |
| Input conversion | None. We read the str buffer in place. | ASCII: none. Any non-ASCII text, including a single `é` or `“` in a comment: a full encode pass, plus a cached UTF-8 copy that stays alive for the life of the str. |
| Building token value strings | Substring of the same kind: a `memcpy` plus a max-char scan. | A UTF-8 decode for every value string. |
| Character classification | One array read per code point. No multi-byte decoding in the hot loop. Anything ≥ 0x80 is an ident char, so non-ASCII needs no special handling. | Bytes ≥ 0x80 can all be treated as ident bytes, which is just as fast. Escapes and U+FFFD handling need real decoding. |
| Code size | 3 instantiations of one template. Only the kind-1 one is really hot. | 1 instantiation. |
| Lone surrogates (e.g. from `surrogateescape`) | Handled naturally. | `AsUTF8` **raises** on surrogates, so they need special handling. |

In practice almost all CSS is ASCII or Latin-1, so it is stored as kind 1 and
the hot path reads plain bytes, the same as option B. Kind 1 data is a byte
buffer, so `memchr` and similar tricks still work on it, e.g. for finding the
end of a comment or string. Option B's only real advantage is a single code
path. In exchange it gives up correct offsets and adds conversion costs,
which T2, T7 and P12 make unacceptable.

### Rule: never preprocess the input

The spec first normalises the input: CR, CRLF and FF become LF, and NUL and
surrogates become U+FFFD. Doing this physically would shift every offset and
break exact round trips (T3). The tokenizer instead applies these rules
**virtually**:
- CRLF, CR and FF count as one newline for line counting and in string
  escapes (`\` followed by CRLF is a single escaped newline).
- NUL and surrogates become U+FFFD only in **unescaped values**. The source
  slice keeps the original characters.

---

## 2. Key decision: stylesheets given as bytes

### Recommendation: decode once, then tokenize the str. Do not tokenize bytes.

- The spec (CSS Syntax §3.2, "decode bytes") defines decoding as a step
  **before** tokenizing. The encoding is chosen in this order:
  1. a BOM;
  2. the protocol encoding (e.g. HTTP `Content-Type`);
  3. the `@charset "…";` byte pattern, checked only in the first 1024 bytes.
     If it names `utf-16be` or `utf-16le`, use `utf-8` instead;
  4. the environment encoding (e.g. the referring document or `<link charset>`);
  5. UTF-8.

  The selected label is decoded with WHATWG Encoding semantics: replacement
  on error, and the BOM wins.
- Even a bytes-level tokenizer could only handle UTF-8 or ASCII-compatible
  input. windows-1252, Shift_JIS, GB18030 and UTF-16 sheets would still have
  to be decoded first. That means a second tokenizer code path for one
  encoding.
- CPython's UTF-8 decoder has an ASCII fast path and runs at close to
  `memcpy` speed. For ASCII or Latin-1 input it produces a kind-1 str, which
  is exactly what the hot path of option A wants. Decoding costs much less
  than tokenizing, and every token value has to end up as a `str` anyway.
- **Positions refer to the decoded text.** Line and column are what users
  see (L2, Reports, jump-to-rule), and those are the same in bytes and in
  text.

### API

```python
def decode_stylesheet(
    data: bytes | bytearray | memoryview,
    *,
    protocol_encoding: str | None = None,
    environment_encoding: str | None = None,
) -> tuple[str, str]:
    """Return (text, encoding_name) using the CSS Syntax algorithm."""
```

- Write it in Python. It only looks at the first 1024 bytes, then calls
  `bytes.decode(codec, 'replace')`. Moving it to C would gain nothing.
- Add a **WHATWG label → Python codec** table (`kgcss/_encodings.py`)
  generated from the WHATWG `encodings.json`. Python codec names do not
  always mean the same thing as the WHATWG labels: `latin1`, `iso-8859-1`
  and `ascii` must map to `cp1252`, and `x-user-defined` needs its own codec.
  A small generator script is checked in alongside the table.
- The sheet-level `parse_stylesheet` accepts `str | bytes`. For bytes it
  calls `decode_stylesheet` and records the encoding (S4, P8).
- The conformance suite's `stylesheet_bytes.json` tests this (§7).

---

## 3. Architecture

```
str ──► [C] tokenizer ──► flat token array ──► [C] syntax parser ──► rule/decl records
             (one pass, templated on kind)      (css-syntax §5)
                                    │                                     │
                                    ▼                                     ▼
                      TokenStream (immutable, owns          SyntaxStylesheet / rules /
                      the source str and the arrays)        declarations (Python objects)
                                    │                                     │
                                    └──── lazy ComponentValueList views ◄─┘
                                          (token ranges, created on access)
```

### 3.1 The C token array (internal)

```c
typedef struct {
    uint32_t start, end;       /* code point offsets of the exact source text */
    uint32_t vstart, vend;     /* range holding the value (name, string body, url, number part) */
    uint32_t match;            /* open bracket/function: index of the matching close token (M2) */
    uint8_t  type;             /* kg_token_type */
    uint8_t  flags;            /* NEEDS_PROCESSING, IS_INTEGER, HASH_ID, UNCLOSED */
} kg_token;                    /* 24 bytes */
```

- One pass over the input. The array grows geometrically. The initial
  capacity is estimated as `len / 4` tokens.
- **Grouping happens in the same pass.** A small stack holds the open
  `{ [ ( function` tokens. When the matching closer arrives, the opener's
  `match` is set. Openers still unclosed at EOF get `match = n_tokens` and
  the `UNCLOSED` flag (T5 recovery). This gives an O(1) skip over any block,
  and the tree is implicit: no per-node allocation.
- Unmatched `) ] }` become error-closer tokens, as the spec and the test
  suite require.
- **Comments are always recorded** as COMMENT tokens, because they cost one
  record. Each consumer decides whether to skip them. T4 becomes a filter
  flag, and P7 gets comments at top level and inside blocks.
- Offsets are `uint32_t`, so the record stays at 24 bytes. Inputs of 4 GiB
  or more raise `OverflowError`.
- **Line and column** are not stored per token. The tokenizer records the
  newline offsets in a `uint32_t` array, and line/column is a binary search
  (O(log n)) done when asked for. Both are 1-based, matching tinycss. The
  column counts code points.
- **Unescaped values are not built during tokenizing.** The `HAS_ESCAPES`
  flag records whether one is needed. Without escapes, the value is
  `PyUnicode_Substring(src, a, b)`. With escapes, a separate unescape
  routine runs.
- **Numbers** are converted when a `Token` is created (see §6.6). The
  tokenizer only records the number's text range and an `IS_INTEGER` flag.
  Integers become `int` (`PyLong_FromUnicodeObject`), so `700` stays `700`
  (V2). Other numbers go through the locale-independent
  `PyOS_string_to_double` (never `strtod`). This also fixes the `1.0E1px`
  bug: the value is 10.0 and the unit is `px`.
- ASCII characters are classified with a 128-entry `static const` table.
  Anything ≥ 0x80 counts as an ident code point.
- Error-producing conditions append to a parse-error array as
  `{offset, kind}` records: bad-string, bad-url, EOF in string, comment or
  url, invalid escape, and unmatched closers.

### 3.2 Python-visible objects (C heap types, immutable)

| Type | Contents |
|---|---|
| `TokenStream` | `source: str`, `__len__`, `__getitem__ -> Token`, `errors`, `line_col(offset) -> tuple[int, int]`. It owns the C arrays. |
| `Token` | `type: TokenType`, `value` (unescaped str, or int/float for numbers), `unit`, `pos`, `end`, `line`, `column`, `source_text` (exact slice), `is_integer`, `hash_type` (id/unrestricted), `is_delim(ch)`. |
| `Block` | `type` (LEFT_CURLY/LEFT_SQUARE/LEFT_PAREN), `opener: '{' \| '[' \| '('`, `content: ComponentValueList`, positions, `source_text`, `closed: bool`. |
| `Function` | `type` (FUNCTION), `name` (unescaped), `arguments: ComponentValueList`, positions, `source_text`, `closed: bool`. |
| `ComponentValueList` | An immutable sequence view. When created, it stores the token index of each top-level item (4 bytes each, found by skipping over `match`), so `len()` and indexing are O(1). `Token`, `Block` and `Function` objects are only created when accessed. It also has `text`, which is the exact source slice, comments included. |
| `SyntaxStylesheet` / `AtRule` / `QualifiedRule` / `Declaration` / `Comment` | The generic css-syntax result (§3.3). Preludes and values are `ComponentValueList` views. |

- `TokenType` is an `IntEnum` in Python whose values match the C enum.
  A test checks that the two stay in sync.
- **Why lazy views:** calibre often needs only the **text** of a value
  (`getPropertyValue`, round-tripping, U4 token-level URL rewriting), and
  that is an O(1) slice. A full Python object per token (tinycss style) would
  make the whole parse cost about 50–100 ns × number of tokens. With lazy
  views we only pay for the tokens someone actually looks at.
- **Free-threading:** after construction, a `TokenStream` and everything
  that refers to it never changes. Views and tokens are created on demand,
  and nothing is cached in shared objects. So nothing needs locking, and
  the objects can be shared freely across threads. If caching is ever added,
  it must use `Py_BEGIN_CRITICAL_SECTION` or atomics.
- **Releasing the GIL:** the tokenizing pass only reads the str buffer
  (immutable, and we hold a reference) and writes to malloc'd arrays. For
  inputs above a threshold (start at about 64 KiB and measure), wrap the
  pass in `Py_BEGIN_ALLOW_THREADS`. This lets threads on a GIL build parse
  in parallel, and costs nothing on free-threaded builds.

### 3.3 Generic syntax parser (C, css-syntax §5)

Entry points, each returning objects from the table above:

| Function | Spec algorithm | Requirement |
|---|---|---|
| `tokenize(css, *, comments=True)` | tokenizer | T1–T4, T7 |
| `parse_component_value_list(css)` | parse a list of component values | T5, P3 |
| `parse_one_component_value(css)` | parse a component value | P3 |
| `parse_stylesheet(css: str \| bytes, ...)` | parse a stylesheet | P1, P5, P7, P12 |
| `parse_rule_list(css)` | parse a rule list | P4 |
| `parse_one_rule(css)` | parse a rule | P4 |
| `parse_block_contents(css)` | parse a block's contents (declarations and nested rules) | P2 (`style=""`) |
| `parse_one_declaration(css)` | parse a declaration | P3 |

- Rules and declarations are real Python objects, because there are few of
  them (thousands, not millions). Preludes, blocks and values stay lazy
  views.
- Each rule and declaration stores its start/end offsets, so line and
  column are available on request (P12), including for nested rules.
- `!important` (any case, with comments or whitespace in between) is found
  in C. `Declaration.important: bool` is set, and the value view excludes
  it. The literal text stays reachable through `source_text`. Other
  `!foo` priorities, such as `!ie` (P15), remain part of the value.
- Custom properties (`--x`) keep their value tokens exactly as written,
  without stripping whitespace, as the spec requires (P15).
- Comments at top level and between declarations come out as `Comment`
  nodes (P7). Comments inside a value stay in the value's token range.
- The parser **never raises** on CSS input (P5). Errors are collected as
  `ParseError(line, column, offset, kind, message)` objects on the result
  (L3). The per-parse logger or callback (L1) belongs to the CSSOM layer,
  which turns these records into log messages.

### 3.4 Unicode-range (T1): decision needed

The current spec **removed** the unicode-range token. With that token,
`u+a` in a selector (a `u` element followed by an `a` sibling) is
mis-tokenized. The spec now builds `<urange>` from ordinary tokens, and only
in contexts that expect it (the `unicode-range` descriptor).

Recommendation: follow the current spec. Do not create a unicode-range
token. Instead provide `parse_urange(values: ComponentValueList)`, which the
`@font-face` layer calls. T1 is then met at the value layer. The conformance
suite still expects unicode-range tokens, so a few cases need overrides (§7).
The `~= |= ^= $= *= ||` match tokens get the same treatment: the current
spec has no such tokens, and the selector parser combines DELIM tokens
instead.

### 3.5 Selector tokenizer (T7)

This is `tokenize(selector_text)`. `.pos`, `.end` and `.value` follow from
§3.1, and `tok.end` takes the place of "the next token's pos". The calibre-specific
behaviour of raising lazily on unclosed strings belongs in calibre's shim,
not in the library: the library reports an `UNCLOSED` flag plus a
ParseError.

---

## 4. File layout

```
csrc/
  kgcss.h              shared header (existing)
  module.c             method table, module state, type registration (existing)
  tokens.h             kg_token, kg_token_type enum, flags, ASCII class table decl
  tokenizer.c          dispatch on kind + array/stack/line-table management
  tokenizer_impl.h     template body: #included 3x with CHAR_T / KG_SUFFIX
  unescape.c           value unescaping (kind-generic, builds str)
  numbers.c            number token → double / PyLong
  syntax.c             css-syntax §5 parse algorithms over the token array
  types.c              heap types: TokenStream, Token, Block, Function,
                       ComponentValueList, rule/decl/comment/error objects
src/kgcss/
  tokens.py            TokenType IntEnum, public re-exports
  syntax.py            parse_* wrappers, input type normalisation
  decode.py            decode_stylesheet (§2)
  _encodings.py        generated WHATWG label table
  _kgcss.pyi           stubs for all new C functions and types
tests/
  css-parsing-tests/   vendored CC0 suite (git subtree, pinned commit)
  test_css_parsing_suite.py   JSON-driven conformance runner
  test_tokenizer.py    positions, round-trip, escapes, numbers, ported tinycss tests
  test_syntax.py       rules, declarations, nesting, !important, comments, errors
  test_decode.py       BOM / @charset / labels
  test_fuzz.py         hypothesis property tests
bench/
  bench_tokenize.py    corpus timing; compare tinycss (C), tinycss2, css_parser
```

---

## 5. Milestones

Status: milestones 1 and 2 are done. Every `component_value_list.json` and
`one_component_value.json` case passes on all three PEP 393 kinds, except
the unicode-range cases that differ by design. The suite now runs on the C
grouping. The Python grouping in `tests/suite.py` is kept as an independent
reference for a differential test on random input.

Notes from milestone 2:
- `Block.type` and `Function.type` return the `TokenType` of the opening
  token, so callers can dispatch on `.type` for any component value.
- Whether comments appear as items is decided by `comments=` at tokenize
  time. `text` always includes them.
- Nothing in the grouping code recurses, so 200k levels of nesting work.
- Timings on the same 1.3 MB input: `tokenize()` with grouping runs at
  470 MB/s (about 15% slower than without). `parse_component_value_list()`
  runs at 230 MB/s. Walking and creating every node runs at 18 MB/s, still
  9x tinycss2.

A first measurement, taskset 0-2, on 1.3 MB of synthetic CSS
(512k tokens): `tokenize()` runs at 540 MB/s. Creating a `Token` and its
value for every token runs at 40 MB/s, dominated by Python object creation.
That gap is why values are only built on demand (§3.2). tinycss2 runs at
2 MB/s on the same input.

Each milestone ends with ruff, mypy and pytest passing on both normal and
free-threaded 3.14, and with no warnings in a `KGCSS_WERROR=1` build.

1. **Tokenizer core.** `tokens.h`, the template tokenizer, the line table,
   comments, the error list, and `TokenStream`/`Token` types. `tokenize()`
   is exposed. Unescaping and numbers are included. Tests:
   - round trip: the concatenation of every `source_text` equals the input;
   - positions;
   - escapes, including `#\5f x` → `_x`, NUL, surrogates, escaped newlines
     and EOF after `\`;
   - all three kinds, checked by running the same tests on inputs padded
     with a U+0100 and a U+10000 character.
2. **Grouping.** Matching brackets in the same pass, unclosed recovery,
   `Block`, `Function`, `ComponentValueList`, and the component-value parse
   functions. Conformance: `component_value_list.json` and
   `one_component_value.json`.
3. **Syntax parser.** Stylesheet, rule list, one rule, block contents
   (nesting-aware), and one declaration; `!important`, comments, positions,
   and `ParseError`. Conformance: `stylesheet.json`, `rule_list.json`,
   `one_rule.json`, `blocks_contents.json`, `declaration_list.json` and
   `one_declaration.json`. Also test the P5 example
   (`color: red; }}} margin: 0`), plus P6, P7 and P15 round trips.
4. **Bytes.** `decode_stylesheet` and the WHATWG table, plus
   `stylesheet_bytes.json`.
5. **Hardening.** Hypothesis property tests (never crashes; round trip
   holds; positions increase), runs under ASan/UBSan
   (`KGCSS_SANITIZE=1 KGCSS_DEBUG=1`), and a free-threaded stress test that
   parses and reads one shared `TokenStream` from 8 threads. An optional
   libFuzzer harness for the C tokenizer can be added later.
6. **Benchmarks and tuning.** Corpus: Bootstrap and Tailwind builds, a large
   calibre-generated sheet, and a synthetic sheet with many non-ASCII
   characters (to hit kind 2 and kind 4). Pin with `taskset -c 0-2`. Report
   MB/s for `tokenize`, `parse_stylesheet`, and "parse plus read every
   declaration value as text". Compare against calibre's tinycss C
   tokenizer, tinycss2 and css_parser. Only then tune: GIL release
   threshold, memchr scans, and interning of repeated idents with a
   per-parse dict (never a global one).

---

## 6. Decisions (settled)

1. **No unicode-range or match tokens**, as in the current spec (§3.4).
   `<urange>` is parsed at the value layer.
2. **Nesting-aware block parsing**, as in the current ED.
3. **1-based line and column**, counted in code points.
4. **4 GiB input limit** per parse (`uint32_t` offsets).
5. Non-ASCII code points are all ident code points (≥ U+0080). This matches
   browsers and tinycss2. The newer ED restricts the set, but no browser
   ships that restriction yet.
6. Numbers are converted **lazily**, when a `Token` is created, not during
   tokenizing. `PyOS_string_to_double` needs an attached thread state, so
   keeping it out of the tokenizer lets that pass run with the GIL released.
   The token record therefore has no numeric field and is 24 bytes.
7. A `TokenStream` does not include an EOF token. Every token has `.end`,
   which is what T7 used "the next token's pos" for.
8. The line table is built lazily, on the first line/column request, under
   `Py_BEGIN_CRITICAL_SECTION` on the stream. It is the only mutable cache.
   Most parses never ask for positions.

## 7. Testing against the conformance suite

- Vendor https://github.com/CourtBouillon/css-parsing-tests (CC0, maintained
  for tinycss2) under `tests/css-parsing-tests/` at a pinned commit.
- The runner converts our objects to the suite's JSON format. The README
  documents it: `["ident", "b"]`, `["number", "42", 42, "integer"]`, and so
  on.
- Each deliberate difference, such as unicode-range (§3.4), goes in an
  explicit `EXPECTED_DIFFERENCES` table in the runner. Each entry gives the
  input, our output and the reason. Unexpected failures stay failures.
- The color, An+B and other files in the suite are for later plans
  (colors, §12; selectors, §9).
