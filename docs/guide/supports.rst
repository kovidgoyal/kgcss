@supports conditions
====================

:func:`kgcss.parse_supports_condition` parses the condition of an
``@supports`` rule, following CSS Conditional Rules Level 4 and 5:
declarations in parentheses, ``not``, ``and`` and ``or``, and the functions
``selector()``, ``font-tech()``, ``font-format()`` and ``at-rule()``. It
never raises: a condition that does not parse is kept, with ``valid=False``
and an ``error``, and is false, as browsers drop such rules. Results are
cached by text.

.. doctest::

   >>> import kgcss
   >>> c = kgcss.parse_supports_condition('(display: grid) and (not selector(a:has(b)))')
   >>> [(d.name, d.value) for d in c.declarations()]
   [('display', 'grid')]
   >>> c.to_css()
   '(display: grid) and (not selector(a:has(b)))'
   >>> bad = kgcss.parse_supports_condition('(a: b) and (c: d) or (e: f)')
   >>> bad.valid, bad.error
   (False, 'and and or mixed without parentheses')

Evaluating
----------

What is supported depends on what uses the CSS, so you decide that, with a
function that is called with each :class:`kgcss.SupportsDeclaration` and
returns whether it is supported. Another, optional, function decides the
functions: by default ``selector()`` is true when kgcss can parse its
selector, so that :class:`kgcss.Select` can match it, and the others are
false. kgcss does the rest. Unlike media queries, there is no unknown:
anything else in parentheses, such as ``(foo bar)``, is false, so ``not``
of it is true.

.. doctest::

   >>> supported = {'display': {'block', 'grid'}, 'color': {'red'}}
   >>> def declaration(d):
   ...     return d.value in supported.get(d.name, ())
   >>> kgcss.supports_matches('(display: grid) and (color: red)', declaration=declaration)
   True
   >>> kgcss.supports_matches('(display: flex) or (foo bar)', declaration=declaration)
   False
   >>> kgcss.supports_matches('not (foo bar)', declaration=declaration)
   True
   >>> kgcss.supports_matches('font-format(woff2)', declaration=declaration,
   ...                        function=lambda f: f.name == 'font-format' and f.argument == 'woff2')
   True

``@supports`` rules have the parsed condition as
:attr:`kgcss.SupportsRule.condition`, and :meth:`kgcss.SupportsRule.matches`
evaluates it. The ``supports()`` of an ``@import``, which can also be a bare
declaration, is :attr:`kgcss.ImportRule.supports_condition`, and
:meth:`kgcss.ImportRule.supports_matches` is true when there is none. To
apply only the rules whose ``@supports`` conditions hold, skip the others
as you walk the sheet:

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('''
   ... a { color: red }
   ... @supports (display: grid) { b { display: grid } }
   ... @supports (display: flex) { c { display: flex } }
   ... ''')
   >>> def applied(rules):
   ...     for rule in rules:
   ...         if isinstance(rule, kgcss.SupportsRule):
   ...             if rule.matches(declaration=declaration):
   ...                 yield from applied(rule.rules)
   ...         elif isinstance(rule, kgcss.StyleRule):
   ...             yield rule.selector_text
   >>> list(applied(sheet.rules))
   ['a', 'b']
