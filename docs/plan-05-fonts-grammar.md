# Plan 05: Font helpers, value grammar and shorthands

This is the last plan for requirements.md. It covers:

- §11: the font helpers that replace `tinycss.fonts3`: `parse_font_family`,
  `serialize_font_family`, `parse_font` and `serialize_font`;
- §8: G1, value matchers for the longhands that calibre's shorthand
  expansion needs; G2, `validate_color`. G3 (profiles) and G4 (validation
  logging) are not needed (§17);
- §8, the optional part: shorthand expansion and condensation, which
  calibre does in `ebooks/oeb/normalize_css.py` on top of css_parser;
- §16: the tinycss `fonts3` tests and the tests of `normalize_css.py`.

**Out of scope:**
- full property grammar for every CSS property, and validation while
  parsing: the CSSOM keeps values as written (plan 02);
- `@font-face` descriptors beyond what plan 02 already does (requirements
  §17: page3 and fonts3 rule objects beyond the tree);
- the cascade itself, and computing values (`em` to `pt` and the like),
  which stay in calibre's stylizer.

---

## 1. Conventions

The conventions of plans 02 to 04 apply. Everything here is pure Python
over the C tokenizer and value parser, without new C code: the inputs are
single declaration values, and §6 shows that Python over the tokenizer
already beats the code it replaces. Nothing raises on bad values: the
functions return empty results or `None`, as the tinycss helpers do,
except for wrong argument types.

---

## 2. Key decisions

### 2.1 Fonts: keep tinycss's behaviour and shapes

calibre calls the four helpers from a dozen places (requirements §11), and
`tinycss/tests/fonts3.py` pins their behaviour. They are reimplemented on
kgcss's tokenizer, with the same results:
- `parse_font_family(text) -> list[str]`: strings unquoted and unescaped,
  runs of idents joined by one space, escapes resolved, empty names
  dropped, generic names unchanged;
- `serialize_font_family(names) -> str`: generic names lowercased
  (`sansserif` becomes `sans-serif`); simple names unquoted; others double
  quoted; joined with `', '`;
- `parse_font(text) -> dict`: only the keys present, among `font-style`,
  `font-variant`, `font-weight`, `font-stretch`, `font-size`,
  `line-height` and `font-family` (a list); legacy system fonts such as
  `caption` give `{'font-family': ['sans-serif']}`;
- `serialize_font(dict) -> str`, so that `parse_font(serialize_font(d)) == d`.

The dict keeps its shape (see D1): calibre puts its items straight into
style maps keyed by property name. It is typed as a `TypedDict`.

Two known quirks of tinycss are worth deciding on:
- `serialize_font_family` quotes names that start with `and`, only
  because css_parser fails on them. kgcss has no such problem (D2);
- a quoted generic name (`"serif"`, a font called serif) comes back as the
  generic `serif`, since the list of str loses the quotes. Fixing that
  would change the return type, which all those callers use, so it stays, and
  is documented.

### 2.2 The value grammar: a small set of types, not a validator

css_parser validates any value of any property against a profile. calibre
uses that in two places only (requirements G1, G2):
- `normalize_css.py` asks, for each token of a shorthand, which longhand
  it belongs to: `list-style-type`, `-position` or `-image`, or
  `border-*-color`, `-style` or `-width`;
- the stylizer asks whether a value is a color.

So kgcss provides value types and the longhands built on them, not a
general validator (D3):

```python
def matches(property_name: str, value: str | Component) -> bool | None
```

It returns `None` for a property it does not know. The types are
`<color>` (the parser of plan 04), `<length>`, `<percentage>`,
`<line-style>`, `<line-width>`, `<image>` (`url()`, gradients and
`image-set()` by name, `none` where the property allows it), the list
style types and positions, and what the font longhands need for §2.4. A
`var()` matches nothing: its value is unknown until computed.

`list-style-type` follows CSS Lists 3 (D4): any `<custom-ident>` is a
counter style name, not only CSS 2's fixed keywords, except the words
that would make `list-style` ambiguous (`inside`, `outside`, `none` and
the CSS-wide keywords). A string is also a valid type.

`is_color(text)` is `parse_rgba(text) is not None` (D5). It accepts the
CSS 4 colors that css_parser's CSS 2 profile rejects (`#abcd`, `lab()`),
and no longer accepts `inherit`, which is not a color; where calibre's
stylizer relied on that, it checks the CSS-wide keywords itself.

### 2.3 Shorthands in the library

calibre's `normalize_css.py` expands `margin`, `padding`, `border` and its
parts, `list-style` and `font` into longhands, and condenses `margin`,
`padding` and `border` back. It is about 250 lines over css_parser value
objects, so it has to be rewritten for kgcss anyway. The requirements
welcome a library version built on G1, and it is recommended (D6):

```python
def expand_shorthand(name: str, value: str, *, initial: Mapping[str, str] = INITIAL_VALUES
                     ) -> dict[str, str] | None
def longhands(name: str) -> tuple[str, ...]     # for normalize_filter_css()
def condense_shorthands(block: DeclarationBlock) -> None
```

- `expand_shorthand` gives every longhand of the shorthand: the ones in
  the value, and the initial value for the rest, as CSS does. It returns
  `None` if the value does not match the shorthand's grammar, or uses
  `var()`, whose longhands cannot be known before computing.
  CSS-wide keywords (`inherit` and the like) apply to every longhand.
- Initial values come from the specs, in `INITIAL_VALUES`. `font-family`
  has no initial value in CSS (it depends on the user agent), so it is
  `serif`, as in calibre's `DEFAULTS`; callers can pass their own table
  (D7).
- `condense_shorthands` does what calibre's `condense_rule` does: four
  edges of `margin` and `padding` become the shortest shorthand; the
  three parts of a border edge become `border-<edge>`, and four equal
  edges become `border`. Only when no declaration involved is
  `!important`, as now.

### 2.4 What expansion follows

The expansions follow the specs' grammars, which differ from calibre's
token-by-token assignment in a few corners:
- `font` uses `parse_font` (§2.1), as calibre does, so it keeps its
  behaviour exactly;
- `border`, `border-<edge>` and `list-style` assign each component to the
  first longhand it matches, in the order of the grammar, which is what
  calibre's loop does too. A value with two components for one longhand
  (`border: solid dotted`) does not match the grammar and gives `None`;
  calibre's loop, which works from the end, kept the first one;
- `list-style: none` sets both `list-style-type` and `list-style-image`
  to `none` when neither is given otherwise, as CSS Lists says;
- `margin` and `padding` take one to four values, as now. Five or more do
  not match (calibre kept the first four).

---

## 3. Entry points

```python
from kgcss import (
    parse_font_family, serialize_font_family, parse_font, serialize_font, FontDict,
    matches, is_color,
    expand_shorthand, longhands, condense_shorthands, INITIAL_VALUES,
)
```

`kgcss.fonts`, `kgcss.grammar` and `kgcss.shorthands` are the modules.

---

## 4. Testing

- **Fonts:** the tinycss fonts3 tests, and the font cases of calibre's
  `test_normalization`; a differential test against tinycss's helpers on
  random font values, from the benchmark cache; round trips of
  `serialize_font_family`/`parse_font_family` on random names, including
  ones that need quotes and escapes, and of `serialize_font`/`parse_font`
  on random font dicts.
- **Grammar:** tables per type and longhand, with the css_parser answers
  for the values both should agree on, and the differences of D4 and D5
  listed; never raises on any text.
- **Shorthands:** every test of calibre's `normalize_css.py`
  (`test_font_normalization`, `test_border_normalization`,
  `test_edge_normalization`, `test_list_style_normalization`,
  `test_filter_css_normalization`, `test_edge_condensation`,
  `test_border_condensation`), with the differences of §2.4 marked;
  property tests: expanding then condensing gives back an equivalent
  block, and condensing never changes what the longhands are.

---

## 5. Milestones

Status: milestone 1 is done.

Notes from milestone 1:
- **Results**, `bench/bench_fonts.py` pinned to the fast cores, against
  tinycss (the target of §6 was twice as fast):

  | call | kgcss µs | tinycss µs |
  |---|---:|---:|
  | `parse_font_family('"Helvetica Neue", Arial, sans-serif')` | 1.87 | 13.91 |
  | `parse_font('bold italic 12pt/1.4 "Helvetica Neue", serif')` | 4.15 | 24.68 |
  | `serialize_font_family` | 1.11 | 1.24 |
  | `serialize_font` | 1.92 | 2.04 |

  The 10,310 font-family values of the corpus parse in 12.9 ms, against
  91.8 ms for tinycss.
- **The port** follows tinycss's algorithm step by step, on the component
  values of kgcss's tokenizer, so functions and blocks are skipped as
  tinycss skipped its containers. Two of tinycss's branches for the line
  height could never run (a size has always been handled before them);
  they are gone.
- **Deliberate differences from tinycss**, besides D2:
  - keywords are matched ASCII case-insensitively and returned in lower
    case: tinycss took `BOLD` for a family name;
  - only an ident can be a legacy system font: tinycss also took the
    string `"caption"` for one;
  - `serialize_font_family` quotes with `serialize_string`, which escapes
    backslashes and control characters too; tinycss escaped only `"`, so
    a name with a backslash did not come back;
  - `serialize_font` quotes family names that are font keywords
    (`"bold"`), which would otherwise be read as the keyword. The plain
    `serialize_font_family` leaves them, as they are fine in
    `font-family`.
- **tinycss crashes** (`AttributeError`) on a `font` value that starts
  with a function, such as `calc(1px) serif`. kgcss does not.
- **Tests** (`tests/test_fonts.py`): the fonts3 tables, the font cases of
  calibre's `normalize_css.py` through a port of its `normalize_font`;
  round trips of family lists over random names and of `parse_font`
  results; a differential test against tinycss on random font values,
  of which 94% are compared (the rest crash tinycss), with up to five
  longhands each.

1. **Font helpers.** `kgcss.fonts`, the fonts3 tests, the differential
   test against tinycss, round trips, benchmarks.
2. **The value grammar.** `kgcss.grammar`: the types, `matches()` for the
   longhands of §2.2, `is_color()`. Tables and the comparison with
   css_parser.
3. **Shorthands.** `kgcss.shorthands`: expansion, `longhands()`,
   condensation on `DeclarationBlock`, `INITIAL_VALUES`. The ported
   `normalize_css.py` tests and the property tests. Benchmarks.

---

## 6. Performance targets

Measured on this machine, pinned to the fast cores, tinycss takes 13.8 µs
for `parse_font_family('"Helvetica Neue", Arial, sans-serif')` and 24.8 µs
for `parse_font('bold italic 12pt/1.4 "Helvetica Neue", serif')`.
css_parser's `profile.validate('border-top-style', 'solid')` takes
0.6 µs.

Targets: the font parsers at least twice as fast as tinycss; `matches()`
under 2 µs for a keyword and under 5 µs for a color; `expand_shorthand`
under 20 µs for `border: 1px solid red`. calibre calls these once per
declaration, not per element, so these leave them far from mattering.

---

## 7. Decisions

Decided on 2026-11-25, as recommended.

| # | Question | Decision |
|---|---|---|
| D1 | `parse_font`: keep the dict of tinycss, or return a dataclass? | Keep the dict, typed as a `TypedDict`: calibre merges it into style maps keyed by property name |
| D2 | `serialize_font_family` quotes names that start with `and`, a workaround for css_parser | Drop it: quote only what CSS needs, plus the CSS-wide keywords and `default`, which unquoted would mean something else |
| D3 | G1: a general validator for every property, or the types and longhands calibre uses? | The types and longhands that the shorthands of §2.3 need (§2.2), with `None` for others |
| D4 | `list-style-type`: CSS 2's fixed keywords (css_parser) or any counter style name (CSS Lists 3)? | CSS Lists 3, without the words that make `list-style` ambiguous |
| D5 | `validate_color`: css_parser's CSS 2 profile (accepts `inherit`, rejects CSS 4 colors) or `parse_rgba`? | `is_color(text)`, on `parse_rgba`; calibre checks CSS-wide keywords itself where it needs them |
| D6 | Shorthand expansion and condensation: leave them in calibre, or provide them? | Provide both (§2.3), with calibre's tests |
| D7 | Initial values for expansion: calibre's `DEFAULTS` or the specs'? | The specs', with `font-family: serif` as calibre has it, and an `initial=` parameter for callers |

---

## Appendix A: calibre names to kgcss names

| calibre | kgcss |
|---|---|
| `tinycss.fonts3.parse_font_family` | `parse_font_family` |
| `tinycss.fonts3.serialize_font_family` | `serialize_font_family` |
| `tinycss.fonts3.parse_font` | `parse_font` |
| `tinycss.fonts3.serialize_font` | `serialize_font` |
| `css_parser.profile.validate(name, value)` | `matches(name, value)` |
| `stylizer.validate_color(value)` | `is_color(value)` |
| `normalize_css.normalizers[name](name, value)` | `expand_shorthand(name, value, initial=DEFAULTS)` |
| `normalize_css.normalize_filter_css(names)` | `names` plus `longhands(name)` for each |
| `normalize_css.condense_rule(style)` | `condense_shorthands(block)` |
