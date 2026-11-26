/*
 * serialize.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * CSSOM serialization primitives:
 * https://drafts.csswg.org/cssom/#common-serializing-idioms
 *
 * These functions only read immutable str objects and keep no shared state,
 * so they are safe to call concurrently without the GIL.
 */

#include "kgcss.h"

/* Size of "\" + lowercase hex digits + " " for a code point */
static Py_ssize_t
hex_escape_len(Py_UCS4 ch) {
    Py_ssize_t n = 1;
    do { n++; ch >>= 4; } while (ch);
    return n + 1;
}

static Py_ssize_t
write_hex_escape(int kind, void *data, Py_ssize_t pos, Py_UCS4 ch) {
    static const char hexdigits[] = "0123456789abcdef";
    char buf[8];
    int n = 0;
    do { buf[n++] = hexdigits[ch & 0xf]; ch >>= 4; } while (ch);
    PyUnicode_WRITE(kind, data, pos++, '\\');
    while (n) PyUnicode_WRITE(kind, data, pos++, (Py_UCS4)buf[--n]);
    PyUnicode_WRITE(kind, data, pos++, ' ');
    return pos;
}

static inline bool
is_control(Py_UCS4 ch) { return (ch >= 0x1 && ch <= 0x1f) || ch == 0x7f; }

static PyObject *
check_str(PyObject *arg) {
    if (!PyUnicode_Check(arg)) {
        PyErr_Format(PyExc_TypeError, "expected str, got %.200s", Py_TYPE(arg)->tp_name);
        return NULL;
    }
    return arg;
}

PyObject *
kg_serialize_string(PyObject *module, PyObject *arg) {
    KG_UNUSED(module);
    if (check_str(arg) == NULL) return NULL;
    const Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    const int in_kind = PyUnicode_KIND(arg);
    const void *in_data = PyUnicode_DATA(arg);

    /* First pass: compute output size and maximum code point */
    Py_ssize_t out_len = 2;
    Py_UCS4 maxchar = 0x7f;
    for (Py_ssize_t i = 0; i < len; i++) {
        Py_UCS4 ch = PyUnicode_READ(in_kind, in_data, i);
        if (ch == 0) { out_len++; if (maxchar < 0xfffd) maxchar = 0xfffd; }
        else if (is_control(ch)) out_len += hex_escape_len(ch);
        else if (ch == '"' || ch == '\\') out_len += 2;
        else { out_len++; if (ch > maxchar) maxchar = ch; }
    }

    PyObject *ans = PyUnicode_New(out_len, maxchar);
    if (ans == NULL) return NULL;
    const int kind = PyUnicode_KIND(ans);
    void *data = PyUnicode_DATA(ans);

    /* Second pass: write */
    Py_ssize_t pos = 0;
    PyUnicode_WRITE(kind, data, pos++, '"');
    for (Py_ssize_t i = 0; i < len; i++) {
        Py_UCS4 ch = PyUnicode_READ(in_kind, in_data, i);
        if (ch == 0) PyUnicode_WRITE(kind, data, pos++, 0xfffd);
        else if (is_control(ch)) pos = write_hex_escape(kind, data, pos, ch);
        else if (ch == '"' || ch == '\\') {
            PyUnicode_WRITE(kind, data, pos++, '\\');
            PyUnicode_WRITE(kind, data, pos++, ch);
        } else PyUnicode_WRITE(kind, data, pos++, ch);
    }
    PyUnicode_WRITE(kind, data, pos++, '"');
    assert(pos == out_len);
    return ans;
}

static inline bool
is_ident_char(Py_UCS4 ch) {
    return ch >= 0x80 || ch == '-' || ch == '_' ||
        (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z');
}

typedef enum { EMIT_LITERAL, EMIT_REPLACEMENT, EMIT_HEX, EMIT_BACKSLASH } emit_type;

static inline emit_type
classify_ident_char(Py_UCS4 ch, Py_ssize_t i, Py_ssize_t len, Py_UCS4 first) {
    if (ch == 0) return EMIT_REPLACEMENT;
    if (is_control(ch)) return EMIT_HEX;
    if (ch >= '0' && ch <= '9' && (i == 0 || (i == 1 && first == '-'))) return EMIT_HEX;
    if (i == 0 && ch == '-' && len == 1) return EMIT_BACKSLASH;
    if (is_ident_char(ch)) return EMIT_LITERAL;
    return EMIT_BACKSLASH;
}

PyObject *
kg_serialize_identifier(PyObject *module, PyObject *arg) {
    KG_UNUSED(module);
    if (check_str(arg) == NULL) return NULL;
    const Py_ssize_t len = PyUnicode_GET_LENGTH(arg);
    if (len == 0) return Py_NewRef(kg_get_module_state(module)->empty_string);
    const int in_kind = PyUnicode_KIND(arg);
    const void *in_data = PyUnicode_DATA(arg);
    const Py_UCS4 first = PyUnicode_READ(in_kind, in_data, 0);

    Py_ssize_t out_len = 0;
    Py_UCS4 maxchar = 0x7f;
    for (Py_ssize_t i = 0; i < len; i++) {
        Py_UCS4 ch = PyUnicode_READ(in_kind, in_data, i);
        switch (classify_ident_char(ch, i, len, first)) {
            case EMIT_REPLACEMENT: out_len++; if (maxchar < 0xfffd) maxchar = 0xfffd; break;
            case EMIT_HEX: out_len += hex_escape_len(ch); break;
            case EMIT_BACKSLASH: out_len += 2; if (ch > maxchar) maxchar = ch; break;
            case EMIT_LITERAL: out_len++; if (ch > maxchar) maxchar = ch; break;
        }
    }

    PyObject *ans = PyUnicode_New(out_len, maxchar);
    if (ans == NULL) return NULL;
    const int kind = PyUnicode_KIND(ans);
    void *data = PyUnicode_DATA(ans);
    Py_ssize_t pos = 0;
    for (Py_ssize_t i = 0; i < len; i++) {
        Py_UCS4 ch = PyUnicode_READ(in_kind, in_data, i);
        switch (classify_ident_char(ch, i, len, first)) {
            case EMIT_REPLACEMENT: PyUnicode_WRITE(kind, data, pos++, 0xfffd); break;
            case EMIT_HEX: pos = write_hex_escape(kind, data, pos, ch); break;
            case EMIT_BACKSLASH:
                PyUnicode_WRITE(kind, data, pos++, '\\');
                PyUnicode_WRITE(kind, data, pos++, ch);
                break;
            case EMIT_LITERAL: PyUnicode_WRITE(kind, data, pos++, ch); break;
        }
    }
    assert(pos == out_len);
    return ans;
}
