# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Results of kgcss.linter in the terms of stylelint's warnings, to compare them."""

from string import Formatter

from kgcss.linter import LINT_RULES, SYNTAX_PROBLEM_TEMPLATES, LintResult

from .stylelint_fixtures import ExpectedWarning
from .stylelint_oracle import utf16_offsets


def template_indices(template: str) -> list[int]:
    """The indices of the replacement fields of a template, in order; ValueError if it has others."""
    ans = []
    for _, field, spec, conversion in Formatter().parse(template):
        if field is None:
            continue
        if not field.isdigit() or spec or conversion:
            raise ValueError(f'Not a plain explicit index: {{{field}}} in {template!r}')
        ans.append(int(field))
    return ans


def check_templates(result: LintResult) -> None:
    """Every message of result is its template with its args, and the template is registered.

    The template of a problem uses as many distinct indices as it has args, but where an arg is empty
    (as no-descending-specificity leaves out the resolved selectors of rules that are not nested), so
    that no value is left out of a message.
    """
    for p in result.problems:
        assert p.template in LINT_RULES[p.rule].messages, p
        assert p.template.format(*p.args) == p.message, p
        used = set(template_indices(p.template))
        assert all(not arg for i, arg in enumerate(p.args) if i not in used), p
    for sp in result.syntax_problems:
        assert sp.template == SYNTAX_PROBLEM_TEMPLATES[sp.kind], sp
        assert sp.template.format(*sp.args) == sp.message, sp
        assert len(set(template_indices(sp.template))) == len(sp.args), sp


def as_stylelint(source: str, result: LintResult) -> list[ExpectedWarning]:
    """The warnings stylelint would give for result, positions in UTF-16 code units.

    Where the text has a syntax error, stylelint reports only that.
    """
    check_templates(result)
    code = source[1:] if source[:1] in ('\ufeff', '\ufffe') else source  # PostCSS strips one
    lines = code.split('\n')

    def column(line: int, col: int) -> int:
        text = lines[line - 1] if line <= len(lines) else ''
        return col + sum(1 for c in text[: col - 1] if ord(c) > 0xFFFF)

    if result.syntax_problems:
        sp = result.syntax_problems[0]
        w: ExpectedWarning = {
            'message': f'{sp.message} (CssSyntaxError)',
            'line': sp.line,
            'column': column(sp.line, sp.column),
        }
        if sp.end_line is not None and sp.end_column is not None:
            w['endLine'] = sp.end_line
            w['endColumn'] = column(sp.end_line, sp.end_column)
        return [w]
    bom = len(source) - len(code)
    offsets = utf16_offsets(code)
    utf16 = {cp: i for i, cp in reversed(list(enumerate(offsets)))}
    ans: list[ExpectedWarning] = []
    for p in result.problems:
        warning: ExpectedWarning = {
            'message': f'{p.message} ({LINT_RULES[p.rule].stylelint_name})',
            'line': p.line,
            'column': column(p.line, p.column),
            'endLine': p.end_line,
            'endColumn': column(p.end_line, p.end_column),
        }
        if p.fix is not None:
            warning['fix'] = {'range': [utf16[p.fix.pos - bom], utf16[p.fix.end - bom]], 'text': p.fix.text}
        ans.append(warning)
    return ans


def sort_key(w: ExpectedWarning) -> tuple[int, int, int, int, str]:
    return w.get('line', 0), w.get('column', 0), w.get('endLine', 0), w.get('endColumn', 0), w.get('message', '')
