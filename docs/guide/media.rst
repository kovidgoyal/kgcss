Media queries
=============

:func:`kgcss.parse_media_queries` parses a media query list, as in
``@media``, ``@import`` or the ``media`` attribute of ``<link>``, following
Media Queries Level 4: media types, ``not`` and ``only``, features, ranges
such as ``(400px < width <= 700px)``, ``and``, ``or`` and ``not`` in
conditions. It never raises: a query that does not parse is kept, with
``valid=False`` and an ``error``, and matches nothing. Results are cached by
text.

.. doctest::

   >>> import kgcss
   >>> q, bad = kgcss.parse_media_queries('only screen and (min-width: 600px) and (orientation: portrait), &')
   >>> q.only, q.media_type, [(f.name, f.operator, f.value.css_text) for f in q.features()]
   (True, 'screen', [('min-width', ':', '600px'), ('orientation', ':', 'portrait')])
   >>> bad.valid, bad.error
   (False, 'expected parentheses, not &')
   >>> kgcss.parse_media_queries('(600px <= width < 900px)')[0].to_css()
   '(width >= 600px) and (width < 900px)'

``@media`` and ``@import`` rules have a :class:`kgcss.MediaList`, which keeps
the text exactly as written, even when it is invalid, and parses it on
demand with ``queries``.

Evaluating
----------

What is true depends on where the CSS is used, so you decide that, with two
functions: one for media types, which says whether a type such as ``screen``
applies, and one for features, which returns ``True``, ``False``, or
``None`` when it does not know. kgcss does the rest, with the three-valued
logic of the specification: ``not`` of unknown is unknown, and a query that
is unknown in the end does not match.

.. doctest::

   >>> screen = {'screen'}.__contains__
   >>> def feature(f):
   ...     if f.name == 'min-width':
   ...         return f.value.value <= 800  # a window 800px wide
   ...     return None  # anything else: unknown
   >>> kgcss.media_matches('screen and (min-width: 600px)', media_type=screen, feature=feature)
   True
   >>> kgcss.media_matches('screen and (hover), print', media_type=screen, feature=feature)
   False
   >>> kgcss.media_matches('not screen and (hover)', media_type=screen, feature=feature)
   False
   >>> kgcss.media_matches('', media_type=screen, feature=feature)
   True

Media types are matched case-insensitively. An empty list matches.
