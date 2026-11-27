API reference
=============

Everything on this page under a heading other than :ref:`api-lower-layers`
is exported by the ``kgcss`` package itself (``from kgcss import ...``), except
the value classes, the selector tree and the condition trees of media queries
and ``@supports``, which live in the submodules named in their headings, and
the facade, which is :mod:`kgcss.compat`.

Parsing
-------

.. autofunction:: kgcss.parse_stylesheet
.. autofunction:: kgcss.parse_style
.. autofunction:: kgcss.parse_rule
.. autofunction:: kgcss.parse_value
.. autofunction:: kgcss.decode_stylesheet
.. autofunction:: kgcss.lookup_encoding
.. data:: kgcss.ErrorHandler

   Where parse problems go: a callable that takes a :class:`ParseError`, or a
   :class:`logging.Logger`, which gets each problem as a warning with its
   location.
.. autoclass:: kgcss.ParseError
   :members:

Stylesheets
-----------

.. autoclass:: kgcss.Stylesheet
.. autoclass:: kgcss.NamespaceMap
   :no-members:

Rules
-----

.. autoclass:: kgcss.RuleType
   :members:
   :undoc-members:
.. autoclass:: kgcss.Rule
.. autoclass:: kgcss.RuleList
.. autoclass:: kgcss.RuleContainer
.. autoclass:: kgcss.StyleRule
.. autoclass:: kgcss.GroupingRule
.. autoclass:: kgcss.MediaRule
.. autoclass:: kgcss.SupportsRule
.. autoclass:: kgcss.ContainerRule
.. autoclass:: kgcss.LayerBlockRule
.. autoclass:: kgcss.LayerStatementRule
.. autoclass:: kgcss.ScopeRule
.. autoclass:: kgcss.StartingStyleRule
.. autoclass:: kgcss.DocumentRule
.. autoclass:: kgcss.NestedDeclarationsRule
.. autoclass:: kgcss.ImportRule
.. autoclass:: kgcss.NamespaceRule
.. autoclass:: kgcss.CharsetRule
.. autoclass:: kgcss.FontFaceRule
.. autoclass:: kgcss.PageRule
.. autoclass:: kgcss.MarginRule
.. autoclass:: kgcss.Comment
.. autoclass:: kgcss.UnknownAtRule
.. autoclass:: kgcss.MediaList

Declarations
------------

.. autoclass:: kgcss.DeclarationBlock
.. autoclass:: kgcss.DeclarationList
.. autoclass:: kgcss.Property

Values (``kgcss.cssom.values``)
-------------------------------

.. autoclass:: kgcss.PropertyValue
.. autoclass:: kgcss.Component
.. autoclass:: kgcss.cssom.values.Ident
.. autoclass:: kgcss.cssom.values.String
.. autoclass:: kgcss.cssom.values.Number
.. autoclass:: kgcss.cssom.values.Percentage
.. autoclass:: kgcss.cssom.values.Dimension
.. autoclass:: kgcss.cssom.values.Url
.. autoclass:: kgcss.cssom.values.Hash
.. autoclass:: kgcss.cssom.values.UnicodeRange
.. autoclass:: kgcss.cssom.values.Operator
.. autoclass:: kgcss.cssom.values.Separator
.. autoclass:: kgcss.cssom.values.Function
.. autoclass:: kgcss.cssom.values.SimpleBlock
.. autoclass:: kgcss.cssom.values.Other

Serialization
-------------

.. autofunction:: kgcss.serialize
.. autoclass:: kgcss.SerializeOptions
.. autofunction:: kgcss.serialize_string
.. autofunction:: kgcss.serialize_identifier

URLs and @import
----------------

.. autofunction:: kgcss.iter_urls
.. autofunction:: kgcss.replace_urls
.. autofunction:: kgcss.replace_urls_in_text
.. autofunction:: kgcss.remove_url_values
.. autofunction:: kgcss.resolve_imports
.. data:: kgcss.ImportFetcher

   ``Callable[[str], tuple[str | None, str | bytes | None] | None]``: called
   with the URL of an ``@import``, resolved against the sheet's ``href``. It
   returns ``(encoding, text)``, where text is ``str``, or ``bytes`` decoded
   with encoding as the protocol encoding, or ``None`` if there is no such
   sheet.

Exceptions
----------

.. autoexception:: kgcss.CSSSyntaxError
.. autoexception:: kgcss.InvalidValueError
.. autoexception:: kgcss.SelectorError
.. autoexception:: kgcss.SelectorSyntaxError
.. autoexception:: kgcss.ExpressionError

Selectors
---------

.. autofunction:: kgcss.parse_selectors
.. autoclass:: kgcss.Selector
.. autoclass:: kgcss.SelectorList
.. autofunction:: kgcss.invalid_selectors
.. autofunction:: kgcss.rename_classes
.. autofunction:: kgcss.rename_ids
.. data:: kgcss.INAPPROPRIATE_PSEUDO_CLASSES

   The pseudo-classes and pseudo-elements that depend on user interaction or
   rendering (``hover``, ``visited``, ``before``, ...), so that they cannot be
   matched against a document. With ``ignore_inappropriate_pseudo_classes=True``,
   :class:`Select` treats them as matching.

Matching
--------

.. autoclass:: kgcss.Select
.. autoclass:: kgcss.ElementTreeLike

The selector tree (``kgcss.selectors``)
---------------------------------------

What :attr:`kgcss.Selector.parsed` gives: frozen dataclasses, each with
``to_css()``.

.. autoclass:: kgcss.selectors.ComplexSelector
.. autoclass:: kgcss.selectors.CompoundSelector
.. autoclass:: kgcss.selectors.Combinator
   :members:
   :undoc-members:
.. autoclass:: kgcss.selectors.TypeSelector
.. autoclass:: kgcss.selectors.IdSelector
.. autoclass:: kgcss.selectors.ClassSelector
.. autoclass:: kgcss.selectors.AttributeSelector
.. autoclass:: kgcss.selectors.NestingSelector
.. autoclass:: kgcss.selectors.PseudoClass
.. autoclass:: kgcss.selectors.SelectorPseudoClass
.. autoclass:: kgcss.selectors.NthPseudoClass
.. autoclass:: kgcss.selectors.LangPseudoClass
.. autoclass:: kgcss.selectors.FunctionalPseudoClass
.. autoclass:: kgcss.selectors.PseudoElement

Colors
------

.. autofunction:: kgcss.parse_rgba
.. autoclass:: kgcss.RGBA
   :no-members:

   A tuple of four floats, each from 0 to 1, with the names ``red``,
   ``green``, ``blue`` and ``alpha``.

.. autofunction:: kgcss.parse_color
.. autoclass:: kgcss.Color
.. data:: kgcss.ColorSpace

   The color spaces of :class:`Color`: ``'rgb'`` for the legacy sRGB forms
   (names, hex, ``rgb()``, ``hsl()``, ``hwb()``), ``'lab'``, ``'lch'``,
   ``'oklab'``, ``'oklch'``, and the predefined spaces of ``color()``.

Media queries
-------------

.. autofunction:: kgcss.parse_media_queries
.. autofunction:: kgcss.media_matches
.. autoclass:: kgcss.MediaQuery
.. autoclass:: kgcss.MediaFeature

The condition tree (``kgcss.media``):

.. autoclass:: kgcss.media.MediaNot
.. autoclass:: kgcss.media.MediaAnd
.. autoclass:: kgcss.media.MediaOr
.. autoclass:: kgcss.media.GeneralEnclosed
.. autoclass:: kgcss.media.Ratio

@supports conditions
--------------------

.. autofunction:: kgcss.parse_supports_condition
.. autofunction:: kgcss.supports_matches
.. autoclass:: kgcss.SupportsCondition
.. autoclass:: kgcss.SupportsDeclaration
.. autoclass:: kgcss.SupportsFunction

The condition tree (``kgcss.supports``), which also uses
:class:`kgcss.media.GeneralEnclosed`:

.. autoclass:: kgcss.supports.SupportsNot
.. autoclass:: kgcss.supports.SupportsAnd
.. autoclass:: kgcss.supports.SupportsOr

Fonts
-----

.. autofunction:: kgcss.parse_font_family
.. autofunction:: kgcss.serialize_font_family
.. autofunction:: kgcss.parse_font
.. autofunction:: kgcss.serialize_font
.. autoclass:: kgcss.FontDict
   :no-members:

Value grammar and shorthands
----------------------------

.. autofunction:: kgcss.matches
.. data:: kgcss.CSS_WIDE_KEYWORDS

   The CSS-wide keywords, in lower case: ``inherit``, ``initial``, ``unset``,
   ``revert`` and ``revert-layer``. Any property can have one of them as its
   whole value.
.. autofunction:: kgcss.is_color
.. data:: kgcss.grammar.PROPERTIES

   The longhands that :func:`matches` knows: the edges of ``margin`` and
   ``padding``, the width, style and color of the border edges, the parts of
   ``list-style``, and the parts of ``font``.
.. autofunction:: kgcss.expand_shorthand
.. autofunction:: kgcss.longhands
.. autofunction:: kgcss.condense_shorthands
.. data:: kgcss.INITIAL_VALUES

   The initial value of each longhand of the shorthands, as CSS defines them,
   with ``font-family: serif``, which CSS leaves to the user agent.

Linting
-------

.. autofunction:: kgcss.lint
.. autoclass:: kgcss.LintResult
.. autoclass:: kgcss.LintProblem
.. autoclass:: kgcss.TextEdit
.. autoclass:: kgcss.SyntaxProblem
.. autoclass:: kgcss.SyntaxProblemKind
   :members:
   :undoc-members:
.. autoclass:: kgcss.LintRule
   :members:
   :undoc-members:
.. autoclass:: kgcss.LintRuleInfo
.. data:: kgcss.LINT_RULES

   The :class:`LintRuleInfo` of every rule, in the order of :class:`LintRule`.

.. data:: kgcss.SYNTAX_PROBLEM_TEMPLATES

   The template of the message of each :class:`SyntaxProblemKind`
   (:attr:`SyntaxProblem.template`).

The css_parser-style facade
---------------------------

.. automodule:: kgcss.compat
   :members:

.. _api-lower-layers:

Lower layers
------------

Tokens (``kgcss.tokenizer``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: kgcss.tokenizer
   :no-members:

.. autofunction:: kgcss.tokenizer.tokenize
.. autoclass:: kgcss.tokenizer.TokenStream
.. autoclass:: kgcss.tokenizer.Token
.. autoclass:: kgcss.tokenizer.TokenType
   :members:
   :undoc-members:

The CSS Syntax tree (``kgcss.syntax``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: kgcss.syntax
   :no-members:

.. autofunction:: kgcss.syntax.parse_stylesheet
.. autofunction:: kgcss.syntax.parse_rule_list
.. autofunction:: kgcss.syntax.parse_block_contents
.. autofunction:: kgcss.syntax.parse_one_rule
.. autofunction:: kgcss.syntax.parse_one_declaration
.. autofunction:: kgcss.syntax.parse_component_value_list
.. autofunction:: kgcss.syntax.parse_one_component_value
.. autoclass:: kgcss.syntax.NodeList
.. autoclass:: kgcss.syntax.AtRule
.. autoclass:: kgcss.syntax.QualifiedRule
.. autoclass:: kgcss.syntax.Declaration
.. autoclass:: kgcss.syntax.ComponentValueList
.. autoclass:: kgcss.syntax.Block
.. autoclass:: kgcss.syntax.Function
