# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""no-duplicate-at-import-rules: stylelint's lib/rules/no-duplicate-at-import-rules."""

import re
from typing import Final

from kgcss.linter._engine import Context
from kgcss.linter._postcss import Root, to_string
from kgcss.linter._utils import RE_JS_WHITESPACE, at_rule_params
from kgcss.linter._value_parser import ValueNode, parse, stringify

RE_IMPORT: Final = re.compile(r'^import$', re.IGNORECASE)


def condition(nodes: ValueNode | list[ValueNode]) -> str:
    """stringifyCondition(): without whitespace, for a consistent key."""
    return RE_JS_WHITESPACE.sub('', stringify(nodes))


def import_conditions(params: list[ValueNode]) -> list[str]:
    """listImportConditions(): the layer, supports and media conditions of an @import."""
    if not params:
        return []
    shared: list[str] = []
    media: list[str] = []
    last_query: list[ValueNode] = []
    for param in params:
        if param.type in ('space', 'comment'):
            continue
        if not media and (
            (param.type == 'function' and param.value in ('supports', 'layer'))
            or (param.type == 'word' and param.value == 'layer')
        ):
            shared.append(condition(param))
            continue
        if param.type == 'div' and param.value == ',':
            media.append(condition(last_query))
            last_query = []
            continue
        last_query.append(param)
    if last_query:
        media.append(condition(last_query))
    if media and not shared:
        return media
    if not media and shared:
        return [' '.join(shared)]
    return [' '.join(shared) + ' ' + m for m in media]


def check(root: Root, ctx: Context) -> None:
    imports: dict[str, list[str]] = {}
    for at_rule in root.walk_at_rules():
        if not RE_IMPORT.match(at_rule.name):
            continue
        nodes = parse(at_rule_params(at_rule))
        if not nodes:
            continue
        first, rest = nodes[0], nodes[1:]
        if first.type == 'function' and first.value.lower() == 'url' and first.nodes:
            uri = first.nodes[0].value
        else:
            uri = first.value
        media = import_conditions(rest)
        imported = imports.get(uri)
        duplicate = (imported is not None and any(q in imported for q in media)) if media else imported is not None
        if duplicate:
            ctx.report(at_rule, f'Duplicate @import rule {uri}', (uri,), word=to_string(at_rule))
            continue
        imports.setdefault(uri, []).extend(media)
