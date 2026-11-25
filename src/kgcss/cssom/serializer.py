# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Serializing CSSOM objects to text."""

from dataclasses import dataclass
from typing import Literal

from kgcss.cssom.rules import Rule
from kgcss.cssom.stylesheet import Stylesheet


@dataclass(frozen=True, slots=True, kw_only=True)
class SerializeOptions:
    """Options for serialize(). They apply to one call only, nothing is global."""

    # 'pretty' writes the normalised calibre format, 'preserve' writes
    # unmodified objects exactly as they were in the source
    style: Literal['pretty', 'preserve'] = 'pretty'
    indent: str = '  '
    normalize: bool = True
    drop_empty_rules: bool = True
    keep_comments: bool = True


DEFAULT_OPTIONS = SerializeOptions()


def serialize(obj: Stylesheet | Rule, options: SerializeOptions = DEFAULT_OPTIONS) -> str:
    """Serialize a stylesheet or rule to text."""
    if options.style == 'preserve' and obj._source is not None:
        return obj._source.source_text
    raise NotImplementedError('pretty serialization, and serialization of constructed objects, are milestone 4')
