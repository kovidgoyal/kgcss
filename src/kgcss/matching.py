# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Matching selectors against element trees: Select."""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Protocol, overload

from kgcss._kgcss import CompiledSelector, MatchError, Snapshot
from kgcss.cssom.exceptions import ExpressionError
from kgcss.cssom.selector_list import Selector, SelectorList
from kgcss.selectors import compile_selectors

__all__ = ['Select']

type SelectorArg = str | Selector | SelectorList | Iterable[Selector]


class _Tree[E](Protocol):
    """An ElementTree: lxml's _ElementTree or xml.etree's ElementTree, whose root is an E."""

    def getroot(self) -> E | None: ...


class Select[E]:
    """Select elements of a tree with CSS selectors::

        select = Select(root)
        for elem in select('p.note > a[href]'):
            ...

    root is an element or an ElementTree. For lxml trees the whole document
    is used, whatever element of it root is: :root is the document's root
    element, and :scope is root. The tree must not be changed while the
    Select is in use, as it works on a snapshot of the tree taken here.

    Matching is HTML-style: tag names, attribute names and namespaces as in
    the requirements (9.2). Tag and attribute names are ASCII
    case-insensitive and namespaces are ignored. Attribute values are
    case-sensitive, unless the selector has the i flag. IDs and classes are
    case-sensitive, as in standards mode HTML, or ASCII case-insensitive with
    quirks_mode=True, which is what css_selectors always did.

    Select is generic over the type of the elements: a Select made from an
    lxml tree is a Select[lxml.etree._Element], and returns those, from
    xml.etree a Select[xml.etree.ElementTree.Element].

    Pseudo-classes and pseudo-elements that need a browser, such as :hover
    and ::before, raise ExpressionError when matched. With
    ignore_inappropriate_pseudo_classes=True, those in
    INAPPROPRIATE_PSEUDO_CLASSES match every element instead, so p:hover
    selects every p.
    """

    @overload
    def __init__(
        self, root: _Tree[E], ignore_inappropriate_pseudo_classes: bool = False, *, quirks_mode: bool = False
    ) -> None: ...
    @overload
    def __init__(
        self, root: E, ignore_inappropriate_pseudo_classes: bool = False, *, quirks_mode: bool = False
    ) -> None: ...
    def __init__(
        self, root: E | _Tree[E], ignore_inappropriate_pseudo_classes: bool = False, *, quirks_mode: bool = False
    ) -> None:
        # Elements are duck typed: the C Snapshot only needs the ElementTree API
        elem: Any = root.getroot() if hasattr(root, 'getroot') else root  # an ElementTree
        self.root: E = elem
        document = elem.getroottree().getroot() if hasattr(elem, 'getroottree') else elem
        self.ignore_inappropriate_pseudo_classes = ignore_inappropriate_pseudo_classes
        self._snapshot = Snapshot(document, quirks_mode)
        self._scope = 0 if document is elem else self._snapshot.index_of(elem)

    @property
    def quirks_mode(self) -> bool:
        return self._snapshot.quirks_mode

    def select(self, selector: SelectorArg, root: E | None = None) -> Iterator[E]:
        """The elements matching the selector, in document order, each once. selector is text (a selector
        list), a Selector, a SelectorList or an iterable of Selectors. With root, only root and its
        descendants are returned, but matching still uses the whole document.

        This is a generator: the selector is parsed and matched when the first element is requested,
        and errors are raised then: SelectorSyntaxError for text that is not a selector list, and
        ExpressionError for selectors that cannot be matched against a document.
        """
        # Hot: calibre's stylizer calls this for every selector of every sheet, for every document
        compiled = compile_selectors(selector, False) if type(selector) is str else self._compile(selector)
        try:
            found = self._snapshot.match(
                compiled,
                self.ignore_inappropriate_pseudo_classes,
                self._scope,
                0 if root is None else self._index(root),
                -1,
                False,
            )
        except MatchError as e:
            raise ExpressionError(str(e)) from None
        assert isinstance(found, list)
        yield from found

    __call__ = select

    def has_matches(self, selector: SelectorArg, root: E | None = None) -> bool:
        """Whether the selector matches at least one element (of the subtree of root, if given)."""
        return bool(self._match(self._compile(selector), self._index(root), -1, True))

    def matches(self, element: E, selector: SelectorArg) -> bool:
        """Whether element matches the selector."""
        target = self._snapshot.index_of(element) if element is not None else -1
        return bool(self._match(self._compile(selector), 0, target, False))

    def _index(self, element: E | None) -> int:
        if element is None:
            return 0
        try:
            return self._snapshot.index_of(element)
        except KeyError:
            raise ValueError(f'{element!r} is not an element of the tree of this Select') from None

    def _match(self, compiled: Sequence[CompiledSelector], root: int, target: int, first_only: bool) -> list[E] | bool:
        try:
            return self._snapshot.match(
                compiled, self.ignore_inappropriate_pseudo_classes, self._scope, root, target, first_only
            )
        except MatchError as e:
            raise ExpressionError(str(e)) from None

    def _compile(self, selector: SelectorArg) -> Sequence[CompiledSelector]:
        if isinstance(selector, str):
            return compile_selectors(selector, False)
        if isinstance(selector, Selector):
            selector = (selector,)
        ans = []
        for s in selector:
            # Selectors of nested style rules are matched as their top level equivalent
            ans.append(s.resolved()._compiled_selector())
        return ans

    def __repr__(self) -> str:
        return f'<Select {self._snapshot.count} elements>'
