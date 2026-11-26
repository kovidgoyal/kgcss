Changelog
=========

Unreleased
----------

- :func:`kgcss.lint` lints stylesheets with rules that behave as stylelint's
  rules of the same names do, parsing them with a port of the PostCSS parser.
  Problems are data (:class:`kgcss.LintProblem`) with their rule, the values
  in their message and their position, fixes are edits of the text
  (:class:`kgcss.TextEdit`), and what PostCSS cannot parse is a
  :class:`kgcss.SyntaxProblem`. Rules take stylelint's secondary options, and
  stylelint's disable comments work. :data:`kgcss.LINT_RULES` lists the rules:
  the 32 that calibre's Check Book uses.

1.2.2
-----

- :meth:`kgcss.Stylesheet.browser_index_path` and
  :meth:`kgcss.Stylesheet.rule_at_browser_index_path` skip the rules that
  browsers leave out of their object model: at-rules they do not know
  (``@-foo-bar {...}``, but not ``@keyframes``, ``@property`` and the like),
  ``@document``, and style rules whose selector list does not parse
  (``*html p {}``). Before, these were counted, so a path from a browser could
  resolve to the wrong rule.

1.2.1
-----

- :func:`kgcss.compat.wrap` is typed by the class of a rule:
  ``wrap(style_rule)`` is a ``CSSStyleRule``, ``wrap(media_rule)`` a
  ``CSSMediaRule``, and :func:`kgcss.compat.unwrap` gives the class back.
- :class:`kgcss.RuleContainer`, the base of every rule that holds rules (with
  ``insert_rule()`` and ``delete_rule()``), is exported.
- :attr:`kgcss.Selector.data` is a dict for a caller's own data about a
  selector, which type checkers accept, unlike attributes of the caller's
  own. :meth:`kgcss.Selector.copy` copies it.
- ``string_value`` of a value component (:class:`kgcss.Component`) is the
  keyword of an ``Ident`` or the unquoted text of a ``String``, and ``None``
  for anything else, so it can be read without checking the class.

1.2.0
-----

- ``@supports`` conditions are parsed and evaluated, with a callback that
  decides which declarations are supported: :func:`kgcss.parse_supports_condition`,
  :func:`kgcss.supports_matches`, :attr:`kgcss.SupportsRule.condition`,
  :meth:`kgcss.SupportsRule.matches`, and for the ``supports()`` of
  ``@import``, :attr:`kgcss.ImportRule.supports_condition` and
  :meth:`kgcss.ImportRule.supports_matches`. See :doc:`guide/supports`.
- :meth:`kgcss.Property.value_to_css` gives the value alone, normalized as in
  a stylesheet (``.50em 0px`` is ``0.5em 0``).
- :func:`kgcss.condense_shorthands` compares values normalized, so
  ``padding-right: 0pt`` and ``padding-top: 0`` are equal, and the shorthand
  is the shortest form: ``padding: 0``, not ``padding: 0 0``.
- A function passed to :func:`kgcss.replace_urls` or
  :func:`kgcss.replace_urls_in_text` can return ``None`` to leave a URL as it
  is. It used to raise ``TypeError``.
- :class:`kgcss.ElementTreeLike`, the protocol of the ElementTrees that
  :class:`kgcss.Select` takes, is exported, for subclasses that repeat its
  overloads.
- Type checkers know the class of the rules that
  :meth:`kgcss.RuleList.of_type` and :meth:`kgcss.Stylesheet.iter_rules`
  return for one rule type: ``of_type(RuleType.STYLE)`` is a
  ``list[StyleRule]``.
- The annotations of the public API can be evaluated at run time, so
  ``help()`` and ``inspect.signature()`` work on everything: names such as
  ``ImportFetcher`` used to be imported only for type checkers.

1.1.1
-----

- :class:`kgcss.Select` is generic over the type of the elements, so type
  checkers know what it returns: a ``Select`` made from an lxml tree is a
  ``Select[lxml.etree._Element]``, one made from an ``xml.etree`` tree a
  ``Select[xml.etree.ElementTree.Element]``. Annotations that name
  ``Select`` may need the element type added, as ``Select[...]``.

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
