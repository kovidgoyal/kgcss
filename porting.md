# Porting calibre to kgcss

This is a guide for a coding agent that ports calibre from css_parser, tinycss
and css_selectors to kgcss. It is written for work in the **calibre**
repository. kgcss itself is complete for this purpose: every capability in
`requirements.md` exists, and the decisions about quirks have been made
(§9 below). If you find something kgcss cannot do, stop and report it rather
than working around it in calibre.

File and line references to calibre (`stylizer.py:213`) come from
`requirements.md` and point at calibre commit `01ad1f246bcd`. Lines will have
moved; search for the code they describe. Paths are relative to
`src/calibre/` unless they start with another top-level directory.

Read these kgcss documents before starting a phase. They explain the
behaviour this guide only summarises:

| Topic | kgcss document |
|---|---|
| Tokens, positions, decoding bytes | `docs/plan-01-tokenizer-syntax.md` |
| The object model, serialization, URLs, imports, logging | `docs/plan-02-cssom.md` |
| Selectors, specificity, `Select` | `docs/plan-03-selectors.md` |
| Media queries and colors | `docs/plan-04-media-colors.md` |
| Fonts, value grammar, shorthands | `docs/plan-05-fonts-grammar.md` |

Each plan ends with an appendix that maps old names to kgcss names. §3 here
collects them.

---

## 1. Ground rules

- **One subsystem at a time.** Port, run that subsystem's tests, commit.
  css_parser and kgcss objects must never meet: a sheet parsed by one cannot
  be passed to code written for the other. Convert at subsystem boundaries
  with text (`serialize(sheet)` / `parse_stylesheet(text)`) while the port
  is in progress. §8 gives an order that keeps the boundaries few.
- **Do not reimplement what kgcss provides.** calibre has helpers that
  exist only because the old libraries lacked something: `css_text()`
  (bytes or str), `force_unicode` on CSS output, the regex rename of
  classes, the recursive `iterrules`, the private `seq`/`_tempSeq` access,
  the `normalize_css` normalizers, `rename_ids_in_selector`. Replace them
  with the kgcss API, then delete them.
- **Keep calibre's tests passing**, except where §6 lists an intended change
  of behaviour. When a test changes, the commit message says which item of
  §6 or §9 it follows.
- **Typing.** calibre's typed modules stay typed. kgcss is fully typed and
  ships `py.typed`.
- **No global state.** kgcss has no module-level settings, loggers or
  serializer preferences. Every option is passed per call (§4.6). Remove the
  global configuration code rather than porting it.
- **Threads.** kgcss works with and without the GIL. Nothing in this port
  needs locks on calibre's side.

---

## 2. Setting up

- Add `kgcss` as a dependency (Python 3.14 or later; it has a C extension).
  It replaces three things:
  - **css_parser**: remove it from calibre's requirements and the bypy
    build recipes;
  - **tinycss**: the vendored `src/tinycss`, and the C extension
    `calibre_extensions.tokenizer` (`setup/extensions.json:58-62`, required
    in `constants.py:299`; `test_build.py:497-500` checks that it loads);
  - **css_selectors**: `src/css_selectors`. Recipes and third-party plugins
    import `css_selectors.Select`, so keep a shim (§7.2).
- Remove these only at the end (§8, phase 7). Until then both stacks are
  installed side by side.
- Find every user with:

  ```sh
  git grep -nE 'css_parser|cssutils|tinycss|css_selectors|calibre_extensions\.tokenizer|parse_css4_color_string|cssprofiles|getCssText|cssText|cssRules|selectorText|propertyValue'
  ```

  `requirements.md` §1 to §15 list the call sites by requirement.

---

## 3. Name mapping

### 3.1 css_parser to kgcss

| css_parser | kgcss |
|---|---|
| `css_parser.parseString(text)`, `CSSParser(...).parseString` | `parse_stylesheet(text, href=..., log=..., fetcher=...)` |
| `parseStyle(text)` | `parse_style(text)` |
| `CSSPageRule().cssText = '@page {...}'`, parsing a one-rule sheet | `parse_rule('@page {...}')` |
| `CSSComment('/* x */')` | `parse_rule('/* x */')`, or `Comment(' x ')`: the constructor takes the text **without** `/*` and `*/` |
| `Property(name, value)` that raised `xml.dom.SyntaxErr` | `Property(name, value, validate=True)`, which raises `InvalidValueError` (a `ValueError`) |
| `sheet.cssRules`, `rule.cssRules` | `sheet.rules`, `rule.rules` (also `for rule in sheet`) |
| `cssRules.rulesOfType(CSSRule.STYLE_RULE)` | `sheet.rules.of_type(RuleType.STYLE)` (top level), or `sheet.iter_rules(RuleType.STYLE)` (all levels) |
| `CSSRule.STYLE_RULE`, `rule.type`, `'STYLE_RULE'` by name | `RuleType.STYLE`, `rule.type`, `RuleType['STYLE']` |
| `sheet.insertRule(r, i)`, `sheet.add(r)` | `sheet.insert_rule(r, i)`, `sheet.add(r)`: both return the inserted rule, a **copy** if `r` belonged to another sheet |
| `sheet.deleteRule(i_or_rule)` | `sheet.delete_rule(i_or_rule)` |
| `rule.parentStyleSheet`, `rule.parentRule` | `rule.parent_style_sheet`, `rule.parent_rule` |
| `rule.cssText`, `sheet.cssText` (bytes for sheets) | `rule.css_text`, `sheet.css_text` (always `str`); `sheet.encode()` for bytes |
| `rule.atkeyword` | `rule.at_keyword` (`'@foo'`), and `rule.name` (`'foo'`) on unknown rules |
| `rule.selectorText`, `rule.selectorList` | `rule.selector_text`, `rule.selectors` |
| `selector.specificity` (4-tuple) | `selector.specificity`, a **3-tuple** (§6.2) |
| `rule.media.mediaText` | `rule.media.media_text`, plus `rule.media.queries` |
| `rule.href` (`@import`) | `rule.href`, read and write |
| `style.getPropertyValue(n)`, `style[n]` | `style.get_value(n)` (`''` if absent); `style[n]` raises `KeyError` if absent |
| `style.getProperty(n)` | `style.get_property(n)` (`None` if absent) |
| `style.getPropertyCSSValue(n)` | `style.get_parsed_value(n)` |
| `style.setProperty(n, v, p)`, `style.setProperty(prop)` | `style.set(n, v, p)`, `style.set_property(prop)` |
| `style.removeProperty(n)` | `style.remove(n)`, returns the old value or `''` |
| `style.getProperties()` | `for prop in style` (the effective view) |
| `style.getProperties(all=True)`, `style.seq`, `_tempSeq`, `_setSeq` | `style.items` (every declaration and comment), `style.remove_declaration(p)`, `style.replace_declaration(p, [...])` |
| `style.length`, `style.item(i)` | `len(style)`, `style.keys()[i]` |
| `style.cssText` | `style.css_text` (multi-line) |
| `style.getCssText(separator=' ')` | `style.to_css(single_line=True)`, safe inside a double-quoted HTML attribute |
| `prop.name`, `prop.value`, `prop.priority`, `prop.literalpriority` | the same, `prop.literal_priority` |
| `prop.propertyValue` | `prop.parsed_value` |
| `value.cssText` | `value.css_text` |
| `component.uri` | `component.url` (also `component.value`) |
| `component.dimension` | `component.unit` |
| `component.type` constants | `component.type` strings (`'ident'`, `'dimension'`, ...) and classes (`Ident`, `Dimension`, ...) in `kgcss.cssom.values` |
| substring removal of a component (`remove_property_value`) | `prop.remove_component(i)` |
| `css_parser.getUrls(sheet)` | `iter_urls(sheet)` |
| `css_parser.replaceUrls(sheet, fn, ignoreImportRules=...)` | `replace_urls(sheet_or_rule_or_block, fn, ignore_imports=...)`, returns whether anything changed |
| re-serializing to rewrite URLs in text | `replace_urls_in_text(css, fn, style_attribute=...)` |
| `css_parser.resolveImports(sheet)` | `resolve_imports(sheet)` |
| `ser.prefs.*` (global) | `SerializeOptions(indent=..., ...)` passed to `serialize()` or `to_css()` |
| `css_parser.log`, `loglevel=` | `log=` per call (a `logging.Logger` or a callable), and `sheet.errors` |
| `profile.validate(name, value)` | `matches(name, value)` (§6.8) |
| `cssproperties._toDOMname`, `style.marginLeft` | not provided: use property names (`page_margin.py:98`, `stylizer.py:598` keep their own small mapping) |

### 3.2 css_selectors to kgcss

| css_selectors | kgcss |
|---|---|
| `Select(root, ignore_inappropriate_pseudo_classes=...)` | the same, plus `quirks_mode=True` to keep today's case-insensitive ids and classes (§6.3) |
| `select(text, root=None)`, `select.select(...)`, `select.has_matches(...)` | the same; they also accept `Selector` and `SelectorList` objects |
| `Select(..., default_lang=, dispatch_map=, trace=)`, `invalidate_caches()` | removed: make a new `Select` after changing the tree |
| `parse(text)`, `get_parsed_selector(text)` | `parse_selectors(text)`, cached |
| `selector.specificity()` | `selector.specificity` (a property, 3-tuple) |
| `selector.pseudo_element` (a name) | `selector.pseudo_element`, a `PseudoElement` with `.name`, or `None` |
| `selector.parsed_tree` | `selector.parsed`, with `.walk()` |
| walking the tree for class names (`classes_in_selector`) | `selector.class_names()`; `selector.ids()`, `pseudo_classes()`, `pseudo_elements()` |
| regexes built from `INAPPROPRIATE_PSEUDO_CLASSES` | `selector.has_pseudo(INAPPROPRIATE_PSEUDO_CLASSES)` |
| `SelectorError`, `SelectorSyntaxError`, `ExpressionError`, `INAPPROPRIATE_PSEUDO_CLASSES` | the same names, exported from `kgcss` |

### 3.3 tinycss to kgcss

| tinycss | kgcss |
|---|---|
| `tokenize_flat`, `tokenize_grouped` | `kgcss.tokenizer.tokenize(text)`, `kgcss.syntax.parse_component_value_list(text)` |
| `token.as_css()` | `token.source_text` |
| `token.line`, `token.column`, `token.pos` | the same names; `token.end` is the end offset |
| `CSS21Parser().parse_stylesheet(text)` with positions | `parse_stylesheet(text)`: every rule has `line` and `column` (1-based), nested rules too |
| `parse_style_attr(text)` | `parse_style(text)` |
| `decoding.decode(...)` | `decode_stylesheet(data)`, or pass bytes to `parse_stylesheet` |
| `color4.parse_color_string(s)` | `parse_rgba(s)` (§6.6) |
| `color3.RGBA` | `RGBA` |
| `media3` `MediaQuery(media_type, expressions, negated)` | `MediaQuery` (`media_type`, `negated`, `only`, `features()`, `condition`, `valid`) |
| `CSSMedia3Parser().parse_stylesheet('@media X {}').rules[0].media` | `parse_media_queries(X)` |
| `fonts3.parse_font_family`, `serialize_font_family`, `parse_font`, `serialize_font` | the same names, from `kgcss` (§6.7) |
| `page3` page selector specificity | not provided: `@page` selectors are kept as text (`PageRule.selector_text`). Nothing in calibre outside tinycss's own tests uses page specificity |

### 3.4 calibre helpers that kgcss replaces

| calibre | kgcss |
|---|---|
| `stylizer.validate_color(v)` | `is_color(v)` (§6.8) |
| `stylizer.media_ok(raw)` | `media_matches(raw, media_type=..., feature=...)` (§5.2) |
| `normalize_css.normalizers[name](name, value)` | `expand_shorthand(name, value, initial=DEFAULTS, quirks_mode=True)` |
| `normalize_css.normalize_filter_css(names)` | `names` plus `longhands(name)` for each |
| `normalize_css.condense_rule(style)`, `condense_sheet(sheet)` | `condense_shorthands(block)`, for each block |
| `cascade.iterrules`, `cascade.iterdeclaration`, the walkers in `polish/css.py` | `sheet.iter_rules(*types, follow_imports=...)`, `sheet.iter_declaration_blocks(follow_imports=...)` |
| `polish/css.py` `rename_ids_in_selector`, the regex class rename (`css.py:611-631`) | `rename_ids(text, mapping)`, `rename_classes(text, mapping)`, or `rule.selectors.rename_ids(mapping)` |
| `polish/css.py` `classes_in_selector` | `selector.class_names()` |
| `oeb/base.py` `css_text(x)`, `force_unicode(sheet.cssText)` | `x.css_text` (already `str`) |
| `polish/utils.py` `setup_css_parser_serialization` | delete; pass `SerializeOptions` per call |
| `polish/utils.py` `parse_css(...)` | `parse_stylesheet(text, href=..., log=...)` with no fetcher (§5.1) |

---

## 4. The kgcss model in brief

### 4.1 Parsing

- `parse_stylesheet(source, *, href=None, log=None, fetcher=None,
  eager_imports=False, resolve_imports=False)`. `source` is `str` or bytes;
  bytes are decoded with BOM, protocol encoding and `@charset` detection
  (P1). It never raises on bad CSS (P5): problems go to `log` and
  `sheet.errors`, each with `line`, `column`, `offset` and `message`.
- `parse_style(text)` parses a `style=""` attribute (P2). `parse_rule(text)`
  parses exactly one rule (P4). `parse_value(text, property_name=...)`
  parses a value (P3).
- Blocks are parsed lazily, on first access, so parsing a large sheet and
  touching only some rules is cheap.

### 4.2 Rules

`RuleType` members and classes: `STYLE` (`StyleRule`), `MEDIA`
(`MediaRule`), `SUPPORTS` (`SupportsRule`), `IMPORT` (`ImportRule`),
`NAMESPACE` (`NamespaceRule`), `CHARSET` (`CharsetRule`), `FONT_FACE`
(`FontFaceRule`), `PAGE` (`PageRule`), `MARGIN` (`MarginRule`, the
`@top-left` boxes, in `page_rule.rules`), `COMMENT` (`Comment`), `UNKNOWN`
(`UnknownAtRule`, kept verbatim), and the grouping rules `ContainerRule`,
`LayerBlockRule`, `LayerStatementRule`, `ScopeRule`, `StartingStyleRule`,
`DocumentRule`. CSS nesting is supported: style rules have `rules`, and
`NestedDeclarationsRule` holds declarations that come after nested rules.

**This changes iteration.** css_parser kept `@supports` and other unknown
rules as opaque objects, and tinycss dropped them. In kgcss, `@supports`,
`@container` and `@layer` blocks contain real rules. Code that loops over
`sheet.rules` and only looks at style rules at the top level misses them, as
it already misses `@media` (requirements 3.4). Use `iter_rules()`.

### 4.3 Selectors

`StyleRule.selectors` is a mutable `SelectorList` of `Selector` objects.
They have `text`, `specificity`, `parsed`, `pseudo_element`, the queries of
§3.2, and accept arbitrary attributes (`selector.calibre_used`). The same
objects are passed to `Select`; there is no need to serialize and re-parse
(9.1). Invalid selectors in a parsed sheet are kept (the rule stays) and
raise `SelectorSyntaxError` when matched or parsed; `invalid_selectors(sheet)`
lists them.

### 4.4 Declarations and values

`DeclarationBlock` has an effective view (iteration, `keys()`, `get_value`,
`get_property`; `!important` beats later normal declarations, otherwise the
last wins) and a full view, `items`, with every declaration and comment in
order. `Property` objects are hashable by identity. `PropertyValue` is an
immutable sequence of components without separators; `.items` includes them.
Assigning `prop.value` reparses.

### 4.5 URLs

`iter_urls` and `replace_urls` cover `@import`, every declaration block
including `@page`, margin rules and `@font-face`, grouping rules, unknown
at-rules (at token level), and `url()` inside functions at any depth
(`image-set()`, `cross-fade()`), including `data:` URLs (U1-U3).
`replace_urls` returns `True` only if something changed, which replaces
`replace_func.replaced` (`container.py:557-559`).

### 4.6 Serialization

`serialize(obj, SerializeOptions(...))` and `obj.to_css(options)`:

| Option | Default | Use |
|---|---|---|
| `style` | `'pretty'` | `'preserve'` keeps the original text of everything not changed since parsing: smallest diffs, for Edit book saves where the user did not ask for reformatting |
| `indent` | two spaces | the editor's tab width (`boss.py:134-135,184,286`, `diff/view.py:102`) |
| `normalize` | `True` | the S3 normalisations: numbers, `#ff0000` to `#f00`, `rgb()` spacing, quotes |
| `drop_empty_rules` | `True` | Kobo relies on this (§6.10) |
| `keep_comments` | `True` | |

The pretty format is what `setup_css_parser_serialization` configured (S1).
Output is always `str`; `sheet.encode()` gives bytes in the `@charset`
encoding, escaping what it cannot represent (S4).

---

## 5. Imports, logging and media

### 5.1 @import

- **Conversion** (`oeb/base.py:1251,1259-1273`, `stylizer.py:318,332-334,466-480`,
  `html_input.py:366`): pass `href=` and `fetcher=`. The fetcher gets the URL
  already resolved against `href` and returns `(encoding, text)`, where text
  is `str` or bytes, or `None` for a missing sheet (the rule is kept and a
  warning logged), or `(encoding, '')` for an empty one. Recursion is
  guarded. With `resolve_imports=False` (the default) imports load lazily on
  `rule.style_sheet`; `eager_imports=True` loads them while parsing.
- **Inlining** (`oeb/base.py:1254`, P11): `resolve_imports(sheet)` returns a
  new sheet with imports inlined, wrapped in `@media` where the import had
  media, with `/* START @import ... */` comments. The two-phase flow of
  `html_input.py:236-238` and `base.py:1327` works as before: parse without
  resolving, rewrite hrefs, serialize, reparse and resolve.
- **Polish** (`polish/utils.py`): today its fetcher returns `(None, None)` to
  never resolve. In kgcss, `(None, None)` means "not found" and logs a
  warning. Pass **no fetcher** instead: imports are then never loaded, and
  polish keeps following `rule.href` itself (`cascade.py:131`,
  `jump_to_class.py:83-85`).

### 5.2 Logging (L1-L3)

- Conversion: `parse_stylesheet(text, log=logging.getLogger('calibre.css'))`,
  which keeps today's forwarding to the OEB log (`oeb/base.py:689-705`,
  wired at 2100). Messages carry `href:line:column`.
- Remove every `loglevel=CRITICAL` parser and every write to `css_parser.log`
  (`embed_fonts.py:115`, `stylizer.py:45`, `plumber.py:1242`,
  `html_input.py:151`, `mobi/writer8/main.py:98`, `oeb/base.py:388-389,1246-1247`).
  For silence, pass no `log`: errors are still collected in `sheet.errors`.
- Polish and Edit book can show `sheet.errors` (L3).

### 5.3 Media (§10)

`media_ok` (`stylizer.py:115-152`) becomes:

```python
ALLOWED_MEDIA_TYPES = frozenset({'screen', 'all', 'aural', 'amzn-kf8'})

def media_ok(raw: str | None) -> bool:
    if not raw:
        return True
    if raw == 'amzn-mobi':
        return False
    return media_matches(
        raw,
        media_type=ALLOWED_MEDIA_TYPES.__contains__,
        feature=lambda f: False if f.name in IGNORED_MEDIA_FEATURES else None,
    )
```

The feature callback returns `True`, `False` or `None` for unknown; kgcss
does the Level 4 logic (`or`, `not`, nesting, unknown). This exact function
is tested in kgcss (`tests/test_media.py`), with calibre's `test_media_ok`.
`cascade.media_allowed` (`cascade.py:82-85`) can call
`rule.media.matches(...)` directly. Media types are now case-insensitive
(M3 quirk fixed), and `MediaList` is falsy when empty (M4).

---

## 6. Behaviour that changes

Each item says what changes, why, and what to do in calibre. Tests that pin
the old behaviour must be updated, citing the item.

### 6.1 Text, not bytes (S4)

Sheet `css_text` is `str`. Remove `css_text()`/`force_unicode` round trips
(`oeb/base.py:230`, `css.py:239,376,653`). Where bytes are written to disk,
use `sheet.encode()`. kepubify's assignment of bytes to `style.text`
(`kepubify.py:364,383,393`) becomes a `str` assignment.

### 6.2 Specificity is a 3-tuple (3.3, D3 of plan 03)

`(ids, classes, types)`, as Selectors Level 4 defines it, with `:is`,
`:not`, `:has`, `:where` and `of S` handled properly and legacy
pseudo-elements counted as types. css_parser gave
`(inline, ids, classes, types)`. Where calibre sorts by specificity with
inline styles in the same order (`stylizer.py:213`, `cascade.py:164-166`,
`css.py:571`), build the 4-tuple yourself: `(0, *selector.specificity)` for
rules and `(1, 0, 0, 0)` for `style=""`.

### 6.3 Selector matching (§9, D5 and D7 of plan 03)

- **Ids and classes are case-sensitive by default**, as in standards mode.
  css_selectors matched them case-insensitively. To keep today's results,
  pass `quirks_mode=True` to every `Select` (`stylizer.py:397-441`,
  `split.py:140-161`, `polish/css.py:43-62,213`, `cascade.py:242-266`,
  `report.py:425-460`, `html_transform_rules.py:435-458`,
  `jump_to_class.py:37-43,112`). Decide with the maintainer whether
  conversion should move to standards mode later; do not change it as part
  of the port.
- `:lang()` honours `xml:lang` (quirk fixed).
- `[a!=b]` is a syntax error (it used to parse, then crash when matched).
- Errors are raised when the generator is first advanced, so `next()`,
  `StopIteration` and `elem in select(...)` keep working
  (`jump_to_class.py:42`).
- Do not change the tree while a `Select` is in use; make a new one.

### 6.4 Selector text (3.3)

Escapes are preserved (`#\31` stays `#\31`, which css_parser turned into the
invalid `#1`); whitespace is normalised (`a , b>c` becomes `a, b > c`);
case and comments are kept. A selector with an undeclared namespace prefix
keeps its rule (P9 quirk fixed). Tests that expected decoded escapes change.

### 6.5 Parsing and serialization (P6, P15, P16, S3)

- Custom properties and `var()` are kept (css_parser dropped them).
- Unknown at-rules are kept verbatim, `url()` and quotes included.
- IE hacks such as `*zoom: 1` are dropped and logged, as today.
  `filter: progid:...` is a valid declaration and kept.
- The S3 normalisations are kept and deterministic, so most expected
  strings in calibre's tests should not change. If one does, check it
  against `docs/plan-02-cssom.md` §6 before changing the test.
- Invalid media text is kept as written (M2 quirk fixed).

### 6.6 Colors (§12, plan 04)

`parse_rgba(text)` returns `RGBA` (a tuple subclass with `red`, `green`,
`blue`, `alpha`, each 0 to 1), the string `'currentColor'`, or `None`.
Callers keep their `isinstance(val, tuple)` checks. Differences from
tinycss: CSS escapes are decoded (`r\65 d` is red) and non-ASCII text in
comments is accepted (D2 of plan 04). Update the calibre test that expects
`None` for `r\65 d`. Callers: `docx/writer/utils.py:22-60`,
`mobi/utils.py:647-675`, `gui2/tweak_book/editor/syntax/css.py:195-213`.
`parse_color(text)` gives the specified color (`Color`) if a caller ever
needs the color space or out-of-gamut values.

### 6.7 Fonts (§11, plan 05)

Same functions, same results, faster, with these differences:
- `serialize_font_family` no longer quotes names that start with `and` (a
  css_parser workaround), and does quote the CSS-wide keywords and
  `default`;
- keywords in `parse_font` are case-insensitive (`BOLD` was a family);
- `serialize_font` quotes family names that are font keywords (`"bold"`).

Callers: `cascade.py:206`, `polish/fonts.py:34,47,74,78,84,90,119`,
`stats.py:257`, `check/fonts.py:143`, `transforms/subset.py:82`,
`normalize_css.py:198-214`, `docx/writer/styles.py:26-54` (which can use
`parse_font_family` instead of reading tokens by hand).

### 6.8 Validation (§8)

- `validate_color` becomes `is_color`, which uses the CSS 4 color parser: it
  accepts `#abcd` and `lab()`, and **rejects `inherit`**, which css_parser's
  CSS 2 profile accepted. In `stylizer.py:631,648,665` check the CSS-wide
  keywords explicitly where `inherit` must pass.
- `matches(name, value)` replaces `profile.validate` for the 30 longhands of
  margin, padding, border, list-style and font. It returns `None` for any
  other property. It follows current specs, so it differs from css_parser on
  20 listed values (`tests/test_grammar.py`, `DIFFERENCES`): `calc()`, CSS 4
  colors, counter-style names, gradients, Fonts 4 weights; negative widths
  and padding are rejected.
- Drop `stylizer.py:315-316` (custom profiles, G3) and `validate=True`
  parsing (G4).

### 6.9 Shorthands (`normalize_css.py`, plan 05 milestone 3)

Replace the normalizers and condensers with `expand_shorthand`,
`longhands` and `condense_shorthands`. Port `normalize_css.py`'s tests;
kgcss already runs them (`tests/test_shorthands.py`), and these results
differ:
- pass `quirks_mode=True`: `margin: 1 2 3 4` (unitless) expands only then;
- `border-width: 10%` is not expanded: a percentage is not a border width;
- `font` also sets `font-stretch`, which the shorthand resets in CSS;
- invalid values give `None` (`border: solid dotted`, five margin values)
  instead of a partial expansion: leave the declaration as it is;
- condensation skips groups with `!important` (as before), groups that an
  overlapping shorthand in the block affects, and groups where one value is
  invalid for its longhand.

Pass calibre's `DEFAULTS` as `initial=` to keep its initial values
(`currentColor` and so on); kgcss's own `INITIAL_VALUES` follow the specs.

### 6.10 Empty rules (D6 of plan 02)

Empty rules are dropped on output by default, as css_parser did. Kobo's
`@page` blanking (`kobo/driver.py:2913`) relies on it: `rule.style = ''`
works unchanged.

### 6.11 Inserting rules from another sheet (3.1)

`insert_rule()` and `add()` insert a **copy** when the rule belongs to
another sheet, and return it. Kobo inserts the same `extra_sheet` rules into
many sheets (`kobo/driver.py:2931`): that now works without reparsing, but
code that keeps a reference to the inserted rule must use the returned one.

---

## 7. Compatibility surface (§15)

### 7.1 Edit book plugins and documented APIs

`Container.parsed()`, `Container.parse_css()` and `Container.replace()` are
documented as returning or accepting css_parser sheets
(`polish/container.py:78,763-783`), and `manual/plugin_examples/editor_demo`
uses `rulesOfType`, `getPropertyValue`, `setProperty`,
`getCssText(separator=' ')` and `serialize(sheet, 'text/css',
pretty_print=True)`.

kgcss has a facade for this: **`kgcss.compat`**, live css_parser-style views
of kgcss objects (`CSSStyleSheet`, `CSSRule` and its constants,
`CSSStyleDeclaration`, `Property`, `PropertyValue`, `parseString`,
`parseStyle`, `serialize`, `getUrls`, `replaceUrls`, ...). calibre keeps
kgcss objects internally and gives plugins views:

- `Container.parsed(name)` for a stylesheet returns
  `kgcss.compat.wrap(sheet)` to plugins, or offers both (for example a
  `parsed_kgcss()` for calibre's own code). A view changes the sheet it
  wraps, so `container.dirty(name)` works as before.
- `Container.parse_css(text, is_declaration=...)` returns
  `kgcss.compat.parseString(text)` or `parseStyle(text)`.
- Code that receives a view from a plugin calls `kgcss.compat.unwrap(view)`.
- The editor demo plugin runs unchanged on the facade (kgcss tests it in
  `tests/test_compat.py`). The facade's `serialize()` returns bytes, as
  calibre's `serialize()` does for CSS.

Decide with the maintainer which calibre functions return views and which
return kgcss objects, and document it in the manual's plugin section
(`manual/polish.rst`).

### 7.2 `css_selectors` for recipes and plugins

Keep `src/css_selectors/__init__.py` as a shim, so `from css_selectors import
Select` keeps working in recipes (`recipes/amspec.recipe`,
`recipes/esquire.recipe`) and third-party code:

```python
from kgcss import INAPPROPRIATE_PSEUDO_CLASSES, ExpressionError, SelectorError, SelectorSyntaxError
from kgcss import Select as _Select
from kgcss import parse_selectors as parse

class Select(_Select):
    def __init__(self, root, ignore_inappropriate_pseudo_classes=False, **kw):
        # css_selectors matched ids and classes case-insensitively
        super().__init__(root, ignore_inappropriate_pseudo_classes, quirks_mode=True)
```

`default_lang`, `dispatch_map` and `trace` are accepted and ignored by
swallowing `**kw`; say so in the docstring.

### 7.3 Duck typing on sheets

Code that detects sheets by a `cssText` or `cssRules` attribute
(`oeb/base.py:623,1365`, `filenames.py:40`, `lit/writer.py:520`,
`docx/writer/fonts.py:52`, `subset.py:254`) becomes
`isinstance(x, Stylesheet)`. `OEBBook` manifest items hold kgcss sheets in
`item.data`; output plugins that read them must be ported in the same phase
as `oeb/base.py`.

---

## 8. Phases

Port in this order. Each phase ends with its tests passing and a commit.

1. **Leaf helpers with no sheet objects.** Colors (`docx/writer/utils.py`,
   `mobi/utils.py`, the editor highlighter), fonts helpers, media
   (`stylizer.media_ok`), `validate_color`. These take and return strings,
   so nothing else changes.
2. **Selectors.** Every `Select` caller (§6.3, with `quirks_mode=True`), the
   `css_selectors` shim (§7.2), `live_css.py:560` (specificity),
   `html_transform_rules.py:140-144` (the cached parse as a validator).
   While sheets are still css_parser sheets, pass `rule.selectorText`; phase
   3 switches to the `Selector` objects.
3. **Conversion pipeline.** `oeb/base.py` (parsing, `serialize()`,
   `css_text()`, `rewrite_links` with `replace_urls_in_text` for U4),
   `oeb/stylizer.py`, `oeb/normalize_css.py` (§6.9), `flatcss.py`,
   `transforms/split.py`, `transforms/subset.py`, `transforms/trimmanifest.py`,
   `embed_fonts.py`, `html_input.py`, `reader.py`, `page_margin.py`,
   `epub_output.py`, `pdf_output.py`, the output plugins that read
   `item.data` (`mobi/writer8`, `lit/writer.py`, `docx`, `htmlz`, `odt`,
   `fb2_input.py` with its `namespaces['h'] = XHTML_NS`), and
   `srv/render_book.py:553`. Port `oeb/base.py` and the readers of
   `item.data` together.
4. **Polish.** `polish/utils.py` (no fetcher, §5.1; delete
   `setup_css_parser_serialization`), `container.py`, `css.py` (use
   `iter_rules`, `rename_classes`, `rename_ids`, `class_names()`), `cascade.py`
   (delete `iterrules`/`iterdeclaration` in favour of kgcss), `fonts.py`,
   `kepubify.py`, `replace.py` (`remove_url_values` for U5), `report.py`,
   `check/fonts.py`, `stats.py`, `subset.py`, `pretty.py`,
   `css_transform_rules.py` (`style.items`, `replace_declaration`),
   `html_transform_rules.py`.
5. **Edit book.** `jump_to_class.py` (`sheet.browser_index_path(rule)` to
   build the path, P13), `editor/smarts/css.py` and `editor/text.py:1306-1335`
   (`sheet.rule_at_browser_index_path(path).line` to resolve a path from live
   CSS to a line), `editor/smarts/html.py:335-355` and `replace.py:412`
   (`to_css(single_line=True)` for `style=""`), `boss.py` and
   `diff/view.py` (indent per call), `editor/widget.py:690-699`, sort CSS
   (`editor/text.py:546-569`), the Reports tab (`report.py:345-377`, rule
   lines plus `style.sourceline - 1`). Settle §7.1 first.
6. **Devices and the rest.** `kobo/driver.py` (§6.10, §6.11),
   `metadata/sources/amazon.py`, the store plugins, recipes.
7. **Removal.** Delete `src/tinycss`, the `calibre_extensions.tokenizer`
   extension and its check in `test_build.py`, `src/css_selectors` except
   the shim, css_parser from the dependencies, and the old test
   registrations in `utils/run_tests.py:145-150,758-772` and
   `test_build.py:615`. The `git grep` of §2 should then only find the shim.

---

## 9. Quirks: what kgcss already fixed, and what is left for calibre

Fixed in kgcss, so calibre just has to stop working around them (§18 of the
requirements):

| Quirk | Now |
|---|---|
| `--x` and `var()` declarations dropped | kept |
| Unknown at-rules corrupted or dropped | kept verbatim; `@supports` and the like are grouping rules |
| Selector escapes decoded | preserved |
| URL helpers missing `@page`, `@supports`, nested functions | covered |
| Jump-to-class index mismatch | one parser, `browser_index_path()` and `rule_at_browser_index_path()` |
| Editor tab width ignored | `SerializeOptions(indent=...)` per call |
| Sheet `cssText` bytes | always `str`, `encode()` |
| Empty rules dropped (Kobo) | kept as the default, `drop_empty_rules` |
| S3 normalisations | kept, deterministic, `normalize=False` to switch off |
| `!=` crashes | syntax error |
| `:lang()` ignores `xml:lang` | honoured |
| Media type case | case-insensitive |
| Invalid media text mangled | kept as written |
| `style=""` broken by quotes or newlines | `to_css(single_line=True)` |

Left for calibre to fix while porting (they are bugs in calibre's code):
- `stats.get_css_text` expects a quoted STRING `.value`, but values are
  unquoted, so `::before`/`::after` content is never counted for font
  subsetting. With kgcss, `String` components have the unquoted text in
  `.value`: count it.
- `fonts.change_font` never writes modified `<style>` sheets back, and
  `remove_embedded_font` parses `src` with `src[4:-1]`. Write the sheets
  back through the container, and read `src` with `iter_urls` or the parsed
  value's `Url` components.
- Polish operations that only look at top-level rules (remove unused CSS,
  merge, `filter_sheet`, `transform_sheet`, font changes, `check/fonts`,
  subset, kepubify) miss rules inside `@media` and the new grouping rules.
  Use `iter_rules()` and `iter_declaration_blocks()`.
- `P14`: `CSSPreProcessor` (`conversion/preprocess.py:322-362`) stays a
  regex pre-pass on text.

---

## 10. Tests

- **calibre tests to keep passing**, with changes only as §6 allows:
  `polish/tests/cascade.py`, `polish/tests/structure.py:616-700` (including
  `rename_ids_in_selector`, now `rename_ids`), `polish/tests/kepubify.py` (the
  byte-identical round trip, S2), `polish/tests/parsing.py`, the in-module
  tests of `oeb/normalize_css.py`, `ebooks/css_transform_rules.py`,
  `docx/writer/utils.py:34-60`, `mobi/utils.py:655-675`,
  `oeb/stylizer.py:142-152`, `html_transform_rules.py:620`.
- **Tests to delete with the old libraries**: `src/tinycss/tests/*` and
  `src/css_selectors/tests.py`. kgcss covers what they test: tokenizing,
  parsing and decoding through its own tests and the CSS parsing
  conformance suite; the color4, fonts3 and media3 cases, and
  css_selectors' parser, specificity, select, level 4 and Shakespeare
  tests, ported directly, with the differences listed in its tests. The
  exceptions are tinycss's color3 quirks and page3 specificity, which
  nothing in calibre uses (§17 of the requirements).
- Several of calibre's in-module tests run unchanged against kgcss already,
  as ports in kgcss's suite: `test_media_ok` (`tests/test_media.py`),
  `convert_color` and `convert_color_for_font_tag` (`tests/test_colors.py`),
  `normalize_css`'s tests (`tests/test_shorthands.py`, `tests/test_fonts.py`)
  and `rename_ids_in_selector` (`tests/test_selectors.py`). If one of them
  fails after porting, the difference is in the calibre glue code, not in
  kgcss.
- **Performance.** In the plans' measurements kgcss is faster than what it
  replaces, usually by a wide margin, and never slower by more than noise
  (the plans' notes have the numbers). If a calibre operation gets
  slower after the port, look for reparsing or re-serializing that the old
  code needed and kgcss does not: kgcss objects can be kept and mutated
  across the pipeline (`flatcss.py:136`, `mobi/writer8/main.py:103-108`,
  `embed_fonts.py:223` reparse sheets only to copy them; use
  `sheet.copy()`).
