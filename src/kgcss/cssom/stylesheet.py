# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""The Stylesheet class and parse_stylesheet()."""

from collections.abc import Iterator

from kgcss.cssom.rules import NamespaceRule, Rule, RuleList, RuleType
from kgcss.cssom.state import ErrorHandler, SheetState, Source
from kgcss.decode import Buffer
from kgcss.syntax import parse_stylesheet as parse_syntax_tree
from kgcss.tokenizer import ParseError


class Stylesheet:
    """A parsed or constructed stylesheet: an ordered list of rules."""

    __slots__ = ('_dirty', '_source', '_state', 'encoding', 'href', 'rules')

    def __init__(self, *, href: str | None = None) -> None:
        self.rules = RuleList()
        self.href = href
        self.encoding: str | None = None
        self._state = SheetState(None, href)
        self._source: Source | None = None
        # True once anything in the sheet was modified, so its source text is stale
        self._dirty = False

    @property
    def errors(self) -> list[ParseError]:
        """Problems found while parsing, including in blocks parsed later on demand."""
        return self._state.errors

    def __iter__(self) -> Iterator[Rule]:
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    @property
    def namespaces(self) -> dict[str, str]:
        """Prefix to namespace URI, from the @namespace rules. The default namespace has the prefix ''."""
        return {r.prefix or '': r.namespace_uri for r in self.rules if isinstance(r, NamespaceRule)}

    def iter_rules(self, *types: RuleType, recursive: bool = True) -> Iterator[Rule]:
        """All rules in document order, optionally only of the given types.

        With recursive=True this includes rules nested in grouping rules (such
        as @media and @supports), in style rules (CSS nesting) and in @page
        rules, at any depth.
        """
        stack: list[Iterator[Rule]] = [iter(self.rules)]
        while stack:
            rule = next(stack[-1], None)
            if rule is None:
                stack.pop()
                continue
            if not types or rule.type in types:
                yield rule
            if recursive and (children := rule.child_rules()) is not None:
                stack.append(iter(children))

    def browser_index_path(self, rule: Rule) -> tuple[int, ...]:
        """The path of indices to rule in the browser CSSOM (cssRules at each level).

        Browsers have no comment rules and no @charset rule, so those are
        skipped when counting, and asking for the path of one is an error.
        """
        if rule.type in (RuleType.COMMENT, RuleType.CHARSET):
            raise ValueError(f'{rule.type.value} rules do not exist in the browser CSSOM')
        path: list[int] = []
        current = rule
        while True:
            parent = current.parent_rule
            siblings = self.rules if parent is None else parent.child_rules()
            if siblings is None:
                raise ValueError('rule is not part of this stylesheet')
            try:
                siblings.index(current)
            except ValueError:
                raise ValueError('rule is not part of this stylesheet') from None
            index = 0
            for sibling in siblings:
                if sibling is current:
                    break
                if sibling.type not in (RuleType.COMMENT, RuleType.CHARSET):
                    index += 1
            path.append(index)
            if parent is None:
                return tuple(reversed(path))
            current = parent

    def __repr__(self) -> str:
        href = f' {self.href!r}' if self.href else ''
        return f'<Stylesheet{href} with {len(self.rules)} rules>'


def parse_stylesheet(
    source: str | Buffer,
    *,
    href: str | None = None,
    protocol_encoding: str | None = None,
    environment_encoding: str | None = None,
    log: ErrorHandler | None = None,
) -> Stylesheet:
    """Parse a stylesheet into the CSS object model.

    source is text, or bytes decoded as described in decode_stylesheet().
    Parsing never fails on bad CSS. Problems are collected in
    Stylesheet.errors and sent to log, which is a callable taking a
    ParseError or a logging.Logger. Declaration blocks are parsed when first
    accessed, and problems found then are reported the same way.
    """
    from kgcss.cssom.build import build_top_level

    nodes = parse_syntax_tree(source, protocol_encoding=protocol_encoding, environment_encoding=environment_encoding)
    sheet = Stylesheet(href=href)
    sheet.encoding = nodes.encoding
    sheet._state.log = log
    sheet._source = Source(nodes.stream, 0, len(nodes.stream.source))
    sheet.rules = RuleList(build_top_level(nodes, sheet))
    return sheet
