Parsing and the object model
============================

Parsing
-------

:func:`kgcss.parse_stylesheet` parses a whole stylesheet,
:func:`kgcss.parse_style` the contents of a ``style=""`` attribute,
:func:`kgcss.parse_rule` exactly one rule, and :func:`kgcss.parse_value` a
property value.

.. doctest::

   >>> import kgcss
   >>> sheet = kgcss.parse_stylesheet('a { color: red }', href='https://example.com/css/main.css')
   >>> kgcss.parse_style('color: red; margin: 0').keys()
   ['color', 'margin']
   >>> kgcss.parse_rule('@page :first { margin: 1in }').type
   <RuleType.PAGE: 'page'>

Parsing follows CSS Syntax Level 3 and recovers from errors as browsers do:
an invalid declaration or rule is dropped, and the rest is kept. Nothing
raises for bad CSS. The problems are in ``sheet.errors``, as
:class:`kgcss.ParseError` objects with ``line``, ``column``, ``offset`` and
``message``, and can also be sent as they happen to a logger or a function
with ``log=``:

.. doctest::

   >>> problems = []
   >>> sheet = kgcss.parse_stylesheet('a { } @import "x.css";', log=problems.append)
   >>> problems
   [<ParseError misplaced-rule at 1:7: @import rule ignored, it is not allowed after other rules>]

Blocks are parsed when they are first used, which makes parsing a large sheet
and reading part of it cheap. Problems inside a block are reported then.

Bytes
~~~~~

Bytes are decoded as CSS Syntax says: a byte order mark first, then the
encoding given by the protocol (``protocol_encoding=``, an HTTP header for
example), then ``@charset``, then the encoding of the referring document
(``environment_encoding=``), and UTF-8 otherwise:

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('@charset "latin1"; a { content: "é" }'.encode('latin1'))
   >>> sheet.encoding, sheet.rules[1].style['content']
   ('windows-1252', '"é"')

:func:`kgcss.decode_stylesheet` does only the decoding. Text is never
re-encoded on output: :meth:`kgcss.Stylesheet.encode` gives bytes in the
encoding of the ``@charset`` rule, escaping characters it cannot represent.

Rules
-----

A :class:`kgcss.Stylesheet` holds its rules in ``sheet.rules``, a mutable
:class:`kgcss.RuleList`, and is iterable. Each rule has a ``type``, a
:class:`kgcss.RuleType`, and is an instance of one of the rule classes:
:class:`kgcss.StyleRule`, :class:`kgcss.MediaRule`, :class:`kgcss.SupportsRule`,
:class:`kgcss.ImportRule`, :class:`kgcss.FontFaceRule`, :class:`kgcss.PageRule`
and so on. Rules that kgcss does not know, such as ``@keyframes``, are
:class:`kgcss.UnknownAtRule` and are written back exactly as they were.
Comments between rules are :class:`kgcss.Comment` rules.

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('''
   ... /* Main styles */
   ... @media screen { @supports (display: grid) { .grid { display: grid } } }
   ... @keyframes spin { to { transform: rotate(1turn) } }
   ... ''')
   >>> [type(rule).__name__ for rule in sheet]
   ['Comment', 'MediaRule', 'UnknownAtRule']
   >>> [rule.type.name for rule in sheet.iter_rules()]
   ['COMMENT', 'MEDIA', 'SUPPORTS', 'STYLE', 'UNKNOWN']

:meth:`kgcss.Stylesheet.iter_rules` walks every rule at any depth, in
document order, optionally only of some types, and optionally following
``@import`` into imported sheets. :meth:`kgcss.Stylesheet.iter_declaration_blocks`
walks every declaration block: style rules, ``@font-face``, ``@page`` and its
margin boxes, at any depth. ``sheet.rules.of_type()`` filters the top level
only.

Every rule knows its ``parent_style_sheet``, its ``parent_rule`` when it is
nested, and its ``line`` and ``column`` in the source. ``css_text`` gives its
text, and assigning to it parses the new text in place.

Changing sheets
~~~~~~~~~~~~~~~

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('a { color: red }')
   >>> rule = sheet.insert_rule('b { color: blue }')
   >>> sheet.add('@import "base.css";').type
   <RuleType.IMPORT: 'import'>
   >>> [rule.type.name for rule in sheet]
   ['IMPORT', 'STYLE', 'STYLE']
   >>> sheet.delete_rule(1)
   >>> print(sheet.css_text)
   @import "base.css";
   b {
     color: blue;
   }

:meth:`kgcss.Stylesheet.add` puts ``@charset``, ``@import`` and ``@namespace``
rules where they are valid. Inserting a rule that belongs to another sheet
inserts a copy, so sheets never share rules. :meth:`kgcss.Stylesheet.copy`
copies a whole sheet.

Namespaces
~~~~~~~~~~

``sheet.namespaces`` maps prefixes to namespace URIs, backed by the
``@namespace`` rules: changing the mapping changes the rules.

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('svg|rect { fill: red }')
   >>> sheet.namespaces['svg'] = 'http://www.w3.org/2000/svg'
   >>> print(sheet.css_text)
   @namespace svg url(http://www.w3.org/2000/svg);
   svg|rect {
     fill: red;
   }

Rule indexes as browsers see them
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Browsers leave some rules out of their object model, so their rule indexes
differ from ``sheet.rules``: comments, ``@charset``, at-rules they do not know
(``@-foo-bar {...}``, but not ``@keyframes``, ``@property`` and the like),
``@document`` and style rules whose selector list does not parse
(``*html p {}``).
:meth:`kgcss.Stylesheet.browser_index_path` gives the path of indexes to a
rule as a browser counts them, and
:meth:`kgcss.Stylesheet.rule_at_browser_index_path` goes the other way, for
example to find the rule, and its line, that browser developer tools point
at:

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('@charset "utf-8";\n/* c */\n@media print {\n  a { color: red }\n}')
   >>> rule = sheet.rule_at_browser_index_path((0, 0))
   >>> rule.selector_text, rule.line, sheet.browser_index_path(rule)
   ('a', 4, (0, 0))
   >>> sheet = kgcss.parse_stylesheet('*html p {} @-foo-bar {x} .target {}')
   >>> sheet.rule_at_browser_index_path((0,)).selector_text
   '.target'
