# License: GPLv3 Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

"""Decoding stylesheets given as bytes.

Implements "decode bytes" from CSS Syntax Level 3 §3.2, with encoding labels
and decoders from the WHATWG Encoding Standard. Single-byte encodings use the
exact WHATWG tables, which differ from Python's codecs for some bytes (for
example windows-1252, which is what the "latin1", "iso-8859-1" and "ascii"
labels mean on the web). Multi-byte legacy encodings use the closest Python
codec.

Positions in the parsed stylesheet refer to the decoded text, which never
includes a byte order mark.
"""

import codecs
from typing import Final

from kgcss._encodings import LABELS, SINGLE_BYTE

type Buffer = bytes | bytearray | memoryview

# The @charset rule has to match this exact byte pattern, in the first 1024 bytes
CHARSET_PREFIX: Final = b'@charset "'
CHARSET_SCAN_LIMIT: Final = 1024

BOMS: Final[tuple[tuple[bytes, str], ...]] = (
    (codecs.BOM_UTF8, 'utf-8'),
    (codecs.BOM_UTF16_BE, 'utf-16be'),
    (codecs.BOM_UTF16_LE, 'utf-16le'),
)

# WHATWG multi-byte encodings and the Python codecs used to decode them
PYTHON_CODECS: Final[dict[str, str]] = {
    'utf-8': 'utf-8',
    'utf-16be': 'utf-16-be',
    'utf-16le': 'utf-16-le',
    'gbk': 'gb18030',  # the WHATWG GBK decoder is the gb18030 decoder
    'gb18030': 'gb18030',
    'big5': 'big5hkscs',  # WHATWG Big5 includes the HKSCS extensions
    'euc-jp': 'euc_jp',
    'iso-2022-jp': 'iso2022_jp',
    'shift_jis': 'cp932',  # WHATWG Shift_JIS is Microsoft's variant
    'euc-kr': 'cp949',  # WHATWG EUC-KR is Microsoft's superset
}

ASCII_WHITESPACE: Final = '\t\n\f\r '
_ASCII = ''.join(map(chr, range(128)))
# 256 entry decoding tables for codecs.charmap_decode, U+FFFE marks errors
CHARMAPS: Final[dict[str, str]] = {name: _ASCII + table for name, table in SINGLE_BYTE.items()}
CHARMAPS['x-user-defined'] = _ASCII + ''.join(chr(0xF780 + b) for b in range(128))


def lookup_encoding(label: str) -> str | None:
    """Return the lowercase WHATWG name of the encoding for label, or None.

    Implements "get an encoding": surrounding ASCII whitespace is ignored and
    matching is ASCII case-insensitive.
    """
    label = label.strip(ASCII_WHITESPACE)
    if not label.isascii():
        return None
    return LABELS.get(label.lower())


def decode_with(data: Buffer, encoding: str) -> str:
    """Decode data with the named WHATWG encoding (as returned by lookup_encoding()), without BOM sniffing."""
    if (table := CHARMAPS.get(encoding)) is not None:
        return codecs.charmap_decode(data, 'replace', table)[0]
    if encoding == 'replacement':
        return '�' if len(data) else ''
    return str(data, PYTHON_CODECS[encoding], 'replace')


def charset_rule_encoding(data: Buffer) -> str | None:
    """The encoding named by an @charset rule at the start of data, following CSS Syntax §3.2."""
    head = bytes(data[:CHARSET_SCAN_LIMIT])
    if not head.startswith(CHARSET_PREFIX):
        return None
    end = head.find(b'"', len(CHARSET_PREFIX))
    if end < 0 or head[end + 1 : end + 2] != b';':
        return None
    encoding = lookup_encoding(head[len(CHARSET_PREFIX) : end].decode('latin-1'))
    # @charset can only select ASCII compatible encodings
    if encoding in ('utf-16be', 'utf-16le'):
        return 'utf-8'
    return encoding


def fallback_encoding(data: Buffer, protocol_encoding: str | None, environment_encoding: str | None) -> str:
    """Determine the fallback encoding (CSS Syntax §3.2): protocol, then @charset, then environment, then UTF-8."""
    if protocol_encoding is not None and (encoding := lookup_encoding(protocol_encoding)) is not None:
        return encoding
    if (encoding := charset_rule_encoding(data)) is not None:
        return encoding
    if environment_encoding is not None and (encoding := lookup_encoding(environment_encoding)) is not None:
        return encoding
    return 'utf-8'


def decode_stylesheet(
    data: Buffer,
    *,
    protocol_encoding: str | None = None,
    environment_encoding: str | None = None,
) -> tuple[str, str]:
    """Decode a stylesheet, returning (text, encoding name).

    A byte order mark takes precedence over everything and is removed.
    Otherwise the encoding comes from protocol_encoding (e.g. the charset
    parameter of an HTTP Content-Type header), then an @charset rule, then
    environment_encoding (e.g. the encoding of the referring document), and
    finally defaults to UTF-8. Unknown labels are ignored. Invalid bytes
    become U+FFFD, decoding never fails.
    """
    view = memoryview(data).cast('B')
    for bom, encoding in BOMS:
        if view[: len(bom)] == bom:
            return decode_with(view[len(bom) :], encoding), encoding
    encoding = fallback_encoding(view, protocol_encoding, environment_encoding)
    return decode_with(view, encoding), encoding
