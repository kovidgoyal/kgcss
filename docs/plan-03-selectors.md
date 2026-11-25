# Plan 03: Selectors (parsing, specificity and matching)

This plan covers the selector layer that replaces calibre's `css_selectors`.
It covers these parts of requirements.md:

- §3.3: the structured side of selectors on style rules (specificity,
  `pseudo_element`, a walkable tree), building on the text-level
  `Selector` and `SelectorList` of plan 02;
- §9.1: parsing, errors and the cached parse;
- §9.2: matching against lxml trees (`Select`);
- §14: indexed and cached matching;
- §15: an import path for recipes and plugins that use
  `css_selectors.Select`;
- §16: the `css_selectors` tests;
- §18: the `!=` and `:lang()` quirks.

**Out of scope:**
- `@page` selectors (`:first`, `:left`) and their specificity, which stay
  text in `PageRule.selector_text`;
- matching against anything but element trees: no live DOM, no styles, so
  UI states such as `:hover` cannot match for real (see §5.5);
- namespace-aware matching, which calibre does not need (requirements §17).

---

## 1. Conventions

The conventions of plan 02 apply: `snake_case`, keyword-only options,
per-call settings, full typing, `@final` classes. In addition:

- The exceptions keep the names calibre already uses, so that callers and
  recipes change as little as possible:
  - `SelectorError(ValueError)`, the base;
  - `SelectorSyntaxError(SelectorError, CSSSyntaxError)`, raised for text
    that is not a selector. It is also a `CSSSyntaxError`, so assigning
    `selector_text` raises a single type that both kinds of caller expect.
    It has `pos`, the offset of the problem in the text;
  - `ExpressionError(SelectorError)`, raised when matching a selector that
    is valid but cannot be evaluated on a tree, such as `:hover` or an
    unknown pseudo-class.
- `Select` keeps the constructor and the call protocol of
  `css_selectors.Select`: `Select(root, ignore_inappropriate_pseudo_classes=False)`,
  then `select(selector, root=None)`, `select.select(...)` and
  `select.has_matches(...)`. With the exception names and
  `INAPPROPRIATE_PSEUDO_CLASSES` exported at the top level, a
  `css_selectors` shim in calibre is a single import line.

---

## 2. Key decisions

### 2.1 How to match: a snapshot of the tree, matched in C

calibre runs every selector of every sheet against every document (the
stylizer and the cascade), so matching speed matters more than anything
else in this plan. `css_selectors` matches set-at-a-time in Python: it
builds maps from tag, id, class and attribute to ordered sets of elements,
then combines the sets for each part of a selector.

A measurement, on a synthetic 4.7k element book chapter with 415 selectors
(400 `.calibreN` class selectors and 15 typical others). The machine was
under heavy unrelated load, so the absolute numbers are rough:

| What | Time |
|---|---|
| `css_selectors`, all 415 selectors, fresh `Select` | 287 ms, about 690 us per selector |
| lxml `root.iter('*')` over the document | 1 ms |
| One Python pass reading each element's tag, id, class, attributes and parent | 10 ms, 2.2 us per element |

**Options:**

**A. Match in Python over lxml**, as `css_selectors` does. No new
machinery, but it is the 690 us per selector we want to leave behind.

**B. Translate selectors to XPath** and let libxml2 evaluate them (the
`cssselect` approach). The evaluation is in C, but XPath cannot express the
HTML-style rules calibre relies on (ASCII case-insensitive names, prefixes
ignored), `:has()` and `of S` need awkward or impossible translations, and
each new selector costs an XPath compile. `css_selectors` already moved
away from this.

**C. Match in C directly on libxml2 nodes**, reaching into lxml's
`_Element` objects. The fastest option, but kgcss would have to link
against libxml2 and match the exact libxml2 that lxml was built with. lxml
wheels link libxml2 statically, so the structure layouts could silently
differ. Too fragile.

**D. Snapshot the tree once, then match in C.** `Select(root)` walks the
document once and records, for each element, in compact C arrays:
- its position in document order (pre-order index) and where its subtree
  ends, so "is a descendant of" is a range check;
- parent, previous and next element siblings, its index among element
  siblings and the number of element siblings;
- the ASCII-lowercased local name, id and classes as interned integers
  (atoms);
- its attributes (local name atom, value);
- a reference to the element object, to return results.

It also builds indexes from tag, id and class atoms to elements. Matching
then never touches Python objects, so it can also release the GIL.

**Recommendation: D.** The snapshot costs about one Python pass over the
tree, which `css_selectors` also pays when it builds its maps; done in C it
should be a few ms for a typical chapter. After that, an indexed selector
such as `.calibre12` costs microseconds. The requirements already say the
tree must not be mutated while a `Select` is alive (9.2), which is exactly
the condition a snapshot needs. The snapshot only needs the ElementTree
protocol (iterating children, `tag`, `items()`, `text`, `tail`), so it
works on `lxml.etree`, `lxml.html` and html5-parser trees, and on the
standard library's `xml.etree` for tests. lxml stays an optional
dependency.

### 2.2 One parser, in C, with a Python tree view

Selectors are parsed in many places: specificity for every selector in the
cascade, validation of `selector_text`, and matching. The 1.55 MB benchmark
sheet alone has 15k selectors, so a Python parser at 20-50 us per selector
would add a large fraction of a second. **Recommendation:** parse in C, from
the tokens of the plan 01 tokenizer (for a parsed sheet, straight from the
rule's prelude tokens, as `normalize_prelude()` already does for the
text). The C parser produces an immutable `CompiledSelector`: the flat form
the matcher uses, plus the specificity, the pseudo-element and the source
span of every part.

The walkable tree of §4.2 is built from it on demand. There is one parser,
so the tree, the specificity and the matcher can never disagree.

Plan 02 found bugs in both the C and the Python versions by comparing them,
and that pairing is kept where it pays: a naive Python **matcher** written
straight from the spec, used only by the tests, is the oracle for the C
matcher (§7). A second parser in Python is not worth its cost; the parser
is checked against the ported `css_selectors` tests, round trips and the
fuzzer instead.

### 2.3 Invalid selectors in parsed sheets

Browsers drop a whole style rule if any of its selectors is invalid.
`css_parser` drops such rules too. Plan 02 keeps every rule whose selector
list is balanced, for fidelity: Edit book must show the user's CSS as
written.

**Recommendation: keep that.** Parsing a sheet does not parse selectors at
all, which keeps sheet parsing fast. An invalid selector raises
`SelectorSyntaxError` when it is first parsed: from `Selector.parsed` or
`specificity`, or during iteration when it is matched. Assigning
`selector_text` or creating `Selector(text)` now checks the full grammar
and raises. A helper, `invalid_selectors(sheet)`, lists the rules whose
selectors do not parse, with positions, for the Reports and Check book
features.

### 2.4 HTML-style matching

As `css_selectors` does today, and as the requirements specify (9.2):
- tag names, attribute names, ids and classes are compared ASCII
  case-insensitively;
- attribute values are compared case-sensitively, unless the selector has
  the `i` flag;
- namespaces are ignored: `{ns}` is stripped from tags and attribute
  names, and namespace prefixes in selectors (`svg|rect`, `[xml|lang]`)
  are accepted and ignored.

Case-insensitive ids and classes are not what standards-mode HTML does,
but calibre's output and tests rely on them (decision D5).

### 2.5 Thread safety

A `Select` is immutable after construction, apart from caches that are
filled on first use (`nth-of-type` positions, languages, the atoms of a
compiled selector). Each cache is filled once, inside a critical section on
the `Select`, and then only read. So one `Select` can be used by several
threads at once, also on free-threaded builds. `CompiledSelector` objects
are immutable and shared through the parse cache.

---

## 3. Entry points

```python
from kgcss import (
    Selector, SelectorList,                    # from plan 02, extended here
    parse_selectors,                            # text -> SelectorList
    Select, INAPPROPRIATE_PSEUDO_CLASSES,
    SelectorError, SelectorSyntaxError, ExpressionError,
)

def parse_selectors(text: str, *, nested: bool = False) -> SelectorList:
    """A new, unattached SelectorList. Raises SelectorSyntaxError. With nested,
    relative selectors (> a) and & are allowed, as in a nested style rule.
    The compiled forms are cached (an LRU cache keyed by text), so parsing
    the same text again is cheap."""

class Select:
    def __init__(self, root: Element | ElementTree, *,
                 ignore_inappropriate_pseudo_classes: bool = False) -> None: ...
    def select(self, selector: str | Selector | SelectorList,
               root: Element | None = None) -> Iterator[Element]: ...
    __call__ = select
    def has_matches(self, selector: str | Selector | SelectorList,
                    root: Element | None = None) -> bool: ...
    def matches(self, element: Element, selector: str | Selector | SelectorList) -> bool: ...
```

`Select(root, ignore_inappropriate_pseudo_classes=...)` may be called
positionally too, as `css_selectors` allows. `Selector` gains:

```python
class Selector:                          # plan 02 class, no __slots__
    text: str                            # as before
    parsed: ComplexSelector              # the tree, §4.2, built on first access
    specificity: tuple[int, int, int]    # §4.3
    pseudo_element: PseudoElement | None
    def resolved(self) -> Selector: ...  # & replaced, for nested rules, §4.5
```

---

## 4. Parsing and the selector model

### 4.1 Grammar

Selectors Level 4, as far as requirements 9.1 lists, plus CSS nesting:

- type selectors and `*`, with optional `ns|`, `*|` and `|` prefixes;
- `#id` and `.class`;
- attribute selectors: `[a]`, `=`, `~=`, `|=`, `^=`, `$=`, `*=`, with the
  `i` and `s` flags. **`!=` is a syntax error** (D6): today it parses and
  then crashes with `KeyError` when matched;
- combinators: descendant, `>`, `+` and `~`;
- pseudo-classes, with any name as far as syntax is concerned:
  - plain ones, `:name`;
  - functional ones whose argument is a selector list: `:is()`, `:where()`,
    `:matches()` (an alias of `:is()`), `:not()` and `:has()`, which takes
    relative selectors;
  - `An+B` ones: `:nth-child()`, `:nth-last-child()`, `:nth-of-type()` and
    `:nth-last-of-type()`, with `of S` for the first two;
  - `:lang()`, with idents or strings;
  - any other functional pseudo-class, kept as its argument tokens;
- pseudo-elements: `::name`, `::name()`, and the legacy single-colon
  `:before`, `:after`, `:first-line` and `:first-letter`, which are
  pseudo-elements, not pseudo-classes;
- the nesting selector `&`, and with `nested=True` relative selectors that
  start with a combinator.

`:is()` and `:where()` take forgiving selector lists: invalid items are
dropped rather than making the whole selector invalid, as Level 4 says.
`:not()` and `:has()` do not forgive.

Unknown pseudo-classes and pseudo-elements parse fine, so that every
selector in real CSS (`::-webkit-scrollbar`, `:focus-visible`) has a tree
and a specificity. Whether one can be matched is decided at match time
(§5.5).

Errors are `SelectorSyntaxError`, with a message and the position of the
problem. The `css_selectors` parse error tests (`tests.py:675-770`) are
ported for the type of error and the position, not the exact message.

### 4.2 The tree

Frozen dataclasses with slots, in `kgcss.selectors`. Every node has `span`,
its `(start, end)` in the selector's text, so edits can splice the text and
keep everything else exactly as written:

| Node | Fields |
|---|---|
| `ComplexSelector` | `parts: tuple[tuple[Combinator \| None, CompoundSelector], ...]`, where the first combinator is `None` unless the selector is relative |
| `CompoundSelector` | `type: TypeSelector \| None`, `subclasses: tuple[SimpleSelector, ...]`, `pseudo_elements: tuple[PseudoElement, ...]` |
| `TypeSelector` | `name` (`'*'` for any), `namespace` (`None` if no prefix) |
| `IdSelector`, `ClassSelector` | `name`, unescaped |
| `AttributeSelector` | `name`, `namespace`, `operator` (`None` for presence), `value`, `case` (`'i'`, `'s'` or `None`) |
| `PseudoClass` | `name`, lowercased |
| `SelectorPseudoClass` | `name` (`is`, `where`, `matches`, `not`, `has`), `selectors: tuple[ComplexSelector, ...]` |
| `NthPseudoClass` | `name`, `a`, `b`, `of: tuple[ComplexSelector, ...] \| None` |
| `LangPseudoClass` | `ranges: tuple[str, ...]` |
| `FunctionalPseudoClass` | `name`, `arguments` (text), for any other function |
| `PseudoElement` | `name`, `arguments` (text or `None`), `legacy` (single colon) |
| `NestingSelector` | (`&`) |
| `Combinator` | `DESCENDANT`, `CHILD`, `NEXT_SIBLING`, `SUBSEQUENT_SIBLING` (an enum) |

`Selector.walk()` yields every simple selector at any depth, including
inside `:is()`, `:not()`, `:has()` and `of S`. That is what
`classes_in_selector` (`polish/css.py:435-467`) needs.

### 4.3 Specificity

A 3-tuple `(ids, classes, types)`, as `css_selectors` returns. Callers that
used `css_parser`'s 4-tuple prepend the inline part themselves (D3). Level 4
rules:
- `:is()`, `:not()` and `:has()` count as their most specific argument;
- `:where()` counts as 0;
- `:nth-child(An+B of S)` counts as a pseudo-class plus the most specific
  selector of S;
- pseudo-elements, including the legacy single-colon ones, count as types,
  so `A:first-letter` is `(0, 0, 2)`;
- `&` counts as the most specific selector of the parent rule, or as
  `:scope`, `(0, 1, 0)`, in a selector that is not attached to a nested
  rule.

Specificity is computed in C while parsing and is cached on the
`Selector`.

### 4.4 Queries and editing (requirements 3.3)

calibre searches selector text with strings and regexes in several places.
Structured replacements:

- `selector.class_names()`, `selector.ids()`, `selector.pseudo_classes()`
  and `selector.pseudo_elements()`: sets of names, at any depth.
- `selector.has_pseudo(names)`: whether the selector uses any of these
  pseudo-classes or pseudo-elements. It replaces the regexes built from
  `INAPPROPRIATE_PSEUDO_CLASSES` (`stylizer.py:394`, `cascade.py:246,259`).
- `rename_classes(selector, mapping)` and `rename_ids(selector, mapping)`,
  for a `Selector`, a `SelectorList` or text. They splice new names into
  the text at the spans of the class or id selectors, so escapes, comments
  and whitespace elsewhere stay exactly as written. That replaces the regex
  rename in `css.py:611-631`. New names are written with
  `serialize_identifier`.

### 4.5 Nested rules

A selector of a nested style rule may contain `&`, or start with a
combinator (`> a`, meaning `& > a`). `selector.resolved()` returns the
equivalent top-level selector: `&` becomes `:is(<parent selectors>)`, and a
relative or `&`-free selector gets `& ` in front first, as CSS Nesting
specifies. `Select` matches the selectors of nested rules in resolved form
automatically. `resolved()` of a selector that is not in a nested rule is
itself.

---

## 5. Matching

### 5.1 The snapshot

`Select(root)` walks the whole document: for an lxml element or tree, the
tree's root (`getroottree().getroot()`), since `:root`, sibling and
ancestor relations need it. For other ElementTree-style trees, the given
root. Non-element nodes (comments, processing instructions, entities) are
skipped, but their tails count as text for `:empty`.

It records what §2.1 D describes. These are computed on first use, as many
documents never need them:
- positions among same-type siblings (`:nth-of-type` and friends);
- each element's language (`:lang()`);
- whether each element has text (`:empty`);
- a map from element object to index, for `root=` and `matches()`.

### 5.2 The algorithm

For each complex selector in the list:
1. **Candidates** come from the rightmost compound selector's most
   selective indexed part: an id, then a class, then a tag, else every
   element. A name that occurs nowhere in the document means no matches,
   without looking at any element.
2. **Each candidate** is checked against the rest of its compound, then
   the combinators are followed right to left. A descendant or subsequent
   sibling combinator backtracks, with a per-selector cache of positions
   already known to fail. That keeps patterns such as `a b c d e` linear,
   rather than exponential, in the tree depth.
3. **Results** are a bitset over document order, so the matches of all the
   selectors in a list are merged and de-duplicated for free, and come out
   in document order. `root=` keeps the range `[index, subtree end)`.

`:has()` looks forward instead: descendants are a range of the
snapshot, children follow `first child` and `next sibling`, and siblings
follow `next sibling`. Its results are cached per element for the
duration of one `select()` call. `of S` and the arguments of `:is()`,
`:not()` and `:has()` are compiled as their own selectors and share the
same machinery.

Atoms in a compiled selector are resolved against the `Select`'s atom
table once per `(Select, selector)` pair, and cached on the `Select`.

### 5.3 Laziness and errors

`select()` returns a generator. Nothing happens until the first `next()`:
that call parses (through the cache), resolves and matches in C, and raises
`SelectorSyntaxError` or `ExpressionError` if there is a problem. The rest
of the iteration only yields elements from the result. This keeps what
callers rely on (9.2): errors are raised during iteration, `next()` with
`StopIteration`, and `elem in select(...)`. Large match operations release
the GIL.

### 5.4 Pseudo-classes

| Pseudo-class | Meaning |
|---|---|
| `:root` | the document's root element |
| `:scope` | the root, or the anchor element inside `:has()` |
| `:any-link` | `a`, `area` and `link` elements with an `href` |
| `:empty` | no element children and no text; comments are allowed |
| `:first-child`, `:last-child`, `:only-child` | by element siblings |
| `:first-of-type`, `:last-of-type`, `:only-of-type` | by element siblings with the same local name |
| `:nth-child()`, `:nth-last-child()` | `An+B`, optionally `of S` |
| `:nth-of-type()`, `:nth-last-of-type()` | `An+B` among same-type siblings |
| `:not()`, `:is()`, `:matches()`, `:where()`, `:has()` | Level 4, with selector lists |
| `:lang()` | the language from the nearest `xml:lang` or `lang` attribute, prefix matching with `-` |

**`:lang()`, a quirk fix (D7):** an element's language comes from its own
`xml:lang`, then its own `lang`, then its parent's language. `xml:lang`
wins when both are present, as HTML specifies. Today `lang` always wins,
so `xml:lang` is ignored.

### 5.5 What cannot be matched

- **The inappropriate pseudo-classes** (`INAPPROPRIATE_PSEUDO_CLASSES`,
  the same set as `css_selectors`), and the pseudo-elements `::before`,
  `::after`, `::first-line` and `::first-letter`: with
  `ignore_inappropriate_pseudo_classes=True` they match every element
  (`p:hover` and `p::before` select every `p`). Without it they raise
  `ExpressionError`.
- **Other pseudo-classes and pseudo-elements** always raise
  `ExpressionError`.
- Inside `:is()`, `:where()` and `:matches()`, an argument that raises
  `ExpressionError` matches nothing instead. `:not()`, `:has()` and
  `of S` let the error through. Both are what `css_selectors` does
  (requirements 9.2).

---

## 6. Performance

### Targets

On the synthetic chapter of §2.1 (4.7k elements), pinned to the fast cores:
- `Select(root)`: under 3 ms;
- the 415 selectors: under 10 ms in total, against about 290 ms for
  `css_selectors`;
- parsing and computing the specificity of all 15k selectors of the
  1.55 MB calibre-style sheet: under 20 ms.

These are first estimates, to be confirmed by the first measurements of
milestones 1 and 3.

### Benchmarks

`bench/bench_select.py`, with `prepare` downloading `css_selectors` from
the pinned calibre commit, as `bench_parse.py` does for tinycss:
- **stylizer:** every selector of a real sheet against a document, with a
  fresh `Select`: the synthetic chapter with the calibre-style sheet, and
  the `css_selectors` Shakespeare document with bootstrap's selectors;
- **construction:** `Select(root)` alone, on documents from 100 to 50k
  elements, to check that it is linear;
- **level 4:** `:is()`, `:has()`, `nth-child(of S)` and deep descendant
  chains.

### A bulk API, only if measurements call for it

The stylizer asks the question the other way round: which rules match
this element? With a fast `select()`, the per-call overhead in Python may
become the bottleneck. If it does, add
`Select.match_all(selectors) -> list[list[int]]`, which matches a whole
sheet's selectors in one C call and returns, per element, the indices of
the selectors that match. Decide in milestone 5, from measurements (D9).

---

## 7. Testing

- **Ported tests:** `css_selectors/tests.py`: the parser (through a
  converter from our tree to their text form), pseudo-elements,
  specificity, parse errors (type and position), select, select level 4,
  and the Shakespeare document. Where a quirk is fixed (`!=`, `:lang()`),
  the expected value changes, with a comment saying why.
- **An oracle matcher:** `tests/selector_oracle.py`, a naive matcher
  written directly from the spec and §5. It walks the lxml tree for each
  element and each selector, recursively, with no indexes or caches. It is
  slow and obviously correct. Hypothesis tests compare `Select` with it on
  random selectors (a strategy generated from the grammar, including
  `:has()`, `of S` and deep combinator chains) and random trees (varying
  depth, siblings, namespaces, case, `lang` and `xml:lang`, comments and
  text for `:empty`).
- **Against `css_selectors`:** the same random inputs, compared with the
  real `css_selectors` from the bench cache, minus a table of intended
  differences. This catches places where the oracle and our reading of the
  spec both differ from what calibre does today.
- **The parser:** for every parsed selector, the tree's spans must cover
  the text exactly. The tree serialized back to text and reparsed must give
  the same tree. And no input may crash: a libFuzzer target for the
  selector parser joins the tokenizer's in `tools/fuzz.py`.
- **Specificity:** the Level 4 table of requirements 3.3, the
  `css_selectors` tests, and nesting.
- **Threads:** concurrent `select()` on one shared `Select`, run with the
  free-threaded build and `KGCSS_STRESS`, so the lazily filled caches are
  exercised.
- The sanitizer suite, as for all C changes.

---

## 8. Milestones

1. **Parsing and specificity.**
   - The C parser, from tokens to `CompiledSelector`, with errors and
     positions. `nested` mode and `&`. Forgiving lists in `:is()` and
     `:where()`.
   - The tree view of §4.2 and `walk()`. Specificity.
   - The CSSOM integration: `Selector.parsed`, `specificity` and
     `pseudo_element`. `selector_text` and `Selector(text)` validate with
     the full grammar. Selectors of parsed rules are parsed from the
     prelude tokens. `parse_selectors()` with its cache.
     `invalid_selectors(sheet)`.
   - The exceptions.
   - Port the parser, pseudo-element, specificity and parse error tests.
     Add the fuzz target.
   - Measure parsing on the 15k selectors of the calibre-style sheet.
2. **Queries and editing.** `class_names()`, `ids()`, `pseudo_classes()`,
   `pseudo_elements()`, `has_pseudo()`, `rename_classes()`,
   `rename_ids()`, and `resolved()` for nested rules. Property tests for
   the renames: only the renamed spans change.
3. **The snapshot and core matching.**
   - `Select`: the snapshot in C, atoms and indexes.
   - Type, universal, id, class and attribute selectors, all operators and
     flags. The four combinators, with the failure cache.
   - `:not()`, `:is()`, `:where()` and `:matches()`; `:root`, `:scope`,
     `:any-link`, `:empty`, and the first, last and only child
     pseudo-classes. The inappropriate pseudo-class rules and
     `ExpressionError`.
   - The lazy generator, `root=`, `has_matches()`, `matches()` and
     `__call__`.
   - The oracle matcher and the differential tests. Port the select tests.
   - Measure `Select(root)` and the basic selectors against the targets.
4. **The remaining pseudo-classes.** The `nth` family with `of S`,
   `:has()`, `:lang()` with the `xml:lang` fix, and the error rules inside
   selector lists. Port the level 4 and Shakespeare tests. The differential
   tests against `css_selectors`. Thread tests.
5. **Benchmarks and tuning.** `bench/bench_select.py` and the `ab_compare`
   integration. Release the GIL for large matches. Decide on `match_all()`
   (D9) from measurements, and build it if needed.

---

## 9. Decisions

| # | Question | Recommendation |
|---|---|---|
| D1 | How to match: in Python over lxml, by translating to XPath, in C on libxml2 nodes, or a snapshot matched in C? | A snapshot matched in C (§2.1) |
| D2 | One parser in C with a tree view, or a parser in Python? | One parser in C; the tree is built from its output on demand (§2.2) |
| D3 | Specificity as a 3-tuple (`css_selectors`) or a 4-tuple with an inline part (`css_parser`)? | 3-tuple; calibre prepends the inline part where it needs it |
| D4 | Rules with selectors that do not parse: keep them (fidelity) or drop them (as browsers and `css_parser` do)? | Keep them, raise when they are parsed or matched, and offer `invalid_selectors(sheet)` (§2.3). Assigning invalid `selector_text` raises. |
| D5 | Ids and classes: ASCII case-insensitive (calibre today) or case-sensitive (standards-mode HTML)? | Case-insensitive, as calibre relies on it |
| D6 | The `!=` attribute operator, which parses today and then crashes when matched | A syntax error, since it is not CSS |
| D7 | `:lang()` ignores `xml:lang` | Fix: `xml:lang`, then `lang`, inherited |
| D8 | lxml as a dependency? | No. `Select` works on any ElementTree-style tree. lxml is in the dev and bench dependency groups for tests. |
| D9 | A bulk `match_all()` for the stylizer | Only if milestone 5 measurements show that per-call overhead dominates |
| D10 | A separate `css_selectors` compatibility module? | No. `Select`, the exceptions and `INAPPROPRIATE_PSEUDO_CLASSES` keep their names and call protocol at the top level of kgcss, so calibre's shim is one import line. Code that walks the old parse tree (`classes_in_selector`, `live_css.py`) is ported to §4.2 and §4.4. |

---

## Appendix A: `css_selectors` names to kgcss names

| `css_selectors` (calibre today) | kgcss |
|---|---|
| `Select(root, ignore_inappropriate_pseudo_classes=...)` | the same |
| `select(selector_text, root=None)`, `select.has_matches(...)` | the same, also taking `Selector` and `SelectorList` |
| `Select(..., default_lang=, dispatch_map=, trace=)` | dropped (requirements 9.2) |
| `select.invalidate_caches()` | dropped: make a new `Select` after changing the tree |
| `parse(text)` | `parse_selectors(text)` |
| `get_parsed_selector(text)` | `parse_selectors(text)`, which is cached |
| `selector.specificity()` (a method) | `selector.specificity` (a property) |
| `selector.pseudo_element` (a name) | `selector.pseudo_element` (a `PseudoElement`, with `.name`) |
| `selector.parsed_tree` | `selector.parsed` |
| walking the tree for classes | `selector.class_names()`, or `walk()` |
| `SelectorError`, `SelectorSyntaxError`, `ExpressionError` | the same names |
| `INAPPROPRIATE_PSEUDO_CLASSES` | the same, and `selector.has_pseudo(...)` |
