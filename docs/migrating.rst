Migrating from css_parser, tinycss and css_selectors
=====================================================

kgcss does what these three libraries do, with one parser and one object
model. If you would rather not change code written for css_parser yet, start
with :doc:`guide/compat`.

From css_parser (cssutils)
--------------------------

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - css_parser
     - kgcss
   * - ``parseString(text)``, ``CSSParser().parseString(text)``
     - :func:`kgcss.parse_stylesheet`, with ``log=`` per call
   * - ``parseStyle(text)``
     - :func:`kgcss.parse_style`
   * - ``sheet.cssRules``, ``rule.cssRules``
     - ``sheet.rules``, ``rule.rules``
   * - ``cssRules.rulesOfType(CSSRule.STYLE_RULE)``
     - ``sheet.rules.of_type(RuleType.STYLE)``, or ``sheet.iter_rules(RuleType.STYLE)`` at every level
   * - ``sheet.insertRule(r, i)``, ``sheet.deleteRule(i)``
     - ``sheet.insert_rule(r, i)``, ``sheet.delete_rule(i)``
   * - ``rule.parentStyleSheet``, ``rule.parentRule``
     - ``rule.parent_style_sheet``, ``rule.parent_rule``
   * - ``sheet.cssText`` (``bytes``)
     - ``sheet.css_text`` (``str``), and ``sheet.encode()`` for bytes
   * - ``rule.selectorText``, ``rule.selectorList``
     - ``rule.selector_text``, ``rule.selectors``
   * - ``selector.specificity`` (4-tuple)
     - ``selector.specificity``, the 3-tuple of Selectors Level 4
   * - ``style.getPropertyValue(n)``, ``style.getProperty(n)``
     - ``style.get_value(n)``, ``style.get_property(n)``
   * - ``style.setProperty(n, v, p)``, ``style.removeProperty(n)``
     - ``style.set(n, v, p)``, ``style.remove(n)``
   * - ``style.getProperties(all=True)``, ``style.seq``
     - ``style.items``
   * - ``style.getCssText(separator=' ')``
     - ``style.to_css(single_line=True)``
   * - ``prop.propertyValue``, ``prop.literalpriority``
     - ``prop.parsed_value``, ``prop.literal_priority``
   * - ``value.cssText``, ``value.uri``, ``value.dimension``
     - ``value.css_text``, ``component.url``, ``component.unit``
   * - ``getUrls(sheet)``, ``replaceUrls(sheet, fn)``
     - :func:`kgcss.iter_urls`, :func:`kgcss.replace_urls`
   * - ``resolveImports(sheet)``
     - :func:`kgcss.resolve_imports`
   * - ``ser.prefs`` (global)
     - :class:`kgcss.SerializeOptions`, per call
   * - ``profile.validate(name, value)``
     - :func:`kgcss.matches`, for the longhands it knows

Differences to expect: custom properties and ``var()`` are kept (css_parser
dropped them); unknown at-rules are kept exactly as written; ``@supports`` and
the like contain real rules; escapes in selectors are kept; output is
``str``.

From tinycss
------------

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - tinycss
     - kgcss
   * - ``tokenize_flat``, ``tokenize_grouped``
     - :func:`kgcss.tokenizer.tokenize`, :func:`kgcss.syntax.parse_component_value_list`
   * - ``token.as_css()``
     - ``token.source_text``
   * - ``parse_style_attr(text)``
     - :func:`kgcss.parse_style`
   * - ``color4.parse_color_string(s)``
     - :func:`kgcss.parse_rgba`
   * - ``media3`` media queries
     - :func:`kgcss.parse_media_queries`
   * - ``fonts3.parse_font_family``, ``serialize_font_family``, ``parse_font``, ``serialize_font``
     - the same names in ``kgcss``

From css_selectors
------------------

.. list-table::
   :header-rows: 1
   :widths: 50 50

   * - css_selectors
     - kgcss
   * - ``Select(root, ignore_inappropriate_pseudo_classes=...)``
     - the same, with ``quirks_mode=True`` for case-insensitive ids and classes
   * - ``select(text, root=None)``, ``has_matches(text)``
     - the same; they also take :class:`kgcss.Selector` objects
   * - ``parse(text)``
     - :func:`kgcss.parse_selectors`
   * - ``selector.specificity()``
     - ``selector.specificity``, a property
   * - ``SelectorError``, ``SelectorSyntaxError``, ``ExpressionError``, ``INAPPROPRIATE_PSEUDO_CLASSES``
     - the same names in ``kgcss``

``:lang()`` honours ``xml:lang``, and ``[a!=b]`` is a syntax error, as it is not
CSS.
