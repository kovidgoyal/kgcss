# What calibre needs from a replacement for css_parser, tinycss and css_selectors

This document surveys how calibre currently uses three CSS libraries and lists what a single replacement library must do.

## The three libraries

| Library | What it is | Where calibre uses it |
|---|---|---|
| **css_parser** | External package, a fork of cssutils. Provides a full CSSOM: parsing, a mutable object model, serialization, URL helpers and property validation profiles. | About 45 files. This is the in-memory representation of every stylesheet in conversion (`OEBBook` manifest items) and in Edit book (`Container.parsed()`). |
| **tinycss** | Vendored and modified in `src/tinycss`. Includes the C extension `calibre_extensions.tokenizer` (`setup/extensions.json:58-62`, required in `src/calibre/constants.py:299`). | Fast CSS4 color parsing, font-family and font shorthand helpers, media query parsing, and parsing with line/column positions (Reports tab, jump to rule). |
| **css_selectors** | calibre's own selector engine for lxml trees, in `src/css_selectors`. | Selector parsing, specificity and tokenizing, and matching against XHTML/HTML lxml trees. |

The new library does not need to reproduce these APIs exactly. It must provide every *capability* listed below. File and line references point to the call sites. Paths are relative to `src/calibre/` unless they start with another top-level directory.

Items marked **[quirk]** are current behaviours that calibre code either depends on or suffers from. Each one needs a decision: keep it, or fix it and update the callers. Section 18 collects them.

---

## 1. Tokenizer

| # | Requirement |
|---|---|
| T1 | A CSS Syntax Level 3 tokenizer for idents, at-keywords, hash, string, bad-string, url, bad-url, number/percentage/dimension (value plus unit), unicode-range, delimiters, whitespace, comments, CDO/CDC and the bracket and function tokens. |
| T2 | Each token records its **line and column**, or its source offset, in the original text. |
| T3 | Token values are **unescaped**: strings without quotes, hashes without `#`, escapes resolved (e.g. `#\5f x` gives `_x`). The exact source text of the token must also be recoverable, so a token or a block can be written back unchanged (tinycss `as_css()` round trip; tests in `src/tinycss/tests/tokenizing.py`). |
| T4 | Optionally keep comments (tinycss `ignore_comments`). |
| T5 | Grouping into nested blocks (`{} () []`) and functions, as tinycss `tokenize_grouped`/`regroup` do. Unclosed brackets and strings at end of input are recovered per spec. |
| T6 | **Speed.** The tokenizer runs on every stylesheet during conversion and on every edit, so it should be in C or similarly fast. `test_build.py:497-500` currently checks that the C tokenizer is loaded. |
| T7 | A **selector tokenizer** that works on a selector string by itself, with unescaped values and source offsets, ending in an EOF token that carries `len(src)`. `polish/css.py:470-505` `rename_ids_in_selector` uses each token's `.type`, `.value`, `.pos`, `.is_delim()`, and the next token's `.pos` as the end of the current token, to rewrite `#id` and `[id=value]` in place while leaving everything else byte-identical. Tests: `polish/tests/structure.py:616-630,691-700`. T1 and T3 together can cover this if offsets are kept. Errors on unclosed strings are currently raised lazily, while iterating. |

## 2. Parsing stylesheets

| # | Requirement |
|---|---|
| P1 | Parse a full stylesheet from **str**. Today callers decode bytes themselves (`container.decode`, `polish/utils.py:338-376`) and pass str. A bytes entry point with BOM and `@charset` detection would be useful; tinycss `decoding.py` provides one, but nothing outside its tests uses it. |
| P2 | Parse a **declaration list** by itself: the contents of a `style=""` attribute (css_parser `parseStyle`, tinycss `parse_style_attr`). Used in `oeb/base.py:435`, `oeb/stylizer.py:578,654`, `normalize_css.py:272`, `ebooks/css_transform_rules.py:200,409`, `gui2/tweak_book/editor/smarts/html.py:338,353`, `docx/writer/styles.py:34`. |
| P3 | Parse a **single value** for a named property (`Property(name, value)` construction: `cascade.py:176,322`, `flatcss.py:464-471`, `css_transform_rules.py:57,106,114`). |
| P4 | Parse a **single rule** from text: `CSSPageRule().cssText = '@page {...}'` (`polish/kepubify.py:358`), `CSSComment('/* ... */')` (`kepubify.py:380`), and parsing a one-rule sheet to get the rule (`flatcss.py:292`, `embed_fonts.py:224,265`). |
| P5 | **Spec-compliant error recovery.** An invalid declaration is dropped and the rest of the block is kept (`color: red; }}} margin: 0` → `color: red`). An invalid rule is dropped. An `@import` after other rules is dropped. Parsing must essentially never raise on bad input. Callers mostly expect a sheet back, plus logged warnings. |
| P6 | **Unknown at-rules** (`@supports`, `@keyframes`, `@layer`, `@container`, vendor rules…) are kept as rule objects and written back faithfully, including `url()` and quoted strings inside them. **[quirk]** css_parser with `formatUnknownAtRules=False` (`polish/utils.py:125`) corrupts `url(c.png)` → `c.png` and `"a"` → `a`. tinycss drops these rules entirely. Ideally `@supports` and similar conditional rules are parsed as **grouping rules with nested rules**, so that URL rewriting, class renaming and so on reach inside them. |
| P7 | **Comments are kept as top-level rule objects**, in order (`CSSComment`). kepubify depends on this: it turns `@page` rules into comments and back, and the round trip must be byte-identical (`kepubify.py:338-383`, `polish/tests/kepubify.py:55-57,205-207`). Comments inside declaration blocks must also survive serialization (they are currently kept in `decl.seq`). |
| P8 | `@charset` is kept as a rule (`CHARSET_RULE`) and written back. `split.py:565-588` removes it when merging sheets. |
| P9 | `@namespace` rules, including default and prefixed forms. `resources/templates/html.css` uses `@namespace url(...)` and `@namespace svg url(...)`, and `svg\|svg` selectors must parse. The sheet must expose a namespaces mapping that can be **modified**: `fb2_input.py:90-97` sets `namespaces['h'] = XHTML_NS` and depends on the serializer then writing `h\|style`. **[quirk]** A selector with an undeclared prefix currently drops the whole rule. |
| P10 | **`@import`**: href, media list, and an optional **fetcher callback** with eager or lazy resolution. The fetcher receives the URL resolved against the sheet's href and returns `(encoding, text)`. `None` means missing (keep the rule); `''` means empty. Recursion must be protected. Fetchers: `oeb/base.py:1251,1259-1273`, `stylizer.py:318,332-334,466-480`, `html_input.py:366`. Polish never resolves imports (`polish/utils.py` fetcher returns `(None, None)`); it follows them itself through `rule.href` (`cascade.py:131`, `jump_to_class.py:83-85`). |
| P11 | **`resolveImports(sheet)`**: inline imported sheets, wrapping them in `@media` when the import had media and adding `/* START @import ... */` comments. Used by conversion when `item.resolve_css_imports` is set (`oeb/base.py:1254`). `html_input.py:236-238` and `base.py:1327` rely on a two-phase flow: parse without resolving, rewrite the hrefs, serialize, then reparse with resolution. |
| P12 | **Source positions for rules**: at least line and column of each rule start, including nested rules. Used by Reports > CSS (`polish/report.py:345-377`, with `<style>` offsets from `style.sourceline - 1`) and jump-to-rule (`gui2/tweak_book/editor/smarts/css.py:30-47`, `editor/text.py:1306-1335`). Positions for declarations would be nice but nothing uses them yet. |
| P13 | **Rule indexing consistent with the browser CSSOM.** Jump to class builds a rule-index path from css_parser `cssRules`, skipping COMMENT and CHARSET (`jump_to_class.py:61-80`), and live CSS builds one from the browser CSSOM (`src/pyj/live_css.pyj:182-208`). tinycss then resolves the path to a line (`smarts/css.py`). **[bug]** tinycss drops `@namespace`, `@supports` and other unknown rules, so the indices shift and the editor jumps to the wrong rule. With one library, every at-rule must keep its index slot the way the CSSOM does, and positions must come from the same parse. |
| P14 | **Pre-processing hook.** Outside tweak mode, `CSSPreProcessor` (`conversion/preprocess.py:322-362`) strips `mso-*` and similar junk with a regex before parsing. The library just needs to accept text; this can stay a pre-pass in calibre. |
| P15 | **Modern syntax must survive a round trip.** Custom properties (`--x: …`), `var()`, `calc()`, `env()`, nested functions like `image-set(url())`, `!important` in any case, and unknown `!priority` values (`!ie` is currently kept). **[bug]** css_parser currently drops `--x` declarations and every declaration that uses `var(--x)`. A replacement should keep them. |
| P16 | IE hacks (`*zoom:1`, `filter: progid:…`) are currently dropped silently. Either outcome is acceptable, but preserving them is safer. |

## 3. Sheet and rule object model (mutable)

Polish, conversion and plugins mutate sheets in place and then serialize them. They need a mutable tree.

### 3.1 Stylesheet

- An ordered, mutable list of top-level rules. The sheet itself is iterable (`for r in sheet`: `replace.py:341`, `epub/__init__.py:20`).
- List operations on `cssRules`:
  - `len`, indexing, `enumerate`, `.index(r)` (`split.py:583`);
  - `.remove(rule)` (`oeb/base.py:1256`, `polish/css.py:99,148,362`, `polish/fonts.py:102`);
  - `.pop(i)` (`css.py:132`) and `.sort(key=)` (`css.py:590`).
- Filter by rule type (`rulesOfType`: `base.py:1255`, `page_margin.py:77`, `epub_output.py:618,627`, `odt/input.py:94,202`, `mobi/writer8/main.py:188`, `kobo/driver.py:2733`, `polish/css.py:70,90,203,236,356`, `check/fonts.py:134`, `css_transform_rules.py:336`, `manual/plugin_examples/editor_demo/main.py:109`). css_parser only filters top-level rules. A version that recurses into nested rules is needed too (see 3.4).
- `insertRule(rule_or_text, index)` and append (`embed_fonts.py:229,265`, `kepubify.py:361,382`, `kobo/driver.py:2931`). Inserting a rule object **owned by another sheet** must work, either by reparenting or by copying it implicitly. Kobo inserts the same `extra_sheet` rules into many sheets.
- `deleteRule(index)` and `deleteRule(rule_object)` (`replace.py:347`, `kepubify.py:360,381`, `split.py:583`, `subset.py:37`, `transforms/subset.py:204`).
- `add(rule)` must place `@charset`/`@import`/`@namespace` in a valid position (`split.py:585`).
- `href`: the base URL used for resolving imports (`stylizer.py:195`).
- `namespaces`: a mutable mapping (P9).
- `encoding`, from `@charset` (see S4).
- Each rule knows its parent sheet (`rule.parentStyleSheet`, `transforms/subset.py:204`) and, for nested rules, its parent rule.
- **Copying.** `mobi/writer8/main.py:103-108` copies a sheet by reparsing its text. A real deep-copy API would be better.
- Sheets are compared by identity (`stylizer.py:271`) and detected by duck typing on a `cssText` or `cssRules` attribute (`oeb/base.py:623,1365`, `filenames.py:40`, `lit/writer.py:520`, `docx/writer/fonts.py:52`, `subset.py:254`, …). These sites need updating, or the new objects need compatible attributes.

### 3.2 Rule types

| Type | Used for | Fields needed |
|---|---|---|
| Style rule | everywhere | selector list, declaration block, source text of the selector |
| `@media` | stylizer, cascade, css.py | media list (with text), nested mutable rule list |
| `@import` | cascade, replace, reader, mobi, jump_to_class | `href` (**read and write**; `mobi/writer8/main.py:194` sets `kindle:flow:…`), media list |
| `@font-face` | fonts, subset, stats, embed_fonts, flatcss, pdf_output, check/fonts | declaration block |
| `@page` | kepubify, page_margin, kobo, epub_output, stylizer | page selector (`:first`, named pages), declaration block, margin boxes (`@top-left`, etc.) as nested rules. `rule.style = ''` empties it (`kobo/driver.py:2913`). **[quirk]** Kobo relies on empty rules being left out of the output. |
| `@charset` | split, jump_to_class | encoding |
| `@namespace` | html.css, fb2_input | prefix, URI |
| Comment | kepubify, jump_to_class | exact text, constructible from text |
| Unknown or other at-rule | everywhere it must pass through untouched | at-keyword (`rule.atkeyword` is the sort key in `css.py:575`), prelude, block (raw or nested), exact source text |

- Type checks happen both through constants (`STYLE_RULE`, `IMPORT_RULE`, `MEDIA_RULE`, `FONT_FACE_RULE`, `PAGE_RULE`, `CHARSET_RULE`, `NAMESPACE_RULE`, `COMMENT`) and through `isinstance` (`stylizer.py:210-220`).
- `cascade.iterrules` looks rule types up **by string name** (`'STYLE_RULE'`, `'FONT_FACE_RULE'`, `cascade.py:104,125-126`, `stats.py:253`).
- Every rule object exposes its serialization as `cssText`, and `@page` and comment rules also accept assignment to it (P4).

### 3.3 Selectors on style rules

- `selectorText`: get and **set**, where setting re-parses (`odt/input.py:219`, `polish/css.py:512-514,581,623-627`). Tests pin the exact text, e.g. `p#id_x-2, [id="id_x-2"]` (`polish/tests/structure.py:661-663`).
- The selector list is **mutable**:
  - iteration, `len`, `sorted()`;
  - `del list[i]` (`css.py:153`);
  - `.append(selector)` taking a selector from another rule (`css.py:130`);
  - comparing the whole list text (`page_margin.py:78`, `epub_output.py:620`).
- Each selector needs its own text, its **specificity**, and arbitrary Python attributes (`selector.calibre_used`, `css.py:47-60,141`), so the classes cannot use `__slots__`, or a side table is needed.
- Specificity in css_parser is a 4-tuple `(inline, id, class, type)` (`stylizer.py:213`, `cascade.py:164-166`, `css.py:571`). css_selectors returns a 3-tuple (`live_css.py:560`). Either form works, but it must follow Selectors Level 4: `:is`/`:not`/`:has` take the maximum of their arguments, `:where` counts 0, `:nth-child(… of S)` adds S, and legacy single-colon pseudo-elements count as type selectors (`A:first-letter` → `(0,0,0,2)`).
- **Selector text fidelity [bug]:** css_parser rewrites `#\31` as `#1` and `.\31 23` as `.123`, which is invalid CSS. Escapes must be preserved. Comments inside selectors are currently kept. Whitespace is normalised (`a , b` → `a, b`, spaces around combinators), case is preserved, and the default namespace prefix is left out.
- Some code searches selector text with plain string or regex matching:
  - `jump_to_class.py:40` (`class_name in selectorText`);
  - `css.py:611-631` (regex rename of classes);
  - `embed_fonts.py:157`, `transforms/subset.py:79-80`, `odt/input.py:96,207` (`.startswith('.')`);
  - `stylizer.py:394`, `cascade.py:246,259` (pseudo regex built from `INAPPROPRIATE_PSEUDO_CLASSES`).

  A structured API (S-section) would let these be rewritten, but the text form must stay available.

### 3.4 Traversal helpers

calibre has its own recursive walkers (`cascade.iterrules`, `cascade.iterdeclaration`, `css.py` helpers). The library should provide:
- a recursive rule walk that goes into `@media`, `@supports` and other grouping rules, and optionally follows `@import` through a callback;
- a walk over every declaration block (style rules, `@font-face`, `@page` and its margin boxes, nested rules).

Many calibre operations currently look only at top-level style rules and miss rules inside `@media`: remove unused CSS, merge, `filter_sheet`, `transform_sheet`, font changes, `check/fonts`, subset, and kepubify. Good helpers make these easy to fix.

## 4. Declaration blocks and properties

### 4.1 Declaration block (CSSStyleDeclaration)

| Need | Uses |
|---|---|
| **Effective view.** One entry per property name, where `!important` beats later normal declarations and otherwise the last one wins, ordered by last occurrence, names lowercased. Plain iteration and `getProperties()` give this. | `stylizer.py:230`, `normalize_css.py:346`, `css.py:83,108,338` |
| **Full view.** Every declaration in source order, duplicates and comments included (`getProperties(all=True)` / `decl.seq`). | `replace.py:364`, `css_transform_rules.py:35-41`, cascade last-wins (`polish/tests/cascade.py:170`) |
| `get value(name)`, returning `''` when absent; also `decl['name']`. | `flatcss.py:672-675`, `epub_output.py:629`, `css.py:584`, `check/fonts.py:143`, `page_margin.py:93`, `kobo/driver.py:2738`, `kepubify.py:663` |
| `get property(name)`, returning a Property or None (truthiness is tested). | `polish/fonts.py:27-117`, `subset.py:32`, `kepubify.py:350,371`, `odt/input.py:147` |
| `get parsed value(name)`, returning a value list or None. | `split.py:116-117`, `odt/input.py:155-158`, `normalize_css.py:273`, `check/fonts.py:135-137` |
| `set(name, value, priority='')` and `set(PropertyObject)`. Setting a name that already exists replaces it in place. | `normalize_css.py:308,326,334`, `embed_fonts.py:226`, `epub_output.py:631`, `css.py:84,347`, `smarts/html.py:339,354`, `css_transform_rules.py:106,114`, editor demo |
| `remove(name)`, returning the old value or `''`; callers use the return value as a "changed" flag. | `split.py:122,129`, `page_margin.py:133,136`, `epub_output.py:622-624`, `kobo/driver.py:2923`, `css.py:335,345,549`, `fonts.py:80,92`, `css_transform_rules.py:113` |
| **Remove one specific declaration instance** (one of several duplicates). | `css_transform_rules.py:61-84`, which currently uses private `seq`, `_tempSeq` and `_setSeq` |
| **Replace one declaration in place with several**, e.g. expanding a shorthand into longhands at the same position. | same file |
| `len`/`length`, `keys()`, `item(i)` → name. | `stylizer.py:221`, `css.py:337,359,385`, `normalize_css.py:273` |
| **Multi-line text** (`name: value;` on separate lines) and **single-line text** (`getCssText(separator=' ')`) for writing `style=""` attributes. The single-line form must be safe inside a double-quoted HTML attribute. **[bug]** `smarts/html.py:335-355` and `replace.py:412` can currently produce broken attributes. | `css.py:388`, `check/fonts.py:92`, `fonts.py:155`, `base.py:440`, editor demo |
| Setting the block from text (`rule.style = '...'`). | `kobo/driver.py:2913` |
| camelCase attribute access (`style.marginLeft`) and `cssproperties._toDOMname`. **Not needed in the new library**: `page_margin.py:98` and `stylizer.py:598` can use their own name mapping. | — |

### 4.2 Property / declaration

- `name`: get and **set**, renaming in place (`kepubify.py:352,373` renames `widows` → `-calibre-removed-css-for-kobo-widows`, and back).
- `value` as a string: get and **set** (`css_transform_rules.py:97-99`).
- The parsed value list (§5).
- `priority`: `'important'` or `''`, plus the literal priority text (`literalpriority`).
- `parent`: the declaration block that contains it (`css.py:549`).
- **Hashable, compared by identity** (`css_transform_rules.py:46,56,70,82`).
- Constructible from `(name, value, priority, parent=)`. It must be possible to construct a property with a **syntax check**: today an invalid value raises `xml.dom.SyntaxErr`, caught at `flatcss.py:13,464-471` and `stylizer.py:15,579`. Callers need a clear "invalid" signal (an exception or `None`) rather than bad data getting through silently.

## 5. Values

| # | Requirement |
|---|---|
| V1 | A property value parses into a **sequence of component values**, without the separators (`font: italic 12px/1.2 "My Font", serif` gives 5 items). The sequence supports `len`, indexing and iteration. Shorthand expansion depends on this (`normalize_css.py:135-136,177`, `stylizer.py:656-664`). The separators (`,` `/`) must still be available from the full value or its text. |
| V2 | Each component has a **type** (ident, string, number, percentage, dimension, URI, function, hash/color, unicode-range, operator), a typed **value**, and its **text**. Numbers are int or float, and `700` must come back as the int `700` so that `str()` gives `'700'` (`stats.py:204-216`, `transforms/subset.py:87`). Strings are unquoted (`stats.py:149,184-188`). Idents are strings (`odt/input.py:157` compares to `'auto'`). Dimensions carry a separate unit. **[bug]** css_parser reports `1.0E1px` with `dimension == 'e1px'`. |
| V3 | URI components expose the URL with no quotes (`.uri`: `replace.py:357-358`, `transforms/subset.py:79`, `pdf_output.py:292`, `check/fonts.py:137`). This includes `url()` inside functions such as `image-set()`, and inside `src:` lists with `format()`. |
| V4 | Function components expose their name and arguments. `local(x)` is currently read as text (`stats.py:259,262`). |
| V5 | The whole value's text can be read and **assigned**, and assignment re-parses (`css.py:554`, `cascade.py:206`, `fonts.py:78,90`). |
| V6 | A component's text must appear **verbatim** inside its parent value's text. `remove_property_value` (`css.py:542-555`) deletes a component by substring replacement, and the test expects `background: black fixed` (`polish/tests/cascade.py:256-260`). A structured "remove component i" operation would be better than relying on this. |
| V7 | `cascade.Values` (`cascade.py:179-198`) is a tuple of components with a combined `cssText`. Tests expect `'"xxx"'` (single quotes normalised to double), `('"Kovid Goyal"', 'sans-serif')`, and `'11pt'` (`polish/tests/cascade.py:149-186`). |

## 6. Serialization

| # | Requirement |
|---|---|
| S1 | **Pretty format.** This is what Edit book shows and saves, and what conversion writes (verified against `setup_css_parser_serialization`, `polish/utils.py:118-125`): one rule per line group, rules separated by one `\n` with no blank lines, one declaration per line written as `name: value;` **including the last one**, `}` at the rule's own indent level, nested rules indented one more level, and comments kept verbatim. The indent is configurable: 2 spaces by default, and the editor tab width for the editor (`gui2/tweak_book/boss.py:134-135,184,286`, `diff/view.py:102`). **[bug]** `oeb/base.py:624-626` resets the indent to 2 on every `serialize()` call, so the editor tab-width setting never takes effect. Formatting options should be passed per call or per serializer, not set in global state. |
| S2 | **Idempotent.** `ser(parse(ser(x))) == ser(x)`. The kepubify ↔ unkepubify round trip must be byte-identical (`polish/tests/kepubify.py:55-57`), and conversion reparses sheets several times (`flatcss.py:136`, `mobi/writer8/main.py:108`, `embed_fonts.py:223`, `reparse_css`). |
| S3 | **Normalisation** that is currently applied and that tests or behaviour depend on. Each item needs a decision: keep it, or make it optional and update the tests. <ul><li>Property names lowercased; value case kept.</li><li>`!IMPORTANT` → `!important`.</li><li>Strings always double-quoted, with `"` inside escaped.</li><li>`url()` quoted only when needed: `url("b c.png")`, otherwise bare.</li><li>`@import "a"` kept as a string, while `@import url("a.css")` → `@import url(a.css)`.</li><li>Numbers: `.5` → `0.5`, `1.0` → `1`, `0px` → `0`, `-0.0px` → `0`, `010` → `10`; `+1px` kept.</li><li>Hex colors shortened: `#ff0000` → `#f00`.</li><li>`RGB( 1 ,2,3 )` → `rgb(1, 2, 3)`; a space after commas.</li><li>Selector whitespace normalised.</li><li>Empty rules dropped (`p{}`, `@media print{}`); Kobo relies on this.</li></ul>Tests pinning this: `normalize_css.py` tests (edge condensation `0 0 0 0` → `0`), `css_transform_rules.py` tests (`margin-top: 10`), `polish/tests/cascade.py:175-179,260`, `polish/tests/kepubify.py:205-207`, `polish/tests/structure.py:661-663`. flatcss uses the `Property('color', attr).value` normalisation to clean up HTML `color`/`bgcolor` attributes (`flatcss.py:464-471`). |
| S4 | **Output type.** Callers currently have to handle sheet `cssText` returning **bytes** in the `@charset` encoding (default UTF-8, with unencodable characters written as escapes) while a declaration block returns **str**. Helpers exist for this: `css_text()` in `oeb/base.py:230`, and `force_unicode` in `css.py:239,376,653`. kepubify assigns bytes straight to `style.text`, which only works for ASCII (`kepubify.py:364,383,393`). The new library should always produce str and offer a separate encode step that honours `@charset`. |
| S5 | Serialization at every level: sheet, any rule, selector, selector list, declaration block (multi-line and single-line), property, value, value component, media list. |
| S6 | **Compact or minified output** is optional. Nothing uses it today. |
| S7 | Serialization must preserve escapes in identifiers and selectors (`#\31`), unknown at-rules verbatim, custom properties, and `var()`/`calc()` (see P6, P15, 3.3). |

## 7. URL extraction and rewriting

| # | Requirement |
|---|---|
| U1 | **`getUrls(sheet)`** yields every URL: `@import` hrefs and `url()` in all declarations (`oeb/reader.py:271`, `trimmanifest.py:68`, `polish/container.py:614`). |
| U2 | **`replaceUrls(sheet_or_decl, fn, ignore_imports=bool)`** calls `fn(url)` with the raw, unquoted URL, including `data:` URLs, and the return value replaces it. Quotes are added on output if needed. It can work on a sheet or on a declaration block. The caller detects whether anything changed (`replace_func.replaced`) and only then marks the file dirty (`container.py:557-559`). Callers: `filenames.py:41`, `html_input.py:233`, `htmlz/oeb2html.py:166`, `oeb/base.py:428,436,439`, `flatcss.py:206`, `stylizer.py:353,582`, `mobi/writer8/main.py:161,170`. |
| U3 | **Coverage [bug].** css_parser currently misses `url()` in `@page` declarations, inside unknown or conditional at-rules (`@supports`), and inside functions (`image-set()`, `cross-fade()`). The replacement should cover all of these. |
| U4 | `oeb/base.py:rewrite_links` (also used by the content server, `srv/render_book.py:553`) re-parses and re-serializes `<style>` text and `style=""` attributes only when they contain `url(` or `@import`. A lighter, **token-level URL rewrite that keeps everything else byte-identical** would serve this caller better than a full re-serialization. |
| U5 | Removing URL-bearing values: drop a `url()` component and, if nothing is left, remove the whole declaration (`replace.py:338-418`). |

## 8. Property grammar and validation

| # | Requirement |
|---|---|
| G1 | **Per-property value matching** for the longhands used in shorthand expansion: `normalize_css.py:183` calls `css_parser.profile.validate(prop, token)` to decide whether a token is a `list-style-type`, `-position` or `-image`, or a `border-*-color`, `-style` or `-width`. At minimum, matchers are needed for `<color>`, `<line-style>`, `<line-width>`/`<length>`, `<image>`/`url()`, and the list-style keywords. |
| G2 | **`validate_color`** (`stylizer.py:51`, CSS2 profile): is this string a valid color? It accepts `rgba()`, `#abc`, `transparent` and `currentColor`. This can use the color parser (§13). |
| G3 | **Custom profiles from output profiles.** `stylizer.py:315-316` registers `output_profile.extra_css_modules` (only the iPad profile defines any, `customize/profiles.py:258,284-296`), but since every parse uses `validate=False` this has **no effect**. This can be dropped, along with the profile mechanism. |
| G4 | Full validation logging during parse (`fb2_input.py:93`, default `validate=True` in a few places) only produces warnings. It is **not needed**. |

Shorthand expansion and condensation (`normalize_css.py:133-359`: margin, padding, border*, list-style, font) are done entirely in calibre code. They can stay there, but a library version would be welcome as long as it is built on G1.

## 9. Selector parsing and matching (replacing css_selectors)

### 9.1 Parsing

- Parse a selector group into a list of selectors. Errors raise a `SelectorSyntaxError`, a subclass of `SelectorError` (a `ValueError`).
- An LRU-cached parse (`get_parsed_selector`, used as a validator in `html_transform_rules.py:140-144`).
- **Syntax:**
  - type and `*`, with `ns|` prefixes;
  - `#id`, `.class`;
  - attribute selectors `[a]`, `=`, `~=`, `|=`, `^=`, `$=`, `*=`, with an ` i`/` s` flag (`!=` currently parses but crashes with `KeyError` when matched);
  - combinators: descendant, `>`, `+`, `~`;
  - pseudo-classes: `:root :scope :any-link :first-child :last-child :only-child :first-of-type :last-of-type :only-of-type :empty :lang() :nth-child(An+B [of S]) :nth-last-child :nth-of-type :nth-last-of-type :not() :is() :where() :matches() :has()` (relative selectors, no nesting);
  - pseudo-elements, both `::x` and legacy single-colon `:before :after :first-line :first-letter`, plus functional `::x()`.
- **Walkable tree.** `polish/css.py:435-467` `classes_in_selector` walks the parsed tree to collect every class name, including inside `:is`, `:not`, `:has` and `of S`.
- Specificity per selector (see 3.3).
- `Selector.pseudo_element`.
- Selectors parsed during stylesheet parsing (3.3) and selectors parsed for matching should **be the same objects**. Today calibre serializes css_parser selectors to text and re-parses them with css_selectors.

### 9.2 Matching (`Select`)

- `Select(root_or_tree, ignore_inappropriate_pseudo_classes=False)`. Unused options that can be dropped: `default_lang`, `dispatch_map`, `trace`, `invalidate_caches`.
- `select(selector_text, root=None)` is a **lazy generator** over matching elements in document order, de-duplicated across a comma group. `root=` limits results to a subtree while matching still uses the whole document. Errors (syntax, unsupported pseudo) are raised **during iteration**. Callers rely on this: `next()`/`StopIteration` and `elem in select(...)` (`jump_to_class.py:42`).
- `has_matches(selector)` (`polish/css.py:213`).
- **HTML-style matching on both XHTML (namespaced) and `lxml.html` trees**: tag, attribute, id and class names are ASCII case-insensitive, attribute values are case-sensitive unless the `i` flag is given, the `{ns}` prefix is stripped when the root is namespaced, and selector namespace prefixes are ignored.
- **`INAPPROPRIATE_PSEUDO_CLASSES`** (`active after disabled visited link before focus first-letter enabled first-line hover checked target focus-within focus-visible target-within`). With `ignore_inappropriate_pseudo_classes=True` these **match everything** (`p:hover` → all `p`). Without the flag they raise `ExpressionError`. Unknown pseudo-classes and other pseudo-elements always raise. Callers also use the *set itself* to build regexes that classify rules as pseudo rules (`stylizer.py:394`, `cascade.py:246,259`). A structured "pseudo-element/pseudo-class of this selector" query would replace those regexes.
- `:is`/`:where` swallow unsupported-expression errors in their arguments; `:not`, `:has` and `of S` do not.
- `:lang()` uses prefix matching. **[quirk]** It effectively reads only `lang`, not `xml:lang` (`select.py:260`).
- **Performance.** Indexes by tag, id and class, plus parse and xpath caches. The stylizer and cascade run every selector of every sheet against every document. The tree must not be mutated while a `Select` is alive.

**Callers:**
- `oeb/stylizer.py:397-441`
- `oeb/transforms/split.py:140-161` (no ignore flag)
- `polish/css.py:43-62,213`
- `polish/cascade.py:242-266`
- `polish/report.py:425-460`
- `html_transform_rules.py:435-458` (user-supplied selectors, `:not(.cls)`)
- `gui2/tweak_book/jump_to_class.py:37-43,112`
- `gui2/tweak_book/live_css.py:560` (specificity only)
- `metadata/sources/amazon.py`
- `gui2/store/stores/kobo_plugin.py`, `gutenberg_plugin.py`
- `recipes/amspec.recipe`, `recipes/esquire.recipe` (third-party recipes and plugins may also use `css_selectors.Select`, so keep a compatible import or a shim)

## 10. Media queries

| # | Requirement |
|---|---|
| M1 | Parse a media query list (from `@media`, `@import`, or `<link media>`) into queries, each with **media type, negation (`not`), `only`, and feature expressions as `(name, value)` pairs**. Values may be ratios (`16/9`) or dimensions. Range syntax (`width >= 600px`) should also be parsed. |
| M2 | Malformed queries evaluate to "not all" (never match), per spec. tinycss returns `MediaQuery('all', (), negated=True)` (`tests/media3.py`). **[bug]** css_parser mangles invalid media text when serializing (`"(min-width:10px) and screen"` → `"(min-width: 10px) and"`). Media text must round-trip unchanged. |
| M3 | Evaluation stays in calibre (`stylizer.py:115-152` `media_ok`, used by `cascade.py:20,85,282` and `stylizer.py:198,320,338,360`). Allowed types are `screen`, `all`, `aural` and `amzn-kf8`; queries using width, height, device-*, aspect-ratio, color, monochrome, resolution, scan or grid are false. **[quirk]** Media type matching is currently case-sensitive; it should be ASCII case-insensitive. |
| M4 | Media lists on `@media` and `@import` objects expose their text and the parsed queries. The text is tested for falsiness (`cascade.py:82-85,140`). |

## 11. Font helpers (replacing `tinycss.fonts3`)

All of these must keep their current behaviour, which is pinned by `src/tinycss/tests/fonts3.py:32-66`.

| Function | Behaviour required | Users |
|---|---|---|
| `parse_font_family(text) -> list[str]` | Unquotes and unescapes strings (`"X \"Y"` → `X "Y`). Joins runs of idents with a single space (`A  B` → `A B`). Resolves escapes (`Red\/Black`, `Hawaii\ 5-0`). Keeps non-ASCII. Drops empty strings. Returns generic names unchanged. `''` → `[]`. | `cascade.py:206`, `polish/fonts.py:34,47,74,119`, `stats.py:257`, `check/fonts.py:143`, `transforms/subset.py:82` |
| `serialize_font_family(list) -> str` | Lowercases generic families (`serif sans-serif cursive fantasy monospace`; `sansserif` → `sans-serif`). Leaves names matching `[a-zA-Z][a-zA-Z0-9_-]*` unquoted unless they start with `and`. Otherwise double-quotes them with `"` escaped. Joins with `', '`. | `cascade.py:206`, `fonts.py:78`, `normalize_css.py:213` |
| `parse_font(text) -> dict` | Parses the `font` shorthand. Returns only the keys present among `font-style font-variant font-weight font-stretch font-size line-height font-family`, with string values and a list for the family. Handles `size/line-height`, weights 100–900, and `normal`/`inherit`/`initial`/`unset` filling slots in order. Legacy system fonts (`caption`, etc.) → `{'font-family': ['sans-serif']}`. | `normalize_css.py:198-214`, `fonts.py:84` |
| `serialize_font(dict) -> str` | Order: style, variant, weight, stretch, `size[/line-height]`, families. Must round-trip: `parse_font(serialize_font(d)) == d`. | `fonts.py:90` |

The docx writer (`docx/writer/styles.py:26-54`) parses `font-family:` + raw text with `parse_style_attr` and then reads STRING/IDENT tokens by hand. `parse_font_family` can replace that.

## 12. Color parsing (replacing `tinycss.color4`)

| # | Requirement |
|---|---|
| C1 | `parse_color_string(str) -> RGBA | 'currentColor' | None`. `RGBA` is a namedtuple `(red, green, blue, alpha)` with every channel a float in 0..1, **gamut-clipped**. `transparent` → `(0,0,0,0)`. `currentColor`, in any case, → the string `'currentColor'`. |
| C2 | Supported syntax (the spec is `src/tinycss/tests/color4.py`): <ul><li>named colors (CSS3 plus `rebeccapurple`), case-insensitive;</li><li>hex with 3, 4, 6 or 8 digits;</li><li>`rgb()`/`rgba()` in both the legacy comma form (all numbers or all percentages; alpha may be a percentage) and the modern space form with `/ alpha`, with `none` allowed in the modern form;</li><li>`hsl()`/`hsla()` with `deg`/`grad`/`rad`/`turn` hues;</li><li>`hwb()`, `lab()`, `lch()`, `oklab()`, `oklch()`;</li><li>`color(srgb | srgb-linear | display-p3 | a98-rgb | prophoto-rgb | rec2020 | xyz | xyz-d50 | xyz-d65 …)`.</li></ul>Scientific notation and comments are allowed, surrounding whitespace is ignored, and an unterminated function at end of input is accepted. |
| C3 | Returns `None` for `calc()`, `var()`, `color-mix()`, relative color syntax, system colors, trailing junk, the wrong argument count, and units on rgb values. Callers pass these strings through unchanged (`mobi/utils.py:647-675` test: `rgb(var(--x))`). |
| C4 | **It must be fast**, because it runs on every highlight pass in the editor (`gui2/tweak_book/editor/syntax/css.py:195-213`), and it is in C today. |
| C5 | Callers: <ul><li>`docx/writer/utils.py:22-60` (`convert_color`: `currentColor` → `auto`; alpha < 0.01 → None; hex from `round(x*255)`);</li><li>`mobi/utils.py:647-675`;</li><li>`gui2/tweak_book/editor/syntax/css.py:195-213`;</li><li>could also back `stylizer.validate_color` (G2).</li></ul> |

The CSS3 parser (`color3`) and its quirks, such as unclipped RGB, have no users outside the tinycss tests and can be dropped.

## 13. Logging, errors, diagnostics

| # | Requirement |
|---|---|
| L1 | Parse warnings (dropped declarations and rules, bad selectors, failed imports) are reported to a **per-parse logger or callback**, not to global module state. Today `css_parser.log` and the serializer prefs are global, and parsers created with `loglevel=CRITICAL` silence warnings for the rest of the process (`embed_fonts.py:115`, `stylizer.py:45`, `plumber.py:1242`, `html_input.py:151`, `mobi/writer8/main.py:98`, `oeb/base.py:388-389,1246-1247`). |
| L2 | In conversion, warnings go to the `calibre.css` logger, which forwards them to the OEB log (`oeb/base.py:689-705`, wired at `2100`). Users see them in the conversion log, so messages should be readable and include line and column. |
| L3 | Polish and Edit book currently swallow parse errors. Check book uses stylelint in QtWebEngine (`polish/check/css.py`) and needs nothing from this library. Collecting errors with positions (tinycss `Stylesheet.errors`, which nothing uses today) would let Check book stop depending on stylelint later. |
| L4 | Exception types: `SelectorError`/`SelectorSyntaxError`/`ExpressionError` for selectors, and a clear error or `None` for invalid property values (4.2). |

## 14. Performance

The library runs on every stylesheet in every conversion, through the full stylizer cascade, and in Edit book on every file open, save and beautify. Requirements:
- the tokenizer and the color parser are in C or similarly fast (T6, C4);
- selector matching is indexed and cached (9.2);
- parsing large sheets is roughly linear;
- whole sheets are never re-serialized just to rewrite one URL (U4).

The content server's own CSS transform (`srv/render_book.py:34,281,490,496`, the C extension `fast_css_transform`) is separate and not in scope. It could share the tokenizer.

## 15. Public API and compatibility surface

- `Container.parsed(name)`, `Container.parse_css()` and `Container.replace()` are **documented** as returning or accepting css_parser stylesheets (`polish/container.py:78,763-783`). Edit book plugins use them, and `manual/plugin_examples/editor_demo/main.py:9,85-122` uses:
  - `CSSRule.STYLE_RULE` and `cssRules.rulesOfType`;
  - `getPropertyValue`, `setProperty`;
  - `getCssText(separator=' ')`;
  - `serialize(sheet, 'text/css', pretty_print=True)`.

  `manual/polish.rst` documents `pretty_css`, `remove_unused_css`, `filter_css` and `change_font`. Decide whether to provide a thin css_parser-compatible facade for plugins, or to document a break (update the manual and the demo plugin).
- `OEBBook` manifest items hold parsed sheets in `item.data` (`oeb/base.py:1243-1328`); conversion plugins and output plugins read them. CSS-string data assigned to an item is parsed lazily on first access (`htmltoc.py:102`, `manglecase.py:50`).
- `Stylizer.font_face_rules` (a list of font-face rules, serialized by `flatcss.py:694`) and `oeb.store_embed_font_rules.rules` (`mobi/writer8/toc.py:94`) expose rule objects.
- `css_selectors.Select` is imported by recipes (`recipes/*.recipe`), and probably by third-party recipes and plugins. Keep an import path or a shim.
- Calibre-wide formatting entry points to update: `oeb/base.py` `serialize()` and `css_text()`, `polish/utils.py` `parse_css`/`setup_css_parser_serialization`, `polish/pretty.py:213-222,265-303` (`pretty_css`, `pretty_script_or_style`, beautify all), `editor/widget.py:690-699`, `gui2/tweak_book/diff/view.py:81-115`, and `editor/text.py:546-569` (sort CSS).

## 16. Tests that define the expected behaviour (to port or keep passing)

- `src/tinycss/tests/*`:
  - tokenizing (Python vs C, `as_css` round trip, positions);
  - css21 (at-rules, `@charset`, `parse_style_attr`, `!important`, `@import`, `@page`, `@media`);
  - decoding;
  - color3 and color4;
  - fonts3;
  - media3;
  - page3 (page selector specificity, margin rules).
- `src/css_selectors/tests.py`: tokenizer, parser, pseudo-elements, specificity (level 4), parse errors (`675-770`), select, select level 4, select on a Shakespeare `lxml.html` document.
- `polish/tests/cascade.py`, `polish/tests/structure.py:616-700`, `polish/tests/kepubify.py`, `polish/tests/parsing.py` (if it touches CSS).
- In-module tests: `oeb/normalize_css.py` (expansion and condensation, ~`:380-560`), `ebooks/css_transform_rules.py` (`:400-420+`), `docx/writer/utils.py:34-60`, `mobi/utils.py:655-675`, `oeb/stylizer.py:142-152` (`media_ok`), `html_transform_rules.py:620`.
- Registered in `utils/run_tests.py:145-150,758-772` and `test_build.py:497-500,615`.

## 17. What is NOT needed

- css_parser's property validation profiles and `addProfile` (G3), and validation logging (G4).
- DOM camelCase property attributes and `cssproperties._toDOMname`; calibre can keep a small mapping.
- Following `@import` through `styleSheet` on import rules (never used; calibre follows imports itself).
- `parseFile`/`parseUrl`.
- tinycss CSS3 color quirks, `parse_stylesheet_bytes/file` (nice to have), and page3 margin-rule *objects* beyond keeping them in the tree and serializing them.
- css_selectors `default_lang`, `dispatch_map`, `trace`, and namespace-aware matching.
- Minified serialization.

## 18. Decisions needed: current quirks and bugs

| Current behaviour | Where it matters | Recommendation |
|---|---|---|
| `--x` custom properties and `var()` declarations are silently dropped | every round trip | **Fix**: keep them |
| Unknown at-rules corrupted (`formatUnknownAtRules=False`) or dropped (tinycss) | `@supports`, `@layer`, `@container` | **Fix**: keep verbatim, or parse them as nested grouping rules |
| Selector escapes decoded into invalid CSS (`#\31` → `#1`) | every round trip | **Fix** |
| URL helpers miss `@page`, `@supports` and nested functions | link replacement, manifest trimming | **Fix** |
| Jump-to-class index mismatch between css_parser and tinycss | Edit book | **Fix**: one parser, CSSOM-compatible indices, positions on rules |
| Editor tab width ignored for CSS | Edit book | **Fix**: pass formatting options per call |
| Sheet `cssText` is bytes while declaration text is str | many callers | **Fix**: always str, encode separately |
| Empty rules dropped on output | Kobo `@page` blanking relies on it | Keep, or change Kobo to delete the rule |
| Number, color and quote normalisation on output | tests, idempotence | Keep a deterministic normalisation and update the tests if it changes |
| `!=` attribute operator parses but crashes when matched | css_selectors | Fix (reject at parse time or implement it) |
| `:lang()` ignores `xml:lang` | css_selectors | Fix |
| Media type case-sensitive in `media_ok` | stylizer | Fix |
| Invalid media text mangled on output | round trip | Fix: keep media text |
| `stats.get_css_text` expects a quoted STRING `.value`, but values are unquoted, so `::before`/`::after` content is never counted for subsetting | font subsetting | Fix in calibre when porting |
| `fonts.change_font` never writes modified `<style>` sheets back; `remove_embedded_font` parses `src` with `src[4:-1]` | polish | Fix in calibre when porting |
| Many polish operations only look at top-level rules (they skip `@media`) | remove unused CSS, merge, filter, transform, fonts, subset, kepubify | Fix in calibre using the recursive helpers (3.4) |
| Raw multi-line declaration text or double quotes written into `style=""` | `replace.py:412`, `smarts/html.py:335-355` | Fix: provide an attribute-safe single-line serializer |
