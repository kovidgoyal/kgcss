# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""A css_parser-style facade over kgcss, for code written for css_parser (cssutils).

Every class here is a thin, live view of a kgcss object: changing the view changes the
object, and the other way round. wrap() gives the view of a kgcss object and unwrap() the
object of a view, so a program can use the Pythonic kgcss API and hand css_parser-style
views to code, such as calibre's Edit book plugins, that expects the old names.

It covers the parts of the css_parser API in common use: parsing, the rule list and its
rule type constants, style rules, @media, @import, @font-face, @page, comments,
declarations and properties, values, serialization and URLs. It does not reproduce
css_parser's quirks: values that css_parser dropped (custom properties, var()) are kept,
and text is serialized as kgcss serializes it.

>>> sheet = parseString('a { color: red } @media print { b { margin: 0 } }')
>>> [r.selectorText for r in sheet.cssRules.rulesOfType(CSSRule.STYLE_RULE)]
['a']
>>> rule = sheet.cssRules[0]
>>> rule.style.getPropertyValue('color')
'red'
>>> rule.style.setProperty('color', 'blue', 'important')
>>> rule.style.getCssText(separator=' ')
'color: blue !important'
>>> unwrap(sheet).rules[0].style['color']
'blue'
"""

from collections.abc import Callable, Iterator, MutableMapping, Sequence
from typing import ClassVar, Final, overload

from kgcss.cssom import (
    CharsetRule,
    Comment,
    DeclarationBlock,
    FontFaceRule,
    ImportRule,
    MarginRule,
    MediaRule,
    NamespaceRule,
    PageRule,
    Rule,
    RuleType,
    StyleRule,
    Stylesheet,
    UnknownAtRule,
    iter_urls,
    parse_rule,
    parse_style,
    parse_stylesheet,
    replace_urls,
    resolve_imports,
)
from kgcss.cssom import MediaList as KMediaList
from kgcss.cssom import Property as KProperty
from kgcss.cssom import PropertyValue as KPropertyValue
from kgcss.cssom import Selector as KSelector
from kgcss.cssom import SelectorList as KSelectorList
from kgcss.cssom.rules import GroupingRule, RuleContainer
from kgcss.cssom.values import Component, Dimension, Function, Url

__all__ = [
    'CSSCharsetRule',
    'CSSComment',
    'CSSFontFaceRule',
    'CSSGroupingRule',
    'CSSImportRule',
    'CSSMarginRule',
    'CSSMediaRule',
    'CSSNamespaceRule',
    'CSSPageRule',
    'CSSRule',
    'CSSRuleList',
    'CSSStyleDeclaration',
    'CSSStyleRule',
    'CSSStyleSheet',
    'CSSUnknownRule',
    'MediaList',
    'Property',
    'PropertyValue',
    'Selector',
    'SelectorList',
    'Value',
    'getUrls',
    'parseRule',
    'parseString',
    'parseStyle',
    'replaceUrls',
    'resolveImports',
    'serialize',
    'unwrap',
    'wrap',
]

type Owner = Stylesheet | RuleContainer


class _View[T]:
    """A view of one kgcss object. Views of the same object are equal and hash alike."""

    __slots__ = ('_obj',)

    def __init__(self, obj: T) -> None:
        self._obj: T = obj

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and isinstance(other, _View) and other._obj is self._obj

    def __hash__(self) -> int:
        return id(self._obj)


# ---------------------------------------------------------------------------
# Rule lists
# ---------------------------------------------------------------------------


def owner_rules(owner: Owner) -> Sequence[Rule]:
    return owner.rules if isinstance(owner, Stylesheet) else owner.child_rules()


def insert(owner: Owner, rule: CSSRule | str, index: int | None = None) -> Rule:
    return owner.insert_rule(unwrap_rule(rule), index)


def index_in(owner: Owner, rule: Rule) -> int:
    for i, r in enumerate(owner_rules(owner)):
        if r is rule:
            return i
    raise ValueError(f'{rule!r} is not in the list')


class CSSRuleList(_View[Owner]):
    """The rules of a sheet or of a rule that contains rules. Changes go through the owner, so a rule
    that belongs elsewhere is copied in, as kgcss does."""

    __slots__ = ()

    def __len__(self) -> int:
        return len(owner_rules(self._obj))

    @property
    def length(self) -> int:
        return len(self)

    @overload
    def __getitem__(self, index: int) -> CSSRule: ...
    @overload
    def __getitem__(self, index: slice) -> list[CSSRule]: ...
    def __getitem__(self, index: int | slice) -> CSSRule | list[CSSRule]:
        rules = owner_rules(self._obj)
        if isinstance(index, slice):
            return [wrap_rule(r) for r in rules[index]]
        return wrap_rule(rules[index])

    def item(self, index: int) -> CSSRule | None:
        """The rule at index, or None, as in the DOM."""
        rules = owner_rules(self._obj)
        return wrap_rule(rules[index]) if 0 <= index < len(rules) else None

    def __iter__(self) -> Iterator[CSSRule]:
        for r in list(owner_rules(self._obj)):
            yield wrap_rule(r)

    def __contains__(self, rule: object) -> bool:
        return isinstance(rule, CSSRule) and any(r is rule._obj for r in owner_rules(self._obj))

    def index(self, rule: CSSRule) -> int:
        return index_in(self._obj, rule._obj)

    def rulesOfType(self, type: int) -> Iterator[CSSRule]:
        """The rules of a type constant, at this level only, as in css_parser."""
        for r in list(owner_rules(self._obj)):
            if RULE_TYPES.get(r.type, CSSRule.UNKNOWN_RULE) == type:
                yield wrap_rule(r)

    def append(self, rule: CSSRule | str) -> None:
        insert(self._obj, rule)

    def remove(self, rule: CSSRule) -> None:
        self._obj.delete_rule(rule._obj)

    def __delitem__(self, index: int) -> None:
        self._obj.delete_rule(owner_rules(self._obj)[index])


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class CSSRule[R: Rule = Rule](_View[R]):
    """The base of the rule views, with css_parser's rule type constants."""

    UNKNOWN_RULE: ClassVar[int] = 0
    STYLE_RULE: ClassVar[int] = 1
    CHARSET_RULE: ClassVar[int] = 2
    IMPORT_RULE: ClassVar[int] = 3
    MEDIA_RULE: ClassVar[int] = 4
    FONT_FACE_RULE: ClassVar[int] = 5
    PAGE_RULE: ClassVar[int] = 6
    NAMESPACE_RULE: ClassVar[int] = 10
    COMMENT: ClassVar[int] = 1001
    MARGIN_RULE: ClassVar[int] = 1006

    __slots__ = ()

    @property
    def type(self) -> int:
        """One of the constants: rules that css_parser did not know, such as @supports, are UNKNOWN_RULE."""
        return RULE_TYPES.get(self._obj.type, CSSRule.UNKNOWN_RULE)

    @property
    def typeString(self) -> str:
        return TYPE_STRINGS[self.type]

    @property
    def cssText(self) -> str:
        return self._obj.css_text

    @cssText.setter
    def cssText(self, text: str) -> None:
        self._obj.css_text = text

    @property
    def parentStyleSheet(self) -> CSSStyleSheet | None:
        sheet = self._obj.parent_style_sheet
        return None if sheet is None else CSSStyleSheet(sheet)

    @property
    def parentRule(self) -> CSSRule | None:
        parent = self._obj.parent_rule
        return None if parent is None else wrap_rule(parent)

    def __repr__(self) -> str:
        return f'<{type(self).__name__} {self._obj.css_text[:60]!r}>'


class CSSGroupingRule[R: RuleContainer = RuleContainer](CSSRule[R]):
    """A rule that contains rules: @media, @page, and @supports, @container, @layer and the like.
    css_parser called the last ones unknown rules, and their type is UNKNOWN_RULE here too, but their
    rules are available."""

    __slots__ = ()

    @property
    def cssRules(self) -> CSSRuleList:
        return CSSRuleList(self._obj)

    def insertRule(self, rule: CSSRule | str, index: int | None = None) -> int:
        """Insert a rule or the text of one, at index or at the end. Returns its index."""
        return index_in(self._obj, insert(self._obj, rule, index))

    def add(self, rule: CSSRule | str) -> int:
        """Add a rule at the end. Returns its index."""
        return self.insertRule(rule)

    def deleteRule(self, rule: CSSRule | int) -> None:
        self._obj.delete_rule(rule._obj if isinstance(rule, CSSRule) else rule)

    @property
    def atkeyword(self) -> str:
        """The at-keyword with the @, such as @supports. '' for style rules."""
        name = self._obj.at_keyword_name if isinstance(self._obj, GroupingRule) else ''
        if isinstance(self._obj, PageRule):
            name = 'page'
        return '@' + name if name else ''


def get_style(rule: StyleRule | FontFaceRule | PageRule | MarginRule) -> CSSStyleDeclaration:
    return CSSStyleDeclaration(rule.style)


def set_style(rule: StyleRule | FontFaceRule | PageRule | MarginRule, value: str | CSSStyleDeclaration) -> None:
    rule.style = value._obj if isinstance(value, CSSStyleDeclaration) else value


class CSSStyleRule(CSSGroupingRule[StyleRule]):
    """A style rule. cssRules has the rules nested in it (CSS nesting)."""

    __slots__ = ()

    @property
    def selectorText(self) -> str:
        return self._obj.selector_text

    @selectorText.setter
    def selectorText(self, text: str) -> None:
        self._obj.selector_text = text

    @property
    def selectorList(self) -> SelectorList:
        return SelectorList(self._obj.selectors)

    @property
    def style(self) -> CSSStyleDeclaration:
        return get_style(self._obj)

    @style.setter
    def style(self, value: str | CSSStyleDeclaration) -> None:
        set_style(self._obj, value)


class CSSMediaRule(CSSGroupingRule[MediaRule]):
    __slots__ = ()

    @property
    def media(self) -> MediaList:
        return MediaList(self._obj.media)


class CSSPageRule(CSSGroupingRule[PageRule]):
    """@page: selectorText (such as :first), style, and the margin rules in cssRules"""

    __slots__ = ()

    @property
    def selectorText(self) -> str:
        return self._obj.selector_text

    @selectorText.setter
    def selectorText(self, text: str) -> None:
        self._obj.selector_text = text

    @property
    def style(self) -> CSSStyleDeclaration:
        return get_style(self._obj)

    @style.setter
    def style(self, value: str | CSSStyleDeclaration) -> None:
        set_style(self._obj, value)


class CSSImportRule(CSSRule[ImportRule]):
    __slots__ = ()

    @property
    def href(self) -> str:
        return self._obj.href

    @href.setter
    def href(self, href: str) -> None:
        self._obj.href = href

    @property
    def media(self) -> MediaList:
        return MediaList(self._obj.media)

    @property
    def styleSheet(self) -> CSSStyleSheet | None:
        sheet = self._obj.style_sheet
        return None if sheet is None else CSSStyleSheet(sheet)


class CSSFontFaceRule(CSSRule[FontFaceRule]):
    __slots__ = ()

    @property
    def style(self) -> CSSStyleDeclaration:
        return get_style(self._obj)

    @style.setter
    def style(self, value: str | CSSStyleDeclaration) -> None:
        set_style(self._obj, value)


class CSSMarginRule(CSSRule[MarginRule]):
    __slots__ = ()

    @property
    def margin(self) -> str:
        """The margin box, such as @top-left."""
        return '@' + self._obj.name

    @property
    def style(self) -> CSSStyleDeclaration:
        return get_style(self._obj)

    @style.setter
    def style(self, value: str | CSSStyleDeclaration) -> None:
        set_style(self._obj, value)


class CSSCharsetRule(CSSRule[CharsetRule]):
    __slots__ = ()

    @property
    def encoding(self) -> str:
        return self._obj.encoding

    @encoding.setter
    def encoding(self, encoding: str) -> None:
        self._obj.encoding = encoding


class CSSNamespaceRule(CSSRule[NamespaceRule]):
    __slots__ = ()

    @property
    def prefix(self) -> str:
        return self._obj.prefix or ''

    @property
    def namespaceURI(self) -> str:
        return self._obj.namespace_uri


class CSSComment(CSSRule[Comment]):
    __slots__ = ()


class CSSUnknownRule(CSSRule[UnknownAtRule]):
    __slots__ = ()

    @property
    def atkeyword(self) -> str:
        return self._obj.at_keyword


RULE_TYPES: Final[dict[RuleType, int]] = {
    RuleType.STYLE: CSSRule.STYLE_RULE,
    RuleType.CHARSET: CSSRule.CHARSET_RULE,
    RuleType.IMPORT: CSSRule.IMPORT_RULE,
    RuleType.MEDIA: CSSRule.MEDIA_RULE,
    RuleType.FONT_FACE: CSSRule.FONT_FACE_RULE,
    RuleType.PAGE: CSSRule.PAGE_RULE,
    RuleType.NAMESPACE: CSSRule.NAMESPACE_RULE,
    RuleType.COMMENT: CSSRule.COMMENT,
    RuleType.MARGIN: CSSRule.MARGIN_RULE,
}
TYPE_STRINGS: Final[dict[int, str]] = {
    getattr(CSSRule, name): name
    for name in (
        'UNKNOWN_RULE', 'STYLE_RULE', 'CHARSET_RULE', 'IMPORT_RULE', 'MEDIA_RULE', 'FONT_FACE_RULE', 'PAGE_RULE',
        'NAMESPACE_RULE', 'COMMENT', 'MARGIN_RULE',
    )
}  # fmt: skip


def wrap_rule(rule: Rule) -> CSSRule:
    match rule:
        case StyleRule():
            return CSSStyleRule(rule)
        case MediaRule():
            return CSSMediaRule(rule)
        case PageRule():
            return CSSPageRule(rule)
        case ImportRule():
            return CSSImportRule(rule)
        case FontFaceRule():
            return CSSFontFaceRule(rule)
        case MarginRule():
            return CSSMarginRule(rule)
        case CharsetRule():
            return CSSCharsetRule(rule)
        case NamespaceRule():
            return CSSNamespaceRule(rule)
        case Comment():
            return CSSComment(rule)
        case UnknownAtRule():
            return CSSUnknownRule(rule)
        case RuleContainer():
            return CSSGroupingRule(rule)
    return CSSRule(rule)


def unwrap_rule(rule: CSSRule | str) -> Rule | str:
    return rule if isinstance(rule, str) else rule._obj


# ---------------------------------------------------------------------------
# Sheets
# ---------------------------------------------------------------------------


class CSSStyleSheet(_View[Stylesheet]):
    """A view of a Stylesheet. cssText is bytes, in the @charset encoding (UTF-8 by default), as in
    css_parser."""

    __slots__ = ()

    @property
    def cssRules(self) -> CSSRuleList:
        return CSSRuleList(self._obj)

    def insertRule(self, rule: CSSRule | str, index: int | None = None) -> int:
        """Insert a rule or the text of one, at index or at the end. Returns its index."""
        return index_in(self._obj, insert(self._obj, rule, index))

    def add(self, rule: CSSRule | str) -> int:
        """Add a rule in a valid position: @charset first, @import and @namespace before other rules.
        Returns its index."""
        return index_in(self._obj, self._obj.add(unwrap_rule(rule)))

    def deleteRule(self, rule: CSSRule | int) -> None:
        self._obj.delete_rule(rule._obj if isinstance(rule, CSSRule) else rule)

    @property
    def cssText(self) -> bytes:
        return self._obj.encode()

    @cssText.setter
    def cssText(self, text: str | bytes) -> None:
        if isinstance(text, bytes):
            text = parse_stylesheet(text).css_text
        self._obj.css_text = text

    @property
    def href(self) -> str | None:
        return self._obj.href

    @property
    def encoding(self) -> str:
        return self._obj.encoding or 'utf-8'

    @property
    def namespaces(self) -> MutableMapping[str, str]:
        return self._obj.namespaces

    def __iter__(self) -> Iterator[CSSRule]:
        return iter(self.cssRules)

    def __repr__(self) -> str:
        return f'<CSSStyleSheet href={self._obj.href!r} with {len(self._obj.rules)} rules>'


# ---------------------------------------------------------------------------
# Selectors and media
# ---------------------------------------------------------------------------


class Selector(_View[KSelector]):
    __slots__ = ()

    @property
    def selectorText(self) -> str:
        return self._obj.text

    @property
    def specificity(self) -> tuple[int, int, int, int]:
        """The 4-tuple of css_parser, (inline, ids, classes, types), from kgcss's Selectors Level 4 3-tuple"""
        return (0, *self._obj.specificity)

    def __repr__(self) -> str:
        return f'<Selector {self._obj.text!r}>'


class SelectorList(_View[KSelectorList]):
    __slots__ = ()

    @property
    def selectorText(self) -> str:
        return self._obj.text

    def __len__(self) -> int:
        return len(self._obj)

    @property
    def length(self) -> int:
        return len(self._obj)

    def __getitem__(self, index: int) -> Selector:
        return Selector(self._obj[index])

    def __iter__(self) -> Iterator[Selector]:
        return (Selector(s) for s in self._obj)


class MediaList(_View[KMediaList]):
    __slots__ = ()

    @property
    def mediaText(self) -> str:
        return self._obj.media_text

    @mediaText.setter
    def mediaText(self, text: str) -> None:
        self._obj.media_text = text

    def __len__(self) -> int:
        return len(self._obj)

    @property
    def length(self) -> int:
        return len(self._obj)

    def item(self, index: int) -> str | None:
        return self._obj[index] if 0 <= index < len(self._obj) else None

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._obj))

    def appendMedium(self, medium: str) -> None:
        if medium not in self._obj:
            self._obj.append(medium)

    def deleteMedium(self, medium: str) -> None:
        self._obj.remove(medium)


# ---------------------------------------------------------------------------
# Declarations and values
# ---------------------------------------------------------------------------

VALUE_TYPES: Final[dict[str, str]] = {
    'ident': 'IDENT', 'string': 'STRING', 'number': 'NUMBER', 'percentage': 'PERCENTAGE',
    'dimension': 'DIMENSION', 'url': 'URI', 'function': 'FUNCTION', 'hash': 'HASH',
    'unicode-range': 'UNICODE_RANGE', 'operator': 'OPERATOR', 'separator': 'OPERATOR', 'block': 'BLOCK',
}  # fmt: skip


class Value(_View[Component]):
    """One component of a value: type is css_parser's name (IDENT, DIMENSION, URI, ...)."""

    __slots__ = ()

    @property
    def cssText(self) -> str:
        return self._obj.css_text

    @property
    def type(self) -> str:
        return VALUE_TYPES.get(self._obj.type, self._obj.type.upper())

    @property
    def value(self) -> object:
        return getattr(self._obj, 'value', self._obj.css_text)

    @property
    def uri(self) -> str | None:
        return self._obj.url if isinstance(self._obj, Url) else None

    @property
    def dimension(self) -> str | None:
        return self._obj.unit if isinstance(self._obj, Dimension) else None

    @property
    def name(self) -> str | None:
        return self._obj.name if isinstance(self._obj, Function) else None

    def __repr__(self) -> str:
        return f'<Value {self.type} {self._obj.css_text!r}>'


class PropertyValue(_View[KProperty]):
    """The parsed value of a property: its components, without the separators, as Value objects.
    Assigning cssText changes the property."""

    __slots__ = ()

    @property
    def _value(self) -> KPropertyValue:
        return self._obj.parsed_value

    @property
    def cssText(self) -> str:
        return self._value.css_text

    @cssText.setter
    def cssText(self, text: str) -> None:
        self._obj.value = text

    def __len__(self) -> int:
        return len(self._value)

    @property
    def length(self) -> int:
        return len(self._value)

    def item(self, index: int) -> Value | None:
        value = self._value
        return Value(value[index]) if 0 <= index < len(value) else None

    def __getitem__(self, index: int) -> Value:
        return Value(self._value[index])

    def __iter__(self) -> Iterator[Value]:
        return (Value(c) for c in self._value)


class Property(_View[KProperty]):
    """A declaration. Construct one as in css_parser, Property(name, value, priority)."""

    __slots__ = ()

    def __init__(self, name: str | KProperty, value: str = '', priority: str = '') -> None:
        super().__init__(name if isinstance(name, KProperty) else KProperty(name, value, priority))

    @property
    def name(self) -> str:
        return self._obj.name

    @name.setter
    def name(self, name: str) -> None:
        self._obj.name = name

    @property
    def value(self) -> str:
        return self._obj.value

    @value.setter
    def value(self, value: str) -> None:
        self._obj.value = value

    @property
    def priority(self) -> str:
        return self._obj.priority

    @priority.setter
    def priority(self, priority: str) -> None:
        self._obj.priority = priority

    @property
    def literalpriority(self) -> str:
        return self._obj.literal_priority

    @property
    def propertyValue(self) -> PropertyValue:
        return PropertyValue(self._obj)

    @property
    def cssText(self) -> str:
        priority = f' !{self._obj.priority}' if self._obj.priority else ''
        return f'{self._obj.name}: {self._obj.value}{priority}'

    def __repr__(self) -> str:
        return f'<Property {self.cssText!r}>'


class CSSStyleDeclaration(_View[DeclarationBlock]):
    """A declaration block. Iteration, length, item() and getProperties() give the effective
    declarations; getProperties(all=True) gives every one, in order. Construct one from text, as in
    css_parser, or from a DeclarationBlock."""

    __slots__ = ()

    def __init__(self, block: DeclarationBlock | str = '') -> None:
        super().__init__(parse_style(block) if isinstance(block, str) else block)

    @property
    def cssText(self) -> str:
        return self._obj.css_text

    @cssText.setter
    def cssText(self, text: str) -> None:
        self._obj.css_text = text

    def getCssText(self, separator: str | None = None) -> str:
        """The declarations, one per line, or on a single line, safe in a style attribute, when separator
        has no newline."""
        return self._obj.css_text if separator is None or '\n' in separator else self._obj.to_css(single_line=True)

    @property
    def parentRule(self) -> CSSRule | None:
        parent = self._obj.parent_rule
        return None if parent is None else wrap_rule(parent)

    def getPropertyValue(self, name: str) -> str:
        return self._obj.get_value(name)

    def getPropertyPriority(self, name: str) -> str:
        p = self._obj.get_property(name)
        return '' if p is None else p.priority

    def getProperty(self, name: str) -> Property | None:
        p = self._obj.get_property(name)
        return None if p is None else Property(p)

    def getPropertyCSSValue(self, name: str) -> PropertyValue | None:
        p = self._obj.get_property(name)
        return None if p is None else PropertyValue(p)

    def getProperties(self, name: str | None = None, all: bool = False) -> list[Property]:
        props = [x for x in self._obj.items if isinstance(x, KProperty)] if all else list(self._obj)
        if name is not None:
            props = [p for p in props if p.name == name.lower()]
        return [Property(p) for p in props]

    def setProperty(self, name: str | Property, value: str | None = None, priority: str = '') -> None:
        """Set a property, by name and value, or as a Property. An existing one is changed in place."""
        if isinstance(name, Property):
            self._obj.set_property(name._obj)
            return
        if value is None:
            raise ValueError('setProperty() needs a value')
        self._obj.set(name, value, priority)

    def removeProperty(self, name: str) -> str:
        """Remove every declaration of name. Returns the old value, or '' if it was not set."""
        return self._obj.remove(name)

    def keys(self) -> list[str]:
        return self._obj.keys()

    def item(self, index: int) -> str:
        """The name of the property at index, or '' as in the DOM"""
        keys = self._obj.keys()
        return keys[index] if 0 <= index < len(keys) else ''

    @property
    def length(self) -> int:
        return len(self._obj)

    def __len__(self) -> int:
        return len(self._obj)

    def __iter__(self) -> Iterator[Property]:
        return (Property(p) for p in self._obj)

    def __contains__(self, name: object) -> bool:
        return name in self._obj

    def __getitem__(self, name: str) -> str:
        """The value of name, or '' if it is not set, as in css_parser"""
        return self._obj.get_value(name)

    def __setitem__(self, name: str, value: str) -> None:
        self._obj.set(name, value)

    def __delitem__(self, name: str) -> None:
        self._obj.remove(name)

    def __repr__(self) -> str:
        return f'<CSSStyleDeclaration {self._obj.to_css(single_line=True)!r}>'


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


@overload
def wrap(obj: Stylesheet) -> CSSStyleSheet: ...
@overload
def wrap(obj: Rule) -> CSSRule: ...
@overload
def wrap(obj: DeclarationBlock) -> CSSStyleDeclaration: ...
@overload
def wrap(obj: KProperty) -> Property: ...
def wrap(
    obj: Stylesheet | Rule | DeclarationBlock | KProperty,
) -> CSSStyleSheet | CSSRule | CSSStyleDeclaration | Property:
    """The css_parser-style view of a kgcss object."""
    match obj:
        case Stylesheet():
            return CSSStyleSheet(obj)
        case DeclarationBlock():
            return CSSStyleDeclaration(obj)
        case KProperty():
            return Property(obj)
        case Rule():
            return wrap_rule(obj)
    raise TypeError(f'Cannot wrap {type(obj).__name__}')


@overload
def unwrap(view: CSSStyleSheet) -> Stylesheet: ...
@overload
def unwrap[R: Rule](view: CSSRule[R]) -> R: ...
@overload
def unwrap(view: CSSStyleDeclaration) -> DeclarationBlock: ...
@overload
def unwrap(view: Property) -> KProperty: ...
def unwrap(
    view: CSSStyleSheet | CSSRule | CSSStyleDeclaration | Property,
) -> Stylesheet | Rule | DeclarationBlock | KProperty:
    """The kgcss object of a view."""
    return view._obj


def parseString(
    cssText: str | bytes,
    encoding: str | None = None,
    href: str | None = None,
    media: object = None,
    title: object = None,
    validate: object = None,
    *,
    fetcher: Callable[[str], tuple[str | None, str | bytes | None] | None] | None = None,
) -> CSSStyleSheet:
    """Parse a stylesheet. encoding is used for bytes, as the protocol encoding. media, title and
    validate are accepted and ignored, as css_parser mostly ignored them. Problems are not logged:
    they are in unwrap(sheet).errors."""
    return CSSStyleSheet(parse_stylesheet(cssText, href=href, protocol_encoding=encoding, fetcher=fetcher))


def parseStyle(cssText: str, encoding: str = 'utf-8', validate: object = None) -> CSSStyleDeclaration:
    """Parse the contents of a style attribute."""
    return CSSStyleDeclaration(parse_style(cssText))


def parseRule(cssText: str) -> CSSRule:
    """Parse one rule, or a comment."""
    return wrap_rule(parse_rule(cssText))


def serialize(
    obj: CSSStyleSheet | CSSStyleDeclaration, media_type: str = 'text/css', pretty_print: bool = True
) -> bytes:
    """The CSS of a sheet or declaration block as bytes, as calibre's serialize() gives it for text/css
    (the call in calibre's editor demo plugin). Sheets are in their @charset encoding, blocks in UTF-8.
    pretty_print is accepted: the output is always the pretty format."""
    if isinstance(obj, CSSStyleSheet):
        return obj._obj.encode()
    return obj._obj.css_text.encode('utf-8')


def getUrls(sheet: CSSStyleSheet) -> Iterator[str]:
    """Every URL of the sheet: @import hrefs and url() values, at any depth."""
    return iter_urls(sheet._obj)


def replaceUrls(
    sheetOrStyle: CSSStyleSheet | CSSStyleDeclaration,
    replacer: Callable[[str], str],
    ignoreImportRules: bool = False,
) -> None:
    """Replace every URL with replacer(url)."""
    replace_urls(sheetOrStyle._obj, replacer, ignore_imports=ignoreImportRules)


def resolveImports(sheet: CSSStyleSheet, target: object = None) -> CSSStyleSheet:
    """A new sheet with the imported sheets inlined. target is accepted and ignored."""
    return CSSStyleSheet(resolve_imports(sheet._obj))
