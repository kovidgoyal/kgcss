# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Sphinx configuration for the kgcss documentation, built on Read the Docs (.readthedocs.yaml)."""

import kgcss

project = 'kgcss'
author = 'Kovid Goyal'
copyright = '2026, Kovid Goyal'
release = kgcss.__version__
version = '.'.join(release.split('.')[:2])

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.doctest',
    'sphinx.ext.intersphinx',
    'sphinx.ext.viewcode',
]

# The plan documents in this directory are for contributors, written in Markdown, and not part of the docs
exclude_patterns = ['_build', 'plan-*.md']
templates_path: list[str] = []

html_theme = 'sphinx_rtd_theme'
html_title = f'kgcss {release}'
html_theme_options = {'navigation_depth': 3}

autodoc_member_order = 'bysource'
autodoc_typehints = 'description'
autodoc_typehints_format = 'short'
autodoc_default_options = {'members': True, 'show-inheritance': True}
# Docstrings are plain text, not reStructuredText markup
default_role = 'code'

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'lxml': ('https://lxml.de/apidoc', None),
}

# The doctest builder (sphinx-build -b doctest) runs the examples in the guide
doctest_global_setup = 'import kgcss'
# Only the explicit doctest blocks of the guide: the examples in docstrings, which autodoc includes, run in
# the test suite, with their module's globals
doctest_test_doctest_blocks = ''
