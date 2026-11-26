Colors
======

kgcss parses colors as CSS Color Level 4 defines them, in C: named colors,
hex colors, ``rgb()``, ``hsl()``, ``hwb()``, ``lab()``, ``lch()``,
``oklab()``, ``oklch()`` and ``color()`` with its predefined color spaces,
in the legacy and modern syntaxes, with ``none``, angles and percentages.

:func:`kgcss.parse_rgba` gives a color as sRGB: an :class:`kgcss.RGBA`, a
tuple of four floats from 0 to 1, with colors outside the sRGB gamut clipped.
It returns ``'currentColor'`` for ``currentColor`` in any case, and ``None``
for anything that is not a color it can compute: ``var()``, ``calc()``,
``color-mix()``, relative colors, system colors such as ``Canvas``.

.. doctest::

   >>> import kgcss
   >>> kgcss.parse_rgba('rebeccapurple')
   kgcss.RGBA(red=0.4, green=0.2, blue=0.6, alpha=1.0)
   >>> kgcss.parse_rgba('hsl(120deg 50% 50% / 25%)').alpha
   0.25
   >>> c = kgcss.parse_rgba('oklch(0.628 0.2577 29.23)')
   >>> '#' + ''.join(f'{round(x * 255):02x}' for x in c[:3])
   '#ff0000'
   >>> kgcss.parse_rgba('CurrentColor'), kgcss.parse_rgba('rgb(var(--x))')
   ('currentColor', None)

:func:`kgcss.parse_color` gives the color as specified instead: a
:class:`kgcss.Color` with its color space, its channels, which may be
outside the gamut, and its alpha. ``to_css()`` writes it as CSS Color 4
serializes specified colors, and ``to_rgba()`` converts it.

.. doctest::

   >>> c = kgcss.parse_color('lch(150% -10 400 / none)')
   >>> c
   Color(space='lch', channels=(100.0, 0.0, 40.0), alpha=None)
   >>> c.to_css()
   'lch(100 0 40 / none)'
   >>> kgcss.parse_color('#f008').to_css()
   'rgba(255, 0, 0, 0.533333)'

Both functions accept any text and never raise for it. :func:`kgcss.is_color`
is ``parse_rgba(text) is not None``.
