URLs and @import
================

Finding and rewriting URLs
--------------------------

:func:`kgcss.iter_urls` gives every URL of a sheet, rule or declaration
block: ``@import`` targets and ``url()`` values in every declaration,
including ``@font-face``, ``@page`` and its margin boxes, rules nested in
``@media``, ``@supports`` and other grouping rules, unknown at-rules, and
``url()`` inside functions such as ``image-set()``. URLs are given unquoted
and unescaped, ``data:`` URLs included.

:func:`kgcss.replace_urls` calls a function for each URL and puts its result in
place, quoting it when it needs quotes. A URL for which the function returns
``None`` is left as it is. It returns whether anything changed,
and marks only what changed as modified, so a ``preserve`` serialization
changes only those bytes.

.. doctest::

   >>> import kgcss
   >>> sheet = kgcss.parse_stylesheet('''
   ... @import "base.css";
   ... @font-face { src: url(fonts/a.woff2) format("woff2") }
   ... @supports (x: y) { .hero { background: image-set(url(hero.png) 1x, url("hero@2x.png") 2x) } }
   ... ''')
   >>> list(kgcss.iter_urls(sheet))
   ['base.css', 'fonts/a.woff2', 'hero.png', 'hero@2x.png']
   >>> kgcss.replace_urls(sheet, lambda url: '../' + url, ignore_imports=True)
   True
   >>> list(kgcss.iter_urls(sheet))
   ['base.css', '../fonts/a.woff2', '../hero.png', '../hero@2x.png']

When all you have is text, such as the contents of a ``<style>`` element or a
``style`` attribute, :func:`kgcss.replace_urls_in_text` rewrites the URLs at
the token level, without building the object model, and keeps every other
byte as it was:

.. doctest::

   >>> kgcss.replace_urls_in_text('a { b: url(x.png) }  /*  keep */', lambda url: 'images/' + url)
   'a { b: url(images/x.png) }  /*  keep */'

:func:`kgcss.remove_url_values` removes the ``url()`` values for which a
function returns true, and the declarations that are left empty.

@import
-------

To load imported sheets, give :func:`kgcss.parse_stylesheet` a ``fetcher``.
It is called with each URL resolved against the sheet's ``href``, and returns
``(encoding, text)``, where text is ``str`` or ``bytes``, or ``None`` if the
sheet does not exist. Imports are loaded on first access to
``rule.style_sheet``, or while parsing with ``eager_imports=True``. Loops of
imports are detected.

.. doctest::

   >>> sheets = {'https://example.com/base.css': 'body { margin: 0 }'}
   >>> sheet = kgcss.parse_stylesheet('@import "base.css" print; a { b: c }',
   ...                                href='https://example.com/main.css',
   ...                                fetcher=lambda url: ('utf-8', sheets[url]) if url in sheets else None)
   >>> sheet.rules[0].style_sheet.rules[0].selector_text
   'body'
   >>> [rule.selector_text for rule in sheet.iter_rules(kgcss.RuleType.STYLE, follow_imports=True)]
   ['body', 'a']

:func:`kgcss.resolve_imports` gives a copy of a sheet with its imports
inlined: wrapped in ``@media``, ``@supports`` or ``@layer`` for the
conditions of their ``@import``, marked with comments, and with relative URLs
rewritten to mean the same thing from their new place:

.. doctest::

   >>> print(kgcss.resolve_imports(sheet).css_text)
   /* START @import https://example.com/base.css */
   @media print {
     body {
       margin: 0;
     }
   }
   /* END @import https://example.com/base.css */
   a {
     b: c;
   }
