# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Ports of the helpers in stylelint's lib/utils that the rules share.

Each function names the stylelint helper it ports. JavaScript regular
expressions are translated with their ASCII-only \\w and \\d.
"""

import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Final, cast

from kgcss.linter._postcss import JS_WHITESPACE, AtRule, Comment, Container, Declaration, Node, Root, Rule

if TYPE_CHECKING:
    from kgcss.linter._selector_parser import Node as SelectorNode
    from kgcss.linter._selector_parser import Root as SelectorRoot

RE_VENDOR_PREFIX: Final = re.compile(r'^-[A-Za-z0-9_]+-')
RE_JS_WHITESPACE: Final = re.compile(f'[{re.escape(JS_WHITESPACE)}]')
CONFIGURATION_COMMANDS: Final = frozenset({'-disable', '-disable-line', '-disable-next-line', '-enable'})
RE_INTERPOLATION: Final = re.compile(r'@\{.+?\}|#\{.+?\}|\{.+?\}|\$\(.+?\)', re.DOTALL)


def vendor_prefix(name: str) -> str:
    """vendor.prefix(): '-webkit-' for '-webkit-foo', else ''."""
    m = RE_VENDOR_PREFIX.match(name)
    return m.group() if m else ''


def is_configuration_comment(node: Node) -> bool:
    """isConfigurationComment(): a /* stylelint-disable */ comment and its kin."""
    if not isinstance(node, Comment) or not node.text:
        return False
    command = RE_JS_WHITESPACE.split(node.text, maxsplit=1)[0]
    return command.replace('stylelint', '', 1) in CONFIGURATION_COMMANDS


def has_block(node: Node) -> bool:
    """hasBlock(): whether the rule or at-rule has a block, even an empty one."""
    return isinstance(node, Container) and node.nodes is not None


def rule_selector(rule: Rule) -> str:
    """getRuleSelector(): the selector as written, comments included."""
    raw = rule.raws.selector
    return raw.raw if raw is not None and raw.value == rule.selector else rule.selector


def at_rule_params(at_rule: AtRule) -> str:
    """getAtRuleParams(): the params as written, comments included."""
    raw = at_rule.raws.params
    return raw.raw if raw is not None and raw.value == at_rule.params else at_rule.params


def before_block_string(statement: Node, no_raw_before: bool = False) -> str:
    """beforeBlockString(): the text of a rule or at-rule up to its block."""
    raws = statement.raws
    result = '' if no_raw_before else raws.before or ''
    if isinstance(statement, Rule):
        result += rule_selector(statement)
    elif isinstance(statement, AtRule):
        result += f'@{statement.name}{raws.after_name or ""}{at_rule_params(statement)}'
    else:
        return ''
    return result + (raws.between or '')


def has_interpolation(text: str) -> bool:
    """hasInterpolation(): Less, SCSS, template or postcss-simple-vars interpolation."""
    return RE_INTERPOLATION.search(text) is not None


def is_standard_syntax_at_rule(at_rule: AtRule) -> bool:
    """isStandardSyntaxAtRule() of stylelint."""
    if at_rule.name.lower() == 'charset':
        return False
    if at_rule.nodes is None and at_rule.params == '':  # SCSS @content
        return False
    # A Less detached ruleset call: @detached-ruleset();
    return not (at_rule.nodes is None and at_rule.raws.after_name == '' and at_rule.params[:1] == '(')


def style_search(source: str, target: str, *, skip_function_arguments: bool = False) -> Iterator[int]:
    """The npm package style-search: where target is in source, outside comments and strings.

    As style-search does by default, it skips comments (block and //), strings
    and function names; with skip_function_arguments, also the arguments of
    functions. Only the options the rules use are ported, for a target of one
    character other than a quote or a parenthesis.
    """
    in_string = in_comment = in_line_comment = in_parens = in_function_args = False
    paren_count = 0
    quote = ''
    length = len(source)
    i = 0
    while i < length:
        c = source[i]
        prev = source[i - 1] if i > 0 else ''
        nxt = source[i + 1] if i + 1 < length else ''
        if not in_string and not in_comment and c == '/' and prev != '\\':
            if nxt == '*':
                in_comment = True
                i += 1
                continue
            if nxt == '/':
                in_comment = in_line_comment = True
                i += 1
                continue
        if in_comment:
            if not in_line_comment and c == '*' and prev != '\\' and nxt == '/' and prev != '/':
                in_comment = False
                i += 1
                continue
            if in_line_comment and c == '\n':
                in_comment = in_line_comment = False
            i += 1
            continue
        if not in_string and c in '"\'':
            if prev != '\\':
                quote = c
                in_string = True
            i += 1
            continue
        if in_string:
            if c == quote and prev != '\\':
                in_string = False
            i += 1
            continue
        if c == '(':
            paren_count += 1
            in_parens = True
            # /[a-zA-Z]/.test(source[i - 1]): at the start that is test(undefined), which is true
            if i == 0 or 'a' <= prev <= 'z' or 'A' <= prev <= 'Z':
                in_function_args = True
            i += 1
            continue
        if in_parens and c == ')':
            paren_count -= 1
            if paren_count == 0:
                in_parens = in_function_args = False
            i += 1
            continue
        if c == target and not is_function_name_at(source, i) and not (skip_function_arguments and in_function_args):
            yield i
        i += 1


RE_FUNCTION_NAME: Final = re.compile(r'[a-zA-Z]*\(')


def is_function_name_at(source: str, i: int) -> bool:
    return RE_FUNCTION_NAME.match(source, i) is not None


def vendor_unprefixed(name: str) -> str:
    """vendor.unprefixed(): 'foo' for '-webkit-foo'."""
    return RE_VENDOR_PREFIX.sub('', name, count=1)


def is_custom_property(name: str) -> bool:
    """isCustomProperty()."""
    return name.startswith('--')


def is_scss_variable(name: str) -> bool:
    """isScssVariable()."""
    return name.startswith('$') or '.$' in name


def is_standard_syntax_property(name: str) -> bool:
    """isStandardSyntaxProperty(): not a SCSS or Less variable, nor interpolated."""
    if is_scss_variable(name) or name.startswith('@'):
        return False
    if name.endswith(('+', '+_')):  # Less: transform+_: scale(2)
        return False
    return not has_interpolation(name)


def is_standard_syntax_declaration(decl: Declaration) -> bool:
    """isStandardSyntaxDeclaration()."""
    prop, parent = decl.prop, decl.parent
    if is_scss_variable(prop):
        return False
    if prop[:1] == '@' and prop[1:2] != '{':  # a Less variable
        return False
    if isinstance(parent, AtRule) and parent.raws.after_name == ':':  # a Less map
        return False
    if isinstance(parent, Rule) and parent.selector:
        selector = parent.selector
        if selector.startswith('#') and selector.endswith('()'):  # a Less map
            return False
        if selector[-1] == ':' and selector[:2] != '--':  # Sass nested properties
            return False
    return True


def each_declaration_block(root: Root) -> Iterator[list[Declaration]]:
    """eachDeclarationBlock(): the declarations of each block (inner blocks first), the root's too."""

    def each(node: Node) -> Iterator[list[Declaration]]:
        if not isinstance(node, (Rule, AtRule, Root)) or not node.nodes:
            return
        decls: list[Declaration] = []
        for child in node.nodes:
            if isinstance(child, Declaration):
                decls.append(child)
            yield from each(child)
        if decls:
            yield decls

    return each(root)


def ancestors(node: Node) -> Iterator[Container]:
    """eachNodeUpToRoot(): the parents of node, not including the root."""
    current = node.parent
    while current is not None and not isinstance(current, Root):
        yield current
        current = current.parent


def is_descriptor_declaration(decl: Declaration, nesting_at_keywords: frozenset[str]) -> bool:
    """isDescriptorDeclaration(): in an at-rule such as @font-face rather than in a rule."""
    for node in ancestors(decl):
        if isinstance(node, Rule):
            return False
        if isinstance(node, AtRule) and node.name.lower() not in nesting_at_keywords:
            return True
    return False


def css_tree_vendor_prefix(name: str) -> str:
    """css-tree's getVendorPrefix(): '-webkit-' for '-webkit-x', '' for '--x' or '-x'."""
    if len(name) >= 3 and name[0] == '-' and name[1] != '-':
        second = name.find('-', 2)
        if second != -1:
            return name[: second + 1]
    return ''


CSS_TREE_HACKS: Final = frozenset('_*$#+&')


def css_tree_property(prop: str, properties: frozenset[str]) -> str | None:
    """css-tree's lexer.getProperty(): the name it knows prop by, or None.

    Like css-tree's names.property(), this strips a hack prefix (_ * $ # + & /
    and //), lowercases, and falls back to the name without a vendor prefix.
    """
    hack = ''
    if prop[:1] == '/':
        hack = '//' if prop[1:2] == '/' else '/'
    elif prop[:1] in CSS_TREE_HACKS:
        hack = prop[0]
    if prop[len(hack) : len(hack) + 2] == '--':
        return None  # a custom property: not in properties
    name = prop.lower()[len(hack) :]
    vendor = css_tree_vendor_prefix(name)
    if name in properties:
        return name
    if vendor and (basename := name[len(vendor) :]) in properties:
        return basename
    return None


RE_LESS_EXTEND: Final = re.compile(r':extend(?:\(.*?\))?')
RE_LESS_MIXIN_RESOLVED: Final = re.compile(r'\.[\w-]+\(.*\).+', re.ASCII)
RE_LESS_PARAMETRIC_MIXIN: Final = re.compile(r'\(@.*\)$')


def is_standard_syntax_selector(selector: str) -> bool:
    """isStandardSyntaxSelector() of stylelint (the plugin has its own)."""
    if has_interpolation(selector) or selector.startswith('%') or selector.endswith(':'):
        return False
    if RE_LESS_EXTEND.search(selector) or RE_LESS_MIXIN_RESOLVED.search(selector):
        return False
    if selector.endswith(')') and ':' not in selector:
        return False
    if RE_LESS_PARAMETRIC_MIXIN.search(selector):
        return False
    return '<%' not in selector and '%>' not in selector and '//' not in selector


def is_standard_syntax_rule(node: Node) -> bool:
    """isStandardSyntaxRule()."""
    return isinstance(node, Rule) and is_standard_syntax_selector(node.selector)


# Values

RE_JS_NOT_WHITESPACE: Final = re.compile(f'[^{re.escape(JS_WHITESPACE)}]')
RE_SCSS_NAMESPACE_VAR: Final = re.compile(r'^[^\n\r\u2028\u2029]+\.\$')
RE_SCSS_NAMESPACE_FUNCTION: Final = re.compile(r'^[^\n\r\u2028\u2029]+\.[-\w]+\(', re.ASCII)
RE_WEBEXTENSION_MSG: Final = re.compile(f'__MSG_[^{re.escape(JS_WHITESPACE)}]+__')


def js_split_whitespace(text: str) -> list[str]:
    """text.split(/\\s+/) on a trimmed text, without empty strings (.filter(Boolean))."""
    return [part for part in RE_JS_WHITESPACE.split(text) if part]


def declaration_value(decl: Declaration) -> str:
    """getDeclarationValue(): the value as written, comments included."""
    raw = decl.raws.value
    return raw.raw if raw is not None and raw.value == decl.value else decl.value


def declaration_value_index(decl: Declaration) -> int:
    """declarationValueIndex(): where the value starts, relative to the start of the declaration."""
    return len(decl.prop) + len(decl.raws.between or ':')


def is_standard_syntax_value(value: str) -> bool:
    """isStandardSyntaxValue(): not a SCSS or Less variable, nor interpolated."""
    normalized = value[1:] if value[:1] in ('-', '+', '*', '/') else value
    if normalized.startswith(('$', '@')):
        return False
    if RE_SCSS_NAMESPACE_VAR.search(value) or RE_SCSS_NAMESPACE_FUNCTION.search(value):
        return False
    return not has_interpolation(normalized) and not RE_WEBEXTENSION_MSG.search(value)


def is_variable(word: str) -> bool:
    """isVariable()."""
    return word.lower().startswith('var(')


def postcss_list_comma(text: str) -> list[str]:
    """postcss.list.comma(): split at top-level commas, outside strings, comments and parentheses."""
    parts: list[str] = []
    current: list[str] = []
    func = 0
    in_quote = escape = in_comment = False
    prev_quote = ''
    i = 0
    n = len(text)
    while i < n:
        letter = text[i]
        if in_comment:
            current.append(letter)
            if letter == '*' and text[i + 1 : i + 2] == '/':
                current.append('/')
                i += 1
                in_comment = False
            i += 1
            continue
        split = False
        if escape:
            escape = False
        elif letter == '\\':
            escape = True
        elif in_quote:
            if letter == prev_quote:
                in_quote = False
        elif letter in '"\'':
            in_quote = True
            prev_quote = letter
        elif letter == '/' and text[i + 1 : i + 2] == '*':
            current.append('/*')
            i += 2
            in_comment = True
            continue
        elif letter == '(':
            func += 1
        elif letter == ')':
            if func > 0:
                func -= 1
        elif func == 0 and letter == ',':
            split = True
        if split:
            parts.append(js_trim_str(''.join(current)))
            current = []
        else:
            current.append(letter)
        i += 1
    parts.append(js_trim_str(''.join(current)))
    return parts


def js_trim_str(text: str) -> str:
    return text.strip(JS_WHITESPACE)


# Number(text) is not NaN: JavaScript's StringToNumber grammar
RE_JS_NUMBER: Final = re.compile(
    r'(?:[+-]?(?:Infinity|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)|0[xX][0-9a-fA-F]+|0[oO][0-7]+|0[bB][01]+)?'
)


def is_numbery(value: str) -> bool:
    """isNumbery(): not blank, and Number(value) is a number (not NaN)."""
    trimmed = js_trim_str(value)
    return bool(trimmed) and RE_JS_NUMBER.fullmatch(trimmed) is not None


def function_arguments_search(
    source: str, function_name: re.Pattern[str], callback: Callable[[str, int], None]
) -> None:
    """functionArgumentsSearch(): call callback(arguments, index) for each function named function_name."""
    from kgcss.linter._value_parser import iter_nodes, parse

    for node in iter_nodes(parse(source)):
        if node.type != 'function' or not function_name.search(node.value):
            continue
        expression_index = node.source_index + len(node.value) + 1
        if node.value.lower() == 'url':
            reparsed = parse(source[node.source_index + len(node.value) :])[0]
            last = expression_index + reparsed.source_end_index - 2
            end = last + 1 - (1 if char_at(source, last) == ')' else 0)
        else:
            end = node.source_end_index - (1 if char_at(source, node.source_end_index - 1) == ')' else 0)
        callback(source[expression_index:end], expression_index)


def char_at(text: str, i: int) -> str:
    """text[i] in JavaScript: undefined (here '') outside the string."""
    return text[i] if 0 <= i < len(text) else ''


def at_rule_param_index(at_rule: AtRule) -> int:
    """atRuleParamIndex(): where the params start, relative to the start of the at-rule."""
    return 1 + len(at_rule.name) + len(at_rule.raws.after_name or '')


def parse_selector_or_none(selector: str, cache: dict[object, object] | None = None) -> SelectorRoot | None:
    """parseSelector(): the parsed selector, or None if it is empty or does not parse.

    stylelint reports a selector that does not parse as a parse error, not as a
    problem: such selectors are skipped here. With cache (which lives for one
    run of lint()), the tree is shared: the caller must not change it.
    """
    from kgcss.linter._selector_parser import SelectorError, parse_selector

    if not selector:
        return None
    key = ('parse_selector', selector)
    if cache is not None and key in cache:
        return cast('SelectorRoot | None', cache[key])
    try:
        ans: SelectorRoot | None = parse_selector(selector)
    except SelectorError:
        ans = None
    if cache is not None:
        cache[key] = ans
    return ans


RE_KEYFRAMES_NAME: Final = re.compile(r'^(-(o|moz|ms|webkit)-)?keyframes$', re.IGNORECASE)


def is_keyframe_rule(rule: Rule) -> bool:
    """isKeyframeRule(): a rule (from, 50%) in @keyframes."""
    return isinstance(rule.parent, AtRule) and RE_KEYFRAMES_NAME.match(rule.parent.name) is not None


def normalize_pseudo_name(value: str) -> str:
    """normalizePseudoName()."""
    return value.lower().lstrip(':')


def selector_index(node: SelectorNode) -> int:
    """The source index of a node of a parsed selector, which the parser always sets."""
    assert node.source_index is not None
    return node.source_index


def node_end_index(node: Rule | AtRule) -> int:
    """atRuleAfterIndex() and ruleAfterIndex(): the end offset of the node, less its raws.after and }.

    This is an offset into the text, which stylelint then uses as an index into
    the node: the position it reports is off by the node's offset, as in stylelint.
    """
    end = node.end
    if not end:
        from kgcss.linter._postcss import to_string

        return len(to_string(node)) - 1
    after = len(node.raws.after or '')
    return end - 1 if not after else end - (after + 1)
