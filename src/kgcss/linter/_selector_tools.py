# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Ports of @csstools/selector-specificity 6.0.1 and @csstools/selector-resolve-nested 4.0.2.

stylelint computes specificities (no-descending-specificity,
selector-max-specificity) and resolves nested selectors against their parents
(resolveNestedSelectorsForRule) with these, on trees of
kgcss.linter._selector_parser. As in the JavaScript, resolving changes the
nodes of the parent tree it is given only by cloning them, and the result
mixes positions from both trees.
"""

import math
from collections.abc import Callable
from typing import Final, NamedTuple

from ._postcss import JS_WHITESPACE
from ._selector_parser import (
    Combinator,
    Container,
    Nesting,
    Node,
    Pseudo,
    Root,
    Selector,
    SelectorError,
    Source,
    is_pseudo_class,
    is_pseudo_element,
)

# selector-specificity {{{


class Specificity(NamedTuple):
    """The specificity of a selector: ids, classes (and attributes and pseudo-classes), types."""

    a: int
    b: int
    c: int


ZERO: Final = Specificity(0, 0, 0)

type CustomSpecificity = Callable[[Node], Specificity | None]


def compare(s1: Specificity, s2: Specificity) -> int:
    """Less than 0 if s1 is less specific than s2, 0 if they are equal, more than 0 if it is more specific."""
    if s1.a == s2.a:
        return s1.c - s2.c if s1.b == s2.b else s1.b - s2.b
    return s1.a - s2.a


def specificity_of_most_specific_list_item(
    nodes: list[Node], custom_specificity: CustomSpecificity | None = None
) -> Specificity:
    """The specificity of the most specific of the nodes (the last of equal ones)."""
    ans = ZERO
    for node in nodes:
        s = selector_specificity(node, custom_specificity)
        if compare(s, ans) >= 0:
            ans = s
    return ans


def _only_universal(node: Node) -> bool:
    """selectorNodeContainsNothingOrOnlyUniversal()."""
    if not isinstance(node, Container):
        return False
    nodes = [n for n in node.nodes if n.type != 'comment']
    return not nodes or (len(nodes) == 1 and nodes[0].type == 'universal')


def selector_specificity(node: Node | None, custom_specificity: CustomSpecificity | None = None) -> Specificity:
    """The specificity of a node: of a selector or a list (the sum of its items), or of a simple selector."""
    if custom_specificity is not None and node is not None and (custom := custom_specificity(node)):
        return custom
    if node is None:
        return ZERO
    a = b = c = 0

    def add(s: Specificity) -> None:
        nonlocal a, b, c
        a += s.a
        b += s.b
        c += s.c

    t = node.type
    if t == 'universal':
        return ZERO
    if t == 'id':
        a += 1
    elif t == 'tag':
        c += 1
    elif t in ('class', 'attribute'):
        b += 1
    elif isinstance(node, Pseudo) and is_pseudo_element(node):
        name = node.value.lower()
        if name == '::slotted':
            c += 1
            if node.nodes:
                add(specificity_of_most_specific_list_item(node.nodes, custom_specificity))
        elif name in (
            '::highlight',
            '::view-transition-group',
            '::view-transition-image-pair',
            '::view-transition-old',
            '::view-transition-new',
        ):
            if len(node.nodes) == 1 and node.nodes[0].type == 'selector' and _only_universal(node.nodes[0]):
                return ZERO
            return Specificity(0, 0, 1)
        else:
            c += 1
    elif isinstance(node, Pseudo) and is_pseudo_class(node):
        name = node.value.lower()
        if name in (':-moz-any', ':has', ':is', ':matches', ':not'):
            if node.nodes:
                add(specificity_of_most_specific_list_item(node.nodes, custom_specificity))
        elif name == ':where':
            pass
        elif name in (':nth-child', ':nth-last-child'):
            b += 1
            if node.nodes:
                first = node.nodes[0]
                children = first.nodes if isinstance(first, Container) else []
                of = next(
                    (i for i, n in enumerate(children) if n.type == 'tag' and n.value.lower() == 'of'),
                    -1,
                )
                if of > -1:
                    selector = Selector(value='', nodes=[])
                    for n in children[of + 1 :]:
                        selector.append(n.clone())
                    items: list[Node] = [selector, *node.nodes[1:]]
                    add(specificity_of_most_specific_list_item(items, custom_specificity))
        elif name in (':local', ':global'):
            for n in node.nodes:
                add(selector_specificity(n, custom_specificity))
        elif name in (':host', ':host-context'):
            b += 1
            if node.nodes:
                add(specificity_of_most_specific_list_item(node.nodes, custom_specificity))
        elif name in (':active-view-transition', ':active-view-transition-type'):
            return Specificity(0, 1, 0)
        else:
            b += 1
    elif isinstance(node, Container) and node.nodes:
        for n in node.nodes:
            add(selector_specificity(n, custom_specificity))
    return Specificity(a, b, c)


# }}}

# selector-resolve-nested {{{

MAX_COMBINATIONS: Final = 10000
TOO_MANY: Final = (
    'Too many combinations when trying to resolve a nested selector with lists, reduce the complexity of your selectors'
)
TYPE_ORDER: Final = {
    'universal': 0,
    'tag': 1,
    'pseudoElement': 2,
    'nesting': 3,
    'id': 4,
    'class': 5,
    'attribute': 6,
    'pseudo': 7,
    'comment': 8,
}


def _source_from(node: Node) -> tuple[int, Source | None]:
    """sourceFrom(): the source index (0 if there is none) and the source, shared, not copied."""
    return (0 if node.source_index is None else node.source_index), node.source


def _type_order(node: Node) -> float:
    if is_pseudo_element(node):
        return TYPE_ORDER['pseudoElement']
    return TYPE_ORDER.get(node.type, math.nan)  # undefined - x is NaN in JavaScript


def _v8_sort(items: list[Node], cmp: Callable[[Node, Node], float]) -> None:
    """Array.prototype.sort() as V8 does it for short arrays, so that NaN comparisons give the same order.

    V8's TimSort finds the first run (reversing a strictly descending one),
    then extends it with a binary insertion sort, comparing with < 0 only.
    Longer arrays, which only pathological selectors have, are merged
    differently but with the same results for consistent comparisons.
    """
    n = len(items)
    if n < 2:
        return
    if n >= 64:
        items.sort(key=_type_order)
        return
    # CountAndMakeRun
    run = 2
    descending = cmp(items[1], items[0]) < 0
    while run < n:
        order = cmp(items[run], items[run - 1])
        if (order >= 0) if descending else (order < 0):
            break
        run += 1
    if descending:
        items[:run] = items[:run][::-1]
    # BinaryInsertionSort
    for start in range(run, n):
        pivot = items[start]
        left, right = 0, start
        while left < right:
            mid = left + ((right - left) >> 1)
            if cmp(pivot, items[mid]) < 0:
                right = mid
            else:
                left = mid + 1
        items[left + 1 : start + 1] = items[left:start]
        items[left] = pivot


def _sort_compound_selectors_inside_complex_selector(node: Container) -> None:
    groups: list[list[Node]] = []
    current: list[Node] = []

    def visit(child: Node) -> None:
        nonlocal current
        if child.type == 'combinator':
            groups.append(current)
            groups.append([child])
            current = []
            return
        if is_pseudo_element(child):
            groups.append(current)
            current = [child]
            return
        if child.type == 'universal' and any(n.type == 'universal' for n in current):
            child.remove()
        elif child.type == 'tag' and any(n.type == 'tag' for n in current):
            child.remove()
            index, source = _source_from(child)
            selector = Selector(value='', source_index=index, source=source)
            selector.append(child)
            index, source = _source_from(child)
            pseudo = Pseudo(value=':is', source_index=index, source=source)
            pseudo.append(selector)
            current.append(pseudo)
        else:
            current.append(child)

    node.each(visit)
    groups.append(current)
    nodes: list[Node] = []
    for group in groups:
        _v8_sort(group, lambda x, y: _type_order(x) - _type_order(y))
        nodes.extend(group)
    node.remove_all()
    for child in reversed(nodes):
        child.remove()
        child.parent = node
        node.nodes.insert(0, child)


def _count_nodes(root: Container) -> int:
    count = 0

    def inc(_: Node) -> None:
        nonlocal count
        count += 1

    root.walk(inc)
    return count


def _is_compound_selector(nodes: list[Node]) -> bool:
    if len(nodes) != 1:
        return False
    first = nodes[0]
    assert isinstance(first, Container)
    return not any(n.type == 'combinator' or is_pseudo_element(n) for n in first.nodes)


def _prepare_parent_selectors(parent: Root, force_is: bool = False) -> list[Node]:
    if force_is or not _is_compound_selector(parent.nodes):
        index, source = _source_from(parent)
        pseudo = Pseudo(value=':is', source_index=index, source=source)
        for n in parent.nodes:
            pseudo.append(n.clone())
        return [pseudo]
    first = parent.nodes[0]
    assert isinstance(first, Container)
    return [n.clone() for n in first.nodes]


def _respace(node: Node) -> None:
    if node.type == 'combinator' and node.value.strip(JS_WHITESPACE) != '':
        node.raw_space_after = ' '
        node.raw_space_before = ' '
    else:
        node.raw_space_after = ''
        node.raw_space_before = ''


def _resolve_one(item: Node, parent: Root, parent_count: int, ignore_implicit_nesting: bool) -> Container:
    sel = item.clone()
    assert isinstance(sel, Container)
    if not ignore_implicit_nesting:
        found = False

        def has_nesting(_: Node) -> bool:
            nonlocal found
            found = True
            return False

        sel.walk_nesting(has_nesting)
        if found:
            if sel.nodes and sel.nodes[0].type == 'combinator':
                index, source = _source_from(sel)
                sel.prepend(Nesting(source_index=index, source=source))
        else:
            index, source = _source_from(sel)
            sel.prepend(Combinator(value=' ', source_index=index, source=source))
            index, source = _source_from(sel)
            sel.prepend(Nesting(source_index=index, source=source))
    containers: dict[int, Container] = {}  # a Set, in insertion order
    count = 0

    def replace(nesting: Nesting) -> None:
        nonlocal count
        container = nesting.parent
        if container is None:
            return
        count += 1
        if count * parent_count > MAX_COMBINATIONS:
            raise SelectorError(TOO_MANY)
        containers.setdefault(id(container), container)
        grand = container.parent
        if grand is not None and grand.type == 'pseudo' and grand.value.lower() == ':has':
            nesting.replace_with(*_prepare_parent_selectors(parent, True))
        else:
            nesting.replace_with(*_prepare_parent_selectors(parent))

    sel.walk_nesting(replace)
    for container in containers.values():
        _sort_compound_selectors_inside_complex_selector(container)
    sel.walk(_respace)
    return sel


def resolve_nested_selector(selector: Root, parent: Root, *, ignore_implicit_nesting: bool = False) -> Root:
    """Resolve a nested selector list against its parent selector list, as resolveNestedSelector().

    Raises SelectorError when the lists have too many combinations to resolve.
    """
    parent_count = _count_nodes(parent)
    resolved = [_resolve_one(item, parent, parent_count, ignore_implicit_nesting) for item in selector.nodes]
    index, source = _source_from(selector)
    root = Root(value='', source_index=index, source=source)
    for sel in resolved:
        root.append(sel)
    return root


# }}}
