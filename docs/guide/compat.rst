The css_parser-style facade
===========================

:mod:`kgcss.compat` gives css_parser-style objects for code written for
css_parser or cssutils: ``cssRules``, ``rulesOfType()``, ``selectorText``,
``getPropertyValue()``, ``setProperty()``, ``getCssText()``, the
``CSSRule`` type constants, ``parseString()``, ``parseStyle()`` and so on.

Its objects are live views of kgcss objects: changing a view changes the
kgcss object under it, and changes to the kgcss object show in the view.
:func:`kgcss.compat.wrap` gives the view of a kgcss object, and
:func:`kgcss.compat.unwrap` the kgcss object of a view. A program can use the
Pythonic API itself and hand views to code that expects css_parser, such as
plugins, without copying anything.

.. doctest::

   >>> import kgcss
   >>> from kgcss.compat import CSSRule, unwrap, wrap
   >>> sheet = kgcss.parse_stylesheet('p { font-size: 12pt } @media print { h1 { font-size: 2em } }')
   >>> view = wrap(sheet)
   >>> for rule in view.cssRules.rulesOfType(CSSRule.STYLE_RULE):
   ...     rule.style.setProperty('font-size', '24pt')
   >>> sheet.rules[0].style['font-size']
   '24pt'
   >>> view.cssRules[1].cssRules[0].selectorText
   'h1'
   >>> view.cssRules[0].style.getCssText(separator=' ')
   'font-size: 24pt'
   >>> unwrap(view) is sheet
   True

As in css_parser, a sheet's ``cssText`` is ``bytes``, in the encoding of its
``@charset`` rule, and specificity is the 4-tuple ``(inline, ids, classes,
types)``. Rules that css_parser did not know, such as ``@supports``, have the
type ``UNKNOWN_RULE``, but their rules are available in ``cssRules``.

The facade does not reproduce css_parser's bugs: custom properties and
``var()`` are kept, escapes in selectors are preserved, and text is written
as kgcss writes it. The single-line form of a declaration block writes strings
with single quotes, so that it is safe in a ``style=""`` attribute.
