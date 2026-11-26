Changelog
=========

1.1.0
-----

- :data:`kgcss.CSS_WIDE_KEYWORDS`, the set of CSS-wide keywords, is now
  exported.
- :func:`kgcss.parse_font` follows the grammar of the ``font`` shorthand in
  CSS Fonts 4, where it used to read what it could, as tinycss did. A value
  that does not match the grammar now gives ``{}``: one without a size or a
  family (``serif``), with a line height after the size but no ``/``
  (``12px 1.4 serif``), or with a CSS-wide keyword in it (``12px inherit``).
  Everything after the size and line height is the family list, so
  ``12px bold serif`` is the family ``bold serif``. It also reads the CSS
  Fonts 4 values: ``oblique`` with an angle, ``left``, ``right``, any weight
  from 1 to 1000, ``xxx-large``, ``math`` and math functions.
- :func:`kgcss.matches` types math functions by their contents, so
  ``calc(10deg)`` is not a length. It accepts ``left`` and ``right`` for
  ``font-style`` and checks that an ``oblique`` angle is within 90 degrees.

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
