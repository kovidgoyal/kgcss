kgcss: fast, comprehensive CSS for Python
=========================================

kgcss parses, inspects, changes and writes CSS. It has a mutable object model
in the spirit of the CSSOM, with a Pythonic API, and a C99 backend that makes
it fast. It covers:

- **Parsing** to the CSS Syntax Level 3 specification, from text or bytes,
  with the error recovery browsers use: it never raises on bad CSS.
- **An object model** of sheets, rules, selectors, declarations and values,
  including CSS nesting, ``@supports``, ``@container`` and ``@layer``, that you
  can change and write back, either reformatted or keeping every unchanged
  byte.
- **URLs and @import**: finding and rewriting every URL, loading and
  inlining imported sheets.
- **Selectors Level 4**: parsing, specificity, renaming classes and ids, and
  matching against lxml or any ElementTree-style tree, including ``:has()``,
  ``:is()``, ``:nth-child(... of S)`` and ``:lang()``.
- **Media Queries Level 4**, **CSS Color Level 4**, **font helpers**, a
  **value grammar** for common longhands, and **shorthand** expansion and
  condensation.
- **A css_parser-style facade** for code written for css_parser or cssutils.

It works on regular and free-threaded (no-GIL) Python 3.14 and later.

.. doctest::

   >>> import kgcss
   >>> sheet = kgcss.parse_stylesheet('a { color: #ff0000; margin: 0px } b { color: red }')
   >>> [rule.selector_text for rule in sheet.iter_rules(kgcss.RuleType.STYLE)]
   ['a', 'b']
   >>> print(sheet.css_text)
   a {
     color: #f00;
     margin: 0;
   }
   b {
     color: red;
   }

.. toctree::
   :maxdepth: 2
   :caption: Getting started

   installation
   quickstart

.. toctree::
   :maxdepth: 2
   :caption: Guide

   guide/parsing
   guide/declarations
   guide/serialization
   guide/urls
   guide/selectors
   guide/media
   guide/supports
   guide/colors
   guide/fonts
   guide/shorthands
   guide/linting
   guide/syntax
   guide/compat
   guide/threads

.. toctree::
   :maxdepth: 2
   :caption: Reference

   api
   migrating
   changelog
   development
