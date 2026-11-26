Value grammar and shorthands
============================

The value grammar
-----------------

:func:`kgcss.matches` says whether a value is valid for a longhand property.
It knows the longhands of the shorthands below (``margin-top``,
``border-left-style``, ``list-style-type``, ``font-weight`` and so on, listed
in :data:`kgcss.grammar.PROPERTIES`), and returns ``None`` for other
properties. It follows the current specifications: CSS Values 4 units and
math functions, CSS Color 4 colors, CSS Lists 3 counter styles, CSS Fonts 4
weights. CSS-wide keywords such as ``inherit`` are valid for every property;
a value with ``var()`` is valid for none, as its value is not known before it
is computed.

.. doctest::

   >>> import kgcss
   >>> kgcss.matches('border-top-style', 'dashed'), kgcss.matches('border-top-width', 'dashed')
   (True, False)
   >>> kgcss.matches('padding-left', '-1em'), kgcss.matches('margin-left', '-1em')
   (False, True)
   >>> kgcss.matches('font-weight', '750'), kgcss.matches('color', 'red')
   (True, None)

Shorthands
----------

:func:`kgcss.expand_shorthand` gives the longhands of a ``margin``,
``padding``, ``border``, ``border-top`` (and the other edges),
``border-style``, ``border-width``, ``border-color``, ``list-style`` or
``font`` value: the ones the value sets, and the initial value for the rest,
as CSS does. It returns ``None`` when the value does not match the
shorthand's grammar, or uses ``var()``. :func:`kgcss.longhands` lists the
longhands of a shorthand.

.. doctest::

   >>> kgcss.expand_shorthand('border-left', 'dotted 2px')
   {'border-left-width': '2px', 'border-left-style': 'dotted', 'border-left-color': 'currentcolor'}
   >>> kgcss.expand_shorthand('list-style', 'none inside')
   {'list-style-type': 'none', 'list-style-position': 'inside', 'list-style-image': 'none'}
   >>> kgcss.expand_shorthand('border', 'solid dotted') is None
   True
   >>> kgcss.longhands('padding')
   ('padding-top', 'padding-right', 'padding-bottom', 'padding-left')

``initial=`` replaces the table of initial values, and ``quirks_mode=True``
accepts numbers without units as lengths, as browsers do in quirks mode.

:func:`kgcss.condense_shorthands` does the reverse in a declaration block:
four edges of ``margin`` or ``padding`` become the shortest shorthand, the
parts of a border edge become ``border-<edge>``, and four equal edges become
``border``. It leaves a group alone when that could change what the
declarations mean: when one is ``!important``, when another shorthand in the
block also sets them, or when one value is invalid.

.. doctest::

   >>> block = kgcss.parse_style('margin-top: 1px; margin-right: 2px; margin-bottom: 1px; margin-left: 2px; '
   ...                           'border-top-width: 1px; border-top-style: solid; border-top-color: red')
   >>> kgcss.condense_shorthands(block)
   >>> block.to_css(single_line=True)
   'margin: 1px 2px; border-top: red solid 1px'
