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

static PyObject *cssesc(PyObject *text, bool is_identifier, bool double_quotes, bool wrap);

/* cssesc(text, is_identifier=..., quotes=..., wrap=...) */
PyObject *
kg_selector_cssesc(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    KG_UNUSED(module);
    if (nargs != 4 || !PyUnicode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "selector_cssesc() takes a str and 3 bools");
        return NULL;
    }
    const int is_identifier = PyObject_IsTrue(args[1]), double_quotes = PyObject_IsTrue(args[2]),
              wrap = PyObject_IsTrue(args[3]);
    if (is_identifier < 0 || double_quotes < 0 || wrap < 0) return NULL;
    return cssesc(args[0], is_identifier, double_quotes, wrap);
}

static PyObject *
cssesc(PyObject *text, bool is_identifier, bool double_quotes, bool wrap) {
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

/* A token of tokenize(): its fields in the order of the tuples */
typedef struct {
    long type;
    Py_ssize_t line, col, end_line, end_col, start, end;
} stoken;

typedef struct {
    stoken *items;
    Py_ssize_t len, cap;
} stokens;

static int
stokens_push(stokens *t, stoken tok) {
    if (t->len == t->cap) {
        const Py_ssize_t cap = t->cap ? t->cap * 2 : 32;
        stoken *items = PyMem_Realloc(t->items, (size_t)cap * sizeof(stoken));
        if (items == NULL) { PyErr_NoMemory(); return -1; }
        t->items = items;
        t->cap = cap;
    }
    t->items[t->len++] = tok;
    return 0;
}

/* tokenize() into out: 0, -1 with a Python error, or 1 with the message of the error in *error */
static int
tokenize_into(PyObject *css, stokens *out, const char **error) {
    const Py_ssize_t length = PyUnicode_GET_LENGTH(css);
    const int kind = PyUnicode_KIND(css);
    const void *data = PyUnicode_DATA(css);
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
                    *error = "Unclosed quote";
                    return 1;
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
                    *error = "Unclosed comment";
                    return 1;
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
        if (stokens_push(out, (stoken){type, line, start - offset, end_line, end_column, start, end}) != 0) return -1;
        if (has_next_offset) {
            if (next_offset) offset = next_offset;  /* 0 is false in JavaScript too */
            has_next_offset = false;
        }
        start = end;
    }
    return 0;
}

/* tokenize(): a list of tokens, or the message of the error */
PyObject *
kg_selector_tokenize(PyObject *module, PyObject *css) {
    KG_UNUSED(module);
    if (!PyUnicode_Check(css)) {
        PyErr_Format(PyExc_TypeError, "selector_tokenize() expects a str, not %.200s", Py_TYPE(css)->tp_name);
        return NULL;
    }
    stokens t = {0};
    const char *error = NULL;
    const int r = tokenize_into(css, &t, &error);
    PyObject *ans = NULL;
    if (r == 1) {
        ans = PyUnicode_FromString(error);
    } else if (r == 0 && (ans = PyList_New(t.len)) != NULL) {
        for (Py_ssize_t i = 0; i < t.len; i++) {
            const stoken k = t.items[i];
            PyObject *tuple = Py_BuildValue("(lnnnnnn)", k.type, k.line, k.col, k.end_line, k.end_col, k.start, k.end);
            if (tuple == NULL) { Py_CLEAR(ans); break; }
            PyList_SET_ITEM(ans, i, tuple);
        }
    }
    PyMem_Free(t.items);
    return ans;
}
#undef CODE_AT
#undef NO_CHAR

/* The selector parser {{{
 *
 * The C version of _Parser in kgcss/linter/_selector_parser.py, for the
 * selectors that most sheets have: type, class and id selectors, *, &,
 * combinators, pseudo-classes and pseudo-elements with selectors as
 * arguments, and lists. For anything else (comments, attributes, strings,
 * namespaces, named combinators, the parentheses of no pseudo) and for
 * every error, it gives NotImplemented and the Python parser parses the
 * whole selector. Each function names the method of _Parser it ports.
 *
 * Nodes are instances of the Python classes, made as their __init__ would
 * make them: SelectorParser knows where their slots are.
 */

/* The node classes, in the order SelectorParser() takes them */
enum { SN_ROOT, SN_SELECTOR, SN_PSEUDO, SN_CLASS, SN_ID, SN_TAG, SN_UNIVERSAL, SN_COMBINATOR, SN_NESTING, SN_STRING,
       SN_COMMENT, SN_ATTRIBUTE, SN_COUNT };
static const char *const SN_NAMES[SN_COUNT] = {
    "Root", "Selector", "Pseudo", "ClassName", "ID", "Tag", "Universal", "Combinator", "Nesting", "String", "Comment",
    "Attribute"};

/* The slots of the nodes */
enum { SS_VALUE, SS_PARENT, SS_RAWS, SS_SOURCE, SS_SOURCE_INDEX, SS_SPACES, SS_NODES, SS_LAST_EACH, SS_INDEXES,
       SS_TRAILING_COMMA, SS_CONSTRUCTED, SS_NAMESPACE, SS__NAMESPACE, SS_ATTR_VALUE, SS_ATTRIBUTE, SS_INSENSITIVE,
       SS_QUOTE_MARK, SS_RAWS_PENDING, SS_SENSITIVE, SS_OPERATOR, SS_COUNT };
static const char *const SS_NAMES[SS_COUNT] = {
    "_value", "parent", "raws", "source", "source_index", "spaces", "nodes", "last_each", "indexes",
    "trailing_comma", "_constructed", "namespace", "_namespace", "_attr_value", "_attribute", "_insensitive",
    "_quote_mark", "_raws_pending", "_sensitive", "operator"};

/* Whether a class has a slot */
static bool
has_slot(int cls, int slot) {
    if (slot <= SS_SPACES) return true;
    switch (slot) {
        case SS_NODES: case SS_LAST_EACH: case SS_INDEXES:
            return cls == SN_ROOT || cls == SN_SELECTOR || cls == SN_PSEUDO;
        case SS_TRAILING_COMMA: return cls == SN_ROOT;
        case SS_CONSTRUCTED: return cls == SN_CLASS || cls == SN_COMBINATOR || cls == SN_ATTRIBUTE;
        case SS_NAMESPACE: return cls == SN_CLASS || cls == SN_ID;
        case SS__NAMESPACE: return cls == SN_TAG || cls == SN_UNIVERSAL || cls == SN_ATTRIBUTE;
        case SS_ATTR_VALUE: case SS_ATTRIBUTE: case SS_INSENSITIVE: case SS_QUOTE_MARK: case SS_RAWS_PENDING:
        case SS_SENSITIVE: case SS_OPERATOR:
            return cls == SN_ATTRIBUTE;
        default: return false;
    }
}

typedef struct {
    PyObject_HEAD
    PyTypeObject *classes[SN_COUNT];
    /* attribute_node(css, tokens, position): the Attribute of the tokens from position, as _Parser makes it */
    PyObject *attribute_node;
    /* The offsets of the slots in the instances of each class, -1 for none */
    Py_ssize_t offsets[SN_COUNT][SS_COUNT];
} kg_SelectorParser;

static int
SelectorParser_traverse(PyObject *o, visitproc visit, void *arg) {
    kg_SelectorParser *self = (kg_SelectorParser *)o;
    Py_VISIT(Py_TYPE(o));
    for (int i = 0; i < SN_COUNT; i++) Py_VISIT(self->classes[i]);
    Py_VISIT(self->attribute_node);
    return 0;
}

static int
SelectorParser_clear(PyObject *o) {
    kg_SelectorParser *self = (kg_SelectorParser *)o;
    for (int i = 0; i < SN_COUNT; i++) Py_CLEAR(self->classes[i]);
    Py_CLEAR(self->attribute_node);
    return 0;
}

static void
SelectorParser_dealloc(PyObject *o) {
    PyTypeObject *tp = Py_TYPE(o);
    PyObject_GC_UnTrack(o);
    SelectorParser_clear(o);
    tp->tp_free(o);
    Py_DECREF(tp);
}

/* The offset of the member descriptor name of cls, or -1 with an error */
static Py_ssize_t
slot_offset(PyTypeObject *cls, const char *name) {
    PyObject *descr = PyObject_GetAttrString((PyObject *)cls, name);
    if (descr == NULL) return -1;
    Py_ssize_t ans = -1;
    if (Py_IS_TYPE(descr, &PyMemberDescr_Type)) {
        PyMemberDef *m = ((PyMemberDescrObject *)descr)->d_member;
        if (m->type == Py_T_OBJECT_EX && !(m->flags & Py_READONLY)) ans = m->offset;
    }
    Py_DECREF(descr);
    if (ans < 0) PyErr_Format(PyExc_TypeError, "%s.%s is not a slot", cls->tp_name, name);
    return ans;
}

static PyObject *
SelectorParser_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    if ((kwds != NULL && PyDict_GET_SIZE(kwds)) || PyTuple_GET_SIZE(args) != SN_COUNT + 1 ||
        !PyCallable_Check(PyTuple_GET_ITEM(args, SN_COUNT))) {
        PyErr_Format(PyExc_TypeError, "SelectorParser() takes the %d node classes and attribute_node()", SN_COUNT);
        return NULL;
    }
    kg_SelectorParser *self = (kg_SelectorParser *)type->tp_alloc(type, 0);
    if (self == NULL) return NULL;
    self->attribute_node = Py_NewRef(PyTuple_GET_ITEM(args, SN_COUNT));
    for (int i = 0; i < SN_COUNT; i++) {
        PyObject *cls = PyTuple_GET_ITEM(args, i);
        if (!PyType_Check(cls) || strcmp(((PyTypeObject *)cls)->tp_name, SN_NAMES[i]) != 0) {
            PyErr_Format(PyExc_TypeError, "SelectorParser() argument %d must be the class %s", i + 1, SN_NAMES[i]);
            goto error;
        }
        self->classes[i] = (PyTypeObject *)Py_NewRef(cls);
        for (int j = 0; j < SS_COUNT; j++) {
            self->offsets[i][j] = -1;
            if (has_slot(i, j) && (self->offsets[i][j] = slot_offset(self->classes[i], SS_NAMES[j])) < 0) goto error;
        }
    }
    return (PyObject *)self;
error:
    Py_DECREF(self);
    return NULL;
}

/* The fields of the records, in the order of records.c */
enum { POS_LINE, POS_COLUMN };
enum { SRC_START, SRC_END };
enum { SP_BEFORE, SP_AFTER };
enum { RAWS_VALUE, RAWS_SPACES = 6 };

typedef struct {
    kg_SelectorParser *f;
    kg_module_state *state;
    PyObject *css;
    Py_ssize_t length;
    stokens t;
    Py_ssize_t position;
    int nesting_depth;
    PyObject *token_list;  /* the tokens as tuples, made for attribute_node() when first needed */
    PyObject *spaces;
    PyObject *root, *current;  /* current is borrowed: the tree holds it */
} sparser;

/* The status of the functions: done, a Python error, or NotImplemented */
enum { SP_OK = 0, SP_ERROR = -1, SP_FALLBACK = 1 };

#define SLOT_OF(f, node, cls, slot) (*(PyObject **)((char *)(node) + (f)->offsets[cls][slot]))
#define SLOT(p, node, cls, slot) SLOT_OF((p)->f, node, cls, slot)

/* The class of a node made by the parser */
static int
class_of(const sparser *p, PyObject *node) {
    for (int i = 0; i < SN_COUNT; i++) {
        if (Py_TYPE(node) == p->f->classes[i]) return i;
    }
    return -1;
}

static PyObject **
slot_of(const sparser *p, PyObject *node, int slot) {
    const int cls = class_of(p, node);
    return (PyObject **)((char *)node + p->f->offsets[cls][slot]);
}

static PyObject *
rec_get(PyObject *record, int field) {
    return ((kg_Record *)record)->values[field];
}

static void
rec_set(PyObject *record, int field, PyObject *value) {
    /* Steals value */
    Py_SETREF(((kg_Record *)record)->values[field], value);
}

static PyObject *
position(sparser *p, Py_ssize_t line, Py_ssize_t column) {
    PyObject *ans = kg_record_new(p->state, KG_RECORD_SELECTOR_POSITION);
    if (ans == NULL) return NULL;
    PyObject *l = PyLong_FromSsize_t(line), *c = PyLong_FromSsize_t(column);
    if (l == NULL || c == NULL) { Py_XDECREF(l); Py_XDECREF(c); Py_DECREF(ans); return NULL; }
    rec_set(ans, POS_LINE, l);
    rec_set(ans, POS_COLUMN, c);
    return ans;
}

/* Source(start, end), stealing them; end may be NULL for None */
static PyObject *
source(sparser *p, PyObject *start, PyObject *end) {
    PyObject *ans = NULL;
    if (start != NULL && (ans = kg_record_new(p->state, KG_RECORD_SELECTOR_SOURCE)) != NULL) {
        rec_set(ans, SRC_START, start);
        start = NULL;
        if (end != NULL) { rec_set(ans, SRC_END, end); end = NULL; }
        return ans;
    }
    Py_XDECREF(start);
    Py_XDECREF(end);
    return ans;
}

/* _get_source() */
static PyObject *
get_source(sparser *p, Py_ssize_t start_line, Py_ssize_t start_column, Py_ssize_t end_line, Py_ssize_t end_column) {
    PyObject *start = position(p, start_line, start_column);
    if (start == NULL) return NULL;
    return source(p, start, position(p, end_line, end_column));
}

static PyObject *
token_source(sparser *p, const stoken *t) {
    return get_source(p, t->line, t->col, t->end_line, t->end_col);
}

static PyObject *
content(const sparser *p, const stoken *t) {
    const Py_ssize_t end = t->end > p->length ? p->length : t->end;
    return PyUnicode_Substring(p->css, t->start, end > t->start ? end : t->start);
}

static const stoken *
token_at(const sparser *p, Py_ssize_t i) {
    return (i >= 0 && i < p->t.len) ? &p->t.items[i] : NULL;
}

/* A node of class cls, as Cls(value=value, source=source, source_index=source_index, spaces=spaces, raws=raws)
 * makes it, stealing the objects: spaces and raws may be NULL for new ones, source_index -1 for None */
static PyObject *
new_snode(sparser *p, int cls, PyObject *value, PyObject *src, Py_ssize_t source_index, PyObject *spaces,
          PyObject *raws) {
    PyTypeObject *tp = p->f->classes[cls];
    PyObject *node = NULL;
    if (value == NULL || src == NULL) goto error;
    if (spaces == NULL && (spaces = kg_record_new(p->state, KG_RECORD_SELECTOR_SPACES)) == NULL) goto error;
    if (raws == NULL && (raws = kg_record_new(p->state, KG_RECORD_SELECTOR_RAWS)) == NULL) goto error;
    PyObject *index = source_index < 0 ? Py_NewRef(Py_None) : PyLong_FromSsize_t(source_index);
    if (index == NULL || (node = tp->tp_alloc(tp, 0)) == NULL) { Py_XDECREF(index); goto error; }
    SLOT(p, node, cls, SS_VALUE) = value;
    SLOT(p, node, cls, SS_PARENT) = Py_NewRef(Py_None);
    SLOT(p, node, cls, SS_RAWS) = raws;
    SLOT(p, node, cls, SS_SOURCE) = src;
    SLOT(p, node, cls, SS_SOURCE_INDEX) = index;
    SLOT(p, node, cls, SS_SPACES) = spaces;
    bool ok = true;
    if (has_slot(cls, SS_NODES)) {
        PyObject *nodes = PyList_New(0), *zero = PyLong_FromLong(0);
        if (nodes == NULL || zero == NULL) { Py_XDECREF(nodes); Py_XDECREF(zero); ok = false; }
        else {
            SLOT(p, node, cls, SS_NODES) = nodes;
            SLOT(p, node, cls, SS_LAST_EACH) = zero;
            SLOT(p, node, cls, SS_INDEXES) = Py_NewRef(Py_None);
        }
    }
    if (has_slot(cls, SS_TRAILING_COMMA)) SLOT(p, node, cls, SS_TRAILING_COMMA) = Py_NewRef(Py_False);
    if (has_slot(cls, SS_CONSTRUCTED)) SLOT(p, node, cls, SS_CONSTRUCTED) = Py_NewRef(Py_True);
    if (has_slot(cls, SS_NAMESPACE)) SLOT(p, node, cls, SS_NAMESPACE) = Py_NewRef(Py_None);
    if (has_slot(cls, SS__NAMESPACE)) SLOT(p, node, cls, SS__NAMESPACE) = Py_NewRef(Py_None);
    if (!ok) { Py_DECREF(node); return NULL; }
    return node;
error:
    Py_XDECREF(value);
    Py_XDECREF(src);
    Py_XDECREF(spaces);
    Py_XDECREF(raws);
    return NULL;
}

/* container.append(node), stealing node */
static int
append_node(sparser *p, PyObject *container, PyObject *node) {
    if (node == NULL) return SP_ERROR;
    Py_XSETREF(*slot_of(p, node, SS_PARENT), Py_NewRef(container));
    const int r = PyList_Append(*slot_of(p, container, SS_NODES), node);
    Py_DECREF(node);
    return r == 0 ? SP_OK : SP_ERROR;
}

/* container.last: borrowed, or NULL */
static PyObject *
last_node(const sparser *p, PyObject *container) {
    PyObject *nodes = *slot_of(p, container, SS_NODES);
    const Py_ssize_t n = PyList_GET_SIZE(nodes);
    return n ? PyList_GET_ITEM(nodes, n - 1) : NULL;
}

static PyObject *
concat(PyObject *a, PyObject *b) {
    if (PyUnicode_GET_LENGTH(b) == 0) return Py_NewRef(a);
    if (PyUnicode_GET_LENGTH(a) == 0) return Py_NewRef(b);
    return PyUnicode_Concat(a, b);
}

/* new_node() without a namespace: node takes the pending spaces, stealing node */
static int
add_node(sparser *p, PyObject *node) {
    if (node == NULL) return SP_ERROR;
    if (PyUnicode_GET_LENGTH(p->spaces)) {
        rec_set(*slot_of(p, node, SS_SPACES), SP_BEFORE, p->spaces);
        p->spaces = Py_NewRef(p->state->empty_string);
    }
    return append_node(p, p->current, node);
}

/* Container._infer_end_position() */
static int
infer_end_position(sparser *p, PyObject *container) {
    PyObject *last = last_node(p, container);
    if (last == NULL) return SP_OK;
    PyObject *last_source = *slot_of(p, last, SS_SOURCE);
    if (last_source == Py_None || rec_get(last_source, SRC_END) == Py_None) return SP_OK;
    PyObject *last_end = rec_get(last_source, SRC_END);
    PyObject **src = slot_of(p, container, SS_SOURCE);
    if (*src == Py_None) {
        PyObject *s = kg_record_new(p->state, KG_RECORD_SELECTOR_SOURCE);
        if (s == NULL) return SP_ERROR;
        Py_SETREF(*src, s);
    }
    if (rec_get(*src, SRC_END) == Py_None) {
        PyObject *end = position(p, 0, 0);
        if (end == NULL) return SP_ERROR;
        rec_set(*src, SRC_END, end);
    }
    PyObject *end = rec_get(*src, SRC_END);
    rec_set(end, POS_LINE, Py_NewRef(rec_get(last_end, POS_LINE)));
    rec_set(end, POS_COLUMN, Py_NewRef(rec_get(last_end, POS_COLUMN)));
    return SP_OK;
}

/* flush_pending_spaces() */
static int
flush_pending_spaces(sparser *p) {
    if (PyUnicode_GET_LENGTH(p->spaces) == 0) return SP_OK;
    PyObject *last = last_node(p, p->current);
    if (last != NULL) {
        PyObject *spaces = *slot_of(p, last, SS_SPACES);
        PyObject *after = concat(rec_get(spaces, SP_AFTER), p->spaces);
        if (after == NULL) return SP_ERROR;
        rec_set(spaces, SP_AFTER, after);
        Py_SETREF(p->spaces, Py_NewRef(p->state->empty_string));
        return SP_OK;
    }
    const stoken *token = token_at(p, p->position);
    if (token == NULL) token = token_at(p, p->position - 1);
    PyObject *src = token ? token_source(p, token) : Py_NewRef(Py_None);
    return add_node(p, new_snode(p, SN_STRING, Py_NewRef(p->state->empty_string), src, token ? token->start : 0,
                                 NULL, NULL));
}

/* _is_escaped() */
static bool
is_escaped(PyObject *word, Py_ssize_t index) {
    Py_ssize_t start = index;
    while (start > 0 && PyUnicode_READ_CHAR(word, start - 1) == '\\') start--;
    return (index - start) % 2 == 1;
}

/* RE_KEYFRAMES_PERCENT.fullmatch(word): [0-9]+\.[0-9]+% */
static bool
is_keyframes_percent(PyObject *word) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(word);
    Py_ssize_t i = 0;
    while (i < n && PyUnicode_READ_CHAR(word, i) >= '0' && PyUnicode_READ_CHAR(word, i) <= '9') i++;
    if (i == 0 || i >= n || PyUnicode_READ_CHAR(word, i) != '.') return false;
    const Py_ssize_t digits = ++i;
    while (i < n && PyUnicode_READ_CHAR(word, i) >= '0' && PyUnicode_READ_CHAR(word, i) <= '9') i++;
    return i > digits && i == n - 1 && PyUnicode_READ_CHAR(word, i) == '%';
}

/* A ClassName, ID or Tag of text, unescaped, with raws.value the text if it has escapes */
static PyObject *
escaped_node(sparser *p, int cls, PyObject *text, PyObject *src, Py_ssize_t source_index) {
    if (text == NULL) { Py_XDECREF(src); return NULL; }
    PyObject *raws = NULL, *value = text;
    if (PyUnicode_FindChar(text, '\\', 0, PyUnicode_GET_LENGTH(text), 1) >= 0) {
        value = kg_selector_unesc(NULL, text);
        if (value == NULL || (raws = kg_record_new(p->state, KG_RECORD_SELECTOR_RAWS)) == NULL) {
            Py_XDECREF(value);
            Py_DECREF(text);
            Py_XDECREF(src);
            return NULL;
        }
        rec_set(raws, RAWS_VALUE, text);
    }
    return new_snode(p, cls, value, src, source_index, NULL, raws);
}

/* split_word(): first is the pseudo-class or pseudo-element string, NULL for none */
static int
split_word(sparser *p, PyObject *pseudo_str, const stoken *starting) {
    const Py_ssize_t word_start = p->t.items[p->position].start;
    Py_ssize_t word_end = p->t.items[p->position].end;
    const stoken *nxt = token_at(p, p->position + 1);
    while (nxt != NULL && (nxt->type == '$' || nxt->type == '^' || nxt->type == '=' || nxt->type == TK_WORD)) {
        p->position++;
        const stoken *cur = &p->t.items[p->position];
        word_end = cur->end;
        const Py_ssize_t cur_end = cur->end > p->length ? p->length : cur->end;
        if (cur_end > cur->start && PyUnicode_READ_CHAR(p->css, cur_end - 1) == '\\') {
            const stoken *n2 = token_at(p, p->position + 1);
            if (n2 != NULL && n2->type == ' ') {
                word_end = n2->end;
                p->position++;
            }
        }
        nxt = token_at(p, p->position + 1);
    }
    /* The tokens follow each other, so the word is a slice of the text */
    PyObject *word = PyUnicode_Substring(p->css, word_start, word_end > p->length ? p->length : word_end);
    if (word == NULL) return SP_ERROR;
    const Py_ssize_t n = PyUnicode_GET_LENGTH(word);
    const bool keyframes = is_keyframes_percent(word);
    /* indices, with whether each is a class (1), an id (2) or neither (0) */
    Py_ssize_t *indices = PyMem_Malloc((size_t)(n + 1) * sizeof(Py_ssize_t));
    char *kinds = PyMem_Malloc((size_t)(n + 1));
    int r = SP_ERROR;
    if (indices == NULL || kinds == NULL) { PyErr_NoMemory(); goto end; }
    Py_ssize_t count = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        const Py_UCS4 c = PyUnicode_READ_CHAR(word, i);
        char kind = 0;
        if (c == '.' && !keyframes && !is_escaped(word, i)) kind = 1;
        else if (c == '#' && !is_escaped(word, i) && !(i + 1 < n && PyUnicode_READ_CHAR(word, i + 1) == '{')) kind = 2;
        if (i == 0 || kind) {
            indices[count] = i;
            kinds[count++] = kind;
        }
    }
    if (n == 0) { indices[0] = 0; kinds[0] = 0; count = 1; }
    const stoken cur = p->t.items[p->position];
    for (Py_ssize_t i = 0; i < count; i++) {
        const Py_ssize_t ind = indices[i], index = i + 1 < count ? indices[i + 1] : n;
        if (i == 0 && pseudo_str != NULL) {
            /* pseudo(): first() */
            PyObject *value = PyUnicode_Substring(word, ind, index);
            PyObject *full = value ? PyUnicode_Concat(pseudo_str, value) : NULL;
            Py_XDECREF(value);
            if (full == NULL) goto end;
            PyObject *src = get_source(p, starting->line, starting->col, cur.end_line, cur.end_col);
            if ((r = add_node(p, new_snode(p, SN_PSEUDO, full, src, starting->start, NULL, NULL))) != SP_OK) goto end;
            r = SP_ERROR;
            const stoken *after = token_at(p, p->position + 1);
            if (count > 1 && after != NULL && after->type == '(') { r = SP_FALLBACK; goto end; }  /* Misplaced parenthesis */
            continue;
        }
        PyObject *src = get_source(p, cur.line, cur.col + ind, cur.end_line, cur.col + (index - 1));
        if (src == NULL) goto end;
        PyObject *node;
        if (kinds[i]) {
            node = escaped_node(p, kinds[i] == 1 ? SN_CLASS : SN_ID, PyUnicode_Substring(word, ind + 1, index), src,
                                cur.start + ind);
        } else {
            node = escaped_node(p, SN_TAG, PyUnicode_Substring(word, ind, index), src, cur.start + ind);
        }
        if (add_node(p, node) != SP_OK) goto end;
    }
    p->position++;
    r = SP_OK;
end:
    PyMem_Free(indices);
    PyMem_Free(kinds);
    Py_DECREF(word);
    return r;
}

/* word() */
static int
word(sparser *p) {
    const stoken *nxt = token_at(p, p->position + 1);
    if (nxt != NULL && nxt->type == TK_COMBINATOR && nxt->end - nxt->start == 1 &&
        PyUnicode_READ_CHAR(p->css, nxt->start) == '|') return SP_FALLBACK;  /* a namespace */
    return split_word(p, NULL, NULL);
}

static bool
next_is_pipe(const sparser *p) {
    const stoken *nxt = token_at(p, p->position + 1);
    return nxt != NULL && nxt->end - nxt->start == 1 && nxt->end <= p->length &&
           PyUnicode_READ_CHAR(p->css, nxt->start) == '|';
}

/* universal() and nesting() */
static int
single(sparser *p, int cls) {
    if (next_is_pipe(p)) return SP_FALLBACK;
    const stoken *tok = &p->t.items[p->position];
    PyObject *value = cls == SN_UNIVERSAL ? PyUnicode_FromOrdinal('*') : PyUnicode_FromOrdinal('&');
    const int r = add_node(p, new_snode(p, cls, value, token_source(p, tok), tok->start, NULL, NULL));
    p->position++;
    return r;
}

/* pseudo() */
static int
pseudo(sparser *p) {
    const stoken *starting = &p->t.items[p->position];
    const Py_ssize_t colons_start = starting->start;
    while (token_at(p, p->position) != NULL && p->t.items[p->position].type == ':') p->position++;
    const stoken *tok = token_at(p, p->position);
    if (tok == NULL || tok->type != TK_WORD) return SP_FALLBACK;
    PyObject *pseudo_str = PyUnicode_Substring(p->css, colons_start, tok->start);
    if (pseudo_str == NULL) return SP_ERROR;
    const int r = split_word(p, pseudo_str, starting);
    Py_DECREF(pseudo_str);
    return r;
}

/* comma() */
static int
comma(sparser *p) {
    if (flush_pending_spaces(p) != SP_OK) return SP_ERROR;
    if (p->position == p->t.len - 1) {
        Py_SETREF(SLOT(p, p->root, SN_ROOT, SS_TRAILING_COMMA), Py_NewRef(Py_True));
        p->position++;
        return SP_OK;
    }
    if (infer_end_position(p, p->current) != SP_OK) return SP_ERROR;
    const stoken *nxt = &p->t.items[p->position + 1];
    PyObject *src = source(p, position(p, nxt->line, nxt->col), NULL);
    PyObject *selector = new_snode(p, SN_SELECTOR, Py_NewRef(p->state->empty_string), src, nxt->start, NULL, NULL);
    if (selector == NULL) return SP_ERROR;
    PyObject *parent = *slot_of(p, p->current, SS_PARENT);
    p->current = selector;
    p->position++;
    return append_node(p, parent, selector);
}

static int parse_token(sparser *p, bool throw_on_parenthesis);

/* parentheses(), for the arguments of a pseudo */
static int
parentheses(sparser *p) {
    PyObject *last = last_node(p, p->current);
    if (last == NULL || class_of(p, last) != SN_PSEUDO) return SP_FALLBACK;
    Py_ssize_t unbalanced = 1;
    p->position++;
    const stoken *first = token_at(p, p->position);
    if (first == NULL) return SP_FALLBACK;
    PyObject *src = source(p, position(p, first->line, first->col), NULL);
    PyObject *selector = new_snode(p, SN_SELECTOR, Py_NewRef(p->state->empty_string), src, first->start, NULL, NULL);
    PyObject *cache = p->current;
    if (append_node(p, last, selector) != SP_OK) return SP_ERROR;
    p->current = selector;
    if (++p->nesting_depth > 256) return SP_FALLBACK;
    while (p->position < p->t.len && unbalanced) {
        const stoken tok = p->t.items[p->position];
        if (tok.type == '(') unbalanced++;
        if (tok.type == ')') unbalanced--;
        if (unbalanced) {
            const int r = parse_token(p, false);
            if (r != SP_OK) return r;
        } else {
            if (flush_pending_spaces(p) != SP_OK) return SP_ERROR;
            PyObject *end = position(p, tok.end_line, tok.end_col);
            if (end == NULL) return SP_ERROR;
            rec_set(*slot_of(p, p->current, SS_SOURCE), SRC_END, end);
            PyObject *parent = *slot_of(p, p->current, SS_PARENT);
            if ((end = position(p, tok.end_line, tok.end_col)) == NULL) return SP_ERROR;
            rec_set(*slot_of(p, parent, SS_SOURCE), SRC_END, end);
            p->position++;
        }
    }
    p->nesting_depth--;
    p->current = cache;
    return unbalanced ? SP_FALLBACK : SP_OK;
}

/* combinator(), for a combinator token or the whitespace before something other than a comma or ) */
static int
combinator(sparser *p) {
    const stoken *tok = &p->t.items[p->position];
    if (tok->type == TK_COMBINATOR && tok->end - tok->start == 1 && PyUnicode_READ_CHAR(p->css, tok->start) == '|') {
        return SP_FALLBACK;  /* a namespace */
    }
    /* locate_next_meaningful_token(): there are no comments */
    Py_ssize_t next_sig = p->position;
    while (next_sig < p->t.len && p->t.items[next_sig].type == ' ') next_sig++;
    if (next_sig >= p->t.len || p->t.items[next_sig].type == ',' || p->t.items[next_sig].type == ')') {
        return SP_FALLBACK;
    }
    const stoken first = *tok;
    PyObject *space = NULL;  /* the whitespace before, if any */
    if (next_sig > p->position) {
        /* parse_whitespace_equivalent_tokens() and convert_whitespace_nodes_to_space(): only whitespace */
        space = PyUnicode_Substring(p->css, first.start, p->t.items[next_sig - 1].end);
        if (space == NULL) return SP_ERROR;
        p->position = next_sig;
    }
    tok = &p->t.items[p->position];
    PyObject *node;
    if (tok->type == '/') {
        Py_XDECREF(space);
        return SP_FALLBACK;  /* is_named_combinator() */
    }
    if (tok->type == TK_COMBINATOR) {
        node = new_snode(p, SN_COMBINATOR, content(p, tok), token_source(p, tok), tok->start, NULL, NULL);
        p->position++;
        if (node != NULL && space != NULL) {
            rec_set(*slot_of(p, node, SS_SPACES), SP_BEFORE, Py_NewRef(space));
            /* node.raw_space_before = None */
            PyObject *raws = *slot_of(p, node, SS_RAWS);
            if (rec_get(raws, RAWS_SPACES) == Py_None) {
                PyObject *rs = kg_record_new(p->state, KG_RECORD_SELECTOR_RAW_SPACES);
                if (rs == NULL) { Py_DECREF(node); node = NULL; }
                else rec_set(raws, RAWS_SPACES, rs);
            }
        }
        Py_XDECREF(space);
    } else if (space == NULL) {
        return SP_FALLBACK;  /* unexpected */
    } else {
        PyObject *spaces = kg_record_new(p->state, KG_RECORD_SELECTOR_SPACES);
        PyObject *raws = kg_record_new(p->state, KG_RECORD_SELECTOR_RAWS);
        PyObject *raw_spaces = kg_record_new(p->state, KG_RECORD_SELECTOR_RAW_SPACES);
        const Py_ssize_t n = PyUnicode_GET_LENGTH(space);
        PyObject *src = get_source(p, first.line, first.col, p->t.items[p->position - 1].end_line,
                                   p->t.items[p->position - 1].end_col);
        if (spaces == NULL || raws == NULL || raw_spaces == NULL || src == NULL) {
            Py_XDECREF(spaces); Py_XDECREF(raws); Py_XDECREF(raw_spaces); Py_XDECREF(src); Py_DECREF(space);
            return SP_ERROR;
        }
        rec_set(raws, RAWS_SPACES, raw_spaces);
        if (PyUnicode_READ_CHAR(space, n - 1) == ' ') {
            PyObject *s = PyUnicode_Substring(space, 0, n - 1);
            if (s == NULL) { Py_DECREF(spaces); Py_DECREF(raws); Py_DECREF(src); Py_DECREF(space); return SP_ERROR; }
            rec_set(spaces, SP_BEFORE, Py_NewRef(s));
            rec_set(raw_spaces, SP_BEFORE, s);
        } else if (PyUnicode_READ_CHAR(space, 0) == ' ') {
            PyObject *s = PyUnicode_Substring(space, 1, n);
            if (s == NULL) { Py_DECREF(spaces); Py_DECREF(raws); Py_DECREF(src); Py_DECREF(space); return SP_ERROR; }
            rec_set(spaces, SP_AFTER, Py_NewRef(s));
            rec_set(raw_spaces, SP_AFTER, s);
        } else {
            rec_set(raws, RAWS_VALUE, Py_NewRef(space));
        }
        Py_DECREF(space);
        node = new_snode(p, SN_COMBINATOR, PyUnicode_FromOrdinal(' '), src, first.start, spaces, raws);
    }
    if (node == NULL) return SP_ERROR;
    const stoken *after = token_at(p, p->position);
    if (after != NULL && after->type == ' ') {
        PyObject *c = content(p, after);
        if (c == NULL) { Py_DECREF(node); return SP_ERROR; }
        rec_set(*slot_of(p, node, SS_SPACES), SP_AFTER, c);
        p->position++;
    }
    return add_node(p, node);
}

/* space() */
static int
space(sparser *p) {
    const stoken *tok = &p->t.items[p->position];
    const stoken *prev = token_at(p, p->position - 1);
    /* current_has_only_comments(): there are no comments, so it has no nodes */
    if (p->position == 0 || (prev != NULL && (prev->type == ',' || prev->type == '(')) ||
        PyList_GET_SIZE(*slot_of(p, p->current, SS_NODES)) == 0) {
        PyObject *c = content(p, tok);
        if (c == NULL) return SP_ERROR;
        Py_SETREF(p->spaces, c);
        p->position++;
        return SP_OK;
    }
    const stoken *nxt = token_at(p, p->position + 1);
    if (p->position == p->t.len - 1 || nxt->type == ',' || nxt->type == ')') {
        PyObject *c = content(p, tok);
        if (c == NULL) return SP_ERROR;
        rec_set(*slot_of(p, last_node(p, p->current), SS_SPACES), SP_AFTER, c);
        p->position++;
        return SP_OK;
    }
    return combinator(p);
}

/* attribute(), with the Python code that makes the node */
static int
attribute(sparser *p) {
    Py_ssize_t close = p->position + 1;
    while (close < p->t.len && p->t.items[close].type != ']') close++;
    if (close >= p->t.len) return SP_FALLBACK;  /* an error */
    if (p->token_list == NULL) {
        if ((p->token_list = PyList_New(p->t.len)) == NULL) return SP_ERROR;
        for (Py_ssize_t i = 0; i < p->t.len; i++) {
            const stoken k = p->t.items[i];
            PyObject *t = Py_BuildValue("(lnnnnnn)", k.type, k.line, k.col, k.end_line, k.end_col, k.start, k.end);
            if (t == NULL) return SP_ERROR;
            PyList_SET_ITEM(p->token_list, i, t);
        }
    }
    PyObject *node = PyObject_CallFunction(p->f->attribute_node, "OOn", p->css, p->token_list, p->position);
    if (node == NULL) {
        /* An error: the Python parser raises it */
        PyErr_Clear();
        return SP_FALLBACK;
    }
    if (!Py_IS_TYPE(node, p->f->classes[SN_ATTRIBUTE])) {
        Py_DECREF(node);
        PyErr_SetString(PyExc_TypeError, "attribute_node() must return an Attribute");
        return SP_ERROR;
    }
    p->position = close + 1;
    return add_node(p, node);
}

/* parse() */
static int
parse_token(sparser *p, bool throw_on_parenthesis) {
    switch (p->t.items[p->position].type) {
        case ' ': return space(p);
        case '(': return parentheses(p);
        case ')': return throw_on_parenthesis ? SP_FALLBACK : SP_OK;
        case TK_WORD: return word(p);
        case ':': return pseudo(p);
        case ',': return comma(p);
        case '*': return single(p, SN_UNIVERSAL);
        case '&': return single(p, SN_NESTING);
        case TK_COMBINATOR: return combinator(p);
        case '[': return attribute(p);
        default: return SP_FALLBACK;
    }
}

static PyObject *
SelectorParser_parse(PyObject *self, PyObject *css) {
    if (!PyUnicode_Check(css)) {
        PyErr_Format(PyExc_TypeError, "parse() expects a str, not %.200s", Py_TYPE(css)->tp_name);
        return NULL;
    }
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(Py_TYPE(self));
    if (state == NULL) return NULL;
    sparser p = {.f = (kg_SelectorParser *)self, .state = state, .css = css, .length = PyUnicode_GET_LENGTH(css)};
    PyObject *ans = NULL;
    int r = SP_ERROR;
    const char *error = NULL;
    const int t = tokenize_into(css, &p.t, &error);
    if (t != 0) {
        r = t == 1 ? SP_FALLBACK : SP_ERROR;
        goto end;
    }
    for (Py_ssize_t i = 0; i < p.t.len; i++) {
        /* Comments are whitespace to the lookahead of combinator(): leave them to the Python parser */
        if (p.t.items[i].type == TK_COMMENT) {
            r = SP_FALLBACK;
            goto end;
        }
    }
    p.spaces = Py_NewRef(state->empty_string);
    PyObject *src = Py_NewRef(Py_None);
    if (p.t.len) src = get_source(&p, p.t.items[0].line, p.t.items[0].col, p.t.items[p.t.len - 1].end_line,
                                  p.t.items[p.t.len - 1].end_col);
    p.root = new_snode(&p, SN_ROOT, Py_NewRef(state->empty_string), src, -1, NULL, NULL);
    if (p.root == NULL) goto end;
    PyObject *selector = new_snode(&p, SN_SELECTOR, Py_NewRef(state->empty_string),
                                   source(&p, position(&p, 1, 1), NULL), 0, NULL, NULL);
    p.current = selector;
    if (append_node(&p, p.root, selector) != SP_OK) goto end;
    /* loop() */
    while (p.position < p.t.len) {
        if ((r = parse_token(&p, true)) != SP_OK) goto end;
    }
    r = SP_ERROR;
    if (flush_pending_spaces(&p) != SP_OK || infer_end_position(&p, p.current) != SP_OK) goto end;
    r = SP_OK;
    ans = Py_NewRef(p.root);
end:
    PyMem_Free(p.t.items);
    Py_XDECREF(p.token_list);
    Py_XDECREF(p.spaces);
    Py_XDECREF(p.root);
    if (r == SP_FALLBACK) return Py_NewRef(Py_NotImplemented);
    return ans;
}

/* Printing and normalizing {{{
 *
 * to_string() is Node.to_string() and normalize() what normalize_selector()
 * in kgcss/linter/_selector_helpers.py does, for trees of the node classes
 * SelectorParser knows other than Attribute, without namespaces. Anything
 * else gives NotImplemented, for the Python versions.
 */

#define MAX_NESTING_DEPTH 256
enum { RS_BEFORE, RS_AFTER };  /* RawSpaces */

/* The class of any node, -1 for a class SelectorParser does not know */
static int
known_class(const kg_SelectorParser *f, PyObject *node) {
    for (int i = 0; i < SN_COUNT; i++) {
        if (Py_TYPE(node) == f->classes[i]) return i;
    }
    return -1;
}

/* A slot of a node others may change: a new reference, or NULL if it is unset */
static PyObject *
get_slot(const kg_SelectorParser *f, PyObject *node, int cls, int slot) {
    PyObject *ans;
    Py_BEGIN_CRITICAL_SECTION(node);
    ans = Py_XNewRef(*(PyObject **)((char *)node + f->offsets[cls][slot]));
    Py_END_CRITICAL_SECTION();
    return ans;
}

/* A field of a record others may change: a new reference */
static PyObject *
get_field(PyObject *record, int field) {
    PyObject *ans;
    Py_BEGIN_CRITICAL_SECTION(record);
    ans = Py_NewRef(((kg_Record *)record)->values[field]);
    Py_END_CRITICAL_SECTION();
    return ans;
}

static bool
is_record(kg_module_state *state, PyObject *o, kg_record_type type) {
    return Py_IS_TYPE(o, state->record_types[type]);
}

/* raw_space_before and raw_space_after: new references to strings, NULL with *status set otherwise */
static int
node_spaces(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, int cls, PyObject **before,
            PyObject **after) {
    PyObject *raws = get_slot(f, node, cls, SS_RAWS), *spaces = get_slot(f, node, cls, SS_SPACES);
    PyObject *rs = NULL, *b = NULL, *a = NULL;
    int r = SP_FALLBACK;
    if (raws == NULL || spaces == NULL || !is_record(state, raws, KG_RECORD_SELECTOR_RAWS) ||
        !is_record(state, spaces, KG_RECORD_SELECTOR_SPACES)) goto end;
    rs = get_field(raws, RAWS_SPACES);
    if (rs != Py_None) {
        b = get_field(rs, RS_BEFORE);
        a = get_field(rs, RS_AFTER);
    }
    if (b == NULL || b == Py_None) Py_XSETREF(b, get_field(spaces, SP_BEFORE));
    if (a == NULL || a == Py_None) Py_XSETREF(a, get_field(spaces, SP_AFTER));
    /* raw or '' */
    if (b == Py_None) Py_SETREF(b, Py_NewRef(state->empty_string));
    if (a == Py_None) Py_SETREF(a, Py_NewRef(state->empty_string));
    if (!PyUnicode_CheckExact(b) || !PyUnicode_CheckExact(a)) goto end;
    *before = b;
    *after = a;
    b = a = NULL;
    r = SP_OK;
end:
    Py_XDECREF(raws);
    Py_XDECREF(spaces);
    Py_XDECREF(rs);
    Py_XDECREF(b);
    Py_XDECREF(a);
    return r;
}

/* Node.value_to_string() without the prefixes: raws.value or value */
static PyObject *
base_value(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, int cls) {
    PyObject *raws = get_slot(f, node, cls, SS_RAWS);
    if (raws == NULL || !is_record(state, raws, KG_RECORD_SELECTOR_RAWS)) { Py_XDECREF(raws); return NULL; }
    PyObject *raw = get_field(raws, RAWS_VALUE);
    Py_DECREF(raws);
    if (PyUnicode_CheckExact(raw) && PyUnicode_GET_LENGTH(raw)) return raw;
    Py_DECREF(raw);
    PyObject *value = get_slot(f, node, cls, SS_VALUE);
    if (value != NULL && PyUnicode_CheckExact(value)) return value;
    Py_XDECREF(value);
    return NULL;
}

static int write_node(kg_module_state *state, const kg_SelectorParser *f, PyUnicodeWriter *w, PyObject *node, int depth);

/* node._stringify(depth) in Python, for what is not printed here */
static int
write_python(PyUnicodeWriter *w, PyObject *node, int depth) {
    PyObject *s = PyObject_CallMethod(node, "_stringify", "i", depth);
    if (s == NULL) return SP_ERROR;
    int r = SP_FALLBACK;
    if (PyUnicode_CheckExact(s)) r = PyUnicodeWriter_WriteStr(w, s) < 0 ? SP_ERROR : SP_OK;
    Py_DECREF(s);
    return r;
}

/* The children of a container, joined by sep (0 for none) */
static int
write_children(kg_module_state *state, const kg_SelectorParser *f, PyUnicodeWriter *w, PyObject *node, int cls,
               Py_UCS4 sep, int depth) {
    PyObject *nodes = get_slot(f, node, cls, SS_NODES);
    if (nodes == NULL || !PyList_CheckExact(nodes)) { Py_XDECREF(nodes); return SP_FALLBACK; }
    PyObject *items = PyList_GetSlice(nodes, 0, PY_SSIZE_T_MAX);
    Py_DECREF(nodes);
    if (items == NULL) return SP_ERROR;
    int r = SP_OK;
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items) && r == SP_OK; i++) {
        if (i && sep && PyUnicodeWriter_WriteChar(w, sep) < 0) r = SP_ERROR;
        else r = write_node(state, f, w, PyList_GET_ITEM(items, i), depth);
    }
    Py_DECREF(items);
    return r;
}

/* node._stringify(depth) */
static int
write_node(kg_module_state *state, const kg_SelectorParser *f, PyUnicodeWriter *w, PyObject *node, int depth) {
    const int cls = known_class(f, node);
    if (cls < 0 || cls == SN_ATTRIBUTE) return write_python(w, node, depth);
    if (cls == SN_PSEUDO && depth >= MAX_NESTING_DEPTH) return SP_FALLBACK;  /* an error */
    const bool leaf = !has_slot(cls, SS_NODES);
    PyObject *before = NULL, *after = NULL, *value = NULL;
    int r = node_spaces(state, f, node, cls, &before, &after);
    if (r != SP_OK) return (r == SP_FALLBACK && leaf) ? write_python(w, node, depth) : r;
    if (leaf) {
        /* What C cannot print is printed in Python, before any of the node is written */
        if (cls == SN_TAG || cls == SN_UNIVERSAL) {
            PyObject *ns = get_slot(f, node, cls, SS__NAMESPACE);
            const int t = ns == NULL ? 1 : PyObject_IsTrue(ns);
            Py_XDECREF(ns);
            if (t != 0) {  /* qualified_name() */
                r = t < 0 ? SP_ERROR : write_python(w, node, depth);
                goto end;
            }
        }
        if ((value = base_value(state, f, node, cls)) == NULL) {
            r = write_python(w, node, depth);
            goto end;
        }
    }
    if (Py_EnterRecursiveCall(" in to_string()")) { r = SP_ERROR; goto end; }
    r = SP_ERROR;
    if (PyUnicodeWriter_WriteStr(w, before) < 0) goto leave;
    switch (cls) {
        case SN_ROOT: {
            if ((r = write_children(state, f, w, node, cls, ',', depth)) != SP_OK) goto leave;
            r = SP_ERROR;
            PyObject *tc = get_slot(f, node, cls, SS_TRAILING_COMMA);
            const int t = tc == NULL ? 0 : PyObject_IsTrue(tc);
            Py_XDECREF(tc);
            if (t < 0 || (t && PyUnicodeWriter_WriteChar(w, ',') < 0)) goto leave;
            break;
        }
        case SN_SELECTOR:
            if ((r = write_children(state, f, w, node, cls, 0, depth)) != SP_OK) goto leave;
            break;
        case SN_PSEUDO: {
            /* stringify_property('value'), then the arguments */
            value = base_value(state, f, node, cls);
            if (value == NULL) { r = SP_FALLBACK; goto leave; }
            if (PyUnicodeWriter_WriteStr(w, value) < 0) goto leave;
            PyObject *nodes = get_slot(f, node, cls, SS_NODES);
            const bool has_nodes = nodes != NULL && PyList_CheckExact(nodes) && PyList_GET_SIZE(nodes);
            Py_XDECREF(nodes);
            if (has_nodes) {
                if (PyUnicodeWriter_WriteChar(w, '(') < 0) goto leave;
                if ((r = write_children(state, f, w, node, cls, ',', depth + 1)) != SP_OK) goto leave;
                r = SP_ERROR;
                if (PyUnicodeWriter_WriteChar(w, ')') < 0) goto leave;
            }
            break;
        }
        default:
            if (cls == SN_CLASS && PyUnicodeWriter_WriteChar(w, '.') < 0) goto leave;
            if (cls == SN_ID && PyUnicodeWriter_WriteChar(w, '#') < 0) goto leave;
            if (PyUnicodeWriter_WriteStr(w, value) < 0) goto leave;
            break;
    }
    r = PyUnicodeWriter_WriteStr(w, after) < 0 ? SP_ERROR : SP_OK;
leave:
    Py_LeaveRecursiveCall();
end:
    Py_XDECREF(before);
    Py_XDECREF(after);
    Py_XDECREF(value);
    return r;
}

/* to_string() of node into *out, or a status */
static int
node_to_string(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, PyObject **out) {
    PyUnicodeWriter *w = PyUnicodeWriter_Create(0);
    if (w == NULL) return SP_ERROR;
    const int r = write_node(state, f, w, node, 0);
    if (r != SP_OK) {
        PyUnicodeWriter_Discard(w);
        return r;
    }
    *out = PyUnicodeWriter_Finish(w);
    return *out == NULL ? SP_ERROR : SP_OK;
}

static PyObject *
SelectorParser_to_string(PyObject *self, PyObject *node) {
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(Py_TYPE(self));
    if (state == NULL) return NULL;
    PyObject *ans = NULL;
    const int r = node_to_string(state, (kg_SelectorParser *)self, node, &ans);
    if (r == SP_FALLBACK) return Py_NewRef(Py_NotImplemented);
    return r == SP_OK ? ans : NULL;
}

/* A copy of a record slot of node: None stays None */
static int
copy_record(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, int cls, int slot, kg_record_type type,
            bool may_be_none, PyObject *copy) {
    PyObject *v = get_slot(f, node, cls, slot);
    if (v == NULL) return SP_FALLBACK;
    if (v == Py_None && may_be_none) {
        SLOT_OF(f, copy, cls, slot) = v;
        return SP_OK;
    }
    if (!is_record(state, v, type)) { Py_DECREF(v); return SP_FALLBACK; }
    PyObject *c = kg_record_deepcopy(v);
    Py_DECREF(v);
    if (c == NULL) return SP_ERROR;
    SLOT_OF(f, copy, cls, slot) = c;
    return SP_OK;
}

/* node._clone(parent, depth) */
static int
clone_node(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, PyObject *parent, int depth,
           PyObject **out) {
    const int cls = known_class(f, node);
    if (cls < 0 || depth + 4 > MAX_NESTING_DEPTH) return SP_FALLBACK;
    PyTypeObject *tp = Py_TYPE(node);
    PyObject *copy = tp->tp_alloc(tp, 0), *items = NULL;
    if (copy == NULL) return SP_ERROR;
    int r = SP_FALLBACK;
    /* _copy_into() */
    if ((SLOT_OF(f, copy, cls, SS_VALUE) = get_slot(f, node, cls, SS_VALUE)) == NULL) goto end;
    if ((SLOT_OF(f, copy, cls, SS_SOURCE_INDEX) = get_slot(f, node, cls, SS_SOURCE_INDEX)) == NULL) goto end;
    if ((r = copy_record(state, f, node, cls, SS_SOURCE, KG_RECORD_SELECTOR_SOURCE, true, copy)) != SP_OK) goto end;
    if ((r = copy_record(state, f, node, cls, SS_SPACES, KG_RECORD_SELECTOR_SPACES, false, copy)) != SP_OK) goto end;
    if ((r = copy_record(state, f, node, cls, SS_RAWS, KG_RECORD_SELECTOR_RAWS, false, copy)) != SP_OK) goto end;
    r = SP_FALLBACK;
    SLOT_OF(f, copy, cls, SS_PARENT) = Py_NewRef(parent);
    if (has_slot(cls, SS_TRAILING_COMMA) &&
        (SLOT_OF(f, copy, cls, SS_TRAILING_COMMA) = get_slot(f, node, cls, SS_TRAILING_COMMA)) == NULL) goto end;
    if (has_slot(cls, SS_CONSTRUCTED)) SLOT_OF(f, copy, cls, SS_CONSTRUCTED) = Py_NewRef(Py_True);
    if (has_slot(cls, SS_NAMESPACE) &&
        (SLOT_OF(f, copy, cls, SS_NAMESPACE) = get_slot(f, node, cls, SS_NAMESPACE)) == NULL) goto end;
    if (has_slot(cls, SS__NAMESPACE) &&
        (SLOT_OF(f, copy, cls, SS__NAMESPACE) = get_slot(f, node, cls, SS__NAMESPACE)) == NULL) goto end;
    if (cls == SN_ATTRIBUTE) {
        static const int copied[] = {SS_ATTR_VALUE, SS_ATTRIBUTE, SS_OPERATOR, SS_QUOTE_MARK, SS_INSENSITIVE,
                                     SS_SENSITIVE};
        for (size_t i = 0; i < sizeof(copied) / sizeof(copied[0]); i++) {
            if ((SLOT_OF(f, copy, cls, copied[i]) = get_slot(f, node, cls, copied[i])) == NULL) goto end;
        }
        SLOT_OF(f, copy, cls, SS_RAWS_PENDING) = Py_NewRef(Py_False);
    }
    if (has_slot(cls, SS_NODES)) {
        if ((SLOT_OF(f, copy, cls, SS_LAST_EACH) = get_slot(f, node, cls, SS_LAST_EACH)) == NULL) goto end;
        PyObject *indexes = get_slot(f, node, cls, SS_INDEXES);
        if (indexes == NULL) goto end;
        if (indexes != Py_None) {
            /* An iteration is going on: leave its bookkeeping to Python */
            const bool empty = PyDict_CheckExact(indexes) && PyDict_GET_SIZE(indexes) == 0;
            Py_DECREF(indexes);
            if (!empty || (indexes = PyDict_New()) == NULL) { r = empty ? SP_ERROR : SP_FALLBACK; goto end; }
        }
        SLOT_OF(f, copy, cls, SS_INDEXES) = indexes;
        PyObject *nodes = get_slot(f, node, cls, SS_NODES);
        if (nodes == NULL || !PyList_CheckExact(nodes)) { Py_XDECREF(nodes); goto end; }
        items = PyList_GetSlice(nodes, 0, PY_SSIZE_T_MAX);
        Py_DECREF(nodes);
        if (items == NULL) { r = SP_ERROR; goto end; }
        for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items); i++) {
            PyObject *child = NULL;
            if ((r = clone_node(state, f, PyList_GET_ITEM(items, i), copy, depth + 1, &child)) != SP_OK) goto end;
            Py_SETREF(PyList_GET_ITEM(items, i), child);
        }
        SLOT_OF(f, copy, cls, SS_NODES) = items;
        items = NULL;
    }
    *out = copy;
    copy = NULL;
    r = SP_OK;
end:
    Py_XDECREF(items);
    Py_XDECREF(copy);
    return r;
}

static PyObject *
slot_ref(const kg_SelectorParser *f, PyObject *node, int slot) {
    /* A slot of a node of the private copy: borrowed */
    return SLOT_OF(f, node, known_class(f, node), slot);
}

/* _escaping() */
static int
escaping(const kg_SelectorParser *f, PyObject *node) {
    if (known_class(f, node) != SN_CLASS) return SP_OK;
    PyObject *raws = slot_ref(f, node, SS_RAWS), *value = slot_ref(f, node, SS_VALUE);
    PyObject *raw = rec_get(raws, RAWS_VALUE);
    if (!PyUnicode_Check(raw) || PyUnicode_GET_LENGTH(raw) == 0) return SP_OK;
    if (!PyUnicode_CheckExact(value)) return SP_FALLBACK;
    /* node.value = node.value */
    PyObject *escaped = cssesc(value, true, false, false);
    if (escaped == NULL) return SP_ERROR;
    const int eq = PyUnicode_Compare(escaped, value);
    if (eq == -1 && PyErr_Occurred()) { Py_DECREF(escaped); return SP_ERROR; }
    if (eq == 0) {
        Py_DECREF(escaped);
        rec_set(raws, RAWS_VALUE, Py_NewRef(Py_None));
    } else {
        rec_set(raws, RAWS_VALUE, escaped);
    }
    return SP_OK;
}

/* _whitespace_and_comments() of a node that is no comment */
static int
whitespace(kg_module_state *state, const kg_SelectorParser *f, PyObject *node) {
    PyObject *raws = slot_ref(f, node, SS_RAWS), *spaces = slot_ref(f, node, SS_SPACES);
    if (rec_get(raws, RAWS_SPACES) == Py_None) {
        PyObject *rs = kg_record_new(state, KG_RECORD_SELECTOR_RAW_SPACES);
        if (rs == NULL) return SP_ERROR;
        rec_set(raws, RAWS_SPACES, rs);
    }
    PyObject *rs = rec_get(raws, RAWS_SPACES);
    rec_set(rs, RS_BEFORE, Py_NewRef(state->empty_string));
    rec_set(rs, RS_AFTER, Py_NewRef(state->empty_string));
    rec_set(spaces, SP_BEFORE, Py_NewRef(state->empty_string));
    rec_set(spaces, SP_AFTER, Py_NewRef(state->empty_string));
    if (known_class(f, node) == SN_ATTRIBUTE) {
        /* The spaces around the parts of the attribute selector go too */
        for (int i = 2; i < 6; i++) rec_set(spaces, i, Py_NewRef(Py_None));
        rec_set(raws, RAWS_SPACES, Py_NewRef(Py_None));
    }
    return SP_OK;
}

/* _clean() */
static int
clean(kg_module_state *state, const kg_SelectorParser *f, PyObject *node, int depth) {
    if (depth > MAX_NESTING_DEPTH) return SP_FALLBACK;
    PyObject *nodes = slot_ref(f, node, SS_NODES);
    Py_ssize_t i = 0;
    while (i < PyList_GET_SIZE(nodes)) {
        PyObject *child = PyList_GET_ITEM(nodes, i);
        int r = escaping(f, child);
        if (r != SP_OK) return r;
        const int cls = known_class(f, child);
        if (cls == SN_COMMENT) {
            /* node.remove() */
            Py_SETREF(SLOT_OF(f, child, cls, SS_PARENT), Py_NewRef(Py_None));
            if (PyList_SetSlice(nodes, i, i + 1, NULL) != 0) return SP_ERROR;
            continue;
        }
        if ((r = whitespace(state, f, child)) != SP_OK) return r;
        if (has_slot(cls, SS_NODES) && PyList_GET_SIZE(slot_ref(f, child, SS_NODES)) &&
            (r = clean(state, f, child, depth + 1)) != SP_OK) return r;
        i++;
    }
    return SP_OK;
}

/* Sort nodes stably by keys, a tuple (*key, index, node) for each */
static PyObject *
sorted_nodes(PyObject *keyed) {
    if (PyList_Sort(keyed) != 0) return NULL;
    const Py_ssize_t n = PyList_GET_SIZE(keyed);
    PyObject *ans = PyList_New(n);
    if (ans == NULL) return NULL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *t = PyList_GET_ITEM(keyed, i);
        PyList_SET_ITEM(ans, i, Py_NewRef(PyTuple_GET_ITEM(t, PyTuple_GET_SIZE(t) - 1)));
    }
    return ans;
}

/* is_pseudo_element() */
static int
is_pseudo_element_node(const kg_SelectorParser *f, PyObject *node) {
    if (known_class(f, node) != SN_PSEUDO) return 0;
    PyObject *v = slot_ref(f, node, SS_VALUE);
    if (!PyUnicode_Check(v)) return -2;  /* leave it to Python */
    const Py_ssize_t n = PyUnicode_GET_LENGTH(v);
    if (n == 0) return 0;
    if (n >= 2 && PyUnicode_READ_CHAR(v, 0) == ':' && PyUnicode_READ_CHAR(v, 1) == ':') return 1;
    static const char *const names[] = {":before", ":after", ":first-letter", ":first-line"};
    for (size_t k = 0; k < sizeof(names) / sizeof(names[0]); k++) {
        const Py_ssize_t len = (Py_ssize_t)strlen(names[k]);
        if (len != n) continue;
        bool same = true;
        /* v.lower(): only ASCII letters lower to the ASCII of these names */
        for (Py_ssize_t i = 0; i < n && same; i++) {
            Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
            if (c >= 'A' && c <= 'Z') c += 32;
            same = c == (Py_UCS4)(unsigned char)names[k][i];
        }
        if (same) return 1;
    }
    return 0;
}

/* TYPE_PRIORITY of _selector_helpers.py */
static long long
type_priority(int cls) {
    switch (cls) {
        case SN_UNIVERSAL: return 0;
        case SN_TAG: return 1;
        case SN_NESTING: return 2;
        case SN_ID: return 3;
        case SN_CLASS: return 4;
        case SN_ATTRIBUTE: return 5;
        case SN_PSEUDO: return 6;
        case SN_COMMENT: return 7;
        default: return (1LL << 53) - 1;
    }
}

/* _sort_compounds() of the nodes of a selector, into a new list */
static int
sort_compounds(kg_module_state *state, const kg_SelectorParser *f, PyObject *nodes, PyObject **out) {
    PyObject *ans = PyList_New(0), *group = PyList_New(0);
    int r = SP_ERROR;
    if (ans == NULL || group == NULL) goto end;
    const Py_ssize_t n = PyList_GET_SIZE(nodes);
    for (Py_ssize_t i = 0; i <= n; i++) {
        PyObject *child = i < n ? PyList_GET_ITEM(nodes, i) : NULL;
        bool split = child == NULL;
        if (child != NULL) {
            const int pe = is_pseudo_element_node(f, child);
            if (pe == -2) { r = SP_FALLBACK; goto end; }
            split = known_class(f, child) == SN_COMBINATOR || pe == 1;
        }
        if (!split) {
            /* (TYPE_PRIORITY, str(n)) and the index, for a stable sort */
            PyObject *str = NULL;
            if ((r = node_to_string(state, f, child, &str)) != SP_OK) goto end;
            r = SP_ERROR;
            PyObject *t = Py_BuildValue("(LNnO)", type_priority(known_class(f, child)), str, i, child);
            if (t == NULL || PyList_Append(group, t) != 0) { Py_XDECREF(t); goto end; }
            Py_DECREF(t);
            continue;
        }
        /* The group ends: sorted, then the separator */
        if (PyList_GET_SIZE(group)) {
            PyObject *sorted = PyList_GET_SIZE(group) > 1 ? sorted_nodes(group) : NULL;
            if (PyList_GET_SIZE(group) > 1 && sorted == NULL) goto end;
            for (Py_ssize_t j = 0; j < PyList_GET_SIZE(group); j++) {
                PyObject *item = sorted ? PyList_GET_ITEM(sorted, j) : PyTuple_GET_ITEM(PyList_GET_ITEM(group, j), 3);
                if (PyList_Append(ans, item) != 0) { Py_XDECREF(sorted); goto end; }
            }
            Py_XDECREF(sorted);
            if (PyList_SetSlice(group, 0, PY_SSIZE_T_MAX, NULL) != 0) goto end;
        }
        if (child != NULL && PyList_Append(ans, child) != 0) goto end;
    }
    *out = ans;
    ans = NULL;
    r = SP_OK;
end:
    Py_XDECREF(ans);
    Py_XDECREF(group);
    return r;
}

/* _order() */
static int
order(kg_module_state *state, const kg_SelectorParser *f, PyObject *node) {
    const int cls = known_class(f, node);
    if (!has_slot(cls, SS_NODES)) return SP_OK;
    PyObject **slot = &SLOT_OF(f, node, cls, SS_NODES);
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(*slot); i++) {
        const int r = order(state, f, PyList_GET_ITEM(*slot, i));
        if (r != SP_OK) return r;
    }
    if (cls == SN_ROOT || cls == SN_PSEUDO) {
        const Py_ssize_t n = PyList_GET_SIZE(*slot);
        PyObject *ordered = NULL;
        if (n > 1) {
            /* The first node of each string, in the order of the strings */
            PyObject *keyed = PyList_New(n);
            if (keyed == NULL) return SP_ERROR;
            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject *str = NULL, *child = PyList_GET_ITEM(*slot, i);
                const int r = node_to_string(state, f, child, &str);
                if (r != SP_OK) { Py_DECREF(keyed); return r; }
                PyObject *t = Py_BuildValue("(NnO)", str, i, child);
                if (t == NULL) { Py_DECREF(keyed); return SP_ERROR; }
                PyList_SET_ITEM(keyed, i, t);
            }
            if (PyList_Sort(keyed) != 0 || (ordered = PyList_New(0)) == NULL) { Py_DECREF(keyed); return SP_ERROR; }
            PyObject *prev = NULL;
            for (Py_ssize_t i = 0; i < n; i++) {
                PyObject *t = PyList_GET_ITEM(keyed, i), *str = PyTuple_GET_ITEM(t, 0);
                if (prev != NULL && PyUnicode_Compare(prev, str) == 0) continue;
                prev = str;
                if (PyList_Append(ordered, PyTuple_GET_ITEM(t, 2)) != 0) { Py_DECREF(keyed); Py_DECREF(ordered); return SP_ERROR; }
            }
            Py_DECREF(keyed);
        } else if ((ordered = PyList_GetSlice(*slot, 0, n)) == NULL) {
            return SP_ERROR;
        }
        Py_SETREF(*slot, ordered);
    }
    if (cls == SN_SELECTOR) {
        PyObject *sorted = NULL;
        const int r = sort_compounds(state, f, *slot, &sorted);
        if (r != SP_OK) return r;
        Py_SETREF(*slot, sorted);
    }
    return SP_OK;
}

static PyObject *
SelectorParser_normalize(PyObject *self, PyObject *node) {
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(Py_TYPE(self));
    if (state == NULL) return NULL;
    const kg_SelectorParser *f = (kg_SelectorParser *)self;
    PyObject *copy = NULL, *str = NULL;
    int r = clone_node(state, f, node, Py_None, 0, &copy);
    if (r != SP_OK) goto end;
    if (known_class(f, copy) == SN_COMMENT) { r = SP_FALLBACK; goto end; }
    if ((r = escaping(f, copy)) != SP_OK || (r = whitespace(state, f, copy)) != SP_OK) goto end;
    if (has_slot(known_class(f, copy), SS_NODES) && (r = clean(state, f, copy, 0)) != SP_OK) goto end;
    if ((r = order(state, f, copy)) != SP_OK) goto end;
    if ((r = node_to_string(state, f, copy, &str)) != SP_OK) goto end;
end:
    if (r == SP_OK) {
        PyObject *ans = PyTuple_Pack(2, copy, str);
        Py_DECREF(copy);
        Py_DECREF(str);
        return ans;
    }
    Py_XDECREF(copy);
    if (r == SP_FALLBACK) return Py_NewRef(Py_NotImplemented);
    return NULL;
}

/* }}} */

static PyMethodDef SelectorParser_methods[] = {
    {"parse", SelectorParser_parse, METH_O,
     "parse(css: str, /) -> Root | NotImplemented\n\nThe tree of a selector list, or NotImplemented if the Python "
     "parser must parse it."},
    {"to_string", SelectorParser_to_string, METH_O,
     "to_string(node: Node, /) -> str | NotImplemented\n\nnode.to_string(), or NotImplemented if Python must do it."},
    {"normalize", SelectorParser_normalize, METH_O,
     "normalize(node: Node, /) -> tuple[Node, str] | NotImplemented\n\nThe normalized copy of normalize_selector() "
     "and its string, or NotImplemented if Python must do it."},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot SelectorParser_slots[] = {
    {Py_tp_new, SelectorParser_new},
    {Py_tp_dealloc, SelectorParser_dealloc},
    {Py_tp_traverse, SelectorParser_traverse},
    {Py_tp_clear, SelectorParser_clear},
    {Py_tp_methods, SelectorParser_methods},
    {Py_tp_doc, (void *)"SelectorParser(*node_classes): the C version of the selector parser of the linter. "
                        "Not a public API."},
    {0, NULL},
};

static PyType_Spec SelectorParser_spec = {
    .name = "kgcss.SelectorParser",
    .basicsize = sizeof(kg_SelectorParser),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = SelectorParser_slots,
};

int
kg_linter_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->SelectorParser_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &SelectorParser_spec, NULL);
    if (state->SelectorParser_type == NULL) return -1;
    return PyModule_AddType(module, state->SelectorParser_type);
}

int
kg_linter_traverse(PyObject *module, visitproc visit, void *arg) {
    Py_VISIT(kg_get_module_state(module)->SelectorParser_type);
    return 0;
}

void
kg_linter_clear(PyObject *module) {
    Py_CLEAR(kg_get_module_state(module)->SelectorParser_type);
}

/* }}} */
