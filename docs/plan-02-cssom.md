# Plan 02: The CSS object model (CSSOM)

This plan covers the mutable object model that calibre mutates and
serializes. It is built on the generic syntax tree from plan 01. It covers
these sections of requirements.md:

- §2: P1-P5, P7-P13 and P15-P16, at the object level;
- §3: sheets, rules, selector lists and traversal;
- §4: declaration blocks and properties;
- §5: values;
- §6: serialization;
- §7: URLs;
- §13: logging and errors;
- §15: the API surface, with a facade decision;
- §18: decisions on each quirk.

**Out of scope, each with its own plan later:**
- selector parsing, specificity and matching (§9), which is plan 03;
- media query parsing (§10);
- font helpers (§11);
- colors (§12);
- property grammar and shorthands (§8).

The CSSOM only has hooks for these, as noted below.

---

## 1. Conventions

The API is Pythonic, not a port of the DOM CSSOM:

- `snake_case` for functions, methods, attributes and keyword arguments:
  `css_text`, `selector_text`, `parent_style_sheet`, `get_value()`.
  Classes use `CapWords` and constants use `UPPER_CASE`, as in PEP 8.
- Containers behave like Python containers. Rule lists are mutable
  sequences (`len`, indexing, slicing, `index`, `remove`, `pop`, `insert`,
  `append`, `sort`). A declaration block is a mapping-like object.
- Python semantics for missing keys: `block['color']` raises `KeyError`, and
  `block.get_value('color')` returns `''`, which calibre callers rely on.
- Use enums (`RuleType`) and `isinstance` rather than integer type
  constants.
- Keyword-only arguments for options, and per-call options objects instead
  of global settings (requirements L1, S1).
- Everything fully typed. Rule classes are `@final` wherever subclassing is
  not meant to be supported.

Appendix A maps the css_parser names that calibre uses to the new ones.

---

## 2. Key decisions

### 2.1 Pure Python on the C parser, with lazy blocks

The object model is written in Python. All parsing goes through the plan 01
C functions. A measurement on the 1.55 MB calibre-style benchmark sheet
(15k rules, 53k declarations), building plain `__slots__` objects:

| What is built | Time |
|---|---|
| Every rule and every declaration | 60 ms |
| Rules only, declaration blocks parsed when first accessed | 14 ms |
| css_parser `parseString()` on the same sheet | 7,000 ms |

So a Python CSSOM is still about 100x faster than what calibre uses today.
It keeps the mutable, introspectable and subclass-friendly objects that
Python code expects. C stays for the hot primitives:

- tokenizing and parsing (plan 01);
- string and identifier serialization;
- later, if profiling shows the need, value normalisation during
  serialization (§6).

**Laziness:**
- All rules, at every depth, are built eagerly. There are few of them.
- A rule's declaration block (`StyleRule.style`, `FontFaceRule.style`,
  `PageRule.style`) is built from its syntax `Block` the first time it is
  accessed.
- A declaration's parsed value is built from its text the first time it is
  accessed.
- Selector lists are split from the prelude text the first time they are
  accessed.

### 2.2 Thread safety

- Free-threaded Python cannot crash on Python objects. Without the GIL, the
  only new hazard is a logical one: lazy materialization racing.
- Each parse creates one small `_SheetState`, shared by all objects of that
  sheet. It holds a `threading.Lock` that guards every lazy
  materialization, so `rule.style is rule.style` always holds, even with
  concurrent readers.
- Mutation is like mutating a `list` or `dict` from several threads: it
  never crashes, but the caller must synchronise. This is documented.
- Reading a fully or partly materialized sheet from many threads is safe.
  Conversion does this.

### 2.3 Source fidelity and serialization modes

Every object remembers the source range it was parsed from (TokenStream
plus offsets) until it is modified. Modifying an object marks it and its
ancestors dirty. This allows two serialization styles (§6):

- **`pretty`** (the default) is the deterministic, normalised format that
  Edit book and conversion write. It must be idempotent (S1-S3).
- **`preserve`** writes unmodified objects as their exact source text and
  modified ones in pretty style. It gives minimal diffs. It serves U4
  (rewrite a URL without reserializing the sheet) and the byte-identical
  kepubify round trip (P7).

### 2.4 Naming at the top level

Top-level `kgcss.parse_*` should return CSSOM objects. The generic syntax
functions from plan 01 move to `kgcss.syntax` and `kgcss.tokenizer` only,
and are no longer re-exported from `kgcss`. Nothing is released yet, so
this costs nothing now.

```python
from kgcss import parse_stylesheet, parse_style, parse_rule, Property
from kgcss.syntax import parse_stylesheet as parse_syntax_tree  # low level
```

### 2.5 No css_parser compatibility facade in the core

The core API is Pythonic only. requirements.md §15 lists a few documented
plugin uses (`cssRules.rulesOfType`, `getPropertyValue`, `setProperty`,
`getCssText(separator=' ')`, `serialize(sheet, 'text/css', pretty_print=True)`).
A thin `kgcss.compat` facade with those camelCase names can be added as the
last, optional milestone, if you want plugins to keep working without
changes (§9, decision D1).

---

## 3. Entry points

```python
def parse_stylesheet(
    source: str | Buffer, *,
    href: str | None = None,                 # base URL for @import resolution
    protocol_encoding: str | None = None,    # bytes only, see plan 01 §2
    environment_encoding: str | None = None,
    fetcher: ImportFetcher | None = None,    # P10
    resolve_imports: bool = False,           # P11, inline @import while parsing
    log: ErrorHandler | None = None,         # L1, per parse
) -> Stylesheet

def parse_style(text: str, *, log: ErrorHandler | None = None) -> DeclarationBlock   # P2, style=""
def parse_rule(text: str, *, log: ErrorHandler | None = None) -> Rule                # P4, raises CSSSyntaxError
def parse_value(text: str) -> PropertyValue                                          # P3
Property(name: str, value: str, priority: str = '', *, validate: bool = False)       # P3, 4.2
def serialize(obj: CSSObject, options: SerializeOptions | None = None) -> str         # S5
```

- `ErrorHandler` is `Callable[[ParseError], None] | logging.Logger`. Every
  parse also keeps its errors in `.errors` on the result, whatever `log` is
  (L3).
- Parsing never raises on bad CSS (P5). Only APIs that must produce exactly
  one valid object raise: `parse_rule()`, assigning `css_text`, and
  `Property(..., validate=True)`.

---

## 4. Object model

### 4.1 Stylesheet

```python
class Stylesheet:
    rules: RuleList                      # top level rules; the sheet is also iterable and sized
    href: str | None
    encoding: str | None                 # from bytes decoding or @charset (S4)
    namespaces: NamespaceMap             # mutable prefix -> URI mapping, kept in sync with @namespace rules (P9)
    errors: list[ParseError]             # with line and column (L2, L3)
    def insert_rule(self, rule: Rule | str, index: int | None = None) -> Rule
    def delete_rule(self, rule_or_index: Rule | int) -> None
    def add(self, rule: Rule | str) -> Rule    # puts @charset/@import/@namespace in a valid position (3.1)
    def iter_rules(self, *types: RuleType, recursive: bool = True, follow_imports: bool = False) -> Iterator[Rule]   # 3.4
    def iter_declaration_blocks(self, recursive: bool = True) -> Iterator[DeclarationBlock]                        # 3.4
    def browser_index_path(self, rule: Rule) -> tuple[int, ...]   # P13: CSSOM indices, skipping comments and @charset
    def copy(self) -> Stylesheet          # deep copy; also __deepcopy__ (3.1)
    css_text: str                         # get: pretty serialization; set: reparse in place
    def encode(self, options: SerializeOptions | None = None) -> bytes   # S4: honours @charset, escapes unencodable characters
```

- **Inserting rules from elsewhere.** `insert_rule()` and `add()` accept a
  rule that belongs to another sheet. They insert a copy and return it.
  Kobo inserts the same rules into many sheets, and copying keeps every
  sheet independent. Decision D4.
- **P5 recovery at object level.** Error recovery that css_parser does at
  object level happens while the CSSOM is built:
  - an `@import` after other rules is dropped and logged;
  - an `@charset` that is not first is dropped;
  - an `@namespace` after other rules is dropped.

### 4.2 Rules

`Rule` is the abstract base. Every rule has:

- `type: RuleType`;
- `parent_style_sheet`, and `parent_rule` for nested rules;
- `css_text`, which can be read and assigned. Assigning reparses and
  replaces the rule in place; it raises `CSSSyntaxError` if the text is not
  exactly one rule of a compatible type (P4);
- `line` and `column` from the original source (P12);
- `copy()`.

`RuleType` is an `Enum`. Members can be looked up by name
(`RuleType['STYLE']`), which `cascade.iterrules` needs (3.2).

| Class | `RuleType` | Fields |
|---|---|---|
| `StyleRule` | `STYLE` | `selector_text` (get/set), `selectors: SelectorList`, `style: DeclarationBlock`, `rules: RuleList` (nested rules, CSS nesting) |
| `MediaRule` | `MEDIA` | `media: MediaList`, `rules: RuleList` |
| `SupportsRule` | `SUPPORTS` | `condition_text`, `rules` |
| `ContainerRule`, `LayerBlockRule`, `ScopeRule`, `StartingStyleRule`, `DocumentRule` | own types | `prelude_text`, `rules` (generic conditional and grouping rules, P6) |
| `LayerStatementRule` | `LAYER_STATEMENT` | `names: list[str]` |
| `ImportRule` | `IMPORT` | `href` (get/set), `media: MediaList`, `layer`, `supports`, `style_sheet: Stylesheet \| None` (P10) |
| `NamespaceRule` | `NAMESPACE` | `prefix: str \| None`, `namespace_uri` |
| `CharsetRule` | `CHARSET` | `encoding` |
| `FontFaceRule` | `FONT_FACE` | `style` |
| `PageRule` | `PAGE` | `selector_text` (`:first`, named pages), `style`, `rules` (margin rules) |
| `MarginRule` | `MARGIN` | `name` (`top-left` and so on), `style` |
| `Comment` | `COMMENT` | `text` (get/set), constructible from `'/* ... */'` (P7) |
| `UnknownAtRule` | `UNKNOWN` | `name` (`rule.at_keyword` is the sort key, `css.py:575`), `prelude_text`, `block_text \| None`, kept verbatim (P6) |

- Grouping rules share a `GroupingRule` base with `rules`, `insert_rule()`
  and `delete_rule()`.
- `RuleList` is a mutable sequence that sets and clears parent pointers. It
  also has `of_type(*types)` (rulesOfType) and `iter_recursive()`.
- `@keyframes`, `@counter-style`, `@property`, `@font-feature-values` and
  vendor rules are `UnknownAtRule`. Their text is preserved, and URL
  operations still reach inside them at token level (U3).
- **Empty rules:** `rule.style = ''`, or assigning an empty block, empties
  the rule. Whether empty rules are written out is a serializer option
  (decision D6).

### 4.3 Selectors on style rules (3.3)

- `StyleRule.selector_text` can be read and assigned. Assigning reparses,
  and raises `CSSSyntaxError` for an empty or unbalanced selector list.
- `StyleRule.selectors` is a `SelectorList`: a mutable sequence of
  `Selector` objects. It supports iteration, `len`, `del sl[i]`,
  `append(selector)` (taking a selector from another rule copies it),
  `sorted()`, and comparison of the whole text.
- `Selector` has `text` and `specificity`. It deliberately has **no
  `__slots__`**, so callers can attach attributes such as
  `selector.calibre_used`.
- **Text fidelity, a bug fix:**
  - escapes are kept (`#\31` stays `#\31`);
  - whitespace is normalised from tokens (`a , b` becomes `a, b`, spaces go
    around combinators), using each token's exact source text, so escapes
    can never be decoded;
  - case is preserved;
  - comments are kept;
  - the default namespace prefix is left out.
- A selector with an undeclared namespace prefix keeps its rule (quirk fix,
  P9). The problem is logged.
- **Splitting** a list into selectors uses top-level commas in the prelude's
  component values, which is exact and cheap.
- **Specificity and structured selectors come from plan 03.**
  `Selector.specificity` and `Selector.parsed` call the plan 03 parser
  lazily. Until plan 03 lands, they raise `NotImplementedError`. Plan 03
  also makes the same `Selector` objects usable for matching (9.1, "the
  same objects").

### 4.4 Media lists (M4)

- `MediaList` has `media_text`, which can be read and assigned, and is kept
  exactly as written, even when invalid (M2 fix).
- It is a mutable sequence of query strings (`append`, `remove`,
  `__bool__`).
- A `queries` property returns parsed queries once the media query plan
  lands.
- It is used by `MediaRule` and `ImportRule`.

### 4.5 Declaration blocks (4.1)

```python
class DeclarationBlock:                    # also used for style="" (P2)
    parent_rule: Rule | None
    # Effective view: one Property per lowercased name. !important beats later
    # normal declarations, otherwise the last one wins. Ordered by last occurrence.
    def __iter__(self) -> Iterator[Property]
    def __len__(self) -> int
    def __contains__(self, name: str) -> bool
    def __getitem__(self, name: str) -> str              # value; KeyError if absent
    def keys(self) -> list[str]
    def get_value(self, name: str, default: str = '') -> str
    def get_property(self, name: str) -> Property | None
    def get_parsed_value(self, name: str) -> PropertyValue | None
    def set(self, name: str, value: str, priority: str = '') -> Property   # replaces in place if the name exists
    def set_property(self, prop: Property) -> Property                     # same; copies if prop has another parent
    def remove(self, name: str) -> str                                     # old value or '' (used as a "changed" flag)
    # Full view: every declaration and comment in source order (getProperties(all=True), decl.seq)
    items: DeclarationList          # mutable sequence of Property | Comment
    def remove_declaration(self, prop: Property) -> None                   # one specific instance
    def replace_declaration(self, prop: Property, replacements: Iterable[Property]) -> None   # shorthand expansion
    css_text: str                   # get: multi-line pretty; set: reparse (kobo: rule.style = '...')
    def to_css(self, options: SerializeOptions | None = None, *, single_line: bool = False) -> str
```

- `items` replaces calibre's use of the private `seq`, `_tempSeq` and
  `_setSeq` (`css_transform_rules.py`).
- `single_line=True` gives text that is safe inside a double-quoted HTML
  attribute. Strings are written with single quotes, and newlines are
  escaped. This fixes the `style=""` bug (§18).
- `bool(block)` is false for an empty block.
- **Invalid declarations** (IE hacks such as `*zoom: 1`, P16) are dropped
  and logged, following the spec and today's behaviour (decision D7).

### 4.6 Properties (4.2)

```python
class Property:
    name: str               # get/set, renames in place (kepubify)
    value: str              # get/set, setting reparses
    parsed_value: PropertyValue   # lazy
    priority: str           # 'important' or ''
    literal_priority: str   # the priority as written, e.g. '!IMPORTANT'
    parent: DeclarationBlock | None
    line: int; column: int  # when parsed
    def __init__(self, name: str, value: str, priority: str = '', *, validate: bool = False) -> None
```

- Properties are hashable and compare by identity.
- `name` is lowercased, except for custom properties, which are
  case-sensitive.
- `validate=True` checks the value syntax: it must be a well-formed
  declaration value with no bad tokens or unbalanced brackets, and must not
  be empty. On failure it raises `InvalidValueError`, a `ValueError`. That
  gives callers the clear "invalid" signal flatcss and stylizer need.
  Checks for each property (G1, G2) come with the property grammar plan.

### 4.7 Values (§5)

- `PropertyValue` is an immutable sequence of **components**. Separators
  (`,` and `/`) and whitespace are not components (V1):
  `font: italic 12px/1.2 "My Font", serif` has 5 components.
- `PropertyValue.items` includes the separators, for code that needs them.
  `css_text` is the whole value's text.
- Assigning `Property.value` replaces the value (V5). The objects stay
  immutable, which keeps them cheap and safe to share.
- Component classes share `type`, `value` and `css_text`. Each component's
  `css_text` appears verbatim in its parent's text (V6):

| Class | `type` | `value` |
|---|---|---|
| `Ident` | `'ident'` | `str` |
| `String` | `'string'` | unquoted `str` |
| `Number` | `'number'` | `int` or `float` (700 stays `int`, V2) |
| `Percentage` | `'percentage'` | `int` or `float` |
| `Dimension` | `'dimension'` | number, plus a separate `unit`, so `1.0E1px` is 10.0 `px` |
| `Url` | `'url'` | the unquoted URL as `value`, also `.url` (V3) |
| `Function` | `'function'` | `name` and `arguments: PropertyValue`, so `url()` inside `image-set()` is reachable (V3, V4) |
| `Hash` | `'hash'` | `str` without `#`; `is_id` |
| `UnicodeRange` | `'unicode-range'` | `start`, `end`, parsed from tokens for `unicode-range` (plan 01 §3.4) |
| `Operator` | `'operator'` | other delimiters such as `+` and `!` |

- `PropertyValue.remove(index)` removes one component structurally (V6,
  U5), instead of calibre's substring replacement.

---

## 5. URLs (§7)

```python
def iter_urls(obj: Stylesheet | Rule | DeclarationBlock) -> Iterator[str]                     # U1
def replace_urls(obj: Stylesheet | Rule | DeclarationBlock, fn: Callable[[str], str],
                 *, ignore_imports: bool = False) -> bool                                       # U2: True if anything changed
def replace_urls_in_text(css: str, fn: Callable[[str], str], *, style_attribute: bool = False) -> str   # U4
def remove_url_values(block: DeclarationBlock, predicate: Callable[[str], bool]) -> bool        # U5
```

- **Coverage (U3):**
  - `@import`;
  - every declaration block, including `@page`, margin rules and
    `@font-face`;
  - nested and grouping rules (`@supports`);
  - unknown at-rules, at token level;
  - functions at any depth (`image-set()`, `cross-fade()`).

  `data:` URLs are passed to `fn` like any other.
- `replace_urls()` changes values in place. It only marks the objects it
  changed as dirty, so a `preserve` serialization changes only those bytes.
- `replace_urls_in_text()` works on the token stream alone. It splices new
  `url()` or string text at the token offsets and copies everything else
  byte for byte, so no CSSOM is built. This is the light path
  `oeb/base.py:rewrite_links` needs, also for `style=""` attributes.
- Quotes are added on output only when the new URL needs them.

---

## 6. Serialization (§6)

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class SerializeOptions:
    style: Literal['pretty', 'preserve'] = 'pretty'
    indent: str = '  '  # S1: per call, e.g. the editor's tab width
    normalize: bool = True  # the S3 normalisations below
    drop_empty_rules: bool = True  # calibre's current behaviour, D6
    keep_comments: bool = True
```

- **Pretty format (S1):**
  - each rule starts on its own line, and rules are separated by a single
    `\n`, with no blank lines;
  - one `name: value;` per line, including after the last declaration;
  - `}` sits at the rule's own indent, and nested rules are indented one
    more level;
  - comments are written verbatim.
- **Normalisations (S3), all kept, as recommended (decision D5):**
  - property names lowercased, except custom properties;
  - `!important` written in lowercase;
  - strings always double-quoted, with `"` escaped;
  - `url()` quoted only when needed;
  - `@import "a"` kept as a string;
  - numbers normalised: `.5` becomes `0.5`, `1.0` becomes `1`, `0px` and
    `-0.0px` become `0`, `010` becomes `10`, and `+1px` is kept;
  - hex colors shortened when that loses nothing;
  - `RGB( 1 ,2,3 )` becomes `rgb(1, 2, 3)`;
  - selector whitespace normalised (§4.3).

  The number rules apply only where they cannot change meaning. For
  example, `0px` is not shortened inside `calc()`, and nothing is touched
  inside custom property values, which are always written verbatim.
- **Escapes (S7):** identifiers and selectors keep their escapes. Unknown
  at-rules, custom properties, `var()` and `calc()` are written verbatim.
- **Idempotence (S2):** `serialize(parse(serialize(x))) == serialize(x)`. A
  hypothesis property test checks this over structured CSS.
- **Output types (S4):** `serialize()` always returns `str`.
  `Stylesheet.encode()` gives bytes in the `@charset` encoding (default
  UTF-8). Characters that encoding cannot represent are written as CSS
  escapes. This works inside strings, comments and identifiers alike,
  because CSS escapes are valid in all of them.
- **Levels (S5):** serialization works at every level:
  - sheet and any rule;
  - selector and selector list;
  - declaration block, multi-line and single-line;
  - property, value and component;
  - media list.
- **Minified output (S6):** not in this plan.
- **Speed:** written in Python first. If benchmarks show value
  normalisation is the bottleneck, it moves to C, as a function that
  serializes a component value range of a TokenStream with the
  normalisations applied.

---

## 7. @import and logging

- **Fetching (P10):**
  - `fetcher(url) -> (encoding, text) | None`, where `url` is resolved
    against the sheet's `href`;
  - `None` means missing, and the rule is kept; `''` means empty;
  - with `resolve_imports=False`, fetching is lazy, on first access to
    `ImportRule.style_sheet`;
  - recursion is guarded by a set of the URLs being loaded.
- **Inlining (P11):** `resolve_imports(sheet) -> Stylesheet` inlines
  imported sheets. An import that had media is wrapped in `@media`, and
  `/* START @import ... */` comments are added. The two-phase flow (parse,
  rewrite hrefs, serialize, reparse with resolution) works naturally
  because nothing is global.
- **Logging (L1-L4):** handlers are per call, and there is no module-level
  logger state. Messages give line and column. The exceptions are
  `CSSSyntaxError` and `InvalidValueError`, both `ValueError` subclasses.

---

## 8. Milestones

Status: milestones 1 and 2 are done.

Notes from milestone 2:
- **`DeclarationBlock`** has the effective view (iteration, `len`, `in`,
  `[name]`, `keys()`, `get_value()`, `get_property()`) and the full view
  (`items`, a `DeclarationList` of `Property` and `Comment` with parent
  pointers). It also has `set()`, `set_property()`, `remove()`,
  `remove_declaration()`, `replace_declaration()` and `copy()`. The
  effective view is cached and invalidated on any change, including changes
  made through a `Property`.
- **`set()` follows the browser CSSOM:** it changes the effective
  declaration in place and removes the other declarations of that name.
  `remove()` removes all of them and returns the old effective value, or
  `''`. `set_property()` uses a copy if the property belongs to another
  block. Inserting into `items` a property owned elsewhere raises
  `ValueError`: an explicit `copy()` is clearer at that level.
- **`Property`** lowercases names, except custom properties and non-ASCII
  names. The value is read from the syntax node on first access, through
  the new C getter `Declaration.value_text`. `literal_priority` is found by
  tokenizing the text between the value and the end of the declaration.
  There are `line`, `column` and `copy()`.
- **Values are always checked structurally** when set (`check_value()`).
  This rejects:
  - a top level `;`;
  - a top level closing bracket;
  - anything unclosed (brackets, strings, `url()`, comments);
  - an embedded `!important`, which belongs in the priority.

  So a value can never corrupt the text around it. `validate=True` also
  rejects empty values and values with any tokenizer error, such as bad
  strings and bad URLs (requirements 4.2). The error is `InvalidValueError`,
  and `CSSSyntaxError` is defined for milestone 5.
- **`style`** is built lazily under the sheet lock. It exists on style,
  `@font-face`, `@page`, margin and nested-declarations rules. You can
  assign text (`rule.style = ''` for Kobo) or a `DeclarationBlock`, which is
  copied if another rule owns it. Errors in assigned text stay in the
  block's `errors`, since their positions refer to that text and not to the
  sheet.
- **`parse_style()`** (P2) drops nested rules with an error. A top level `}`
  ends the input (P5).
- **Modifications** mark the rule, its ancestors and the sheet as modified.
  `preserve` serialization then refuses those objects until milestone 4
  provides pretty output. Unmodified siblings are still written from
  source.
- **Performance:**
  - Parsing the 1.55 MB calibre-style sheet and reading the name, value and
    priority of all 53k effective properties takes about 145 ms, against
    about 50 ms for the same work on the syntax tree and 7.4 s for
    css_parser.
  - Two fixes got there: `MutableSequence` iteration (`__getitem__` once per
    item) in the hot effective-view loop, and per-item adoption when blocks
    are built.
  - The plan's target of under 100 ms is left to milestone 8.

Notes from milestone 1:
- **Layout.** The package is `src/kgcss/cssom/`. Top-level `kgcss` exports
  the CSSOM, and the syntax and tokenizer layers moved to `kgcss.syntax` and
  `kgcss.tokenizer` (D2). The old `kgcss/serialize.py` became
  `kgcss/escaping.py`, because importing a submodule named `serialize`
  rebinds the package attribute and shadowed the new `serialize()`
  function.
- **`ParseError`** can now be constructed from Python
  (`ParseError(kind, message, offset, line, column)`), so the CSSOM reports
  its own problems in the same way. The new error kinds are
  `misplaced-rule`, for @charset, @import and @namespace in the wrong place
  (P5), plus `invalid-rule` and `invalid-declaration` for rules and
  declarations in the wrong context.
- **Errors are reported in source order.** The batch from the initial parse
  is sorted before it is logged. Errors from lazily parsed blocks are
  reported when the block is first accessed, and only syntax-level errors
  are reported then, because tokenizer errors were already reported for the
  whole sheet.
- **What is lazy.**
  - Style, `@page`, `@font-face` and margin rules keep their syntax node and
    parse their block on first access, under the sheet's `RLock`. That
    includes the nested rules of style rules.
  - `StyleRule.selector_text` is also read from the node on first access.
  - Grouping rules build their children eagerly.
  - Parsed rules keep their syntax node as their source, which has `pos`,
    `end`, `line`, `column` and `source_text`. `Source` is only used for
    nested declarations, which span several nodes.
- **Nesting (D8).** Declarations after a nested rule become
  `NestedDeclarationsRule`s. Inside grouping rules that are themselves
  inside style rules, declarations are valid nested declarations. In
  grouping rules at the top level they are dropped with
  `invalid-declaration`.
- **`preserve` output.** For an unmodified sheet it is exactly the source,
  including rules that were dropped. Nothing is lost in editors. A property
  test checks this.
- **Performance.**
  - A quadratic loop in the top-level placement check made a 0.8 MB sheet
    take seconds. It is fixed, and a scaling test now guards against
    regressions of that kind.
  - `RuleType` uses identity hashing, because `Enum.__hash__` is Python
    code.
  - The CSSOM parse of the 1.55 MB calibre-style sheet takes 17-26 ms
    (noisy machine), against 6 ms for the syntax tree alone. Materializing
    every nested rule and selector takes about 100 ms.
- **Packaging.** `pyproject.toml` used `packages = ["kgcss"]`, so wheels
  silently left out `kgcss.cssom`. The editable install hid this, and the
  wheel based free-threaded run found it. It now uses package discovery.

Each milestone ends with ruff, mypy and pytest passing, regular and
free-threaded, with hypothesis properties for its invariants and benchmark
numbers where relevant.

1. **Read-only sheet and rules.**
   - Build `Stylesheet`, `RuleList`, every rule class, `Comment`, parent
     links, positions, `RuleType`, `iter_rules()` and
     `browser_index_path()` from the plan 01 trees.
   - Do the P5 recovery for `@charset`, `@import` and `@namespace`.
   - `preserve` serialization of an unmodified sheet, which must return the
     source exactly (property test).
   - Move the syntax functions to `kgcss.syntax` (§2.4).
2. **Declaration blocks and properties.**
   - Effective and full views, get, set, remove, and operations on single
     instances; `parse_style()`.
   - `Property` construction and validation, renaming, literal priority.
   - Lazy blocks under the sheet lock, with concurrency tests.
3. **Values.** `PropertyValue` and its components, nested functions,
   `unicode-range`, and structured removal.
4. **Pretty serialization.**
   - Options, S3 normalisation, single-line attribute-safe output,
     `encode()`.
   - Property tests for idempotence. Port calibre's expected outputs
     (§16: the kepubify round trip, `polish/tests/cascade.py`,
     `structure.py:661-663`, the `normalize_css` and
     `css_transform_rules` tests) as tests.
5. **Mutation.**
   - Assigning `css_text` at every level.
   - `insert_rule()` and `add()` placement, copying of foreign rules,
     `delete_rule()`.
   - Selector lists and `selector_text`, media lists, the namespace map,
     `copy()`.
   - Dirty tracking for `preserve` output.
6. **URLs.** `iter_urls()`, `replace_urls()`, `replace_urls_in_text()` and
   `remove_url_values()`, with the U3 coverage tests.
7. **@import.** Fetcher, lazy and eager loading, recursion guard,
   `resolve_imports()`, per-call logging.
8. **Benchmarks and tuning.**
   - Add CSSOM workloads to `bench/bench_parse.py`: parse to CSSOM, read
     every value, rewrite every URL, serialize. Compare with css_parser
     `parseString()` and `cssText`.
   - Targets on the 1.55 MB sheet: under 100 ms to parse and read
     everything, and under 100 ms to serialize. css_parser takes about 7 s
     to parse.
   - Move hot spots to C only with A/B evidence.
   - Optionally, the `kgcss.compat` facade (D1).

---

## 9. Decisions

All the recommendations below were accepted on 2026-11-25.

| # | Question | Decision |
|---|---|---|
| D1 | A css_parser-compatible camelCase facade for Edit book plugins, or document the break? | Pythonic core only. Add a thin `kgcss.compat` as an optional last step, if plugin compatibility matters to you. |
| D2 | Top-level `kgcss.parse_stylesheet()` returns the CSSOM, and the syntax tree moves to `kgcss.syntax`? | Yes (§2.4) |
| D3 | `block['x']` for a missing property: `KeyError` (Pythonic) or `''` (css_parser)? | `KeyError`, plus `get_value()` returning `''` |
| D4 | Inserting a rule owned by another sheet: copy it, or move it? | Copy, and return the copy |
| D5 | Keep the S3 normalisations (numbers, colors, quotes, `rgb()` spacing)? | Keep them all, deterministic and switchable with `normalize=False` |
| D6 | Empty rules on output (Kobo relies on them being dropped) | Drop by default (`drop_empty_rules=True`) |
| D7 | IE hacks such as `*zoom: 1` (P16) | Drop and log them, as the spec and calibre do today. `filter: progid:...` is a valid declaration and is already kept. |
| D8 | Nested style rules (CSS nesting) | Support them in the object model and serializer, since the parser already handles them |
| D9 | Order of the remaining plans | 03 selectors (parsing, specificity, matching), then media queries and colors, then fonts and property grammar |

Quirks from requirements §18 that this plan fixes:

| Quirk | Fixed by |
|---|---|
| `--x` and `var()` dropped | the parser keeps them (plan 01), and they are written out verbatim |
| Unknown at-rules corrupted | kept verbatim (`UnknownAtRule`), or as grouping rules (P6) |
| Selector escapes decoded | §4.3 |
| URL helpers missing `@page`, `@supports` and nested functions | §5 |
| Jump-to-class index mismatch | one parser, `browser_index_path()`, positions on rules (P13) |
| Editor tab width ignored | per-call `indent` |
| Sheet `css_text` bytes, block text str | always `str`, plus `encode()` |
| Invalid media text mangled | text kept as written |
| Multi-line or double-quoted text written into `style=""` | `single_line=True` |

Not fixed here: `!=`, `:lang()` and the case of media types belong to
later plans. The `stats`, `fonts` and polish fixes belong to calibre.

---

## Appendix A: css_parser names to kgcss names

| css_parser (calibre today) | kgcss |
|---|---|
| `css_parser.parseString(text)`, `CSSParser(...).parseString` | `parse_stylesheet(text, log=...)` |
| `parseStyle(text)` | `parse_style(text)` |
| `sheet.cssRules`, `rule.cssRules` | `sheet.rules`, `rule.rules` |
| `cssRules.rulesOfType(CSSRule.STYLE_RULE)` | `sheet.rules.of_type(RuleType.STYLE)` |
| `CSSRule.STYLE_RULE`, `rule.type` | `RuleType.STYLE`, `rule.type` |
| `sheet.insertRule(r, i)`, `deleteRule(i)` | `sheet.insert_rule(r, i)`, `sheet.delete_rule(i)` |
| `rule.parentStyleSheet`, `rule.parentRule` | `rule.parent_style_sheet`, `rule.parent_rule` |
| `rule.cssText`, `sheet.cssText` (bytes) | `rule.css_text`, `sheet.css_text` (str), `sheet.encode()` |
| `rule.selectorText`, `rule.selectorList` | `rule.selector_text`, `rule.selectors` |
| `selector.specificity` (4-tuple) | `selector.specificity`, the form defined in plan 03 |
| `rule.media.mediaText` | `rule.media.media_text` |
| `rule.style`, `style.getPropertyValue(n)` | `rule.style`, `style.get_value(n)` |
| `style.getProperty(n)`, `style.setProperty(n, v, p)` | `style.get_property(n)`, `style.set(n, v, p)` |
| `style.removeProperty(n)` | `style.remove(n)` |
| `style.getProperties(all=True)`, `style.seq` | `style.items` |
| `style.getCssText(separator=' ')` | `style.to_css(single_line=True)` |
| `prop.propertyValue`, `prop.value`, `prop.priority` | `prop.parsed_value`, `prop.value`, `prop.priority` |
| `prop.literalpriority` | `prop.literal_priority` |
| `value.cssText`, `component.uri`, `component.dimension` | `value.css_text`, `component.url`, `component.unit` |
| `css_parser.getUrls(sheet)`, `replaceUrls(sheet, fn)` | `iter_urls(sheet)`, `replace_urls(sheet, fn)` |
| `css_parser.resolveImports(sheet)` | `resolve_imports(sheet)` |
| `ser.prefs.indent = ...` (global) | `SerializeOptions(indent=...)` (per call) |
| `css_parser.log`, `loglevel=` | `log=` per call, plus `.errors` |
