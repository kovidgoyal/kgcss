Quickstart
==========

Parse a stylesheet, look at it, change it and write it back:

.. doctest::

   >>> import kgcss
   >>> sheet = kgcss.parse_stylesheet('''
   ... @import "base.css" screen;
   ... .note, p#intro { color: #ff0000; margin: 0px }
   ... @media print { .note { display: none } }
   ... ''')
   >>> for rule in sheet.iter_rules(kgcss.RuleType.STYLE):
   ...     print(rule.selector_text, '->', rule.style.keys())
   .note, p#intro -> ['color', 'margin']
   .note -> ['display']
   >>> rule = sheet.rules[1]
   >>> rule.style['color']
   '#ff0000'
   >>> rule.style.set('font-weight', 'bold')
   <Property font-weight: 'bold'>
   >>> rule.selectors[1].specificity
   (1, 0, 1)
   >>> print(sheet.css_text)
   @import "base.css" screen;
   .note, p#intro {
     color: #f00;
     margin: 0;
     font-weight: bold;
   }
   @media print {
     .note {
       display: none;
     }
   }

Rewrite URLs, and find which elements of a document a selector matches:

.. doctest::

   >>> kgcss.replace_urls(sheet, lambda url: 'styles/' + url)
   True
   >>> sheet.rules[0].href
   'styles/base.css'
   >>> from xml.etree.ElementTree import fromstring
   >>> root = fromstring('<body><p id="intro">Hi</p><p class="note">Note</p></body>')
   >>> [p.text for p in kgcss.Select(root)('.note, p#intro')]
   ['Hi', 'Note']

Bad CSS never raises: what browsers would drop is dropped, and the problems
are collected with their positions. Blocks are parsed when they are first
used, so their problems appear then:

.. doctest::

   >>> sheet = kgcss.parse_stylesheet('a { color: red; margin 0; top: 1px } @import "late.css";')
   >>> print(sheet.css_text)
   a {
     color: red;
     top: 1px;
   }
   >>> [(e.line, e.column, e.message) for e in sheet.errors]
   [(1, 38, '@import rule ignored, it is not allowed after other rules'), (1, 17, 'Invalid rule ignored')]

The :doc:`guide/parsing` and the pages after it go through each part.
