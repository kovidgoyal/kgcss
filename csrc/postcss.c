/*
 * postcss.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * The C version of the PostCSS parser of the linter: Tokenizer, Parser and
 * SafeParser in kgcss/linter/_postcss.py, which are the reference. It must
 * build exactly the same trees, quirks included, and report the same
 * errors: tests/test_linter_postcss.py compares them on random input. The
 * comments name the methods of the Python version that each function ports.
 *
 * PostcssNode is the C base of the node classes of _postcss.py, with the
 * fields of all of them: a field a node type does not have is left unset,
 * and reading it raises AttributeError, as for the Python classes.
 *
 * Tokens are offsets into the text: their text is only made into a string
 * when a node needs it.
 */

#include "kgcss.h"
#include "objects.h"
#include <structmember.h>

/* PostcssNode {{{ */

typedef struct {
    PyObject_HEAD
    PyObject *parent, *raws, *start, *end, *nodes;
    PyObject *name, *params, *selector, *prop, *value, *important, *text;
    PyObject *css, *descendants, *by_type;
} kg_PostcssNode;

#define NODE_FIELDS(X) \
    X(parent) X(raws) X(start) X(end) X(nodes) X(name) X(params) X(selector) X(prop) X(value) X(important) \
    X(text) X(css) X(descendants) X(by_type)

static int
PostcssNode_traverse(PyObject *o, visitproc visit, void *arg) {
    kg_PostcssNode *self = (kg_PostcssNode *)o;
    Py_VISIT(Py_TYPE(o));
#define V(f) Py_VISIT(self->f);
    NODE_FIELDS(V)
#undef V
    return 0;
}

static int
PostcssNode_clear(PyObject *o) {
    kg_PostcssNode *self = (kg_PostcssNode *)o;
#define C(f) Py_CLEAR(self->f);
    NODE_FIELDS(C)
#undef C
    return 0;
}

static void
PostcssNode_dealloc(PyObject *o) {
    PyTypeObject *tp = Py_TYPE(o);
    PyObject_GC_UnTrack(o);
    PostcssNode_clear(o);
    tp->tp_free(o);
    Py_DECREF(tp);
}

#define M(f) {#f, Py_T_OBJECT_EX, offsetof(kg_PostcssNode, f), 0, NULL},
static PyMemberDef PostcssNode_members[] = {
    NODE_FIELDS(M)
    {NULL, 0, 0, 0, NULL},
};
#undef M

static PyType_Slot PostcssNode_slots[] = {
    {Py_tp_dealloc, PostcssNode_dealloc},
    {Py_tp_traverse, PostcssNode_traverse},
    {Py_tp_clear, PostcssNode_clear},
    {Py_tp_members, PostcssNode_members},
    {Py_tp_new, PyType_GenericNew},
    {Py_tp_doc, (void *)"The fields of the nodes of kgcss.linter._postcss. Not a public API."},
    {0, NULL},
};

static PyType_Spec PostcssNode_spec = {
    .name = "kgcss.PostcssNode",
    .basicsize = sizeof(kg_PostcssNode),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = PostcssNode_slots,
};

/* }}} */

/* Tokens {{{ */

typedef enum {
    PT_SPACE, PT_WORD, PT_STRING, PT_AT_WORD, PT_COMMENT, PT_BRACKETS,
    PT_OPEN_PAREN, PT_CLOSE_PAREN, PT_OPEN_SQUARE, PT_CLOSE_SQUARE, PT_OPEN_CURLY, PT_CLOSE_CURLY, PT_SEMICOLON, PT_COLON,
} token_type;

/* A PostCSS token: its text is css[ts:te], start and end are -1 for undefined */
typedef struct {
    token_type type;
    Py_ssize_t ts, te, start, end;
} token;

/* A list of tokens */
typedef struct {
    token *items;
    Py_ssize_t len, cap;
} tokens;

static int
tokens_push(tokens *t, token tok) {
    if (t->len == t->cap) {
        const Py_ssize_t cap = t->cap ? t->cap * 2 : 16;
        token *items = PyMem_Realloc(t->items, (size_t)cap * sizeof(token));
        if (items == NULL) { PyErr_NoMemory(); return -1; }
        t->items = items;
        t->cap = cap;
    }
    t->items[t->len++] = tok;
    return 0;
}

static void
tokens_free(tokens *t) {
    PyMem_Free(t->items);
    t->items = NULL;
    t->len = t->cap = 0;
}

/* tokens[a:b], as a new list (b may be below a: the slice is empty) */
static int
tokens_slice(const tokens *t, Py_ssize_t a, Py_ssize_t b, tokens *out) {
    *out = (tokens){0};
    for (Py_ssize_t i = a; i < b; i++) {
        if (tokens_push(out, t->items[i]) != 0) { tokens_free(out); return -1; }
    }
    return 0;
}

/* Python's slice indexes: a negative index counts from the end */
static Py_ssize_t
py_index(Py_ssize_t i, Py_ssize_t len) {
    if (i < 0) i += len;
    if (i < 0) i = 0;
    if (i > len) i = len;
    return i;
}

/* x or y with JavaScript's truthiness, for offsets that may be undefined (-1) or 0 */
static Py_ssize_t
js_or(Py_ssize_t x, Py_ssize_t y) {
    return x > 0 ? x : y;
}

/* }}} */

/* The parser state {{{ */

typedef enum { ERR_NONE, ERR_PYTHON, ERR_SYNTAX } parse_status;
#define NT_END (-1)

typedef struct {
    kg_module_state *state;
    PyObject *css;
    int kind;
    const void *data;
    Py_ssize_t length;
    bool safe;
    /* The tokenizer */
    Py_ssize_t pos, last_bad_paren;
    tokens returned;
    /* The classes of the nodes */
    PyTypeObject *Root, *AtRule, *Rule, *Declaration, *Comment;
    /* The parser */
    kg_PostcssNode *root, *current;
    PyObject *spaces;
    bool semicolon;
    /* The error of the non-safe parser */
    const char *reason;
    PyObject *reason_text;
    Py_ssize_t err_offset, err_end;
} parser;

#define CH(i) PyUnicode_READ(p->kind, p->data, (i))
/* The character at i, or 0x110000 (which is no character) out of range, like '' in the Python version */
#define NO_CHAR 0x110000
static inline Py_UCS4
char_at(const parser *p, Py_ssize_t i) {
    return (i >= 0 && i < p->length) ? CH(i) : NO_CHAR;
}

static PyObject *
text_of(const parser *p, token t) {
    const Py_ssize_t te = t.te > p->length ? p->length : t.te;
    if (te <= t.ts) return Py_NewRef(p->state->empty_string);
    return PyUnicode_Substring(p->css, t.ts, te);
}

static Py_ssize_t
text_len(const parser *p, token t) {
    const Py_ssize_t te = t.te > p->length ? p->length : t.te;
    return te > t.ts ? te - t.ts : 0;
}

/* Raise a CssSyntaxError: remembered, for parse() to return */
static int
syntax_error(parser *p, const char *reason, Py_ssize_t offset, Py_ssize_t end) {
    p->reason = reason;
    p->err_offset = offset;
    p->err_end = end;
    return ERR_SYNTAX;
}

static PyObject *
join_tokens(const parser *p, const tokens *t, Py_ssize_t a, Py_ssize_t b) {
    /* Tokens that follow each other in the text are joined by one slice */
    if (a >= b) return Py_NewRef(p->state->empty_string);
    bool contiguous = true;
    for (Py_ssize_t i = a + 1; i < b && contiguous; i++) {
        const Py_ssize_t prev_end = t->items[i - 1].te > p->length ? p->length : t->items[i - 1].te;
        if (t->items[i].ts != prev_end) contiguous = false;
    }
    if (contiguous) {
        token span = {PT_WORD, t->items[a].ts, t->items[b - 1].te, -1, -1};
        return text_of(p, span);
    }
    PyUnicodeWriter *w = PyUnicodeWriter_Create(0);
    if (w == NULL) return NULL;
    for (Py_ssize_t i = a; i < b; i++) {
        const token tok = t->items[i];
        const Py_ssize_t n = text_len(p, tok);
        if (n && PyUnicodeWriter_WriteSubstring(w, p->css, tok.ts, tok.ts + n) < 0) {
            PyUnicodeWriter_Discard(w);
            return NULL;
        }
    }
    return PyUnicodeWriter_Finish(w);
}

/* a + b, stealing a */
static PyObject *
concat_steal(PyObject *a, PyObject *b) {
    if (a == NULL) return NULL;
    if (PyUnicode_GET_LENGTH(b) == 0) return a;
    if (PyUnicode_GET_LENGTH(a) == 0) { Py_DECREF(a); return Py_NewRef(b); }
    PyObject *ans = PyUnicode_Concat(a, b);
    Py_DECREF(a);
    return ans;
}

static int
add_spaces(parser *p, PyObject *text) {
    PyObject *s = concat_steal(p->spaces, text);
    p->spaces = s;
    return s == NULL ? ERR_PYTHON : ERR_NONE;
}

static int
add_token_to_spaces(parser *p, token t) {
    PyObject *text = text_of(p, t);
    if (text == NULL) return ERR_PYTHON;
    const int r = add_spaces(p, text);
    Py_DECREF(text);
    return r;
}

static int
take_spaces(parser *p, PyObject **dest) {
    /* dest = spaces; spaces = '' */
    Py_XSETREF(*dest, p->spaces);
    p->spaces = Py_NewRef(p->state->empty_string);
    return ERR_NONE;
}

static PyObject *
offset_object(Py_ssize_t offset) {
    return offset < 0 ? Py_NewRef(Py_None) : PyLong_FromSsize_t(offset);
}

static int
set_offset(PyObject **field, Py_ssize_t offset) {
    PyObject *o = offset_object(offset);
    if (o == NULL) return ERR_PYTHON;
    Py_XSETREF(*field, o);
    return ERR_NONE;
}

static int
set_field(PyObject **field, PyObject *value) {
    /* Steals value */
    if (value == NULL) return ERR_PYTHON;
    Py_XSETREF(*field, value);
    return ERR_NONE;
}

static kg_Record *
raws_of(kg_PostcssNode *node) {
    return (kg_Record *)node->raws;
}

/* Raws fields, in the order of PostcssRaws_fields in records.c */
enum { R_BEFORE, R_AFTER, R_BETWEEN, R_AFTER_NAME, R_SEMICOLON, R_OWN_SEMICOLON, R_IMPORTANT, R_LEFT, R_RIGHT,
       R_SELECTOR, R_PARAMS, R_VALUE };

static void
set_raw(kg_PostcssNode *node, int field, PyObject *value) {
    /* Steals value, which is not NULL */
    Py_SETREF(raws_of(node)->values[field], value);
}

static PyObject *
get_raw(kg_PostcssNode *node, int field) {
    return raws_of(node)->values[field];
}

/* raws.field = (raws.field or '') + text */
static int
append_raw(parser *p, kg_PostcssNode *node, int field, PyObject *text) {
    PyObject *old = get_raw(node, field);
    PyObject *base = (old == Py_None || PyUnicode_GET_LENGTH(old) == 0) ? p->state->empty_string : old;
    PyObject *s = concat_steal(Py_NewRef(base), text);
    if (s == NULL) return ERR_PYTHON;
    set_raw(node, field, s);
    return ERR_NONE;
}

static kg_PostcssNode *
new_node(parser *p, PyTypeObject *cls) {
    kg_PostcssNode *node = (kg_PostcssNode *)cls->tp_alloc(cls, 0);
    if (node == NULL) return NULL;
    node->raws = kg_record_new(p->state, KG_RECORD_POSTCSS_RAWS);
    if (node->raws == NULL) { Py_DECREF(node); return NULL; }
    node->parent = Py_NewRef(Py_None);
    node->start = Py_NewRef(Py_None);
    node->end = Py_NewRef(Py_None);
    return node;
}

/* }}} */

/* Tokenizer {{{ */

static bool
is_space(Py_UCS4 c) {
    return c == ' ' || c == '\n' || c == '\t' || c == '\r' || c == '\f';
}

static bool
is_hex(Py_UCS4 c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

/* RE_AT_END */
static bool
is_at_end(Py_UCS4 c) {
    switch (c) {
        case '\t': case '\n': case '\f': case '\r': case ' ': case '"': case '#': case '\'': case '(': case ')':
        case '/': case ';': case '[': case '\\': case ']': case '{': case '}':
            return true;
        default:
            return false;
    }
}

/* RE_WORD_END, without its /(?=\*) */
static bool
is_word_end(Py_UCS4 c) {
    switch (c) {
        case '\t': case '\n': case '\f': case '\r': case ' ': case '!': case '"': case '#': case '\'': case '(':
        case ')': case ':': case ';': case '@': case '[': case '\\': case ']': case '{': case '}':
            return true;
        default:
            return false;
    }
}

/* The characters around url in RE_URL_NAME */
static bool
is_url_prefix(Py_UCS4 c) {
    switch (c) {
        case '\t': case '\n': case '\f': case '\r': case ' ': case '"': case '\'': case '(': case ')': case ',':
        case '/': case ':': case ';': case '[': case ']': case '{': case '}':
            return true;
        default:
            return false;
    }
}

static Py_ssize_t
find_char(const parser *p, Py_UCS4 c, Py_ssize_t from) {
    for (Py_ssize_t i = from; i < p->length; i++) {
        if (CH(i) == c) return i;
    }
    return -1;
}

/* Tokenizer._escaped_at() */
static bool
escaped_at(const parser *p, Py_ssize_t index) {
    bool escaped = false;
    while (char_at(p, index - 1) == '\\') {
        index--;
        escaped = !escaped;
    }
    return escaped;
}

static bool
end_of_file(const parser *p) {
    return p->returned.len == 0 && p->pos >= p->length;
}

/* Tokenizer.next_token(): returns 0 with a token, NT_END at the end, or ERR_SYNTAX */
static int
next_token(parser *p, token *out) {
    if (p->returned.len) {
        *out = p->returned.items[--p->returned.len];
        return 0;
    }
    Py_ssize_t pos = p->pos, nxt;
    const Py_ssize_t length = p->length;
    if (pos >= length) return NT_END;
    const bool ignore = p->safe;
    const Py_UCS4 code = CH(pos);
    token t = {PT_WORD, pos, pos + 1, pos, -1};
    if (is_space(code)) {
        nxt = pos + 1;
        while (nxt < length && is_space(CH(nxt))) nxt++;
        t = (token){PT_SPACE, pos, nxt, -1, -1};
        pos = nxt - 1;
    } else if (code == '[' || code == ']' || code == '{' || code == '}' || code == ':' || code == ';' || code == ')') {
        switch (code) {
            case '[': t.type = PT_OPEN_SQUARE; break;
            case ']': t.type = PT_CLOSE_SQUARE; break;
            case '{': t.type = PT_OPEN_CURLY; break;
            case '}': t.type = PT_CLOSE_CURLY; break;
            case ':': t.type = PT_COLON; break;
            case ';': t.type = PT_SEMICOLON; break;
            default: t.type = PT_CLOSE_PAREN; break;
        }
    } else if (code == '(') {
        /* RE_URL_NAME.search(css[max(pos - 4, 0):pos]): ^ is the start of that slice */
        bool url = false;
        if (pos >= 3 && (CH(pos - 3) | 0x20) == 'u' && (CH(pos - 2) | 0x20) == 'r' && (CH(pos - 1) | 0x20) == 'l') {
            url = pos == 3 || is_url_prefix(CH(pos - 4));
        }
        if (url) {
            /* not RE_QUOTED.match(css, pos + 1) */
            Py_ssize_t q = pos + 1;
            while (q < length && (CH(q) == '\t' || CH(q) == '\n' || CH(q) == '\f' || CH(q) == '\r' || CH(q) == ' ')) q++;
            if (q < length && (CH(q) == '"' || CH(q) == '\'')) url = false;
        }
        if (url) {
            nxt = pos;
            while (true) {
                nxt = find_char(p, ')', nxt + 1);
                if (nxt == -1) {
                    if (ignore) { nxt = pos; break; }
                    return syntax_error(p, "Unclosed bracket", p->pos, -1);
                }
                if (!escaped_at(p, nxt)) break;
            }
            t = (token){PT_BRACKETS, pos, nxt + 1, pos, nxt};
            pos = nxt;
        } else if (pos <= p->last_bad_paren) {
            t.type = PT_OPEN_PAREN;
        } else {
            nxt = find_char(p, ')', pos + 1);
            bool bad = nxt == -1;
            /* RE_BAD_BRACKET.search(css[pos:nxt + 1]) */
            for (Py_ssize_t i = pos; !bad && i < nxt; i++) {
                const Py_UCS4 c = CH(i), d = CH(i + 1);
                if (c != '\n' && c != '\r' && c != 0x2028 && c != 0x2029 &&
                    (d == '\r' || d == '\n' || d == '"' || d == '\'' || d == '(' || d == '/' || d == '\\')) bad = true;
            }
            if (bad) {
                p->last_bad_paren = nxt == -1 ? length : nxt;
                t.type = PT_OPEN_PAREN;
            } else {
                t = (token){PT_BRACKETS, pos, nxt + 1, pos, nxt};
                pos = nxt;
            }
        }
    } else if (code == '\'' || code == '"') {
        nxt = pos;
        while (true) {
            nxt = find_char(p, code, nxt + 1);
            if (nxt == -1) {
                if (ignore) { nxt = pos + 1; break; }
                return syntax_error(p, "Unclosed string", p->pos, -1);
            }
            if (!escaped_at(p, nxt)) break;
        }
        t = (token){PT_STRING, pos, nxt + 1, pos, nxt};
        pos = nxt;
    } else if (code == '@') {
        nxt = pos + 1;
        while (nxt < length && !is_at_end(CH(nxt))) nxt++;
        nxt--;
        t = (token){PT_AT_WORD, pos, nxt + 1, pos, nxt};
        pos = nxt;
    } else if (code == '\\') {
        nxt = pos;
        bool escape = true;
        while (char_at(p, nxt + 1) == '\\') {
            nxt++;
            escape = !escape;
        }
        const Py_UCS4 following = char_at(p, nxt + 1);
        if (escape && following != '/' && following != ' ' && following != '\n' && following != '\t' &&
            following != '\r' && following != '\f') {
            nxt++;
            if (is_hex(char_at(p, nxt))) {
                while (is_hex(char_at(p, nxt + 1))) nxt++;
                if (char_at(p, nxt + 1) == ' ') nxt++;
            }
        }
        t = (token){PT_WORD, pos, nxt + 1, pos, nxt};
        pos = nxt;
    } else if (code == '/' && char_at(p, pos + 1) == '*') {
        nxt = -1;
        for (Py_ssize_t i = pos + 2; i + 1 < length; i++) {
            if (CH(i) == '*' && CH(i + 1) == '/') { nxt = i; break; }
        }
        nxt += 1;
        if (nxt == 0) {
            if (!ignore) return syntax_error(p, "Unclosed comment", p->pos, -1);
            nxt = length;
        }
        t = (token){PT_COMMENT, pos, nxt + 1, pos, nxt};
        pos = nxt;
    } else {
        nxt = pos + 1;
        while (nxt < length) {
            const Py_UCS4 c = CH(nxt);
            if (is_word_end(c) || (c == '/' && nxt + 1 < length && CH(nxt + 1) == '*')) break;
            nxt++;
        }
        nxt--;
        t = (token){PT_WORD, pos, nxt + 1, pos, nxt};
        pos = nxt;
    }
    p->pos = pos + 1;
    *out = t;
    return 0;
}

static int
back(parser *p, token t) {
    return tokens_push(&p->returned, t) == 0 ? ERR_NONE : ERR_PYTHON;
}

/* }}} */

/* Parser {{{ */

static bool
is_space_or_comment(token t) {
    return t.type == PT_SPACE || t.type == PT_COMMENT;
}

/* JavaScript's whitespace, which trim() strips */
static bool
is_js_space(Py_UCS4 c) {
    switch (c) {
        case '\t': case '\n': case 0x0b: case '\f': case '\r': case ' ': case 0xa0: case 0x1680: case 0x2028:
        case 0x2029: case 0x202f: case 0x205f: case 0x3000: case 0xfeff:
            return true;
        default:
            return c >= 0x2000 && c <= 0x200a;
    }
}

/* Whether css[a:b] is text, case-insensitively for ASCII (no other characters lower to ASCII letters of text) */
static bool
equals_ascii_ci(const parser *p, Py_ssize_t a, Py_ssize_t b, const char *text) {
    const Py_ssize_t n = (Py_ssize_t)strlen(text);
    if (b - a != n) return false;
    for (Py_ssize_t i = 0; i < n; i++) {
        Py_UCS4 c = CH(a + i);
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != (Py_UCS4)(unsigned char)text[i]) return false;
    }
    return true;
}

static bool
token_text_is(const parser *p, token t, const char *text) {
    const Py_ssize_t te = t.te > p->length ? p->length : t.te;
    return equals_ascii_ci(p, t.ts, te, text);
}

/* token.text == text, for ASCII text */
static bool
token_text_equals(const parser *p, token t, const char *text) {
    const Py_ssize_t n = (Py_ssize_t)strlen(text);
    if (text_len(p, t) != n) return false;
    for (Py_ssize_t i = 0; i < n; i++) {
        if (CH(t.ts + i) != (Py_UCS4)(unsigned char)text[i]) return false;
    }
    return true;
}

/* Parser.init() */
static int
init_node(parser *p, kg_PostcssNode *node, Py_ssize_t offset) {
    kg_PostcssNode *current = p->current;
    if (current->nodes == NULL || current->nodes == Py_None) {
        PyObject *list = PyList_New(0);
        if (list == NULL) return ERR_PYTHON;
        Py_XSETREF(current->nodes, list);
    }
    if (PyList_Append(current->nodes, (PyObject *)node) != 0) return ERR_PYTHON;
    Py_XSETREF(node->parent, Py_NewRef((PyObject *)current));
    if (set_offset(&node->start, offset) != ERR_NONE) return ERR_PYTHON;
    PyObject *before = NULL;
    take_spaces(p, &before);
    set_raw(node, R_BEFORE, before);
    if (Py_TYPE(node) != p->Comment) p->semicolon = false;
    return ERR_NONE;
}

/* Parser.raw(): the value, and node.raws[field] if the text holds comments or trailing space */
static PyObject *
raw_value(parser *p, kg_PostcssNode *node, int field, const tokens *t, bool custom_property) {
    PyUnicodeWriter *w = PyUnicodeWriter_Create(0);
    if (w == NULL) return NULL;
    Py_UCS4 last = NO_CHAR;
    bool clean = true;
    const Py_ssize_t length = t->len;
    for (Py_ssize_t i = 0; i < length; i++) {
        const token tok = t->items[i];
        bool append = false;
        if (tok.type == PT_SPACE && i == length - 1 && !custom_property) {
            clean = false;
        } else if (tok.type == PT_COMMENT) {
            const bool prev_safe = i == 0 || t->items[i - 1].type == PT_SPACE;
            const bool next_safe = i + 1 >= length || t->items[i + 1].type == PT_SPACE;
            if (!prev_safe && !next_safe) {
                if (last == ',') clean = false;
                else append = true;
            } else clean = false;
        } else append = true;
        if (append) {
            const Py_ssize_t n = text_len(p, tok);
            if (n) {
                if (PyUnicodeWriter_WriteSubstring(w, p->css, tok.ts, tok.ts + n) < 0) goto error;
                last = CH(tok.ts + n - 1);
            } else last = NO_CHAR;
        }
    }
    PyObject *value = PyUnicodeWriter_Finish(w);
    if (value == NULL || clean) return value;
    PyObject *raw = join_tokens(p, t, 0, t->len);
    if (raw == NULL) { Py_DECREF(value); return NULL; }
    PyObject *rv = kg_record_new(p->state, KG_RECORD_POSTCSS_RAW_VALUE);
    if (rv == NULL) { Py_DECREF(raw); Py_DECREF(value); return NULL; }
    Py_SETREF(((kg_Record *)rv)->values[0], raw);
    Py_SETREF(((kg_Record *)rv)->values[1], Py_NewRef(value));
    set_raw(node, field, rv);
    return value;
error:
    PyUnicodeWriter_Discard(w);
    return NULL;
}

/* Parser.spaces_and_comments_from_end() */
static PyObject *
spaces_and_comments_from_end(parser *p, tokens *t) {
    Py_ssize_t n = t->len;
    while (n > 0 && is_space_or_comment(t->items[n - 1])) n--;
    PyObject *ans = join_tokens(p, t, n, t->len);
    t->len = n;
    return ans;
}

/* Parser.spaces_and_comments_from_start() */
static PyObject *
spaces_and_comments_from_start(parser *p, tokens *t) {
    Py_ssize_t n = 0;
    while (n < t->len && is_space_or_comment(t->items[n])) n++;
    PyObject *ans = join_tokens(p, t, 0, n);
    if (ans == NULL) return NULL;
    memmove(t->items, t->items + n, (size_t)(t->len - n) * sizeof(token));
    t->len -= n;
    return ans;
}

/* Parser.colon(): the index of the colon, -1 if there is none */
static int
colon_index(parser *p, const tokens *t, Py_ssize_t *ans) {
    Py_ssize_t brackets = 0;
    const token *prev = NULL;
    *ans = -1;
    for (Py_ssize_t i = 0; i < t->len; i++) {
        const token *tok = &t->items[i];
        if (tok->type == PT_OPEN_PAREN) brackets++;
        if (tok->type == PT_CLOSE_PAREN) brackets--;
        if (brackets == 0 && tok->type == PT_COLON) {
            if (prev == NULL) {
                if (!p->safe) {
                    return syntax_error(p, "Double colon", tok->start, tok->start + text_len(p, *tok));
                }
            } else if (prev->type == PT_WORD && token_text_equals(p, *prev, "progid")) {
                continue;  /* without updating prev, as in PostCSS */
            } else {
                *ans = i;
                return ERR_NONE;
            }
        }
        prev = tok;
    }
    return ERR_NONE;
}

/* Parser.unknown_word() and SafeParser.unknown_word() */
static int
unknown_word(parser *p, const tokens *t, Py_ssize_t a, Py_ssize_t b) {
    if (p->safe) {
        PyObject *text = join_tokens(p, t, a, b);
        if (text == NULL) return ERR_PYTHON;
        const int r = add_spaces(p, text);
        Py_DECREF(text);
        return r;
    }
    const token first = t->items[a];
    const Py_ssize_t start = first.start < 0 ? 0 : first.start;
    PyObject *text = text_of(p, first);
    if (text == NULL) return ERR_PYTHON;
    p->reason_text = PyUnicode_FromFormat("Unknown word %U", text);
    Py_DECREF(text);
    if (p->reason_text == NULL) return ERR_PYTHON;
    return syntax_error(p, NULL, start, start + text_len(p, first));
}

/* Parser.end() */
static int
end_block(parser *p, token tok) {
    kg_PostcssNode *current = p->current;
    if (current->nodes != NULL && current->nodes != Py_None && PyList_GET_SIZE(current->nodes)) {
        set_raw(current, R_SEMICOLON, Py_NewRef(p->semicolon ? Py_True : Py_False));
    }
    p->semicolon = false;
    if (append_raw(p, current, R_AFTER, p->spaces) != ERR_NONE) return ERR_PYTHON;
    Py_SETREF(p->spaces, Py_NewRef(p->state->empty_string));
    if (current->parent != NULL && current->parent != Py_None) {
        if (set_offset(&current->end, tok.start + 1) != ERR_NONE) return ERR_PYTHON;
        p->current = (kg_PostcssNode *)current->parent;
        return ERR_NONE;
    }
    if (!p->safe) return syntax_error(p, "Unexpected }", tok.start, tok.start + 1);
    /* SafeParser.unexpected_close() */
    PyObject *after = get_raw(current, R_AFTER);
    PyObject *base = after == Py_None ? NULL : Py_NewRef(after);
    if (base == NULL) base = PyUnicode_FromString("undefined");
    PyObject *brace = PyUnicode_FromOrdinal('}');
    if (brace == NULL) { Py_XDECREF(base); return ERR_PYTHON; }
    PyObject *s = concat_steal(base, brace);
    Py_DECREF(brace);
    if (s == NULL) return ERR_PYTHON;
    set_raw(current, R_AFTER, s);
    return ERR_NONE;
}

/* Parser.end_file() and SafeParser.end_file() */
static int
end_file(parser *p) {
    kg_PostcssNode *current = p->current;
    if (!p->safe && current->parent != Py_None) {
        Py_ssize_t start = 0;
        if (current->start != NULL && PyLong_Check(current->start)) {
            start = PyLong_AsSsize_t(current->start);
            if (start == -1 && PyErr_Occurred()) return ERR_PYTHON;
        }
        return syntax_error(p, "Unclosed block", start, -1);
    }
    if (current->nodes != NULL && current->nodes != Py_None && PyList_GET_SIZE(current->nodes)) {
        set_raw(current, R_SEMICOLON, Py_NewRef(p->semicolon ? Py_True : Py_False));
    }
    if (append_raw(p, current, R_AFTER, p->spaces) != ERR_NONE) return ERR_PYTHON;
    if (p->safe) {
        while (current->parent != Py_None) {
            current = (kg_PostcssNode *)current->parent;
            set_raw(current, R_AFTER, Py_NewRef(p->state->empty_string));
        }
        p->current = current;
    }
    return set_offset(&p->root->end, p->pos);
}

/* Parser.free_semicolon() */
static int
free_semicolon(parser *p, token tok) {
    if (add_token_to_spaces(p, tok) != ERR_NONE) return ERR_PYTHON;
    PyObject *nodes = p->current->nodes;
    if (nodes == NULL || nodes == Py_None || PyList_GET_SIZE(nodes) == 0) return ERR_NONE;
    kg_PostcssNode *prev = (kg_PostcssNode *)PyList_GET_ITEM(nodes, PyList_GET_SIZE(nodes) - 1);
    if (!PyObject_TypeCheck((PyObject *)prev, p->Rule)) return ERR_NONE;
    PyObject *own = get_raw(prev, R_OWN_SEMICOLON);
    if (own != Py_None && PyUnicode_GET_LENGTH(own)) return ERR_NONE;
    PyObject *spaces = NULL;
    take_spaces(p, &spaces);
    set_raw(prev, R_OWN_SEMICOLON, spaces);
    return set_offset(&prev->end, tok.start + 1);
}

/* Parser.comment() and SafeParser.comment() */
static int
comment(parser *p, token tok) {
    kg_PostcssNode *node = new_node(p, p->Comment);
    if (node == NULL) return ERR_PYTHON;
    int r = init_node(p, node, tok.start);
    if (r != ERR_NONE) goto end;
    if ((r = set_offset(&node->end, (p->safe ? (tok.end < 0 ? 0 : tok.end) : js_or(js_or(tok.end, tok.start), 0)) + 1)) != ERR_NONE) goto end;
    Py_ssize_t a = tok.ts + 2, b = tok.te > p->length ? p->length : tok.te;
    if (p->safe) {
        /* text[2:], without a closing */
        if (a > b) a = b;
        if (b - a >= 2 && CH(b - 2) == '*' && CH(b - 1) == '/') b -= 2;
    } else {
        /* text[2:-2] */
        b -= 2;
        if (a > b) b = a;
    }
    Py_ssize_t l = a, rr = b;
    while (l < rr && is_js_space(CH(l))) l++;
    while (rr > l && is_js_space(CH(rr - 1))) rr--;
    r = ERR_PYTHON;
    if (l == rr) {
        if (set_field(&node->text, Py_NewRef(p->state->empty_string)) != ERR_NONE) goto end;
        PyObject *left = PyUnicode_Substring(p->css, a, b);
        if (left == NULL) goto end;
        set_raw(node, R_LEFT, left);
        set_raw(node, R_RIGHT, Py_NewRef(p->state->empty_string));
    } else {
        PyObject *left = PyUnicode_Substring(p->css, a, l), *right = PyUnicode_Substring(p->css, rr, b);
        if (left == NULL || right == NULL) { Py_XDECREF(left); Py_XDECREF(right); goto end; }
        set_raw(node, R_LEFT, left);
        set_raw(node, R_RIGHT, right);
        if (set_field(&node->text, PyUnicode_Substring(p->css, l, rr)) != ERR_NONE) goto end;
    }
    r = ERR_NONE;
end:
    Py_DECREF(node);
    return r;
}

static int decl(parser *p, tokens *t, bool custom_property);

/* SafeParser.precheck_missed_semicolon() */
static int
precheck_missed_semicolon(parser *p, tokens *t) {
    if (!p->safe) return ERR_NONE;
    Py_ssize_t colon;
    int r = colon_index(p, t, &colon);
    if (r != ERR_NONE || colon < 0) return r;
    Py_ssize_t next_start = colon - 1;
    while (next_start >= 0) {
        if (t->items[next_start].type == PT_WORD) break;
        next_start--;
    }
    if (next_start <= 0) return ERR_NONE;
    Py_ssize_t prev_end = next_start - 1;
    while (prev_end >= 0) {
        if (t->items[prev_end].type != PT_SPACE) {
            prev_end++;
            break;
        }
        prev_end--;
    }
    tokens other = {0};
    if (tokens_slice(t, next_start, t->len, &other) != 0) return ERR_PYTHON;
    const Py_ssize_t pe = py_index(prev_end, t->len);
    PyObject *spaces = join_tokens(p, t, pe, next_start);
    if (spaces == NULL) { tokens_free(&other); return ERR_PYTHON; }
    t->len = pe;
    Py_SETREF(p->spaces, spaces);
    r = decl(p, &other, false);
    tokens_free(&other);
    return r;
}

/* Parser.check_missed_semicolon() */
static int
check_missed_semicolon(parser *p, const tokens *t) {
    if (p->safe) return ERR_NONE;
    Py_ssize_t colon;
    int r = colon_index(p, t, &colon);
    if (r != ERR_NONE || colon < 0) return r;
    Py_ssize_t founded = 0;
    token tok = t->items[0];
    for (Py_ssize_t j = colon - 1; j >= 0; j--) {
        tok = t->items[j];
        if (tok.type != PT_SPACE) {
            founded++;
            if (founded == 2) break;
        }
    }
    const Py_ssize_t offset = tok.type == PT_WORD ? (tok.end < 0 ? 0 : tok.end) + 1 : tok.start;
    return syntax_error(p, "Missed semicolon", offset < 0 ? 0 : offset, -1);
}

/* Parser.decl() and SafeParser.decl() */
static int
decl(parser *p, tokens *t, bool custom_property) {
    if (p->safe) {
        bool word = false;
        for (Py_ssize_t i = 0; i < t->len && !word; i++) word = t->items[i].type == PT_WORD;
        if (t->len <= 1 || !word) return ERR_NONE;
        custom_property = false;  /* the safe parser drops customProperty */
    }
    kg_PostcssNode *node = new_node(p, p->Declaration);
    if (node == NULL) return ERR_PYTHON;
    tokens rest = {0}, value = {0};
    PyObject *prop = NULL, *s = NULL;
    int r = init_node(p, node, t->items[0].start);
    if (r != ERR_NONE) goto end;
    r = ERR_PYTHON;
    if (set_field(&node->important, Py_NewRef(Py_False)) != ERR_NONE) goto end;
    const token last = t->items[t->len - 1];
    if (last.type == PT_SEMICOLON) {
        p->semicolon = true;
        t->len--;
    }
    Py_ssize_t end = js_or(last.end, last.start);
    if (end <= 0) {
        /* _find_last_with_position() */
        end = -1;
        for (Py_ssize_t i = t->len - 1; i >= 0; i--) {
            const Py_ssize_t pos = js_or(t->items[i].end, t->items[i].start);
            if (pos > 0) { end = pos; break; }
        }
    }
    if (set_offset(&node->end, end < 0 ? -1 : end + 1) != ERR_NONE) goto end;

    Py_ssize_t start = 0;
    while (start < t->len && t->items[start].type != PT_WORD) {
        if (start == t->len - 1 && (r = unknown_word(p, t, start, start + 1)) != ERR_NONE) goto end;
        start++;
    }
    r = ERR_PYTHON;
    if (start >= t->len) {
        /* Unreachable: the safe parser only parses declarations with a word */
        PyErr_SetString(PyExc_SystemError, "declaration without a word");
        goto end;
    }
    if ((s = join_tokens(p, t, 0, start)) == NULL) goto end;
    if (append_raw(p, node, R_BEFORE, s) != ERR_NONE) goto end;
    Py_CLEAR(s);
    if (set_offset(&node->start, t->items[start].start) != ERR_NONE) goto end;

    const Py_ssize_t prop_start = start;
    while (start < t->len) {
        const token_type k = t->items[start].type;
        if (k == PT_COLON || k == PT_SPACE || k == PT_COMMENT) break;
        start++;
    }
    if ((prop = join_tokens(p, t, prop_start, start)) == NULL) goto end;

    const Py_ssize_t between_start = start;
    while (start < t->len) {
        const token tok = t->items[start];
        start++;
        if (tok.type == PT_COLON) break;
        if (tok.type == PT_WORD) {
            /* RE_ASCII_WORD.search(token.text) */
            bool word = false;
            const Py_ssize_t te = tok.te > p->length ? p->length : tok.te;
            for (Py_ssize_t i = tok.ts; i < te && !word; i++) {
                const Py_UCS4 c = CH(i);
                word = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_';
            }
            if (word && (r = unknown_word(p, t, start - 1, start)) != ERR_NONE) goto end;
            r = ERR_PYTHON;
        }
    }
    if ((s = join_tokens(p, t, between_start, start)) == NULL) goto end;
    set_raw(node, R_BETWEEN, s);
    s = NULL;

    if (PyUnicode_GET_LENGTH(prop) && (PyUnicode_READ_CHAR(prop, 0) == '_' || PyUnicode_READ_CHAR(prop, 0) == '*')) {
        PyObject *first = PyUnicode_Substring(prop, 0, 1);
        if (first == NULL) goto end;
        const int ar = append_raw(p, node, R_BEFORE, first);
        Py_DECREF(first);
        if (ar != ERR_NONE) goto end;
        Py_SETREF(prop, PyUnicode_Substring(prop, 1, PyUnicode_GET_LENGTH(prop)));
        if (prop == NULL) goto end;
    }
    Py_XSETREF(node->prop, prop);
    prop = NULL;

    const Py_ssize_t first_spaces_start = start;
    while (start < t->len && is_space_or_comment(t->items[start])) start++;
    Py_ssize_t first_spaces_end = start;
    /* tokens = tokens[start:] */
    if (tokens_slice(t, start, t->len, &rest) != 0) goto end;

    if ((r = precheck_missed_semicolon(p, &rest)) != ERR_NONE) goto end;
    r = ERR_PYTHON;

    for (Py_ssize_t i = rest.len - 1; i >= 0; i--) {
        const token tok = rest.items[i];
        if (token_text_is(p, tok, "!important")) {
            Py_XSETREF(node->important, Py_NewRef(Py_True));
            /* string_from() and spaces_from_end() */
            PyObject *string = join_tokens(p, &rest, i, rest.len);
            if (string == NULL) goto end;
            rest.len = i;
            Py_ssize_t n = rest.len;
            while (n > 0 && rest.items[n - 1].type == PT_SPACE) n--;
            PyObject *spaces = join_tokens(p, &rest, n, rest.len);
            rest.len = n;
            if (spaces == NULL) { Py_DECREF(string); goto end; }
            PyObject *full = PyUnicode_Concat(spaces, string);
            Py_DECREF(spaces);
            Py_DECREF(string);
            if (full == NULL) goto end;
            if (PyUnicode_EqualToUTF8(full, " !important")) Py_DECREF(full);
            else set_raw(node, R_IMPORTANT, full);
            break;
        }
        if (token_text_is(p, tok, "important")) {
            /* cache = tokens[:]; popped from its end while j goes down from i */
            Py_ssize_t cache_len = rest.len;
            Py_ssize_t text_start = cache_len;  /* the text is the tokens cache[text_start:] */
            for (Py_ssize_t j = i; j > 0; j--) {
                const token_type k = rest.items[j].type;
                if (text_start < rest.len && k != PT_SPACE) {
                    /* js_trim(text).startswith('!') */
                    PyObject *text = join_tokens(p, &rest, text_start, rest.len);
                    if (text == NULL) goto end;
                    Py_ssize_t a = 0;
                    const Py_ssize_t n = PyUnicode_GET_LENGTH(text);
                    while (a < n && is_js_space(PyUnicode_READ_CHAR(text, a))) a++;
                    const bool bang = a < n && PyUnicode_READ_CHAR(text, a) == '!';
                    Py_DECREF(text);
                    if (bang) break;
                }
                cache_len--;
                text_start = cache_len;
            }
            PyObject *text = join_tokens(p, &rest, text_start, rest.len);
            if (text == NULL) goto end;
            Py_ssize_t a = 0;
            const Py_ssize_t n = PyUnicode_GET_LENGTH(text);
            while (a < n && is_js_space(PyUnicode_READ_CHAR(text, a))) a++;
            if (a < n && PyUnicode_READ_CHAR(text, a) == '!') {
                Py_XSETREF(node->important, Py_NewRef(Py_True));
                set_raw(node, R_IMPORTANT, text);
                rest.len = cache_len;
            } else Py_DECREF(text);
        }
        if (!is_space_or_comment(tok)) break;
    }

    bool has_value = false;
    for (Py_ssize_t i = 0; i < rest.len && !has_value; i++) has_value = !is_space_or_comment(rest.items[i]);
    if (has_value) {
        if ((s = join_tokens(p, t, first_spaces_start, first_spaces_end)) == NULL) goto end;
        if (append_raw(p, node, R_BETWEEN, s) != ERR_NONE) goto end;
        Py_CLEAR(s);
        first_spaces_end = first_spaces_start;
    }
    /* first_spaces + tokens */
    for (Py_ssize_t i = first_spaces_start; i < first_spaces_end; i++) {
        if (tokens_push(&value, t->items[i]) != 0) goto end;
    }
    for (Py_ssize_t i = 0; i < rest.len; i++) {
        if (tokens_push(&value, rest.items[i]) != 0) goto end;
    }
    PyObject *v = raw_value(p, node, R_VALUE, &value, custom_property);
    if (v == NULL) goto end;
    Py_XSETREF(node->value, v);
    if (!custom_property && PyUnicode_FindChar(v, ':', 0, PyUnicode_GET_LENGTH(v), 1) >= 0) {
        if ((r = check_missed_semicolon(p, &rest)) != ERR_NONE) goto end;
    }
    r = ERR_NONE;
end:
    Py_XDECREF(prop);
    Py_XDECREF(s);
    tokens_free(&rest);
    tokens_free(&value);
    Py_DECREF(node);
    return r;
}

/* Parser.rule() */
static int
rule(parser *p, tokens *t) {
    t->len--;
    kg_PostcssNode *node = new_node(p, p->Rule);
    if (node == NULL) return ERR_PYTHON;
    int r = ERR_PYTHON;
    if (set_field(&node->nodes, PyList_New(0)) != ERR_NONE) goto end;
    if (init_node(p, node, t->items[0].start) != ERR_NONE) goto end;
    PyObject *between = spaces_and_comments_from_end(p, t);
    if (between == NULL) goto end;
    set_raw(node, R_BETWEEN, between);
    if (set_field(&node->selector, raw_value(p, node, R_SELECTOR, t, false)) != ERR_NONE) goto end;
    p->current = node;  /* borrowed: its parent holds it */
    r = ERR_NONE;
end:
    Py_DECREF(node);
    return r;
}

/* Parser.empty_rule() */
static int
empty_rule(parser *p, token tok) {
    kg_PostcssNode *node = new_node(p, p->Rule);
    if (node == NULL) return ERR_PYTHON;
    int r = ERR_PYTHON;
    if (set_field(&node->nodes, PyList_New(0)) != ERR_NONE) goto end;
    if (init_node(p, node, tok.start) != ERR_NONE) goto end;
    if (set_field(&node->selector, Py_NewRef(p->state->empty_string)) != ERR_NONE) goto end;
    set_raw(node, R_BETWEEN, Py_NewRef(p->state->empty_string));
    p->current = node;
    r = ERR_NONE;
end:
    Py_DECREF(node);
    return r;
}

/* A stack of the closing brackets to expect */
typedef struct {
    token_type *items;
    Py_ssize_t len, cap;
} brackets;

static int
brackets_push(brackets *b, token_type t) {
    if (b->len == b->cap) {
        const Py_ssize_t cap = b->cap ? b->cap * 2 : 8;
        token_type *items = PyMem_Realloc(b->items, (size_t)cap * sizeof(token_type));
        if (items == NULL) { PyErr_NoMemory(); return -1; }
        b->items = items;
        b->cap = cap;
    }
    b->items[b->len++] = t;
    return 0;
}

/* Parser.atrule() */
static int
atrule(parser *p, token tok) {
    kg_PostcssNode *node = new_node(p, p->AtRule);
    if (node == NULL) return ERR_PYTHON;
    tokens params = {0};
    brackets br = {0};
    int r = ERR_PYTHON;
    token name_token = tok;
    name_token.ts++;
    if (set_field(&node->name, text_of(p, name_token)) != ERR_NONE) goto end;
    if (PyUnicode_GET_LENGTH(node->name) == 0 && !p->safe) {
        r = syntax_error(p, "At-rule without name", tok.start, tok.start + text_len(p, tok));
        goto end;
    }
    if (init_node(p, node, tok.start) != ERR_NONE) goto end;
    bool last = false, open = false;
    while (!end_of_file(p)) {
        if ((r = next_token(p, &tok)) != 0) goto end;
        r = ERR_PYTHON;
        const token_type kind = tok.type;
        if (kind == PT_OPEN_PAREN || kind == PT_OPEN_SQUARE) {
            if (brackets_push(&br, kind == PT_OPEN_PAREN ? PT_CLOSE_PAREN : PT_CLOSE_SQUARE) != 0) goto end;
        } else if (kind == PT_OPEN_CURLY && br.len) {
            if (brackets_push(&br, PT_CLOSE_CURLY) != 0) goto end;
        } else if (br.len && kind == br.items[br.len - 1]) {
            br.len--;
        }
        if (br.len == 0) {
            if (kind == PT_SEMICOLON) {
                if (set_offset(&node->end, tok.start + 1) != ERR_NONE) goto end;
                p->semicolon = true;
                break;
            }
            if (kind == PT_OPEN_CURLY) {
                open = true;
                break;
            }
            if (kind == PT_CLOSE_CURLY) {
                if (params.len) {
                    Py_ssize_t shift = params.len - 1;
                    while (shift >= 0 && params.items[shift].type == PT_SPACE) shift--;
                    if (shift >= 0) {
                        const token prev = params.items[shift];
                        if (set_offset(&node->end, js_or(js_or(prev.end, prev.start), 0) + 1) != ERR_NONE) goto end;
                    }
                }
                if ((r = end_block(p, tok)) != ERR_NONE) goto end;
                r = ERR_PYTHON;
                break;
            }
        }
        if (tokens_push(&params, tok) != 0) goto end;
        if (end_of_file(p)) {
            last = true;
            break;
        }
    }
    PyObject *between = spaces_and_comments_from_end(p, &params);
    if (between == NULL) goto end;
    set_raw(node, R_BETWEEN, between);
    if (params.len) {
        PyObject *after_name = spaces_and_comments_from_start(p, &params);
        if (after_name == NULL) goto end;
        set_raw(node, R_AFTER_NAME, after_name);
        if (set_field(&node->params, raw_value(p, node, R_PARAMS, &params, false)) != ERR_NONE) goto end;
        if (last) {
            const token t = params.items[params.len - 1];
            if (set_offset(&node->end, js_or(js_or(t.end, t.start), 0) + 1) != ERR_NONE) goto end;
            Py_SETREF(p->spaces, Py_NewRef(get_raw(node, R_BETWEEN)));
            set_raw(node, R_BETWEEN, Py_NewRef(p->state->empty_string));
        }
    } else {
        set_raw(node, R_AFTER_NAME, Py_NewRef(p->state->empty_string));
        if (set_field(&node->params, Py_NewRef(p->state->empty_string)) != ERR_NONE) goto end;
    }
    if (open) {
        if (set_field(&node->nodes, PyList_New(0)) != ERR_NONE) goto end;
        p->current = node;
    } else if (node->nodes == NULL) {
        node->nodes = Py_NewRef(Py_None);
    }
    r = ERR_NONE;
end:
    tokens_free(&params);
    PyMem_Free(br.items);
    Py_DECREF(node);
    return r;
}

/* Parser.other() */
static int
other(parser *p, token start) {
    bool end = false, colon = false;
    token bracket = {0};
    bool has_bracket = false;
    brackets br = {0};
    tokens t = {0};
    int r = ERR_PYTHON;
    /* start.text.startswith('--') */
    const bool custom_property = text_len(p, start) >= 2 && CH(start.ts) == '-' && CH(start.ts + 1) == '-';
    token tok = start;
    bool have = true;
    while (have) {
        const token_type kind = tok.type;
        if (tokens_push(&t, tok) != 0) goto end;
        if (kind == PT_OPEN_PAREN || kind == PT_OPEN_SQUARE) {
            if (!has_bracket) { bracket = tok; has_bracket = true; }
            if (brackets_push(&br, kind == PT_OPEN_PAREN ? PT_CLOSE_PAREN : PT_CLOSE_SQUARE) != 0) goto end;
        } else if (custom_property && colon && kind == PT_OPEN_CURLY) {
            if (!has_bracket) { bracket = tok; has_bracket = true; }
            if (brackets_push(&br, PT_CLOSE_CURLY) != 0) goto end;
        } else if (br.len == 0) {
            if (kind == PT_SEMICOLON) {
                if (colon) {
                    r = decl(p, &t, custom_property);
                    goto end;
                }
                break;
            }
            if (kind == PT_OPEN_CURLY) {
                r = rule(p, &t);
                goto end;
            }
            if (kind == PT_CLOSE_CURLY) {
                if (back(p, t.items[--t.len]) != ERR_NONE) goto end;
                end = true;
                break;
            }
            if (kind == PT_COLON) colon = true;
        } else if (kind == br.items[br.len - 1]) {
            br.len--;
            if (br.len == 0) has_bracket = false;
        }
        const int n = next_token(p, &tok);
        if (n != 0 && n != NT_END) { r = n; goto end; }
        have = n == 0;
    }
    if (end_of_file(p)) end = true;
    if (br.len && !p->safe) {
        r = syntax_error(p, "Unclosed bracket", bracket.start, bracket.start + 1);
        goto end;
    }
    if (end && colon) {
        if (!custom_property) {
            while (t.len) {
                if (!is_space_or_comment(t.items[t.len - 1])) break;
                if (back(p, t.items[--t.len]) != ERR_NONE) goto end;
            }
        }
        r = decl(p, &t, custom_property);
    } else {
        r = unknown_word(p, &t, 0, t.len);
    }
end:
    tokens_free(&t);
    PyMem_Free(br.items);
    return r;
}

/* Parser.parse() */
static int
parse_all(parser *p) {
    while (!end_of_file(p)) {
        token tok;
        int r = next_token(p, &tok);
        if (r == NT_END) break;
        if (r != 0) return r;
        switch (tok.type) {
            case PT_SPACE: r = add_token_to_spaces(p, tok); break;
            case PT_SEMICOLON: r = free_semicolon(p, tok); break;
            case PT_CLOSE_CURLY: r = end_block(p, tok); break;
            case PT_COMMENT: r = comment(p, tok); break;
            case PT_AT_WORD: r = atrule(p, tok); break;
            case PT_OPEN_CURLY: r = empty_rule(p, tok); break;
            default: r = other(p, tok); break;
        }
        if (r != ERR_NONE) return r;
    }
    return end_file(p);
}

/* }}} */

static PyObject *
parse(kg_module_state *state, PyObject *css, bool safe, PyTypeObject **classes) {
    parser p = {
        .state = state, .css = css, .kind = PyUnicode_KIND(css), .data = PyUnicode_DATA(css),
        .length = PyUnicode_GET_LENGTH(css), .safe = safe, .last_bad_paren = -1,
        .Root = classes[0], .AtRule = classes[1], .Rule = classes[2], .Declaration = classes[3],
        .Comment = classes[4],
    };
    PyObject *ans = NULL;
    p.spaces = Py_NewRef(state->empty_string);
    p.root = new_node(&p, p.Root);
    if (p.root == NULL) goto end;
    p.root->nodes = PyList_New(0);
    p.root->by_type = PyDict_New();
    if (p.root->nodes == NULL || p.root->by_type == NULL) goto end;
    p.root->css = Py_NewRef(css);
    p.root->descendants = Py_NewRef(Py_None);
    if (set_offset(&p.root->start, 0) != ERR_NONE) goto end;
    p.current = p.root;
    const int r = parse_all(&p);
    if (r == ERR_NONE) {
        ans = Py_NewRef((PyObject *)p.root);
    } else if (r == ERR_SYNTAX) {
        PyObject *reason = p.reason_text != NULL ? Py_NewRef(p.reason_text) : PyUnicode_FromString(p.reason);
        if (reason != NULL) {
            if (p.err_end < 0) ans = Py_BuildValue("(NnO)", reason, p.err_offset, Py_None);
            else ans = Py_BuildValue("(Nnn)", reason, p.err_offset, p.err_end);
        }
    }
end:
    Py_XDECREF(p.reason_text);
    Py_XDECREF(p.spaces);
    Py_XDECREF(p.root);
    tokens_free(&p.returned);
    return ans;
}

PyObject *
kg_postcss_parse(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "postcss_parse() takes exactly 3 arguments");
        return NULL;
    }
    PyObject *css = args[0], *classes = args[2];
    if (!PyUnicode_Check(css)) {
        PyErr_Format(PyExc_TypeError, "postcss_parse() expects a str, not %.200s", Py_TYPE(css)->tp_name);
        return NULL;
    }
    const int safe = PyObject_IsTrue(args[1]);
    if (safe < 0) return NULL;
    if (!PyTuple_Check(classes) || PyTuple_GET_SIZE(classes) != 5) {
        PyErr_SetString(PyExc_TypeError, "postcss_parse() needs a tuple of the 5 node classes");
        return NULL;
    }
    PyTypeObject *types[5];
    for (int i = 0; i < 5; i++) {
        PyObject *c = PyTuple_GET_ITEM(classes, i);
        if (!PyType_Check(c) || !PyType_IsSubtype((PyTypeObject *)c, state->PostcssNode_type) ||
            ((PyTypeObject *)c)->tp_basicsize != state->PostcssNode_type->tp_basicsize) {
            PyErr_SetString(PyExc_TypeError, "postcss_parse() needs subclasses of PostcssNode without extra slots");
            return NULL;
        }
        types[i] = (PyTypeObject *)c;
    }
    return parse(state, css, safe, types);
}

int
kg_postcss_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->PostcssNode_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &PostcssNode_spec, NULL);
    if (state->PostcssNode_type == NULL) return -1;
    return PyModule_AddType(module, state->PostcssNode_type);
}

int
kg_postcss_traverse(PyObject *module, visitproc visit, void *arg) {
    Py_VISIT(kg_get_module_state(module)->PostcssNode_type);
    return 0;
}

void
kg_postcss_clear(PyObject *module) {
    Py_CLEAR(kg_get_module_state(module)->PostcssNode_type);
}
