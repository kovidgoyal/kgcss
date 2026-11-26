Declarations and values
=======================

Declaration blocks
------------------

The declarations of a style rule, ``@font-face``, ``@page`` or a ``style``
attribute are a :class:`kgcss.DeclarationBlock`. It has two views:

- the **effective view**, as a browser sees the block: one
  :class:`kgcss.Property` per name, where ``!important`` beats later normal
  declarations, and otherwise the last one wins. Iteration, ``len()``,
  ``keys()``, ``block[name]``, ``get_value()`` and ``get_property()`` use it;
- the **full view**, ``block.items``: every declaration and comment, in
  order, duplicates included.

.. doctest::

   >>> import kgcss
   >>> block = kgcss.parse_style('color: red !important; color: blue; /* note */ margin: 0')
   >>> block['color'], block.keys()
   ('red', ['color', 'margin'])
   >>> [type(item).__name__ for item in block.items]
   ['Property', 'Property', 'Comment', 'Property']
   >>> block.get_value('padding'), block.get_property('padding')
   ('', None)

``set()`` changes a property in place, or adds it; ``remove()`` removes every
declaration of a name and returns the old value, or ``''``:

.. doctest::

   >>> prop = block.set('margin', '1em', 'important')
   >>> block.remove('color'), block.remove('color')
   ('red', '')
   >>> print(block.css_text)
   /* note */
   margin: 1em !important;
   >>> block.to_css(single_line=True)
   'margin: 1em !important'

The single-line form is for ``style=""`` attributes: it is safe inside a
double-quoted HTML attribute, as strings are written with single quotes and
newlines escaped, and it leaves out comments.

``remove_declaration()`` removes one particular declaration among duplicates,
and ``replace_declaration()`` replaces one with several in place, for
expanding a shorthand where it was.

Properties
----------

A :class:`kgcss.Property` has a ``name`` (lowercased, except for custom
properties), a ``value``, a ``priority`` (``'important'`` or ``''``) and the
priority as written, ``literal_priority``. All can be changed. Properties
compare by identity, so they can be kept in sets and dicts.

.. doctest::

   >>> prop = kgcss.parse_style('Color: red !IMPORTANT').get_property('color')
   >>> prop.name, prop.priority, prop.literal_priority
   ('color', 'important', '!IMPORTANT')
   >>> kgcss.Property('margin', '0', 'important').value
   '0'
   >>> kgcss.Property('color', 'red; }')
   Traceback (most recent call last):
   ...
   kgcss.cssom.exceptions.InvalidValueError: Invalid value for color: 'red; }'

A value is always checked so that it cannot break the text around it when
written. With ``validate=True`` it must also be free of every parse error.

Values
------

``prop.parsed_value`` is a :class:`kgcss.PropertyValue`: an immutable
sequence of components, without separators and whitespace. ``.items`` has the
separators too. Each component has a ``type``, a typed ``value`` and its
exact ``css_text``:

.. doctest::

   >>> value = kgcss.parse_value('italic 700 12px/1.2 "My Font", serif')
   >>> [(c.type, c.value) for c in value]
   [('ident', 'italic'), ('number', 700), ('dimension', 12), ('number', 1.2), ('string', 'My Font'), ('ident', 'serif')]
   >>> value[2].unit, [c.css_text for c in value.items if c.type == 'separator']
   ('px', ['/', ','])
   >>> image = kgcss.parse_value('image-set(url("a b.png") 1x)')[0]
   >>> image.name, image.arguments[0].url
   ('image-set', 'a b.png')

The component classes are in :mod:`kgcss.cssom.values`:
:class:`~kgcss.cssom.values.Ident`, :class:`~kgcss.cssom.values.String`,
:class:`~kgcss.cssom.values.Number`, :class:`~kgcss.cssom.values.Percentage`,
:class:`~kgcss.cssom.values.Dimension`, :class:`~kgcss.cssom.values.Url`,
:class:`~kgcss.cssom.values.Function`, :class:`~kgcss.cssom.values.Hash`,
:class:`~kgcss.cssom.values.UnicodeRange` and a few more. Integers stay
``int``, so ``700`` is not ``700.0``.

``value`` is typed differently by each class, so it is not an attribute of
:class:`kgcss.Component`. To read a keyword or a string without checking the
class, use ``string_value``: the keyword of an ``Ident``, the unquoted text of a
``String`` and ``None`` for anything else:

.. doctest::

   >>> [c.string_value for c in kgcss.parse_value('bold "My Font" 12px')]
   ['bold', 'My Font', None]

To change a value, assign ``prop.value``. :meth:`kgcss.Property.remove_component`
removes one component with its separator:

.. doctest::

   >>> block = kgcss.parse_style('background: black url(x.png) fixed')
   >>> block.get_property('background').remove_component(1)
   >>> block['background']
   'black fixed'

Custom properties and ``var()``
-------------------------------

Custom properties, ``var()``, ``env()``, ``calc()`` and the like are kept
exactly as written, and their values are never normalized.
