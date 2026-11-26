Serialization
=============

``css_text`` on a sheet, rule or declaration block, :func:`kgcss.serialize`
and the ``to_css()`` methods write CSS. Options are given per call, with
:class:`kgcss.SerializeOptions`; there are no global settings.

The pretty format
-----------------

By default, the output is the pretty format: each rule on its own lines,
one declaration per line ending with a semicolon, nested rules indented, and
comments kept. Values are normalized where that cannot change their
meaning: numbers (``.5`` to ``0.5``, ``0px`` to ``0``), hex colors
(``#ff0000`` to ``#f00``), the spacing of functions, and quotes (strings use
double quotes). The case of values is kept. Empty rules are dropped.

.. doctest::

   >>> import kgcss
   >>> sheet = kgcss.parse_stylesheet("a{margin:0px .50EM;color:#FF0000;font-family:'A'} p{} @media print{b{x:RGB( 1 ,2,3 )}}")
   >>> print(sheet.css_text)
   a {
     margin: 0 0.5EM;
     color: #F00;
     font-family: "A";
   }
   @media print {
     b {
       x: rgb(1, 2, 3);
     }
   }
   >>> print(kgcss.serialize(sheet, kgcss.SerializeOptions(indent='    ', normalize=False, drop_empty_rules=False)))
   a {
       margin: 0px .50EM;
       color: #FF0000;
       font-family: 'A';
   }
   p {
   }
   @media print {
       b {
           x: RGB( 1 ,2,3 );
       }
   }

Writing is idempotent: parsing the output and writing it again gives the same
text. Escapes in identifiers and selectors (``#\31 23``) are kept, and
unknown at-rules, custom properties, ``var()`` and ``calc()`` are written
exactly as they were.

Keeping the original text
-------------------------

With ``style='preserve'``, the text of everything that was not changed since
parsing is written exactly as it was, and only what changed is written in the
pretty format. Use it to save a file that a person edited, so that changing
one declaration does not reformat the rest:

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('a{color:red}   /* keep */  b{ top : 0 }')
   >>> _ = sheet.rules[2].style.set('top', '1px')
   >>> kgcss.serialize(sheet, kgcss.SerializeOptions(style='preserve'))
   'a{color:red}   /* keep */  b {\n  top: 1px;\n}'

Bytes
-----

Output is always ``str``. :meth:`kgcss.Stylesheet.encode` gives bytes in the
encoding named by the sheet's ``@charset`` rule (UTF-8 if there is none),
writing characters that the encoding cannot represent as CSS escapes:

.. doctest::

   >>> kgcss.parse_stylesheet('@charset "latin1"; a::before { content: "→" }').encode()
   b'@charset "latin1";\na::before {\n  content: "\\2192 ";\n}'
