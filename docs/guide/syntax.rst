Tokens and the syntax tree
==========================

Under the object model are two lower layers, for tools that need them, such
as syntax highlighters, linters or code that rewrites text in place.

Tokens
------

:func:`kgcss.tokenizer.tokenize` splits text into the tokens of CSS Syntax
Level 3, in C. Each :class:`kgcss.tokenizer.Token` has a ``type``, a decoded
``value`` (strings unquoted, escapes resolved, numbers as ``int`` or
``float``), a ``unit`` for dimensions, its exact ``source_text``, and its
position: ``pos`` and ``end`` as offsets into the text, which are Python
string indexes, and ``line`` and ``column``.

.. doctest::

   >>> from kgcss.tokenizer import tokenize
   >>> tokens = tokenize('#\\5f x { width: 1.5E1px }')
   >>> [(t.type.name, t.value) for t in tokens if t.type.name != 'WHITESPACE'][:5]
   [('HASH', '_x'), ('LEFT_CURLY', '{'), ('IDENT', 'width'), ('COLON', ':'), ('DIMENSION', 15.0)]
   >>> t = next(t for t in tokens if t.type.name == 'DIMENSION')
   >>> t.unit, t.source_text, t.pos, t.end, t.line, t.column
   ('px', '1.5E1px', 16, 23, 1, 17)
   >>> ''.join(t.source_text for t in tokens) == '#\\5f x { width: 1.5E1px }'
   True

The source text of the tokens always adds up to the input, so text can be
changed token by token and everything else kept. ``comments=False`` leaves
comments out. Problems, such as a string left open, are in
``tokens.errors``.

The syntax tree
---------------

:mod:`kgcss.syntax` implements the parsing algorithms of CSS Syntax: the
result is a tree of at-rules, qualified rules, declarations, blocks and
functions, with positions, and without any knowledge of what the rules mean.

.. doctest::

   >>> from kgcss import syntax
   >>> nodes = syntax.parse_stylesheet('@media print { a { color: red } } b { c: d }')
   >>> [type(n).__name__ for n in nodes]
   ['AtRule', 'QualifiedRule']
   >>> media = nodes[0]
   >>> media.name, media.prelude.text, media.block.source_text
   ('media', ' print ', '{ a { color: red } }')
   >>> values = syntax.parse_component_value_list('calc(1px + 2%) [a]')
   >>> [type(v).__name__ for v in values if not getattr(v, 'type', None) or v.type.name != 'WHITESPACE']
   ['Function', 'Block']

``parse_rule_list``, ``parse_block_contents``, ``parse_one_rule``,
``parse_one_declaration`` and ``parse_one_component_value`` are the other
entry points of the specification.
