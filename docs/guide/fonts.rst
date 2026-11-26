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

``parse_font(serialize_font(font)) == font`` for what ``parse_font`` returns,
apart from the result for a system font, and with generic family names
lowercased.

``parse_font`` follows the grammar of the shorthand in CSS Fonts 4::

   [ [ <'font-style'> || <font-variant-css2> || <'font-weight'> || <font-width-css3> ]?
     <'font-size'> [ / <'line-height'> ]? <'font-family'># ] | <system-font-family-name>

So the size and the family are required, a line height is only read after a
``/``, and everything after the size and line height is the family list. A
``normal`` before the size sets the first of style, variant, weight and
stretch that nothing else sets. A value that does not match gives ``{}``, as
CSS ignores the whole declaration. So does a value with ``var()``, which is
not known before it is computed.

.. doctest::

   >>> kgcss.parse_font('oblique 10deg normal 450 12px bold serif')
   {'font-style': 'oblique 10deg', 'font-variant': 'normal', 'font-weight': '450', 'font-size': '12px', 'font-family': ['bold serif']}
   >>> kgcss.parse_font('12px normal')
   {'font-size': '12px', 'font-family': ['normal']}
   >>> kgcss.parse_font('12px inherit'), kgcss.parse_font('serif'), kgcss.parse_font('12px 1.4 serif')
   ({}, {}, {})

A value that is just a CSS-wide keyword is not a list of family names and not
a font. ``parse_font`` gives ``{}`` for it, and ``parse_font_family``, which
does not check the grammar, cannot tell ``inherit`` from ``"inherit"``. Check
the text against :data:`kgcss.CSS_WIDE_KEYWORDS` first:

.. doctest::

   >>> kgcss.parse_font_family('inherit'), kgcss.parse_font_family('"inherit"')
   (['inherit'], ['inherit'])
   >>> sorted(kgcss.CSS_WIDE_KEYWORDS)
   ['inherit', 'initial', 'revert', 'revert-layer', 'unset']
   >>> ' Inherit'.strip().lower() in kgcss.CSS_WIDE_KEYWORDS
   True
