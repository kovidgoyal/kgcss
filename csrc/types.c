/*
 * types.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Python visible types of the tokenizer: TokenStream, Token and ParseError,
 * plus the tokenize() entry point.
 *
 * Thread safety: all objects are immutable once created, except for the
 * lazily built line table of a TokenStream, which is guarded by a critical
 * section on the stream.
 */

#include "objects.h"
#include <stddef.h>

/* Token offsets are exposed with Py_T_UINT */
typedef char kg_assert_uint_is_32_bits[sizeof(unsigned int) == sizeof(uint32_t) ? 1 : -1];

/* Inputs are limited so that offsets fit in uint32_t with room for lookahead */
#define KG_MAX_INPUT_LENGTH ((Py_ssize_t)UINT32_MAX - 16)
/* Above this many code points, tokenize with the GIL released */
#define KG_RELEASE_GIL_THRESHOLD (64 * 1024)

static const char *token_type_names[KG_TOK__COUNT] = {
    [KG_TOK_IDENT] = "IDENT",
    [KG_TOK_FUNCTION] = "FUNCTION",
    [KG_TOK_AT_KEYWORD] = "AT_KEYWORD",
    [KG_TOK_HASH] = "HASH",
    [KG_TOK_STRING] = "STRING",
    [KG_TOK_BAD_STRING] = "BAD_STRING",
    [KG_TOK_URL] = "URL",
    [KG_TOK_BAD_URL] = "BAD_URL",
    [KG_TOK_DELIM] = "DELIM",
    [KG_TOK_NUMBER] = "NUMBER",
    [KG_TOK_PERCENTAGE] = "PERCENTAGE",
    [KG_TOK_DIMENSION] = "DIMENSION",
    [KG_TOK_WHITESPACE] = "WHITESPACE",
    [KG_TOK_CDO] = "CDO",
    [KG_TOK_CDC] = "CDC",
    [KG_TOK_COLON] = "COLON",
    [KG_TOK_SEMICOLON] = "SEMICOLON",
    [KG_TOK_COMMA] = "COMMA",
    [KG_TOK_LEFT_SQUARE] = "LEFT_SQUARE",
    [KG_TOK_RIGHT_SQUARE] = "RIGHT_SQUARE",
    [KG_TOK_LEFT_PAREN] = "LEFT_PAREN",
    [KG_TOK_RIGHT_PAREN] = "RIGHT_PAREN",
    [KG_TOK_LEFT_CURLY] = "LEFT_CURLY",
    [KG_TOK_RIGHT_CURLY] = "RIGHT_CURLY",
    [KG_TOK_COMMENT] = "COMMENT",
};

static const struct { const char *kind, *message; } error_info[KG_ERR__COUNT] = {
    [KG_ERR_EOF_IN_COMMENT] = {"eof-in-comment", "Unterminated comment"},
    [KG_ERR_EOF_IN_STRING] = {"eof-in-string", "Unterminated string"},
    [KG_ERR_NEWLINE_IN_STRING] = {"newline-in-string", "Unescaped newline in string"},
    [KG_ERR_EOF_IN_URL] = {"eof-in-url", "Unterminated url()"},
    [KG_ERR_BAD_URL] = {"bad-url", "Invalid character in unquoted url()"},
    [KG_ERR_INVALID_ESCAPE] = {"invalid-escape", "Backslash followed by a newline is not a valid escape"},
    [KG_ERR_EOF_IN_ESCAPE] = {"eof-in-escape", "Backslash at end of input"},
    [KG_ERR_EMPTY] = {"empty", "Expected a value, found only whitespace and comments"},
    [KG_ERR_EXTRA_INPUT] = {"extra-input", "Unexpected input after the value"},
};

/* ---------------------------------------------------------------------------
 * Object layouts
 * ------------------------------------------------------------------------- */

typedef struct {
    PyObject_HEAD
    Py_ssize_t offset, line, column;
    PyObject *kind;
    PyObject *message;
} kg_ParseError;

/* ---------------------------------------------------------------------------
 * TokenStream
 * ------------------------------------------------------------------------- */

static void
TokenStream_dealloc(PyObject *o) {
    kg_TokenStream *self = (kg_TokenStream *)o;
    PyTypeObject *tp = Py_TYPE(o);
    kg_token_array_free(&self->arr);
    PyMem_RawFree(self->lines);
    Py_XDECREF(self->source);
    tp->tp_free(o);
    Py_DECREF(tp);
}

/* Returns 1-based line and column for offset, or false with an exception set */
bool
kg_line_col(kg_TokenStream *self, uint32_t offset, Py_ssize_t *line, Py_ssize_t *col) {
    bool ok = true;
    Py_BEGIN_CRITICAL_SECTION(self);
    if (self->lines == NULL && !kg_build_line_table(self->kind, self->data, self->len, &self->lines, &self->line_count)) {
        PyErr_NoMemory();
        ok = false;
    }
    if (ok) {
        /* Find the last line start <= offset */
        uint32_t lo = 0, hi = self->line_count;
        while (hi - lo > 1) {
            uint32_t mid = lo + (hi - lo) / 2;
            if (self->lines[mid] <= offset) lo = mid; else hi = mid;
        }
        *line = (Py_ssize_t)lo + 1;
        *col = (Py_ssize_t)(offset - self->lines[lo]) + 1;
    }
    Py_END_CRITICAL_SECTION();
    return ok;
}

static Py_ssize_t
TokenStream_len(PyObject *o) {
    return (Py_ssize_t)((kg_TokenStream *)o)->arr.count;
}

PyObject *
kg_range_value(kg_TokenStream *ts, const kg_token *t, uint32_t start, uint32_t end, bool in_string) {
    if (t->flags & KG_TF_NEEDS_PROCESSING) return kg_unescape(ts->kind, ts->data, start, end, in_string);
    return PyUnicode_Substring(ts->source, start, end);
}

static PyObject *
number_value(kg_TokenStream *ts, const kg_token *t) {
    const uint32_t a = t->vstart, b = t->vend;
    if ((t->flags & KG_TF_IS_INTEGER) && b - a <= 18) {
        int64_t v = 0;
        bool negative = false;
        uint32_t i = a;
        Py_UCS4 c = PyUnicode_READ(ts->kind, ts->data, i);
        if (c == '+' || c == '-') { negative = c == '-'; i++; }
        for (; i < b; i++) v = v * 10 + (int64_t)(PyUnicode_READ(ts->kind, ts->data, i) - '0');
        return PyLong_FromLongLong(negative ? -v : v);
    }
    PyObject *text = PyUnicode_Substring(ts->source, a, b);
    if (text == NULL) return NULL;
    PyObject *ans = (t->flags & KG_TF_IS_INTEGER) ? PyLong_FromUnicodeObject(text, 10) : PyFloat_FromString(text);
    Py_DECREF(text);
    return ans;
}

PyObject *
kg_make_token(kg_TokenStream *ts, uint32_t idx) {
    kg_module_state *state = kg_state_for((PyObject *)ts);
    kg_Token *self = PyObject_New(kg_Token, state->Token_type);
    if (self == NULL) return NULL;
    self->stream = (kg_TokenStream *)Py_NewRef(ts);
    self->rec = ts->arr.tokens[idx];
    self->value = NULL;
    self->unit = Py_NewRef(Py_None);
    const kg_token *t = &self->rec;
    switch ((kg_token_type)t->type) {
        case KG_TOK_IDENT: case KG_TOK_FUNCTION: case KG_TOK_AT_KEYWORD: case KG_TOK_HASH: case KG_TOK_URL:
            self->value = kg_range_value(ts, t, t->vstart, t->vend, false);
            break;
        case KG_TOK_STRING:
            self->value = kg_range_value(ts, t, t->vstart, t->vend, true);
            break;
        case KG_TOK_BAD_STRING: case KG_TOK_BAD_URL:
            self->value = Py_NewRef(Py_None);
            break;
        case KG_TOK_DIMENSION:
            Py_SETREF(self->unit, kg_range_value(ts, t, t->vend, t->end, false));
            if (self->unit == NULL) { Py_DECREF(self); return NULL; }
            self->value = number_value(ts, t);
            break;
        case KG_TOK_NUMBER: case KG_TOK_PERCENTAGE:
            self->value = number_value(ts, t);
            break;
        case KG_TOK_COMMENT:
            self->value = PyUnicode_Substring(ts->source, t->vstart, t->vend);
            break;
        default:
            self->value = PyUnicode_Substring(ts->source, t->start, t->end);
            break;
    }
    if (self->value == NULL) { Py_DECREF(self); return NULL; }
    return (PyObject *)self;
}

static PyObject *
TokenStream_item(PyObject *o, Py_ssize_t i) {
    kg_TokenStream *self = (kg_TokenStream *)o;
    if (i < 0 || i >= (Py_ssize_t)self->arr.count) {
        PyErr_SetString(PyExc_IndexError, "token index out of range");
        return NULL;
    }
    return kg_make_token(self, (uint32_t)i);
}

static PyObject *
TokenStream_errors(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_TokenStream *self = (kg_TokenStream *)o;
    PyObject *ans = PyTuple_New(self->arr.error_count);
    if (ans == NULL) return NULL;
    for (uint32_t i = 0; i < self->arr.error_count; i++) {
        const kg_error *e = self->arr.errors + i;
        PyObject *pe = kg_make_parse_error(self, e->offset, (kg_error_kind)e->kind);
        if (pe == NULL) { Py_DECREF(ans); return NULL; }
        PyTuple_SET_ITEM(ans, i, pe);
    }
    return ans;
}

static PyObject *
TokenStream_line_col(PyObject *o, PyObject *arg) {
    kg_TokenStream *self = (kg_TokenStream *)o;
    Py_ssize_t offset = PyLong_AsSsize_t(arg);
    if (offset == -1 && PyErr_Occurred()) return NULL;
    if (offset < 0 || offset > (Py_ssize_t)self->len) {
        PyErr_SetString(PyExc_IndexError, "offset out of range");
        return NULL;
    }
    Py_ssize_t line, col;
    if (!kg_line_col(self, (uint32_t)offset, &line, &col)) return NULL;
    return Py_BuildValue("nn", line, col);
}

static PyObject *
TokenStream_repr(PyObject *o) {
    kg_TokenStream *self = (kg_TokenStream *)o;
    return PyUnicode_FromFormat("<TokenStream with %u tokens and %u errors>", self->arr.count, self->arr.error_count);
}

static PyMemberDef TokenStream_members[] = {
    {"source", Py_T_OBJECT_EX, offsetof(kg_TokenStream, source), Py_READONLY, "The source text that was tokenized"},
    {NULL, 0, 0, 0, NULL}
};

static PyGetSetDef TokenStream_getset[] = {
    {"errors", TokenStream_errors, NULL, "Parse errors found while tokenizing, as a tuple of ParseError", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyMethodDef TokenStream_methods[] = {
    {"line_col", TokenStream_line_col, METH_O,
     "line_col(offset: int) -> tuple[int, int]\n\nThe 1-based (line, column) of the code point at offset in the source."},
    {NULL, NULL, 0, NULL}
};

static PyType_Slot TokenStream_slots[] = {
    {Py_tp_dealloc, TokenStream_dealloc},
    {Py_tp_repr, TokenStream_repr},
    {Py_tp_members, TokenStream_members},
    {Py_tp_getset, TokenStream_getset},
    {Py_tp_methods, TokenStream_methods},
    {Py_sq_length, TokenStream_len},
    {Py_sq_item, TokenStream_item},
    {Py_tp_doc, (void *)"An immutable sequence of the tokens of a source text. Create it with tokenize()."},
    {0, NULL}
};

static PyType_Spec TokenStream_spec = {
    .name = "kgcss.TokenStream",
    .basicsize = sizeof(kg_TokenStream),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = TokenStream_slots,
};

/* ---------------------------------------------------------------------------
 * Token
 * ------------------------------------------------------------------------- */

static void
Token_dealloc(PyObject *o) {
    kg_Token *self = (kg_Token *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->stream);
    Py_XDECREF(self->value);
    Py_XDECREF(self->unit);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static PyObject *
Token_type(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    return Py_NewRef(PyTuple_GET_ITEM(kg_state_for(o)->token_types, self->rec.type));
}

static PyObject *
Token_source_text(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    return PyUnicode_Substring(self->stream->source, self->rec.start, self->rec.end);
}

static bool
is_numeric(const kg_token *t) {
    return t->type == KG_TOK_NUMBER || t->type == KG_TOK_PERCENTAGE || t->type == KG_TOK_DIMENSION;
}

static PyObject *
Token_representation(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    if (!is_numeric(&self->rec)) Py_RETURN_NONE;
    return PyUnicode_Substring(self->stream->source, self->rec.vstart, self->rec.vend);
}

static PyObject *
Token_is_integer(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    return PyBool_FromLong(is_numeric(&self->rec) && (self->rec.flags & KG_TF_IS_INTEGER));
}

static PyObject *
Token_hash_is_id(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    return PyBool_FromLong(self->rec.type == KG_TOK_HASH && (self->rec.flags & KG_TF_HASH_ID));
}

static PyObject *
Token_closed(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    return PyBool_FromLong(!(self->rec.flags & KG_TF_UNCLOSED));
}

static PyObject *
Token_line(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->rec.start, &line, &col)) return NULL;
    return PyLong_FromSsize_t(line);
}

static PyObject *
Token_column(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Token *self = (kg_Token *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->rec.start, &line, &col)) return NULL;
    return PyLong_FromSsize_t(col);
}

static PyObject *
Token_is_delim(PyObject *o, PyObject *arg) {
    kg_Token *self = (kg_Token *)o;
    if (!PyUnicode_Check(arg)) {
        PyErr_Format(PyExc_TypeError, "expected str, got %.200s", Py_TYPE(arg)->tp_name);
        return NULL;
    }
    if (self->rec.type != KG_TOK_DELIM) Py_RETURN_FALSE;
    int eq = PyUnicode_Compare(self->value, arg);
    if (eq == -1 && PyErr_Occurred()) return NULL;
    return PyBool_FromLong(eq == 0);
}

static PyObject *
Token_repr(PyObject *o) {
    kg_Token *self = (kg_Token *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->rec.start, &line, &col)) return NULL;
    const char *name = token_type_names[self->rec.type];
    if (self->rec.type == KG_TOK_DIMENSION)
        return PyUnicode_FromFormat("<Token %s %R%R at %zd:%zd>", name, self->value, self->unit, line, col);
    return PyUnicode_FromFormat("<Token %s %R at %zd:%zd>", name, self->value, line, col);
}

static PyMemberDef Token_members[] = {
    {"value", Py_T_OBJECT_EX, offsetof(kg_Token, value), Py_READONLY,
     "The value: unescaped str for names, strings and urls, int or float for numbers, the comment text without "
     "delimiters for comments, None for bad strings and bad urls, and the source text otherwise"},
    {"unit", Py_T_OBJECT_EX, offsetof(kg_Token, unit), Py_READONLY, "The unescaped unit of a dimension, otherwise None"},
    {"pos", Py_T_UINT, offsetof(kg_Token, rec) + offsetof(kg_token, start), Py_READONLY,
     "Offset of the first code point of this token in the source"},
    {"end", Py_T_UINT, offsetof(kg_Token, rec) + offsetof(kg_token, end), Py_READONLY,
     "Offset just past the last code point of this token in the source"},
    {"stream", Py_T_OBJECT_EX, offsetof(kg_Token, stream), Py_READONLY, "The TokenStream this token belongs to"},
    {NULL, 0, 0, 0, NULL}
};

static PyGetSetDef Token_getset[] = {
    {"type", Token_type, NULL, "The TokenType of this token", NULL},
    {"source_text", Token_source_text, NULL, "The exact source text of this token", NULL},
    {"representation", Token_representation, NULL, "Source text of the number in a numeric token, otherwise None", NULL},
    {"is_integer", Token_is_integer, NULL, "True for numeric tokens of integer type", NULL},
    {"hash_is_id", Token_hash_is_id, NULL, "True for hash tokens whose type flag is id", NULL},
    {"closed", Token_closed, NULL, "False for a string, url or comment terminated by the end of the input", NULL},
    {"line", Token_line, NULL, "1-based line number of the start of this token", NULL},
    {"column", Token_column, NULL, "1-based column (in code points) of the start of this token", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyMethodDef Token_methods[] = {
    {"is_delim", Token_is_delim, METH_O, "is_delim(ch: str) -> bool\n\nTrue if this is a DELIM token for ch."},
    {NULL, NULL, 0, NULL}
};

static PyType_Slot Token_slots[] = {
    {Py_tp_dealloc, Token_dealloc},
    {Py_tp_repr, Token_repr},
    {Py_tp_members, Token_members},
    {Py_tp_getset, Token_getset},
    {Py_tp_methods, Token_methods},
    {Py_tp_doc, (void *)"A single CSS token. Obtained by indexing a TokenStream."},
    {0, NULL}
};

static PyType_Spec Token_spec = {
    .name = "kgcss.Token",
    .basicsize = sizeof(kg_Token),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = Token_slots,
};

/* ---------------------------------------------------------------------------
 * ParseError
 * ------------------------------------------------------------------------- */

static void
ParseError_dealloc(PyObject *o) {
    kg_ParseError *self = (kg_ParseError *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->kind);
    Py_XDECREF(self->message);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static PyObject *
ParseError_repr(PyObject *o) {
    kg_ParseError *self = (kg_ParseError *)o;
    return PyUnicode_FromFormat("<ParseError %U at %zd:%zd: %U>", self->kind, self->line, self->column, self->message);
}

static PyMemberDef ParseError_members[] = {
    {"offset", Py_T_PYSSIZET, offsetof(kg_ParseError, offset), Py_READONLY, "Offset of the error in the source"},
    {"line", Py_T_PYSSIZET, offsetof(kg_ParseError, line), Py_READONLY, "1-based line number of the error"},
    {"column", Py_T_PYSSIZET, offsetof(kg_ParseError, column), Py_READONLY, "1-based column of the error"},
    {"kind", Py_T_OBJECT_EX, offsetof(kg_ParseError, kind), Py_READONLY, "A short machine readable identifier"},
    {"message", Py_T_OBJECT_EX, offsetof(kg_ParseError, message), Py_READONLY, "A human readable message"},
    {NULL, 0, 0, 0, NULL}
};

static PyType_Slot ParseError_slots[] = {
    {Py_tp_dealloc, ParseError_dealloc},
    {Py_tp_repr, ParseError_repr},
    {Py_tp_members, ParseError_members},
    {Py_tp_doc, (void *)"A recoverable error found while parsing CSS."},
    {0, NULL}
};

static PyType_Spec ParseError_spec = {
    .name = "kgcss.ParseError",
    .basicsize = sizeof(kg_ParseError),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = ParseError_slots,
};

PyObject *
kg_make_parse_error(kg_TokenStream *ts, uint32_t offset, kg_error_kind kind) {
    kg_module_state *state = kg_state_for((PyObject *)ts);
    kg_ParseError *pe = PyObject_New(kg_ParseError, state->ParseError_type);
    if (pe == NULL) return NULL;
    pe->offset = offset;
    pe->kind = Py_NewRef(PyTuple_GET_ITEM(state->error_kinds, kind));
    pe->message = PyUnicode_FromString(error_info[kind].message);
    pe->line = pe->column = 0;
    if (pe->message == NULL || !kg_line_col(ts, offset, &pe->line, &pe->column)) {
        Py_DECREF(pe);
        return NULL;
    }
    return (PyObject *)pe;
}

/* ---------------------------------------------------------------------------
 * tokenize()
 * ------------------------------------------------------------------------- */

kg_TokenStream *
kg_tokenize_to_stream(PyObject *module, PyObject *source, bool comments) {
    const Py_ssize_t len = PyUnicode_GET_LENGTH(source);
    if (len > KG_MAX_INPUT_LENGTH) {
        PyErr_SetString(PyExc_OverflowError, "CSS source text too large, must be less than 4 GiB");
        return NULL;
    }
    kg_module_state *state = kg_get_module_state(module);
    kg_TokenStream *self = PyObject_New(kg_TokenStream, state->TokenStream_type);
    if (self == NULL) return NULL;
    self->source = Py_NewRef(source);
    self->kind = PyUnicode_KIND(source);
    self->data = PyUnicode_DATA(source);
    self->len = (uint32_t)len;
    memset(&self->arr, 0, sizeof(self->arr));
    self->lines = NULL;
    self->line_count = 0;
    bool ok;
    /* The tokenizer only reads the (immutable) str buffer and writes to raw
     * malloced memory, so it can run without an attached thread state. */
    if (len > KG_RELEASE_GIL_THRESHOLD) {
        Py_BEGIN_ALLOW_THREADS
        ok = kg_tokenize(self->kind, self->data, self->len, comments, &self->arr);
        Py_END_ALLOW_THREADS
    } else ok = kg_tokenize(self->kind, self->data, self->len, comments, &self->arr);
    if (!ok) {
        Py_DECREF(self);
        PyErr_NoMemory();
        return NULL;
    }
    return self;
}

PyObject *
kg_tokenize_py(PyObject *module, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"", "comments", NULL};
    PyObject *source;
    int comments = 1;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "U|$p:tokenize", kwlist, &source, &comments)) return NULL;
    return (PyObject *)kg_tokenize_to_stream(module, source, comments != 0);
}

/* ---------------------------------------------------------------------------
 * Module integration
 * ------------------------------------------------------------------------- */

static int
load_token_types(kg_module_state *state, PyObject *module) {
    int ret = -1;
    PyObject *enums = NULL, *cls = NULL, *names = NULL;
    enums = PyImport_ImportModule("kgcss.enums");
    if (enums == NULL) goto end;
    cls = PyObject_GetAttrString(enums, "TokenType");
    if (cls == NULL) goto end;
    Py_ssize_t n = PyObject_Length(cls);
    if (n < 0) goto end;
    if (n != KG_TOK__COUNT - 1) {
        PyErr_Format(PyExc_ImportError, "kgcss.enums.TokenType has %zd members, C code has %d", n, KG_TOK__COUNT - 1);
        goto end;
    }
    state->token_types = PyTuple_New(KG_TOK__COUNT);
    names = PyTuple_New(KG_TOK__COUNT);
    if (state->token_types == NULL || names == NULL) goto end;
    PyTuple_SET_ITEM(state->token_types, 0, Py_NewRef(Py_None));
    PyTuple_SET_ITEM(names, 0, Py_NewRef(Py_None));
    for (int i = 1; i < KG_TOK__COUNT; i++) {
        PyObject *member = PyObject_CallFunction(cls, "i", i);
        if (member == NULL) goto end;
        PyTuple_SET_ITEM(state->token_types, i, member);
        PyObject *name = PyUnicode_FromString(token_type_names[i]);
        if (name == NULL) goto end;
        PyTuple_SET_ITEM(names, i, name);
    }
    if (PyModule_AddObjectRef(module, "_TOKEN_TYPE_NAMES", names) != 0) goto end;
    ret = 0;
end:
    Py_XDECREF(enums);
    Py_XDECREF(cls);
    Py_XDECREF(names);
    return ret;
}

int
kg_types_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
#define T(name) \
    state->name##_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &name##_spec, NULL); \
    if (state->name##_type == NULL || PyModule_AddType(module, state->name##_type) != 0) return -1;
    T(TokenStream) T(Token) T(ParseError)
#undef T
    state->error_kinds = PyTuple_New(KG_ERR__COUNT);
    if (state->error_kinds == NULL) return -1;
    for (int i = 0; i < KG_ERR__COUNT; i++) {
        PyObject *k = PyUnicode_InternFromString(error_info[i].kind);
        if (k == NULL) return -1;
        PyTuple_SET_ITEM(state->error_kinds, i, k);
    }
    if (PyModule_AddObjectRef(module, "_ERROR_KINDS", state->error_kinds) != 0) return -1;
    return load_token_types(state, module);
}

int
kg_types_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->TokenStream_type);
    Py_VISIT(state->Token_type);
    Py_VISIT(state->ParseError_type);
    Py_VISIT(state->token_types);
    Py_VISIT(state->error_kinds);
    return 0;
}

void
kg_types_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->TokenStream_type);
    Py_CLEAR(state->Token_type);
    Py_CLEAR(state->ParseError_type);
    Py_CLEAR(state->token_types);
    Py_CLEAR(state->error_kinds);
}
