# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Results of kgcss.linter in the terms of stylelint's warnings, to compare them."""

from kgcss.linter import LINT_RULES, LintResult

from .stylelint_fixtures import ExpectedWarning
from .stylelint_oracle import utf16_offsets


def as_stylelint(source: str, result: LintResult) -> list[ExpectedWarning]:
    """The warnings stylelint would give for result, positions in UTF-16 code units.

    Where the text has a syntax error, stylelint reports only that.
    """
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
