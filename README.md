# kgcss

A fast and comprehensive CSS library for Python, with a C99 backend. It
parses CSS as browsers do, gives a mutable object model of sheets, rules,
selectors, declarations and values, and writes it back, reformatted or
byte for byte. It also rewrites URLs and handles `@import`, matches
Selectors Level 4 against lxml and ElementTree trees, and covers media
queries, colors, fonts and shorthand properties. It runs on regular and
free-threaded Python 3.14 and later.

```python
>>> import kgcss
>>> sheet = kgcss.parse_stylesheet('a { color: #ff0000; margin: 0px }')
>>> print(sheet.css_text)
a {
  color: #f00;
  margin: 0;
}

```

**Documentation: <https://kgcss.readthedocs.io>**: installation, a guide to
every part, the API reference, migrating from css_parser, tinycss and
css_selectors, and development.

## License

BSD 3-Clause. See [LICENSE](LICENSE).
