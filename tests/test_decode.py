# License: BSD-3-Clause Copyright: 2026, Kovid Goyal <kovid at kovidgoyal.net>

import codecs
from array import array

import pytest

from kgcss import decode_stylesheet, lookup_encoding
from kgcss._encodings import LABELS, SINGLE_BYTE
from kgcss.decode import CHARMAPS, PYTHON_CODECS, decode_with
from kgcss.syntax import AtRule, QualifiedRule, parse_stylesheet


@pytest.mark.parametrize(
    ('label', 'name'),
    [
        ('utf-8', 'utf-8'),
        (' UTF8\t\n', 'utf-8'),
        ('unicode-1-1-utf-8', 'utf-8'),
        ('latin1', 'windows-1252'),
        ('ISO-8859-1', 'windows-1252'),
        ('ascii', 'windows-1252'),
        ('us-ascii', 'windows-1252'),
        ('utf-16', 'utf-16le'),
        ('UTF-16BE', 'utf-16be'),
        ('sjis', 'shift_jis'),
        ('gb2312', 'gbk'),
        ('iso-2022-kr', 'replacement'),
        ('x-user-defined', 'x-user-defined'),
        ('iso-8859-8-i', 'iso-8859-8-i'),
        ('kamoulox', None),
        ('', None),
        ('utf 8', None),
        # Matching is ASCII case-insensitive only: KELVIN SIGN lowercases to "k" in Python
        ('\N{KELVIN SIGN}oi8-r', None),
    ],
)
def test_lookup_encoding(label: str, name: str | None) -> None:
    assert lookup_encoding(label) == name


def test_every_encoding_can_decode() -> None:
    data = bytes(range(256))
    for name in set(LABELS.values()):
        text = decode_with(data, name)
        assert isinstance(text, str)
        assert name in CHARMAPS or name in PYTHON_CODECS or name == 'replacement'


def test_single_byte_tables() -> None:
    data = bytes(range(256))
    assert len(SINGLE_BYTE) == 28
    for name, table in SINGLE_BYTE.items():
        assert len(table) == 128
        text = decode_with(data, name)
        assert len(text) == 256
        assert text[:128] == ''.join(map(chr, range(128)))
        for i, ch in enumerate(table):
            assert text[128 + i] == ('�' if ch == '￾' else ch), (name, i)


def test_windows_1252_differs_from_python_cp1252() -> None:
    # WHATWG maps the five bytes cp1252 leaves undefined to C1 controls
    data = bytes((0x80, 0x81, 0x8D, 0x8F, 0x90, 0x9D, 0x9F))
    assert decode_with(data, 'windows-1252') == '€\x81\x8d\x8f\x90\x9dŸ'
    assert data.decode('cp1252', 'replace') != decode_with(data, 'windows-1252')


def test_unmapped_single_byte() -> None:
    # 0xA5 is not assigned in ISO-8859-3
    assert decode_with(b'a\xa5b', 'iso-8859-3') == 'a�b'


def test_special_encodings() -> None:
    assert decode_with(b'a\x80\xff', 'x-user-defined') == 'a'
    assert decode_with(b'abc', 'replacement') == '�'
    assert decode_with(b'', 'replacement') == ''
    assert decode_with(b'a\x00b', 'utf-16le') == 'a�'
    assert decode_with('日本'.encode('cp932'), 'shift_jis') == '日本'
    assert decode_with('中文'.encode('gb18030'), 'gbk') == '中文'
    assert decode_with('한국'.encode('cp949'), 'euc-kr') == '한국'


def test_bom() -> None:
    assert decode_stylesheet(codecs.BOM_UTF8 + 'é'.encode()) == ('é', 'utf-8')
    assert decode_stylesheet(codecs.BOM_UTF16_LE + 'é'.encode('utf-16-le')) == ('é', 'utf-16le')
    assert decode_stylesheet(codecs.BOM_UTF16_BE + 'é'.encode('utf-16-be')) == ('é', 'utf-16be')
    # The BOM wins over everything else
    data = codecs.BOM_UTF8 + b'@charset "iso-8859-5"; \xc3\xa9'
    assert decode_stylesheet(data, protocol_encoding='iso-8859-2', environment_encoding='iso-8859-5') == (
        '@charset "iso-8859-5"; é',
        'utf-8',
    )


def test_fallback_order() -> None:
    data = b'@charset "iso-8859-5"; \xe9'
    assert decode_stylesheet(data)[1] == 'iso-8859-5'
    assert decode_stylesheet(data, protocol_encoding='iso-8859-2')[1] == 'iso-8859-2'
    assert decode_stylesheet(data, protocol_encoding='bogus')[1] == 'iso-8859-5'
    assert decode_stylesheet(data, environment_encoding='iso-8859-2')[1] == 'iso-8859-5'
    assert decode_stylesheet(b'\xe9', environment_encoding='iso-8859-2') == ('é', 'iso-8859-2')
    assert decode_stylesheet(b'\xe9', environment_encoding='bogus') == ('�', 'utf-8')
    assert decode_stylesheet(b'') == ('', 'utf-8')


def test_charset_rule() -> None:
    assert decode_stylesheet(b'@charset "utf-16";')[1] == 'utf-8'
    assert decode_stylesheet(b'@charset "UTF-16BE";')[1] == 'utf-8'
    assert decode_stylesheet(b'@charset "latin1"; a')[1] == 'windows-1252'
    assert decode_stylesheet(b'@charset "x-user-defined";')[1] == 'x-user-defined'
    # The exact byte pattern is required
    for data in (b'@CHARSET "latin1";', b'@charset  "latin1";', b"@charset 'latin1';", b'@charset "latin1" ;'):
        assert decode_stylesheet(data)[1] == 'utf-8', data
    assert decode_stylesheet(b' @charset "latin1";')[1] == 'utf-8'
    assert decode_stylesheet(b'@charset "latin1"')[1] == 'utf-8'
    # ... within the first 1024 bytes
    padded = b'@charset "' + b' ' * 1011 + b'latin1";'
    assert len(padded) > 1024
    assert decode_stylesheet(padded)[1] == 'utf-8'
    assert decode_stylesheet(b'@charset "' + b' ' * 1005 + b'latin1";')[1] == 'windows-1252'


def test_buffer_types() -> None:
    expected = ('é', 'utf-8')
    assert decode_stylesheet(bytearray('é'.encode())) == expected
    assert decode_stylesheet(memoryview('é'.encode())) == expected
    assert decode_stylesheet(memoryview(b'x\xc3\xa9x')[1:3]) == expected
    # Non byte formats are reinterpreted as raw bytes: U+FEFF in native byte order is a UTF-16 BOM
    wide = array('H', [0xFEFF, ord('a')])
    assert decode_stylesheet(memoryview(wide)) == ('a', 'utf-16le' if wide.tobytes()[0] == 0xFF else 'utf-16be')


def test_parse_stylesheet_from_bytes() -> None:
    sheet = parse_stylesheet(codecs.BOM_UTF8 + b'a { b: "\xc3\xa9" }')
    assert sheet.encoding == 'utf-8'
    # Positions refer to the decoded text, which has no BOM
    (rule,) = sheet
    assert isinstance(rule, QualifiedRule)
    assert rule.pos == 0
    assert sheet.stream.source == 'a { b: "é" }'
    sheet = parse_stylesheet(b'@charset "windows-1251"; @\xe0', comments=False)
    assert sheet.encoding == 'windows-1251'
    at = sheet[1]
    assert isinstance(at, AtRule)
    assert at.name == '\N{CYRILLIC SMALL LETTER A}'
    sheet = parse_stylesheet(b'@\xe9', protocol_encoding='iso-8859-2')
    assert sheet.encoding == 'iso-8859-2'
    assert parse_stylesheet('a{}').encoding is None
    assert parse_stylesheet(bytearray(b'a{}')).encoding == 'utf-8'
