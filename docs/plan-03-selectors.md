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
  `css_selectors` shim in calibre is a single import, plus
  `quirks_mode=True` to keep today's case-insensitive ids and classes
  (§2.4).

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
- the ASCII-lowercased local name, and the id and classes (lowercased only
  in quirks mode, §2.4), as interned integers (atoms);
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

As the requirements specify (9.2):
- tag names and attribute names are compared ASCII case-insensitively;
- attribute values are compared case-sensitively, unless the selector has
  the `i` flag;
- namespaces are ignored: `{ns}` is stripped from tags and attribute
  names, and namespace prefixes in selectors (`svg|rect`, `[xml|lang]`)
  are accepted and ignored.

**Ids and classes (D5)** follow HTML: case-sensitive in the default
standards mode, and ASCII case-insensitive with `Select(...,
quirks_mode=True)`, as in an HTML document in quirks mode. `css_selectors`
always matches them case-insensitively, so calibre passes
`quirks_mode=True` wherever it needs today's behaviour. The option decides
how the snapshot stores id and class atoms (exactly, or lowercased) and how
a selector's names are looked up in them, so it costs nothing at match
time.

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
                 ignore_inappropriate_pseudo_classes: bool = False,
                 quirks_mode: bool = False) -> None: ...
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

A 3-tuple `(a, b, c)`: ids, then classes, attributes and pseudo-classes,
then types and pseudo-elements, as Selectors Level 4 defines it (D3).
`css_selectors` returns the same. The 4-tuple of `css_parser` adds a first
part for the style attribute. In the CSS cascade that is a separate level,
not part of specificity, so callers that need it compare it separately.
Level 4 rules:
- `:is()`, `:not()` and `:has()` count as their most specific argument;
- `:where()` counts as 0;
- `:nth-child(An+B of S)` counts as a pseudo-class plus the most specific
  selector of S;
- pseudo-elements, including the legacy single-colon ones, count as types,
  so `A:first-letter` is `(0, 0, 2)`;
- in a nested rule, the specificity is that of `resolved()` (§4.5).
  CSS Nesting defines `&` as `:is()` of the parent rule's selectors,
  specificity included, and implies `&` before selectors that start with a
  combinator or have none. So `> a` in `.p, #q` is `(1, 0, 1)`. In a
  selector that is not in a nested rule, `&` counts as `:scope`,
  `(0, 1, 0)`.

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
  text for `:empty`), in both standards and quirks mode.
- **Against `css_selectors`:** the same random inputs, compared with the
  real `css_selectors` from the bench cache, with `quirks_mode=True`, minus
  a table of intended differences. This catches places where the oracle and our reading of the
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

Status: milestones 1 to 5 are done.

Notes from milestone 5:
- **Results**, `bench/bench_select.py` pinned to the fast cores, best of
  several runs:

  | workload | kgcss ms | `css_selectors` ms |
  |---|---:|---:|
  | construct-1k | 0.93 | - |
  | construct-10k | 10.7 | - |
  | construct-50k | 63.7 | - |
  | stylizer-calibre | 7.9 | 290.6 |
  | stylizer-bootstrap | 6.1 | 37.8 |
  | level4 | 0.53 | 3.98 |

  - Construction is linear, at 1.1 to 1.3 µs per element. On the
    synthetic chapter of 1,500 paragraphs (4.6k elements), it is 4.7 ms,
    over the 3 ms target of §6. Most of that is lxml's: a bare walk with
    `iter()` that reads the parent, tag, text, tail and attributes of each
    element takes 2.7 ms.
  - stylizer-calibre is 37 times faster than `css_selectors`, under the
    10 ms target; it was 22.7 ms at the start of the milestone.
  - `css_selectors` builds its caches lazily, so construction is not
    compared on its own.
- **Matching from the left.** The stylizer sheets are full of selectors
  such as `.calibre5 > p`, whose last compound matches most of the
  document while the left one matches almost nothing. Right-to-left
  matching then tried every `p`: about 54 µs per selector. When the
  candidates of the leftmost compound are fewer than a quarter of those of
  the last one, `run()` now goes from the left: for each element that
  matches the leftmost compound, it walks the elements related by the
  combinator, children, descendants or siblings, and checks the rest of the
  selector right to left from those. Ranges already covered by an earlier
  candidate's descendants or subsequent siblings are skipped, so the work
  stays linear. That selector now takes 0.94 µs. `match_complex()` became
  `match_prefix()`, which matches the compounds up to a limit, so that the
  failure memo serves both directions.
- **Per-call overhead.** `select()` with a string calls the cached
  compiler and `Snapshot.match` directly, without the generic list path:
  a selector that matches nothing went from 1.61 µs to 0.91 µs a call.
  The result bitset is scanned a word at a time; that did not change the
  timings much, as the cost was in the candidate scan.
- **D9: no `match_all()`.** On stylizer-bootstrap, the 3,000 selectors take
  about 2 µs each, of which `Snapshot.match` is about 0.6 µs. A bulk call
  would save the remaining Python overhead, under 1 µs a selector: at most
  about 3 ms a document for a sheet of 3,000 selectors, small next to the
  stylizer's own per-rule work in Python. It is not worth a second API.
- **The GIL** is released around every match: the cost of releasing it is
  tens of nanoseconds, small even for the cheapest calls, so there is no
  size threshold.
- **`ab_compare.py`** takes the select workloads with `--workload`; they
  run once each instead of once per corpus file.
- The comparison tests with `css_selectors` found one more difference: in a
  tree whose root has no namespace, `css_selectors` does not strip
  namespaces from attribute names. The generated trees avoid that case.

Notes from milestone 4:
- **The nth family.**
  - Plain `:nth-child()` and `:nth-last-child()` use the sibling positions
    of the snapshot.
  - With `of S`, a match run computes, once per group of siblings, each
    element's position among the siblings that match S, so a long list of
    siblings stays linear.
  - The of-type positions (and their counts) are filled lazily in the
    snapshot, inside a critical section, as `:empty` was.
- **`:has()`.**
  - Its relative selectors are matched right to left as usual. The
    leftmost compound must then relate to the anchor by the combinator
    before it.
  - Candidates come from the index for the selector's last compound,
    binary-searched to the anchor's range: its subtree for `>` and the
    descendant combinator, the following siblings' subtrees for `+` and
    `~`. Without that, `p:has(~ p.x)` on 1,500 sibling paragraphs took
    25 ms; with it, 0.08 ms.
  - The failure memo is not used there, as it would depend on the anchor.
    Results are cached per anchor element for the run instead.
- **`:lang()`** (D7, fixed).
  - An element's language is its own `xml:lang`, else its own `lang`, else
    its parent's, so `xml:lang` wins on the same element.
  - `lang` counts only without a namespace; `xml:lang` counts in the XML
    namespace and as the literal name lxml.html keeps. `lang=""` is an
    unknown language.
  - Ranges match by extended filtering (RFC 4647), so `"*-CH"` and
    `"de-*-CH"` work.
- **Relative selectors.** A selector that starts with a combinator and is
  not from a nested rule, such as from `parse_selectors('> a',
  nested=True)`, is matched relative to `:scope`, like
  `querySelector()`.
- **More intended differences from `css_selectors`**, found by the new
  comparison test (`tests/test_select_vs_css_selectors.py`):
  - `css_selectors` counts comments and processing instructions as
    siblings for `:first-child`, `:last-child`, `:only-child` and
    `:nth-child()` without `of` (it uses lxml's `parent.index()`).
    Selectors counts only elements, and so does kgcss.
  - `css_selectors` does not always return document order (`div > div` on
    nested divs).
  - `css_selectors` raises `ExpressionError` only when matching reaches
    the part it cannot match, so `div div:hover` raises nothing in a
    document without `div div`. kgcss always raises.
  - `css_selectors` strips namespaces from tags only when the root element
    has one.
- **Testing.**
  - The level 4 and Shakespeare tests are ported, the latter with
    `quirks_mode=True` and its brute force check of `:has()`. The rest of
    `test_select` passes too.
  - The oracle covers the new pseudo-classes. The random strategy was also
    fixed: it had made 80% syntax errors (type selectors in the middle of
    compounds, unquoted wildcard ranges, `:has()` in `:has()`).
  - `bench_parse.py prepare` now downloads `css_selectors` at the pinned
    calibre commit. The comparison test uses it, wrapping each tree in an
    element that no generated selector can match, and is skipped without
    it.
  - The free-threaded thread test covers the lazily filled positions and
    languages and the per-run caches.

Notes from milestone 3:
- **The snapshot** (`csrc/select.c`, `Snapshot`) records each element in
  arrays:
  - parent, previous and next sibling, first child and subtree end;
  - position among its siblings;
  - local name, id and classes as atoms (indices into a dict of strings);
  - attributes, with their local names as atoms.

  Indexes by tag, id and class are built with a counting sort, so their
  lists are in document order. For lxml trees, the walk is one `iter()`
  over the whole document, with parents from `getparent()` checked against
  a stack. That is 2.3 times faster than iterating each element's children,
  which is still the way for other trees, such as `xml.etree`.
- **Matching.**
  - The rightmost compound gets its candidates from the smallest index list
    among its id, classes and type, or from every element in the range of
    `root=`. A name that is in no element means no matches at once.
  - Combinators are followed right to left with an explicit stack, so long
    selectors cannot overflow the C stack.
  - A bitset per complex selector and compound records where the selector
    to the left is known to fail. A test matches chains of 1500 compounds
    on a tree 2000 deep.
  - Results are a bitset over document order, so selector lists come out
    merged, de-duplicated and in order. Matching runs without the GIL.
- **Preparing** a compiled selector for a snapshot resolves its names to
  atoms and its pseudo-classes to codes. It decides what cannot be matched:
  in `:is()`, `:where()` and `:matches()` such arguments match nothing,
  elsewhere they raise `ExpressionError`. The nth family, the of-type
  pseudo-classes, `:has()` and `:lang()` say "not supported yet" until
  milestone 4.
- **`Select`** (`kgcss.matching`):
  - It takes an element anywhere in an lxml tree, or an ElementTree, and
    snapshots the whole document. `:root` is the document's root and
    `:scope` is the element given.
  - `select()` and `__call__` are generators that parse and match on the
    first `next()`, so errors are raised during iteration.
  - `has_matches()` stops at the first match, and `matches(element,
    selector)` tests one element.
  - Selectors of nested rules are matched in their `resolved()` form.
  - `quirks_mode` decides how ids and classes are stored, so it costs
    nothing.
- **Intended differences from `css_selectors`**:
  - the root element matches `:first-child`, `:last-child` and
    `:only-child`, as in Selectors Level 4;
  - `li:root` matches nothing, where `css_selectors` ignored the rest of a
    compound with `:root` and returned the root;
  - ids and classes are case-sensitive unless `quirks_mode=True` (D5).
- **Testing.**
  - `test_select` is ported, with the parts that need milestone 4 in a
    strict expected failure.
  - The oracle (`tests/selector_oracle.py`) is compared with `Select` on
    random trees and selectors: namespaced and plain trees, case variants,
    comments with tails, both modes, with and without ignoring
    pseudo-classes. 20,000 examples agree; 3,100 of them have matches,
    with over 700 each through `+`, `>` and `~`.
  - A thread test shares a `Select` on the free-threaded build, using
    `xml.etree`, as lxml enables the GIL there.
- **Speed** on the synthetic 4.7k element chapter with 415 selectors:

  | What | Time |
  |---|---|
  | `Select(root)` | 4.1 ms (target 3 ms) |
  | the 415 selectors | 2.8 ms (target 10 ms) |
  | both, with parsing | 4.8 ms |
  | `css_selectors`, for comparison | about 290 ms |

  The snapshot misses its target. lxml's own calls (`iter`, `tag`,
  `items`, `getparent`) take 2.7 ms of it, so there is little left to gain
  there. A cache of class attribute values made no measurable difference
  and was left out.
- **A serializer bug found** by the property tests, in both the C writer
  and the Python reference. In `1e1e3`, `1e1` is the number and `e3` the
  unit; rewriting the number as `10` gave `10e3`, which is a number. A unit
  that starts like an exponent now keeps the number as written. The
  writer's differential tests generate such units now.

Notes from milestone 2:
- **Queries** are on the tree (`ComplexSelector`) and on `Selector`:
  - `class_names()`, `ids()`, `pseudo_classes()` and `pseudo_elements()`
    give sets of names at any depth;
  - `has_pseudo(names)` compares ASCII case-insensitively.
    `INAPPROPRIATE_PSEUDO_CLASSES` is exported now, so
    `selector.has_pseudo(INAPPROPRIATE_PSEUDO_CLASSES)` replaces calibre's
    regexes;
  - the legacy pseudo-elements (`:first-line`) count as pseudo-elements.
- **Renames.**
  - `rename_classes(text, mapping)` and `rename_ids(text, mapping)` take a
    mapping or a function, and replace only the spans of the class or id
    selectors. New names are written with `serialize_identifier`, whose hex
    escapes end with a space, so they never absorb what follows.
  - `Selector.rename_classes()`/`rename_ids()` change the selector in place
    and raise for a selector that is not valid.
    `SelectorList.rename_classes()`/`rename_ids()` leave invalid selectors
    as they are, since browsers ignore them. Both return whether anything
    changed.
  - A property test checks that a rename changes only the class names, and
    that the text outside the class selectors is the same.
- **`resolved()`** follows CSS Nesting. `&` becomes
  `:is(<the parent's resolved selectors>)`. A selector that starts with a
  combinator, or has no `&`, gets `:is(...)` and a space in front.
  Selectors outside nested rules resolve to themselves.
- **Specificity of nested selectors, a fix to milestone 1.** It was
  computed directly, which missed the implied `&`: `> a` in `.p, #q` gave
  `(0, 0, 1)` instead of `(1, 0, 1)`. It is now the specificity of
  `resolved()`. That is exactly CSS Nesting's definition: `&` is `:is()`,
  whose forgiving list drops parent selectors that are not valid in its
  place, such as one with `:has()` when `&` is inside `:has()`, or one with
  a pseudo-element. A property test on random nested sheets checks it, and
  that every resolved selector is a valid top-level one.
- Resolved selectors grow with the product of the selector list sizes at
  each nesting level, as `:is()` repeats every parent selector. The matcher
  of milestone 3 can handle `&` natively rather than through the text, if
  deep nesting turns out to matter.

Notes from milestone 1:
- **The parser** is `csrc/selectors.c`, about 1000 lines.
  - It works on the tokenizer's token records and ignores comment tokens.
    Whitespace is the descendant combinator.
  - For each complex selector it produces a `CompiledSelector`: a flat
    pre-order array of nodes, each with its kind, operator, flags, span,
    subtree end, `a` and `b`, and name, value and namespace strings. The
    matcher of milestone 3 will work on this array.
  - Functional pseudo-classes nest at most 32 deep, so hostile input cannot
    exhaust the C stack.
  - Text with tokenizer errors (an unclosed string or comment) or with
    unbalanced closing brackets is rejected.
- **The tree** (`kgcss.selectors`) is built from the nodes on demand. Each
  node class has `to_css()`, which writes a canonical form. Spans are not
  compared, so a tree written with `to_css()` and parsed again equals the
  original, which a property test checks.
- **Differences from `css_selectors`**, all following Selectors Level 4:
  - pseudo-classes may follow a pseudo-element (`a::before:hover`);
  - a `:has()` inside a forgiving `:is()` inside `:has()` is dropped,
    rather than making the whole selector invalid;
  - `!=` and IDs that are not identifiers (`#1`) are syntax errors;
  - An+B follows CSS Syntax exactly, and is stored as `a` and `b` rather
    than as tokens;
  - the arguments of unknown functional pseudo-classes are kept as text,
    whatever tokens they contain.

  The error positions match those of `css_selectors` for most of its error
  tests. Messages differ.
- **The CSSOM.**
  - `Selector` has `parsed`, `specificity`, `pseudo_element` and `walk()`.
  - Its fixed fields are slots, with a `__dict__` slot for caller
    attributes, so the dict is only created when one is set.
  - `selector_text`, `Selector(text)` and `Selector.text` check the full
    grammar. A rule in a nested context accepts relative selectors; a
    selector not in any rule is parsed in that nested mode, which accepts
    every top-level selector too.
  - A rule's `SelectorList` comes from one parse of its whole
    `selector_text`: each selector's text is its span in the list, and it
    keeps its compiled form.
  - `parse_selectors()` and `invalid_selectors()` are new, and so are the
    exceptions.
- **Speed.** On the 1.55 MB calibre-style sheet (18k selectors), parsing
  every selector in C takes 4.5 to 9 ms, 0.25 to 0.5 us each, which meets
  the target of §6. The whole path through the CSSOM, creating the selector
  lists and reading every specificity, takes about 3 us per selector (52 ms
  for the sheet), mostly in creating Python objects. If that matters for the
  stylizer, the lists could be created in C. Walking every rule of a freshly
  parsed sheet costs more than either, as it parses every block to find the
  nested rules.
- **Testing.**
  - The `css_selectors` parser, pseudo-element, specificity and parse error
    tests are ported, comparing trees through a converter to its text form.
  - Property tests on selectors generated from the grammar (checked to
    produce `:has()`, `of S`, `&`, attribute flags and pseudo-elements in
    numbers) and on random text: spans, round trips, error positions.
  - A thread test of lazily created selector lists and parses.
  - A libFuzzer target, `tools/fuzz.py run --target selectors`, which
    compiles the whole extension into the fuzzer. It found one bug: a name
    in an error message, cut at 60 bytes in the middle of a UTF-8 sequence,
    raised `UnicodeDecodeError`. Then 4.4 million runs were clean.
- **Bugs found in earlier milestones** by the thorough property tests, all
  fixed:
  - in preserve mode, an at-rule whose `;` is inside a bracket left open by
    the end of the input (`@x(;`) was written verbatim, and swallowed the
    rules after it once an import was inlined before them;
  - the same for a comment left open after `!important`;
  - the import property test's bound on fetches was too low, and its
    rough selector check now uses the parser.

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
   - `Select`: the snapshot in C, atoms and indexes, and `quirks_mode`.
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

Decided on 2026-11-25.

| # | Question | Decision |
|---|---|---|
| D1 | How to match: in Python over lxml, by translating to XPath, in C on libxml2 nodes, or a snapshot matched in C? | A snapshot matched in C (§2.1) |
| D2 | One parser in C with a tree view, or a parser in Python? | One parser in C; the tree is built from its output on demand (§2.2) |
| D3 | Specificity as a 3-tuple or a 4-tuple with an inline part? | What the CSS spec does: the 3-tuple `(a, b, c)` of Selectors Level 4 (§4.3) |
| D4 | Rules with selectors that do not parse: keep them (fidelity) or drop them (as browsers and `css_parser` do)? | Keep them, raise when they are parsed or matched, and offer `invalid_selectors(sheet)` (§2.3). Assigning invalid `selector_text` raises. |
| D5 | Ids and classes: ASCII case-insensitive (calibre today) or case-sensitive (standards-mode HTML)? | An option per `Select`, `quirks_mode`, defaulting to standards mode: case-sensitive (§2.4) |
| D6 | The `!=` attribute operator, which parses today and then crashes when matched | A syntax error, since it is not CSS |
| D7 | `:lang()` ignores `xml:lang` | Fix: `xml:lang`, then `lang`, inherited |
| D8 | lxml as a dependency? | No. `Select` works on any ElementTree-style tree. lxml is in the dev and bench dependency groups for tests. |
| D9 | A bulk `match_all()` for the stylizer | Only if milestone 5 measurements show that per-call overhead dominates. Milestone 5: not built, the overhead is under 1 µs a selector (§8, notes of milestone 5) |
| D10 | A separate `css_selectors` compatibility module? | No. `Select`, the exceptions and `INAPPROPRIATE_PSEUDO_CLASSES` keep their names and call protocol at the top level of kgcss, so calibre's shim is an import plus `quirks_mode=True`. Code that walks the old parse tree (`classes_in_selector`, `live_css.py`) is ported to §4.2 and §4.4. |

---

## Appendix A: `css_selectors` names to kgcss names

| `css_selectors` (calibre today) | kgcss |
|---|---|
| `Select(root, ignore_inappropriate_pseudo_classes=...)` | the same, plus `quirks_mode=True` for case-insensitive ids and classes |
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
