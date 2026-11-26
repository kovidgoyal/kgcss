Changelog
=========

Unreleased
----------

- :data:`kgcss.CSS_WIDE_KEYWORDS`, the set of CSS-wide keywords, is now
  exported.
- :func:`kgcss.parse_font` reads a line height only after a ``/``, as the
  grammar says. Before, ``12px inherit`` gave ``line-height: inherit``. Now
  other words after the size are the family list, and a CSS-wide keyword or a
  number there makes the value invalid.

1.0.0
-----

The first release.

- Tokenizer and parser of CSS Syntax Level 3, in C, with positions and
  error recovery.
- A mutable object model: sheets, every kind of rule including CSS nesting,
  selectors, declarations and values, with pretty and preserving
  serialization.
- URLs and ``@import``: finding, rewriting, loading and inlining.
- Selectors Level 4: parsing, specificity, renaming, and matching in C
  against ElementTree-style trees.
- Media Queries Level 4, CSS Color Level 4, font helpers, a value grammar
  for common longhands, and shorthand expansion and condensation.
- A css_parser-style facade, :mod:`kgcss.compat`.
- Support for free-threaded Python.
