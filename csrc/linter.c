/*
 * linter.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * C versions of string functions of the linter's port of
 * postcss-selector-parser, kgcss/linter/_selector_parser.py, which has
 * their reference versions: unesc(), cssesc() and tokenize().
 * tests/test_linter_selector_c.py compares them on random input.
 */

#include "kgcss.h"
#include "objects.h"

static bool
is_hex(Py_UCS4 c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static unsigned
hex_value(Py_UCS4 c) {
    if (c <= '9') return c - '0';
    if (c <= 'F') return c - 'A' + 10;
    return c - 'a' + 10;
}

/* unesc() */
PyObject *
kg_selector_unesc(PyObject *module, PyObject *text) {
    KG_UNUSED(module);
    if (!PyUnicode_Check(text)) {
        PyErr_Format(PyExc_TypeError, "selector_unesc() expects a str, not %.200s", Py_TYPE(text)->tp_name);
        return NULL;
    }
    const Py_ssize_t n = PyUnicode_GET_LENGTH(text);
    if (PyUnicode_FindChar(text, '\\', 0, n, 1) == -1) return Py_NewRef(text);
    const int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
#define C(i) PyUnicode_READ(kind, data, (i))
    Py_UCS4 *out = PyMem_Malloc((size_t)(n ? n : 1) * sizeof(Py_UCS4));
    if (out == NULL) return PyErr_NoMemory();
    Py_ssize_t o = 0, i = 0;
    while (i < n) {
        const Py_UCS4 c = C(i);
        if (c != '\\') {
            out[o++] = c;
            i++;
            continue;
        }
        /* gobbleHex(str.slice(i + 1, i + 9)) */
        Py_ssize_t j = i + 1;
        const Py_ssize_t end = i + 7 < n ? i + 7 : n;
        uint32_t cp = 0;
        while (j < end && is_hex(C(j))) {
            cp = cp * 16 + hex_value(C(j));
            j++;
        }
        const Py_ssize_t hex_len = j - i - 1;
        if (hex_len) {
            Py_ssize_t consumed = hex_len;
            const Py_ssize_t at = i + 1 + consumed;
            const Py_UCS4 nxt = (at < n && consumed < 8) ? C(at) : 0x110000;
            if (nxt == ' ' || nxt == '\t' || nxt == '\n' || nxt == '\r' || nxt == '\f') {
                const Py_ssize_t after = i + 2 + consumed;
                consumed += (nxt == '\r' && after < n && after < i + 9 && C(after) == '\n') ? 2 : 1;
            }
            out[o++] = ((cp >= 0xD800 && cp <= 0xDFFF) || cp == 0 || cp > 0x10FFFF) ? 0xFFFD : (Py_UCS4)cp;
            i += consumed + 1;
            continue;
        }
        if (i + 1 < n && C(i + 1) == '\\') {
            out[o++] = '\\';
            i += 2;
            continue;
        }
        if (n == i + 1) out[o++] = c;
        i++;
    }
#undef C
    PyObject *ans = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, out, o);
    PyMem_Free(out);
    return ans;
}

/* _single_escape(): /[ -,./:-@[\]^`{-~]/ */
static bool
single_escape(Py_UCS4 c) {
    return (c >= 0x20 && c <= 0x2C) || c == '.' || c == '/' || c == '[' || c == ']' || c == '^' || c == '`' ||
           (c >= 0x3A && c <= 0x40) || (c >= 0x7B && c <= 0x7E);
}

static bool
is_upper_hex(Py_UCS4 c) {
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F');
}

static bool
is_hex_or_space(Py_UCS4 c) {
    return is_upper_hex(c) || (c >= 'a' && c <= 'f') || c == ' ';
}

/* _remove_excessive_spaces(), in place: returns the new length */
static Py_ssize_t
remove_excessive_spaces(Py_UCS4 *s, Py_ssize_t length) {
    Py_ssize_t out = 0, copied = 0, i = 0;
    bool changed = false;
#define CODE(k) ((k) < length ? s[k] : 0x110000)
    while (i < length) {
        if (s[i] != '\\') { i++; continue; }
        Py_ssize_t run_end = i + 1;
        while (run_end < length && s[run_end] == '\\') run_end++;
        Py_ssize_t hex_end = run_end;
        while (hex_end < length && hex_end - run_end < 7 && is_upper_hex(s[hex_end])) hex_end++;
        const Py_ssize_t hex_length = hex_end - run_end;
        if (hex_length >= 1 && hex_length <= 6 && CODE(hex_end) == ' ' && !is_hex_or_space(CODE(hex_end + 1))) {
            if ((run_end - i - 1) % 2 == 0) {
                /* result.append(output[copied:hex_end]); copied = hex_end + 1 */
                memmove(s + out, s + copied, (size_t)(hex_end - copied) * sizeof(Py_UCS4));
                out += hex_end - copied;
                copied = hex_end + 1;
                changed = true;
            }
            i = hex_end + 1;
        } else {
            i = run_end;
        }
    }
#undef CODE
    if (!changed) return length;
    memmove(s + out, s + copied, (size_t)(length - copied) * sizeof(Py_UCS4));
    return out + length - copied;
}

static void
append_hex(Py_UCS4 *out, Py_ssize_t *o, Py_UCS4 c) {
    char buf[16];
    const int len = PyOS_snprintf(buf, sizeof(buf), "%X", (unsigned)c);
    for (int k = 0; k < len; k++) out[(*o)++] = (Py_UCS4)(unsigned char)buf[k];
}

/* cssesc(text, is_identifier=..., quotes=..., wrap=...) */
PyObject *
kg_selector_cssesc(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    KG_UNUSED(module);
    if (nargs != 4 || !PyUnicode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "selector_cssesc() takes a str and 3 bools");
        return NULL;
    }
    PyObject *text = args[0];
    const int is_identifier = PyObject_IsTrue(args[1]), double_quotes = PyObject_IsTrue(args[2]),
              wrap = PyObject_IsTrue(args[3]);
    if (is_identifier < 0 || double_quotes < 0 || wrap < 0) return NULL;
    const Py_UCS4 quote = double_quotes ? '"' : '\'';
    const Py_ssize_t n = PyUnicode_GET_LENGTH(text);
    const int kind = PyUnicode_KIND(text);
    const void *data = PyUnicode_DATA(text);
    /* At most \XXXXXX and a space for each character, a \3 prefix and the quotes */
    if (n > (PY_SSIZE_T_MAX / (Py_ssize_t)sizeof(Py_UCS4) - 8) / 8) return PyErr_NoMemory();
    Py_UCS4 *out = PyMem_Malloc((size_t)(8 * n + 8) * sizeof(Py_UCS4));
    if (out == NULL) return PyErr_NoMemory();
    Py_ssize_t o = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        const Py_UCS4 c = PyUnicode_READ(kind, data, i);
        if (c < 0x20 || c > 0x7E) {
            out[o++] = '\\';
            append_hex(out, &o, c);
            out[o++] = ' ';
        } else if (c == '\\' || (!is_identifier && c == quote) || (is_identifier && single_escape(c))) {
            out[o++] = '\\';
            out[o++] = c;
        } else {
            out[o++] = c;
        }
    }
    Py_UCS4 *s = out + 3;  /* room for a prefix */
    memmove(s, out, (size_t)o * sizeof(Py_UCS4));
    Py_ssize_t len = o;
    if (is_identifier) {
        const Py_UCS4 first = n ? PyUnicode_READ(kind, data, 0) : 0;
        if (len > 1 && s[0] == '-' && (s[1] == '-' || (s[1] >= '0' && s[1] <= '9'))) {
            /* '\\-' + output[1:] */
            s--;
            s[0] = '\\';
            len++;
        } else if (n && first >= '0' && first <= '9') {
            /* '\\3' + first + ' ' + output[1:] */
            s -= 3;
            s[0] = '\\';
            s[1] = '3';
            s[2] = first;
            s[3] = ' ';
            len += 3;
        }
    }
    len = remove_excessive_spaces(s, len);
    PyObject *ans;
    if (!is_identifier && wrap) {
        /* There is room: the buffer holds 8 characters more than the escaped text needs */
        Py_UCS4 *w = out;
        memmove(w + 1, s, (size_t)len * sizeof(Py_UCS4));
        w[0] = quote;
        w[len + 1] = quote;
        ans = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, w, len + 2);
    } else {
        ans = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, s, len);
    }
    PyMem_Free(out);
    return ans;
}

/* The token types of tokenize(): character codes, and these */
enum { TK_COMMENT = -1, TK_WORD = -2, TK_COMBINATOR = -3 };

static bool
is_space_code(Py_UCS4 c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f';
}

static bool
is_combinator_code(Py_UCS4 c) {
    return c == '+' || c == '>' || c == '~' || c == '|';
}

static bool
is_single_token(Py_UCS4 c) {
    switch (c) {
        case '*': case '&': case '!': case ',': case '=': case '$': case '^': case '[': case ']': case ':': case ';':
        case '(': case ')':
            return true;
        default:
            return false;
    }
}

static bool
is_word_delimiter(Py_UCS4 c) {
    switch (c) {
        case ' ': case '\t': case '\n': case '\r': case '\f': case '&': case '*': case '!': case ',': case ':':
        case ';': case '(': case ')': case '[': case ']': case '\'': case '"': case '+': case '|': case '~':
        case '>': case '=': case '$': case '^': case '/':
            return true;
        default:
            return false;
    }
}

#define NO_CHAR 0x110000
#define CODE_AT(i) (((i) >= 0 && (i) < length) ? PyUnicode_READ(kind, data, (i)) : NO_CHAR)

/* _consume_escape() */
static Py_ssize_t
consume_escape(int kind, const void *data, Py_ssize_t length, Py_ssize_t start) {
    Py_ssize_t nxt = start;
    Py_UCS4 code = CODE_AT(nxt + 1);
    if (code == '\t' || code == '\n' || code == '\r' || code == '\f') {
        /* unescapable */
    } else if (is_hex(code)) {
        int hex_digits = 0;
        do {
            nxt++;
            hex_digits++;
            code = CODE_AT(nxt + 1);
        } while (is_hex(code) && hex_digits < 6);
        if (hex_digits < 6 && code == ' ') nxt++;
    } else {
        nxt++;
    }
    return nxt;
}

/* _consume_word() */
static Py_ssize_t
consume_word(int kind, const void *data, Py_ssize_t length, Py_ssize_t start) {
    Py_ssize_t nxt = start;
    while (true) {
        const Py_UCS4 code = CODE_AT(nxt);
        if (is_word_delimiter(code)) return nxt - 1;
        if (code == '\\') nxt = consume_escape(kind, data, length, nxt) + 1;
        else nxt++;
        if (nxt >= length) return nxt - 1;
    }
}

static int
append_token(PyObject *tokens, long type, Py_ssize_t line, Py_ssize_t col, Py_ssize_t end_line, Py_ssize_t end_col,
             Py_ssize_t start, Py_ssize_t end) {
    PyObject *t = Py_BuildValue("(lnnnnnn)", type, line, col, end_line, end_col, start, end);
    if (t == NULL) return -1;
    const int r = PyList_Append(tokens, t);
    Py_DECREF(t);
    return r;
}

/* tokenize(): a list of tokens, or the message of the error */
PyObject *
kg_selector_tokenize(PyObject *module, PyObject *css) {
    KG_UNUSED(module);
    if (!PyUnicode_Check(css)) {
        PyErr_Format(PyExc_TypeError, "selector_tokenize() expects a str, not %.200s", Py_TYPE(css)->tp_name);
        return NULL;
    }
    const Py_ssize_t length = PyUnicode_GET_LENGTH(css);
    const int kind = PyUnicode_KIND(css);
    const void *data = PyUnicode_DATA(css);
    PyObject *tokens = PyList_New(0);
    if (tokens == NULL) return NULL;
    Py_ssize_t offset = -1, line = 1, start = 0, next_offset = 0;
    bool has_next_offset = false;
    while (start < length) {
        Py_UCS4 code = CODE_AT(start);
        long type;
        Py_ssize_t end_line, end_column, end, nxt;
        if (code == '\n') {
            offset = start;
            line++;
        }
        if (is_space_code(code)) {
            nxt = start;
            while (true) {
                nxt++;
                code = CODE_AT(nxt);
                if (code == '\n') {
                    offset = nxt;
                    line++;
                }
                if (!is_space_code(code)) break;
            }
            type = ' ';
            end_line = line;
            end_column = nxt - offset - 1;
            end = nxt;
        } else if (is_combinator_code(code)) {
            nxt = start;
            do {
                nxt++;
                code = CODE_AT(nxt);
            } while (is_combinator_code(code));
            type = TK_COMBINATOR;
            end_line = line;
            end_column = start - offset;
            end = nxt;
        } else if (is_single_token(code)) {
            type = (long)code;
            end_line = line;
            end_column = start - offset;
            end = start + 1;
        } else if (code == '\'' || code == '"') {
            const Py_UCS4 quote = code;
            nxt = start;
            while (true) {
                bool escaped = false;
                nxt++;
                while (nxt < length && PyUnicode_READ(kind, data, nxt) != quote) nxt++;
                if (nxt >= length) {
                    Py_DECREF(tokens);
                    return PyUnicode_FromString("Unclosed quote");
                }
                Py_ssize_t escape_pos = nxt;
                while (CODE_AT(escape_pos - 1) == '\\') {
                    escape_pos--;
                    escaped = !escaped;
                }
                if (!escaped) break;
            }
            type = '\'';
            end_line = line;
            end_column = start - offset;
            end = nxt + 1;
        } else {
            if (code == '/' && CODE_AT(start + 1) == '*') {
                nxt = -1;
                for (Py_ssize_t i = start + 2; i + 1 < length; i++) {
                    if (PyUnicode_READ(kind, data, i) == '*' && PyUnicode_READ(kind, data, i + 1) == '/') {
                        nxt = i;
                        break;
                    }
                }
                nxt += 1;
                if (nxt == 0) {
                    Py_DECREF(tokens);
                    return PyUnicode_FromString("Unclosed comment");
                }
                /* content = css[start:nxt + 1] */
                Py_ssize_t newlines = 0, last_newline = -1;
                for (Py_ssize_t i = start; i <= nxt; i++) {
                    if (PyUnicode_READ(kind, data, i) == '\n') {
                        newlines++;
                        last_newline = i;
                    }
                }
                Py_ssize_t next_line;
                if (newlines) {
                    next_line = line + newlines;
                    /* nxt - (len(content) - content.rfind('\n') - 1) */
                    next_offset = nxt - ((nxt + 1 - start) - (last_newline - start) - 1);
                } else {
                    next_line = line;
                    next_offset = offset;
                }
                has_next_offset = true;
                type = TK_COMMENT;
                line = next_line;
                end_line = next_line;
                end_column = nxt - next_offset;
            } else if (code == '/') {
                nxt = start;
                type = '/';
                end_line = line;
                end_column = start - offset;
            } else {
                nxt = consume_word(kind, data, length, start);
                type = TK_WORD;
                end_line = line;
                end_column = nxt - offset;
            }
            end = nxt + 1;
        }
        if (append_token(tokens, type, line, start - offset, end_line, end_column, start, end) != 0) {
            Py_DECREF(tokens);
            return NULL;
        }
        if (has_next_offset) {
            if (next_offset) offset = next_offset;  /* 0 is false in JavaScript too */
            has_next_offset = false;
        }
        start = end;
    }
    return tokens;
}
#undef CODE_AT
#undef NO_CHAR
