Fonts
=====

Four helpers read and write ``font-family`` lists and the ``font``
shorthand.

:func:`kgcss.parse_font_family` gives the names of a ``font-family`` value:
strings unquoted and unescaped, runs of identifiers joined with one space.
:func:`kgcss.serialize_font_family` writes names back, quoting the ones that
need it and lowercasing the generic families.

.. doctest::

   >>> import kgcss
   >>> kgcss.parse_font_family('"Helvetica Neue",  Arial   Black, Hawaii\\ 5-0, SANS-SERIF')
   ['Helvetica Neue', 'Arial Black', 'Hawaii 5-0', 'SANS-SERIF']
   >>> kgcss.serialize_font_family(['Helvetica Neue', 'Arial', 'SansSerif', 'inherit'])
   '"Helvetica Neue", Arial, sans-serif, "inherit"'

:func:`kgcss.parse_font` gives the longhands that a value of the ``font``
shorthand sets, as a :class:`kgcss.FontDict`: a dict with only the keys the
value sets. :func:`kgcss.serialize_font` writes one back.

.. doctest::

   >>> font = kgcss.parse_font('italic bold 12pt/1.4 "Helvetica Neue", serif')
   >>> font
   {'font-style': 'italic', 'font-weight': 'bold', 'font-size': '12pt', 'line-height': '1.4', 'font-family': ['Helvetica Neue', 'serif']}
   >>> kgcss.serialize_font(font)
   'italic bold 12pt/1.4 "Helvetica Neue", serif'
   >>> kgcss.parse_font('caption')
   {'font-family': ['sans-serif']}

``parse_font(serialize_font(font)) == font`` for what ``parse_font`` returns.
