# Plan 04: Media queries and colors

This plan covers two small layers that calibre uses on their own, outside
the object model. It covers these parts of requirements.md:

- §10: media queries, M1 to M4, including the media type case quirk of
  §18;
- §12: colors, C1 to C5, replacing `tinycss.color4`;
- §16: the tinycss `media3`, `color3` and `color4` tests;
- §14: a color parser in C.

Plan 02 decided the order (its D9): selectors, then media queries and
colors, then fonts and property grammar. The two layers share nothing but
the tokenizer, so this plan keeps them in separate modules and milestones.

**Out of scope:**
- `@supports` conditions, `@container` queries and `@custom-media`: the
  rules keep their preludes as text, as now;
- CSS Color Level 5: `color-mix()`, relative colors (`rgb(from red r g b)`),
  `light-dark()`, `device-cmyk()` and `contrast-color()`. They return
  `None`, as C3 requires;
- `validate_color` (G2) and the property matchers (G1), which are plan 05.
  This plan gives them their base: a color is valid if `parse_rgba(text)`
  is not `None`.

---

## 1. Conventions

The conventions of plans 02 and 03 apply: `snake_case`, keyword-only
options, full typing, `@final` frozen classes for values. In addition:

- Neither layer raises on bad input. A color that does not parse is
  `None`. A media query that does not parse is a `MediaQuery` with
  `valid=False`, which never matches, as the spec says ("not all"). Only
  wrong argument types raise `TypeError`.
- Everything here is immutable and needs no locks. Both parsers are pure
  functions of their input; the color parser in C keeps no state at all.

---

## 2. Key decisions

### 2.1 Colors: port calibre's color.c

calibre already has a CSS Color 4 parser in C, `src/tinycss/color.c`
(2026), which `tinycss.color4.parse_color_string` wraps. It parses the
string directly, without a tokenizer, and converts every color space to
sRGB. Its behaviour is what calibre's tests pin. It is fast: measured on
this machine, with the call overhead of a lambda included:

| input | time |
|---|---:|
| `red` | 138 ns |
| `#ff0000` | 130 ns |
| `rgb(255, 0, 0)` | 208 ns |
| `oklch(0.628 0.2577 29.23)` | 357 ns |
| `margin` (not a color) | 59 ns |
| `rgb(var(--x))` (not a color) | 47 ns |

The plan ports it into `csrc/color.c` rather than writing a new one, with
three changes:
- **Two steps.** It parses into a `kg_color` struct (the space, three
  channels, a mask for `none`, the alpha), then converts. The rich API of
  §2.2 needs the specified values; the fast path converts at once.
- **Escapes.** It returns `None` for any input with a backslash or a
  non-ASCII character, so `r\65 d` is not `red`, although CSS says it is.
  See D2: the recommendation is to follow CSS, with a fallback through the
  tokenizer for the rare inputs that have escapes, and to keep the fast
  path for the rest.
- **The `RGBA` type** is a heap struct sequence (`PyStructSequence`), built
  in C without calling into Python. It is a `tuple`, so `isinstance(x,
  tuple)`, `x[:3]` and `x.alpha` all work as calibre's callers expect,
  and it is created as fast as a plain tuple.

Colors outside sRGB are **clipped** per channel, as C1 requires and
`tinycss` does. CSS Color 4 recommends gamut mapping in OKLCH for display,
which gives different results for very saturated colors. calibre's tests
pin clipping (`lab(54.29 80.82 69.91)` is pure red), so clipping stays.

### 2.2 Colors: a value model as well as RGBA

calibre only needs `RGBA` (C1). But the vendored CSS parsing suite has
8,000 color cases, in `tests/css-parsing-tests/color_*.json`, which plan 01
left for this plan. They expect the *specified* value, serialized as CSS
Color 4 §15 says: `lab(50% 40 59.5)` stays a `lab()` color, `color(srgb-linear
400% 0 10 / 50%)` keeps its out-of-gamut `4`, and `none` is kept. Passing
them needs a model that remembers the space and the unclipped channels.

So there are two functions over the same parser:
- `parse_rgba(text)`: the fast path for calibre, `RGBA`, `'currentColor'`
  or `None`, as C1 specifies;
- `parse_color(text)`: a `Color` with the space and specified channels,
  `to_css()` per §15, and `to_rgba()`.

The model costs little: one small frozen class, and the parser already
has to know the space. It also makes the test suite a much stronger check
of the parser than the hundred or so cases of calibre's tests.

### 2.3 Media queries: Level 4, in Python over the tokenizer

tinycss parses Media Queries Level 3: a type, `and`, and `(feature: value)`
expressions. calibre's callers only use that shape (M1). But sheets now
use Level 4 syntax, which tinycss rejects as malformed: ranges
(`(width >= 600px)`), `or`, `not` inside conditions and nested
parentheses. Range syntax is in M1 too.

The parser implements the Level 4 grammar:

```
<media-query> = <media-condition>
              | [ not | only ]? <media-type> [ and <media-condition-without-or> ]?
<media-condition> = <media-not> | <media-in-parens> [ <media-and>* | <media-or>* ]
<media-in-parens> = ( <media-condition> ) | <media-feature> | <general-enclosed>
```

It is written in Python, over the token tree from the C tokenizer
(`parse_component_value_list`). Queries are short, and parsed results are
cached by text (§4.3), so a C parser would not pay for itself:
`media_ok()` sees the same few texts again and again.

`<general-enclosed>` matters: `(foo bar baz)` or `(color: rgb(0 0 0))`
are not malformed. They are valid queries whose condition is *unknown*,
which counts as false at the top level, but `not (unknown)` is still
unknown, not true. tinycss treats them as malformed, which gives the same
answer at the top level but not under `not`. The model keeps them as
`GeneralEnclosed`, and evaluation (§2.4) uses the three-valued logic of
the spec.

### 2.4 Evaluation: the logic in kgcss, the policy in calibre

M3 says evaluation stays in calibre: calibre decides which media types and
features are true for an e-book. But the *logic* of combining them is the
spec's, and with Level 4 it is no longer a loop over `(name, value)`
pairs: `or`, `not`, nesting and unknown results need three-valued logic.
Getting that wrong in calibre is easy.

So queries have a `matches()` method that takes calibre's policy as two
callbacks, one for media types and one for features, and does the rest
(see D4):

```python
ALLOWED = frozenset({'screen', 'all', 'aural', 'amzn-kf8'})

def media_ok(raw: str | None) -> bool:
    return not raw or raw != 'amzn-mobi' and media_matches(
        raw, media_type=ALLOWED.__contains__,
        feature=lambda f: False if f.name in IGNORED_MEDIA_FEATURES else None)
```

A feature callback returns `True`, `False` or `None` (unknown). The query
also exposes its features as plain `(name, value)` data, for callers that
want to do it all themselves (M1).

### 2.5 Media types are lowercased

The media type is ASCII-lowercased in the model, as Media Queries says, so
`SCREEN` matches calibre's `screen` (the M3 quirk). Feature names are
lowercased too. `MediaList.media_text` keeps the text as written (plan 02,
M2).

---

## 3. Entry points

```python
from kgcss import (
    RGBA, Color, parse_color, parse_rgba,
    MediaQuery, MediaFeature, parse_media_queries, media_matches,
)

class RGBA(tuple[float, float, float, float]):   # a struct sequence, in C
    red: float; green: float; blue: float; alpha: float   # each 0..1, clipped

def parse_rgba(text: str) -> RGBA | Literal['currentColor'] | None:
    """C1: sRGB, clipped. transparent is (0, 0, 0, 0); currentColor in any case
    is 'currentColor'. None for anything else, including var() and calc()."""

def parse_color(text: str | Component) -> Color | Literal['currentColor'] | None:
    """The specified color (§4.1). A Component is parsed from its css_text."""

@final
class Color:                       # frozen, slots
    space: ColorSpace              # 'rgb', 'lab', 'lch', 'oklab', 'oklch', or a color() space
    channels: tuple[float | None, float | None, float | None]   # None is `none`
    alpha: float | None
    def to_rgba(self) -> RGBA: ...
    def to_css(self) -> str: ...   # CSS Color 4 §15

def parse_media_queries(text: str) -> tuple[MediaQuery, ...]:
    """Never raises. Cached by text. '' is the empty list, which matches."""

def media_matches(text: str, *, media_type: Callable[[str], bool],
                  feature: Callable[[MediaFeature], bool | None]) -> bool:
    """Whether any query of the list matches. True for an empty list."""
```

`MediaList` (plan 02) gains `queries` (the parsed tuple, cached on the
text) and `matches(media_type=, feature=)`.

---

## 4. Colors

### 4.1 The model

| Input | `space` | `channels` |
|---|---|---|
| names, `#hex`, `rgb()`, `rgba()`, `hsl()`, `hsla()`, `hwb()` | `'rgb'` | 0..255, clamped at parse time as the spec says; hsl and hwb converted |
| `lab()`, `lch()`, `oklab()`, `oklch()` | the function name | as specified, percentages resolved |
| `color(<space> ...)` | the space: `srgb`, `srgb-linear`, `display-p3`, `a98-rgb`, `prophoto-rgb`, `rec2020`, `xyz-d50`, `xyz-d65` (`xyz` is `xyz-d65`) | as specified, not clipped |

- `alpha` is clamped to 0..1. `None` is `none`. An omitted alpha is 1.
- `none` is only allowed in the modern (space-separated) syntax. In the
  legacy `rgb` space, it becomes 0 when serialized, as §15 says.
- Hues are in degrees: `deg`, `grad`, `rad` and `turn` are converted.
  Negative chroma becomes 0, lightness is clamped, as §8 and §9 say.
- `to_rgba()` converts through XYZ and clips. `to_css()` writes
  `rgb(r, g, b)` or `rgba(r, g, b, a)` for the `rgb` space, and the
  function or `color()` form otherwise, with `none` kept.

### 4.2 Syntax (C2, C3)

Everything in C2 and the tinycss `color4` tests:
- names of CSS 3 plus `rebeccapurple`, ASCII case-insensitive;
  `transparent`; `currentColor` in any case;
- hex with 3, 4, 6 or 8 digits;
- `rgb()`/`rgba()` and `hsl()`/`hsla()` in the legacy comma form (rgb all
  numbers or all percentages; alpha may be a percentage) and the modern
  form with `/ alpha` and `none`;
- `hwb()`, `lab()`, `lch()`, `oklab()`, `oklch()`, `color()`;
- scientific notation, comments, surrounding whitespace, and a function or
  comment left open at the end of the input.

`None` for: `calc()`, `var()`, `env()` and any other nested function;
Level 5 functions; system colors such as `canvas`; trailing tokens; the
wrong number of arguments; units on rgb values; mixed legacy and modern
syntax.

### 4.3 Performance (C4)

The editor calls the parser for every color-like token on every highlight
pass. Target: `parse_rgba` no slower than calibre's `parse_css4_color_string`
on the inputs of §2.1, measured the same way; not-a-color inputs, the most
common case in the editor, under 60 ns. `parse_color` may cost one more
object.

---

## 5. Media queries

### 5.1 The model

```python
@final
class MediaQuery:                 # frozen, slots
    text: str                     # as written, without surrounding whitespace
    valid: bool                   # False: malformed, matches nothing ("not all")
    error: str | None             # why it is malformed, for diagnostics
    negated: bool                 # `not` before the type
    only: bool                    # `only` before the type
    media_type: str               # lowercased; 'all' if omitted
    condition: MediaCondition | None
    def features(self) -> Iterator[MediaFeature]: ...   # every feature, in order (M1)
    def matches(self, *, media_type: ..., feature: ...) -> bool: ...
    def to_css(self) -> str: ...  # canonical form

type MediaCondition = MediaNot | MediaAnd | MediaOr | MediaFeature | GeneralEnclosed

@final
class MediaFeature:
    name: str                     # lowercased: 'min-width', 'color', 'width'
    operator: Literal['', ':', '<', '<=', '>', '>=', '=']   # '' for (color)
    value: Number | Dimension | Ident | Ratio | Component | None
```

- A **range** is normalized so the name comes first: `(600px <= width)` is
  `width >= 600px`. A double range, `(400px < width <= 700px)`, is a
  `MediaAnd` of two features, which the spec defines as equivalent.
- **Values** reuse the value components of plan 02 (`Number`, `Dimension`,
  `Ident`), plus a new `Ratio(numerator, denominator)` for `16/9` and
  `16 / 9`. Anything else that parses as a value, such as `calc()`, is
  kept as its `Component`.
- A malformed query is kept with `valid=False`, its text and an `error`,
  so the media list keeps one entry per query, the same count as
  `MediaList`'s items.

### 5.2 Rules from the spec

- The list is split on top-level commas. An empty list matches; an empty
  query between commas (`, screen`) is malformed. tinycss treats it as
  `all` (see §7).
- `not`, `and`, `or`, `only` and `layer` are not media types.
- `and` and `or` cannot be mixed at one level without parentheses;
  `screen and (a) or (b)` is malformed.
- `only` and `not` need a media type: `only (color)` is malformed, but
  `not (color)` is a condition, as Level 4 says.
- Unknown feature names are valid syntax: evaluation asks the callback.

### 5.3 Evaluation

`matches()` evaluates with the three values of Media Queries 4 §3.2 (true,
false, unknown):
- `media_type(name)` is asked for the type; `all` is always true;
- `feature(f)` gives true, false or `None` for unknown;
- `GeneralEnclosed` is unknown;
- `not`, `and` and `or` follow Kleene logic; at the top level unknown is
  false, then `not` before the type negates the result;
- a malformed query is false.

`parse_media_queries` is cached by text (an LRU cache of 1,024 entries),
so `media_ok()` on the `media` attribute of every element costs a lookup.

---

## 6. Performance targets

Pinned to the fast cores:
- `parse_rgba`: §4.3;
- `parse_media_queries`: under 20 µs for a typical uncached query such as
  `only screen and (min-width: 600px) and (max-width: 900px)`, under
  0.3 µs cached;
- `media_matches` for a cached list of one query: under 2 µs.

`bench/bench_color.py` compares with calibre's C parser, already in the
bench cache (`calibre_extensions.tokenizer.parse_css4_color_string`), and
with tinycss's media parser, on the colors and `@media` preludes of the
corpus.

---

## 7. Testing

- **Colors:**
  - the color files of the vendored suite, through `parse_color(...).to_css()`,
    compared numerically to 6 significant digits, as the suite writes them.
    `color_functions_5.json` is Level 5: its valid cases are recorded as
    expected differences;
  - the tinycss `color4` tests, both directions (values and `None`);
  - `color4` as a superset of `color3` on valid CSS 3 colors, to 1e-10,
    with the `color3` results stored in the test, since kgcss has no CSS 3
    parser;
  - calibre's `convert_color` and `convert_color_for_font_tag` tests, on
    `parse_rgba`;
  - property tests: `parse_color(c.to_css()) == c` for generated colors;
    `parse_rgba(text) == parse_color(text).to_rgba()` for every input;
    never raises on any string;
  - a fuzz target for the C parser, and the sanitizer and free-threaded
    runs.
- **Media queries:**
  - the tinycss `media3` tests, ported to the new model, with the
    differences of §5.2 recorded;
  - calibre's `test_media_ok`, through `media_matches` with calibre's
    policy;
  - the examples of Media Queries 4, including ranges, `or`, `not` inside
    conditions and `<general-enclosed>` under `not`;
  - property tests: generated queries round trip through `to_css()`;
    `MediaList.media_text` is unchanged by reading `queries`; the
    evaluator agrees with a naive oracle written from §5.3 on random
    conditions and policies; never raises.

---

## 8. Milestones

Status: planned.

1. **The color parser in C.** Port `color.c` into `csrc/color.c` with the
   two-step design of §2.1. `parse_rgba` and `RGBA`. The escape fallback
   per D2. The tinycss color tests, calibre's color tests, the fuzz
   target. `bench/bench_color.py` against calibre's parser.
2. **The color model.** `Color`, `parse_color`, `to_css()` and `to_rgba()`.
   The suite's color files. The property tests. Accept a `Component`.
3. **Media queries.** The parser, the model, evaluation, the cache,
   `MediaList.queries` and `matches()`. The media3 port, calibre's
   `media_ok` tests, the spec examples, the oracle and the property tests.
   Benchmarks.

---

## 9. Decisions

| # | Question | Recommendation |
|---|---|---|
| D1 | Port calibre's `color.c`, or write a new color parser? | Port it (§2.1): it is fast, already pinned by calibre's tests, and the same license |
| D2 | `r\65 d`: tinycss and its test say it is not a color; CSS says it is `red` | Follow CSS: escapes go through the tokenizer, and the calibre test changes |
| D3 | Only `RGBA`, as C1 asks, or also a `Color` model with the specified values? | Both (§2.2): `parse_rgba` for calibre, `parse_color` for the suite and for a future `validate_color` with more detail |
| D4 | Evaluation: only expose features and let calibre combine them (M3 as written), or evaluate in kgcss with calibre's policy as callbacks? | Evaluate in kgcss (§2.4): Level 4 needs three-valued logic, and the policy stays in calibre |
| D5 | `, screen`: tinycss parses the empty query as `all`; Media Queries says it is malformed | Follow the spec |
| D6 | Names: `parse_rgba` and `parse_color`, or keep tinycss's `parse_color_string`? | `parse_rgba` and `parse_color`; the calibre shim is one line |
| D7 | Log malformed media queries when a sheet is parsed (L2), or only on access? | Only on access: queries are parsed lazily, and `MediaQuery.error` says what is wrong. The sheet's parse stays as fast as now |

---

## Appendix A: tinycss names to kgcss names

| tinycss | kgcss |
|---|---|
| `color4.parse_color_string(s)` | `parse_rgba(s)` |
| `color3.RGBA` | `RGBA` |
| `media3.MediaQuery(media_type, expressions, negated)` | `MediaQuery`: `media_type`, `features()`, `negated`, plus `only`, `condition`, `valid` |
| `CSSMedia3Parser().parse_stylesheet('@media X {}').rules[0].media` | `parse_media_queries(X)` |
| a malformed query: `MediaQuery('all', (), negated=True)` | `MediaQuery(valid=False)` |
| expression value tokens (`DIMENSION`, `RATIO`, `IDENT`) | `Dimension`, `Ratio`, `Ident` components |
