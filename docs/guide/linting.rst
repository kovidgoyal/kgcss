Linting
=======

:func:`kgcss.lint` checks a stylesheet with rules that behave as the
`stylelint <https://stylelint.io>`_ rules of the same names do (stylelint 18).
It parses as stylelint does, with a port of the PostCSS parser that stylelint
uses, so the rules see what stylelint's rules see. :data:`kgcss.LINT_RULES`
lists the rules, as :class:`kgcss.LintRuleInfo`; :class:`kgcss.LintRule` names
them.

.. doctest::

   >>> import kgcss
   >>> result = kgcss.lint('a { color: red;; }\n@foo bar {}')
   >>> for p in result.problems:
   ...     print(p.line, p.column, p.rule.value, p.message, p.args)
   1 16 no-extra-semicolons Unexpected extra semicolon ()
   2 1 at-rule-no-unknown Unknown at-rule "@foo" ('@foo',)
   2 10 block-no-empty Empty block ()

Each :class:`kgcss.LintProblem` has its rule, a message as stylelint words
it, the values in the message (``args``), so that it can be worded or
translated anew, and its position: ``pos`` and ``end`` index the text, and
``line``, ``column``, ``end_line`` and ``end_column`` are those stylelint
reports, except that columns count code points where stylelint counts UTF-16
code units. Rules can be turned off with ``disabled``.

Translating messages
--------------------

The ``template`` of a problem is its message with the values replaced by
``{0}``, ``{1}`` and so on, so that ``template.format(*args)`` is the message.
The indices are explicit, so a translation can put the values in another
order, and literal braces are doubled (``'Unexpected }}'``). With gettext, a
message is translated by ``_(p.template).format(*p.args)``, and
:attr:`kgcss.LintRuleInfo.messages` and :data:`kgcss.SYNTAX_PROBLEM_TEMPLATES`
list every template that can be in a message catalogue.

.. doctest::

   >>> p = result.problems[1]
   >>> p.template, p.args
   ('Unknown at-rule "{0}"', ('@foo',))
   >>> p.template in kgcss.LINT_RULES[p.rule].messages
   True
   >>> sp = kgcss.lint('a b').syntax_problems[0]
   >>> sp.template, sp.args, sp.message
   ('Unknown word {0}', ('a',), 'Unknown word a')
   >>> kgcss.SYNTAX_PROBLEM_TEMPLATES[sp.kind]
   'Unknown word {0}'

Options
-------

Rules take the secondary options of their stylelint documentation (see
:attr:`kgcss.LintRuleInfo.url`), under the same names. Where stylelint takes
a regular expression, a compiled pattern works, or a string such as
``'/^my-/i'``, as in a stylelint configuration. An option or a value that a
rule does not support raises :class:`ValueError`.

.. doctest::

   >>> import re
   >>> css = 'my-element { colr: red; color: blue }'
   >>> [p.message for p in kgcss.lint(css).problems]
   ['Unknown type selector "my-element"', 'Unknown property "colr"']
   >>> options = {
   ...     kgcss.LintRule.SELECTOR_TYPE_NO_UNKNOWN: {'ignore': ['custom-elements']},
   ...     kgcss.LintRule.PROPERTY_NO_UNKNOWN: {'ignoreProperties': [re.compile('^col')]},
   ... }
   >>> kgcss.lint(css, options=options).problems
   ()

Fixing
------

Problems that a rule can fix have a :class:`kgcss.TextEdit` in ``fix``.
``fix=True`` applies them and lints the result again, until nothing more can
be fixed. Only the text of the problems changes: the rest of the stylesheet,
its formatting and comments, stays as it was.

.. doctest::

   >>> result.problems[0].fix
   TextEdit(pos=15, end=16, text='')
   >>> fixed = kgcss.lint('a { color: red;; }\n@foo bar {}', fix=True)
   >>> print(fixed.fixed)
   a { color: red; }
   @foo bar {}
   >>> [p.rule.value for p in fixed.problems]
   ['at-rule-no-unknown', 'block-no-empty']

Syntax errors
-------------

What PostCSS cannot parse is a :class:`kgcss.SyntaxProblem`, which stylelint
reports as ``CssSyntaxError``. PostCSS stops at the first, so there is at
most one. stylelint then runs no rules; kgcss runs them on the tree that
postcss-safe-parser recovers, as stylelint does with ``fix: 'lax'``.

.. doctest::

   >>> result = kgcss.lint('a { color: red;; ')
   >>> result.syntax_problems[0].kind, result.syntax_problems[0].column
   (<SyntaxProblemKind.UNCLOSED_BLOCK: 'Unclosed block'>, 1)
   >>> [p.rule.value for p in result.problems]
   ['no-extra-semicolons']

Style attributes and disable comments
-------------------------------------

The declarations of a ``style`` attribute can be linted as they are: PostCSS
parses declarations outside any rule, and the rules check them as a
declaration block.

stylelint's disable comments work as they do in stylelint:
``/* stylelint-disable */``, ``/* stylelint-enable */``,
``/* stylelint-disable-line */`` and ``/* stylelint-disable-next-line */``,
for all rules or for those named after the command. A rule can be named as
stylelint names it (:attr:`kgcss.LintRuleInfo.stylelint_name`, such as
``@stylistic/no-extra-semicolons``) or by its :class:`kgcss.LintRule` value.
A command that makes no sense, such as enabling a rule that is not disabled,
is ignored, where stylelint reports it and lints nothing.

.. doctest::

   >>> [p.rule.value for p in kgcss.lint('a {} /* stylelint-disable-line block-no-empty */').problems]
   []

The rules are loaded when they are first used, and so is the data that some
of them need.
