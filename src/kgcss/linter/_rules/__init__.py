# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The rules, one module each, named after the rule.

Each module ports the stylelint rule of its name and has a function
check(root, ctx). They are imported only when the rule is used, so the
data some of them need (kgcss._css_data) is too.
"""
