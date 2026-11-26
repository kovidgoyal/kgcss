Selectors and matching
======================

Selectors in sheets
-------------------

``rule.selector_text`` is the selector list of a style rule as text, which
can be assigned. ``rule.selectors`` is a mutable :class:`kgcss.SelectorList`
of :class:`kgcss.Selector` objects, which accept attributes of your own, for
bookkeeping. Selector text keeps escapes, comments and case; whitespace is
normalized.

.. doctest::

   >>> import kgcss
   >>> rule = kgcss.parse_stylesheet('a.x , #\\31 23>p::before { color: red }').rules[0]
   >>> rule.selector_text
   'a.x, #\\31 23 > p::before'
   >>> [(s.text, s.specificity) for s in rule.selectors]
   [('a.x', (0, 1, 1)), ('#\\31 23 > p::before', (1, 0, 2))]
   >>> rule.selectors[1].pseudo_element.name
   'before'

Specificity is the ``(ids, classes, types)`` triple of Selectors Level 4:
``:is()``, ``:not()`` and ``:has()`` count their most specific argument,
``:where()`` counts nothing, and ``:nth-child(An+B of S)`` adds ``S``.

Parsing selectors
-----------------

:func:`kgcss.parse_selectors` parses a selector list by itself, and caches
what it parsed. It raises :class:`kgcss.SelectorSyntaxError`, a
:class:`ValueError`, with the position of the problem:

.. doctest::

   >>> selectors = kgcss.parse_selectors(':is(.a, #b) > li:nth-child(2n+1 of .item), p:has(> img)')
   >>> [s.specificity for s in selectors]
   [(1, 2, 1), (0, 0, 2)]
   >>> kgcss.parse_selectors('a[href!=x]')
   Traceback (most recent call last):
   ...
   kgcss.cssom.exceptions.SelectorSyntaxError: Expected an attribute operator, got !=

``selector.parsed`` is the tree of the selector, made of the classes in
:mod:`kgcss.selectors`, and ``selector.walk()`` goes through all its simple
selectors, at any depth. There are queries for the common questions:

.. doctest::

   >>> s = selectors[0]
   >>> sorted(s.class_names()), s.ids(), sorted(s.pseudo_classes())
   (['a', 'item'], {'b'}, ['is', 'nth-child'])
   >>> kgcss.parse_selectors('a:hover')[0].has_pseudo(kgcss.INAPPROPRIATE_PSEUDO_CLASSES)
   True

A sheet can contain selectors that do not parse: their rules are kept, as
written, and :func:`kgcss.invalid_selectors` lists them.

Renaming classes and ids
------------------------

:func:`kgcss.rename_classes` and :func:`kgcss.rename_ids` rename class names
and ids, at any depth, and change nothing else in the text. ``rename_ids``
also renames ``[id=...]`` attribute selectors. Both take a mapping or a
function. ``rule.selectors.rename_classes()`` does the same in place.

.. doctest::

   >>> kgcss.rename_classes('a.old  > :not(.old) /* .old */ .older', {'old': 'new'})
   'a.new  > :not(.new) /* .old */ .older'
   >>> kgcss.rename_ids('#x, [id="x"]', {'x': '1st'})
   '#\\31 st, [id="1st"]'

Matching
--------

:class:`kgcss.Select` matches selectors against a tree of elements: lxml
trees, ``xml.etree.ElementTree`` trees, or anything with the same interface.
It takes a snapshot of the tree, indexes it, and matches in C, without the
GIL, so a ``Select`` made once answers many selectors quickly. Calling it
gives a generator of the matching elements, in document order.

.. doctest::

   >>> from xml.etree.ElementTree import fromstring
   >>> root = fromstring('''<html><body>
   ...   <div class="note"><p lang="en-GB">One</p><p>Two</p></div>
   ...   <p id="last">Three</p>
   ... </body></html>''')
   >>> select = kgcss.Select(root)
   >>> [p.text for p in select('div.note > p:first-child, #last')]
   ['One', 'Three']
   >>> [p.text for p in select('p:lang(en)')]
   ['One']
   >>> [e.tag for e in select('div:has(> p:nth-child(2))')]
   ['div']
   >>> select.has_matches('p:only-child')
   False
   >>> select.matches(root.find('body/p'), 'body > p')
   True

``root=`` limits the results to the elements under an element, while the
selectors still see the whole document. Matching is HTML-style: tag and
attribute names are ASCII case-insensitive, and namespaces are ignored. Ids
and classes are case-sensitive, as in standards mode; ``quirks_mode=True``
matches them case-insensitively, as browsers do in quirks mode.

Pseudo-classes that depend on user interaction or rendering, such as
``:hover``, cannot be matched against a document: they raise
:class:`kgcss.ExpressionError`, unless the ``Select`` is made with
``ignore_inappropriate_pseudo_classes=True``, in which case they match, so
that ``p:hover`` selects every ``p``. Errors are raised when the generator is
first advanced.

Do not change the tree while a ``Select`` made from it is in use: make a new
one.
