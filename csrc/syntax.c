/*
 * syntax.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * The generic parse algorithms of CSS Syntax Level 3 (Editor's Draft, with
 * nesting) over the grouped token array: stylesheets, rule lists, block
 * contents, single rules and single declarations.
 *
 * Notes on the mapping from the spec:
 *  - Blocks are already grouped, so "consume a component value" is
 *    kg_next_sibling() and nothing here recurses.
 *  - Comments are not tokens in the spec. They are skipped wherever the spec
 *    skips whitespace, kept inside preludes and values, and emitted as
 *    COMMENT tokens between rules and declarations (requirements P7).
 *  - In nested mode (block contents) a <}-token> ends every consumer exactly
 *    as EOF does, so nested parsing just truncates its range at the first
 *    top level }. Inside a {} block there never is one, since it would have
 *    closed the block.
 *  - "Mark and restore" is retrying from the same token index.
 *
 * Thread safety: the result objects are immutable.
 */

#include "objects.h"
#include <stddef.h>
#include <stdlib.h>

#define KG_NO_BLOCK UINT32_MAX

typedef struct {
    PyObject_HEAD
    kg_TokenStream *stream;
    PyObject *name;          /* AtRule and Declaration, NULL for QualifiedRule */
    uint32_t pos, end;       /* source range of the whole node */
    uint32_t first, last;    /* token range of the prelude (rules) or value (declarations) */
    uint32_t text_pos, text_end; /* source range of the prelude or value */
    uint32_t block;          /* token index of the {} block, or KG_NO_BLOCK */
    char important;
} kg_Node;

typedef struct {
    PyObject_HEAD
    kg_TokenStream *stream;
    PyObject *items;    /* tuple */
    PyObject *errors;   /* tuple of ParseError */
    PyObject *encoding; /* str or None */
} kg_NodeList;

typedef struct {
    uint32_t offset, kind, seq;
} sortable_error;

typedef struct {
    kg_TokenStream *ts;
    kg_module_state *state;
    PyObject *nodes; /* list */
    kg_error *errors;
    uint32_t error_count, error_capacity;
    uint32_t *sibs;
    uint32_t sib_count, sib_capacity;
} kg_parser;

/* ---------------------------------------------------------------------------
 * Parser helpers
 * ------------------------------------------------------------------------- */

static inline uint8_t ttype(const kg_parser *p, uint32_t i) { return p->ts->arr.tokens[i].type; }
static inline const kg_token *tok(const kg_parser *p, uint32_t i) { return p->ts->arr.tokens + i; }

/* Source offset where the token range ending at last ends */
static inline uint32_t
range_end_offset(const kg_TokenStream *ts, uint32_t last) {
    return last < ts->arr.count ? ts->arr.tokens[last].start : ts->len;
}

static inline uint32_t
skip_ignorable(const kg_parser *p, uint32_t i, uint32_t last) {
    while (i < last && kg_is_ignorable(p->ts, i)) i = kg_next_sibling(p->ts, i);
    return i;
}

static bool
add_error(kg_parser *p, uint32_t offset, kg_error_kind kind) {
    if (p->error_count >= p->error_capacity) {
        uint32_t cap = p->error_capacity ? p->error_capacity * 2 : 8;
        kg_error *e = PyMem_Realloc(p->errors, cap * sizeof(kg_error));
        if (e == NULL) { PyErr_NoMemory(); return false; }
        p->errors = e;
        p->error_capacity = cap;
    }
    p->errors[p->error_count].offset = offset;
    p->errors[p->error_count++].kind = (uint32_t)kind;
    return true;
}

static bool
push_sib(kg_parser *p, uint32_t idx) {
    if (p->sib_count >= p->sib_capacity) {
        uint32_t cap = p->sib_capacity ? p->sib_capacity * 2 : 64;
        uint32_t *s = PyMem_Realloc(p->sibs, cap * sizeof(uint32_t));
        if (s == NULL) { PyErr_NoMemory(); return false; }
        p->sibs = s;
        p->sib_capacity = cap;
    }
    p->sibs[p->sib_count++] = idx;
    return true;
}

static void
parser_free(kg_parser *p) {
    PyMem_Free(p->errors);
    PyMem_Free(p->sibs);
    Py_XDECREF(p->nodes);
}

/* Whether the unescaped value of the token at idx starts with "--". Returns -1 on error. */
static int
value_starts_with_dashes(kg_parser *p, uint32_t idx) {
    const kg_token *t = tok(p, idx);
    if (t->vend - t->vstart < 2) return 0;
    if (!(t->flags & KG_TF_NEEDS_PROCESSING)) {
        return PyUnicode_READ(p->ts->kind, p->ts->data, t->vstart) == '-' &&
               PyUnicode_READ(p->ts->kind, p->ts->data, t->vstart + 1) == '-';
    }
    PyObject *v = kg_range_value(p->ts, t, t->vstart, t->vend, false);
    if (v == NULL) return -1;
    int ans = PyUnicode_GET_LENGTH(v) >= 2 && PyUnicode_READ_CHAR(v, 0) == '-' && PyUnicode_READ_CHAR(v, 1) == '-';
    Py_DECREF(v);
    return ans;
}

/* Whether the IDENT at idx is an ASCII case-insensitive match for "important". Returns -1 on error. */
static int
is_important_ident(kg_parser *p, uint32_t idx) {
    const kg_token *t = tok(p, idx);
    if (t->type != KG_TOK_IDENT) return 0;
    PyObject *v = kg_range_value(p->ts, t, t->vstart, t->vend, false);
    if (v == NULL) return -1;
    static const char important[] = "important";
    int ans = PyUnicode_GET_LENGTH(v) == 9;
    for (Py_ssize_t i = 0; ans && i < 9; i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        if (c >= 'A' && c <= 'Z') c |= 0x20;
        ans = c == (Py_UCS4)important[i];
    }
    Py_DECREF(v);
    return ans;
}

static inline bool
is_delim(const kg_parser *p, uint32_t idx, Py_UCS4 ch) {
    const kg_token *t = tok(p, idx);
    return t->type == KG_TOK_DELIM && PyUnicode_READ(p->ts->kind, p->ts->data, t->start) == ch;
}

static kg_Node *
node_new(kg_parser *p, PyTypeObject *type, uint32_t pos) {
    kg_Node *n = PyObject_New(kg_Node, type);
    if (n == NULL) return NULL;
    n->stream = (kg_TokenStream *)Py_NewRef(p->ts);
    n->name = NULL;
    n->pos = n->end = pos;
    n->first = n->last = 0;
    n->text_pos = n->text_end = pos;
    n->block = KG_NO_BLOCK;
    n->important = 0;
    return n;
}

/* ---------------------------------------------------------------------------
 * Consumers. Each takes the index of its first token and the end of the
 * range, stores the index to continue from in *next, and returns a new
 * reference to a node, Py_None for "nothing" or NULL with an exception set.
 * ------------------------------------------------------------------------- */

static PyObject *
consume_at_rule(kg_parser *p, uint32_t i, uint32_t last, uint32_t *next) {
    const kg_token *at = tok(p, i);
    kg_Node *n = node_new(p, p->state->AtRule_type, at->start);
    if (n == NULL) return NULL;
    if ((n->name = kg_range_value(p->ts, at, at->vstart, at->vend, false)) == NULL) { Py_DECREF(n); return NULL; }
    n->first = i + 1;
    n->text_pos = at->end;
    uint32_t j = i + 1;
    for (; j < last; j = kg_next_sibling(p->ts, j)) {
        const uint8_t type = ttype(p, j);
        if (type == KG_TOK_SEMICOLON) {
            n->last = j;
            n->text_end = tok(p, j)->start;
            n->end = tok(p, j)->end;
            *next = j + 1;
            return (PyObject *)n;
        }
        if (type == KG_TOK_LEFT_CURLY) {
            n->last = j;
            n->text_end = tok(p, j)->start;
            n->block = j;
            n->end = kg_item_end(p->ts, j);
            *next = kg_next_sibling(p->ts, j);
            return (PyObject *)n;
        }
    }
    /* Ended by EOF (or a } in nested mode): the prelude, trailing whitespace
     * included, is part of the rule */
    n->last = last;
    n->text_end = range_end_offset(p->ts, last);
    n->end = n->text_end;
    *next = last;
    return (PyObject *)n;
}

/* Whether the prelude [i, j) starts with an ident beginning with -- and a colon, -1 on error */
static int
looks_like_custom_property(kg_parser *p, uint32_t i, uint32_t j) {
    i = skip_ignorable(p, i, j);
    if (i >= j || ttype(p, i) != KG_TOK_IDENT) return 0;
    int dashes = value_starts_with_dashes(p, i);
    if (dashes != 1) return dashes;
    i = skip_ignorable(p, i + 1, j);
    return i < j && ttype(p, i) == KG_TOK_COLON;
}

static PyObject *
consume_qualified_rule(kg_parser *p, uint32_t i, uint32_t last, bool nested, bool stop_at_semicolon, uint32_t *next) {
    const uint32_t start = tok(p, i)->start;
    for (uint32_t j = i; j < last; j = kg_next_sibling(p->ts, j)) {
        const uint8_t type = ttype(p, j);
        if (type == KG_TOK_LEFT_CURLY) {
            int custom = looks_like_custom_property(p, i, j);
            if (custom < 0) return NULL;
            if (custom) {
                if (!add_error(p, start, KG_ERR_INVALID_RULE)) return NULL;
                if (nested) {
                    /* Consume the remnants of a bad declaration */
                    while (j < last && ttype(p, j) != KG_TOK_SEMICOLON) j = kg_next_sibling(p->ts, j);
                    *next = j;
                } else *next = kg_next_sibling(p->ts, j);
                Py_RETURN_NONE;
            }
            kg_Node *n = node_new(p, p->state->QualifiedRule_type, start);
            if (n == NULL) return NULL;
            n->first = i;
            n->last = j;
            n->text_end = tok(p, j)->start;
            n->block = j;
            n->end = kg_item_end(p->ts, j);
            *next = kg_next_sibling(p->ts, j);
            return (PyObject *)n;
        }
        if (type == KG_TOK_SEMICOLON && stop_at_semicolon) {
            if (!add_error(p, start, KG_ERR_INVALID_RULE)) return NULL;
            *next = j;
            Py_RETURN_NONE;
        }
    }
    if (!add_error(p, start, KG_ERR_INVALID_RULE)) return NULL;
    *next = last;
    Py_RETURN_NONE;
}

/* Consume a declaration. Failure returns None without recording an error,
 * the caller decides whether it is one. */
static PyObject *
consume_declaration(kg_parser *p, uint32_t i, uint32_t last, uint32_t *next) {
    kg_TokenStream *ts = p->ts;
    *next = i;
    if (ttype(p, i) != KG_TOK_IDENT) Py_RETURN_NONE;
    uint32_t j = skip_ignorable(p, i + 1, last);
    if (j >= last || ttype(p, j) != KG_TOK_COLON) Py_RETURN_NONE;
    const uint32_t colon_end = tok(p, j)->end;
    j = skip_ignorable(p, j + 1, last);
    p->sib_count = 0;
    uint32_t k = j;
    for (; k < last && ttype(p, k) != KG_TOK_SEMICOLON; k = kg_next_sibling(ts, k)) {
        if (!push_sib(p, k)) return NULL;
    }
    const uint32_t *sibs = p->sibs;
    uint32_t n = p->sib_count, m = n;
    /* The declaration's own extent ends at its last non-whitespace item, comments included */
    while (m && ttype(p, sibs[m - 1]) == KG_TOK_WHITESPACE) m--;
    const uint32_t decl_end = m ? kg_item_end(ts, sibs[m - 1]) : colon_end;
    while (n && kg_is_ignorable(ts, sibs[n - 1])) n--;
    bool important = false;
    if (n) {
        int imp = is_important_ident(p, sibs[n - 1]);
        if (imp < 0) return NULL;
        if (imp) {
            uint32_t q = n - 1;
            while (q && kg_is_ignorable(ts, sibs[q - 1])) q--;
            if (q && is_delim(p, sibs[q - 1], '!')) {
                important = true;
                n = q - 1;
                while (n && kg_is_ignorable(ts, sibs[n - 1])) n--;
            }
        }
    }
    const kg_token *name_tok = tok(p, i);
    PyObject *name = kg_range_value(ts, name_tok, name_tok->vstart, name_tok->vend, false);
    if (name == NULL) return NULL;
    const bool custom = PyUnicode_GET_LENGTH(name) >= 2 && PyUnicode_READ_CHAR(name, 0) == '-' &&
                        PyUnicode_READ_CHAR(name, 1) == '-';
    if (!custom) {
        /* A top level {} block is only allowed as the entire value */
        bool has_curly = false;
        uint32_t significant = 0;
        for (uint32_t x = 0; x < n; x++) {
            if (kg_is_ignorable(ts, sibs[x])) continue;
            significant++;
            if (ttype(p, sibs[x]) == KG_TOK_LEFT_CURLY) has_curly = true;
        }
        if (has_curly && significant > 1) { Py_DECREF(name); Py_RETURN_NONE; }
    }
    kg_Node *d = node_new(p, p->state->Declaration_type, name_tok->start);
    if (d == NULL) { Py_DECREF(name); return NULL; }
    d->name = name;
    d->end = decl_end;
    d->important = important;
    if (n) {
        d->first = sibs[0];
        d->last = kg_next_sibling(ts, sibs[n - 1]);
        d->text_pos = tok(p, sibs[0])->start;
        d->text_end = kg_item_end(ts, sibs[n - 1]);
    } else {
        d->first = d->last = j;
        d->text_pos = d->text_end = colon_end;
    }
    *next = k;
    return (PyObject *)d;
}

static bool
append_node(kg_parser *p, PyObject *node) {
    if (node == NULL) return false;
    int ret = node == Py_None ? 0 : PyList_Append(p->nodes, node);
    Py_DECREF(node);
    return ret == 0;
}

/* "Consume a stylesheet's contents" (top_level) or "consume a list of rules"
 * from CSS Syntax 2021, where CDO and CDC are not special. */
static bool
consume_rule_list(kg_parser *p, uint32_t i, uint32_t last, bool top_level) {
    while (i < last) {
        const uint8_t type = ttype(p, i);
        uint32_t next = i;
        PyObject *node;
        switch (type) {
            case KG_TOK_WHITESPACE: i++; continue;
            case KG_TOK_COMMENT:
                if (!append_node(p, kg_make_token(p->ts, i))) return false;
                i++;
                continue;
            case KG_TOK_CDO: case KG_TOK_CDC:
                if (top_level) { i++; continue; }
                node = consume_qualified_rule(p, i, last, false, false, &next);
                break;
            case KG_TOK_AT_KEYWORD:
                node = consume_at_rule(p, i, last, &next);
                break;
            default:
                node = consume_qualified_rule(p, i, last, false, false, &next);
                break;
        }
        if (!append_node(p, node)) return false;
        i = next;
    }
    return true;
}

/* "Consume a block's contents": declarations and nested rules */
static bool
consume_block_contents(kg_parser *p, uint32_t i, uint32_t last) {
    /* A top level } ends nested parsing, exactly like EOF */
    for (uint32_t j = i; j < last; j = kg_next_sibling(p->ts, j)) {
        if (ttype(p, j) == KG_TOK_RIGHT_CURLY) { last = j; break; }
    }
    while (i < last) {
        const uint8_t type = ttype(p, i);
        uint32_t next = i;
        PyObject *node;
        switch (type) {
            case KG_TOK_WHITESPACE: case KG_TOK_SEMICOLON: i++; continue;
            case KG_TOK_COMMENT:
                if (!append_node(p, kg_make_token(p->ts, i))) return false;
                i++;
                continue;
            case KG_TOK_AT_KEYWORD:
                node = consume_at_rule(p, i, last, &next);
                break;
            default:
                node = consume_declaration(p, i, last, &next);
                if (node == Py_None) {
                    Py_DECREF(node);
                    node = consume_qualified_rule(p, i, last, true, true, &next);
                }
                break;
        }
        if (!append_node(p, node)) return false;
        i = next;
    }
    return true;
}

/* ---------------------------------------------------------------------------
 * Results
 * ------------------------------------------------------------------------- */

static int
compare_errors(const void *a, const void *b) {
    const sortable_error *x = a, *y = b;
    if (x->offset != y->offset) return x->offset < y->offset ? -1 : 1;
    return x->seq < y->seq ? -1 : (x->seq > y->seq);
}

/* All tokenizer errors in [pos, end] plus the syntax errors, ordered by offset */
static PyObject *
collect_errors(kg_parser *p, uint32_t pos, uint32_t end) {
    const kg_token_array *a = &p->ts->arr;
    uint32_t n = 0, seq = 0;
    sortable_error *all = PyMem_Malloc(((size_t)a->error_count + p->error_count + 1) * sizeof(sortable_error));
    if (all == NULL) return PyErr_NoMemory();
    for (uint32_t i = 0; i < a->error_count; i++) {
        const kg_error *e = a->errors + i;
        if (e->offset >= pos && e->offset <= end) all[n++] = (sortable_error){e->offset, e->kind, seq++};
    }
    for (uint32_t i = 0; i < p->error_count; i++) all[n++] = (sortable_error){p->errors[i].offset, p->errors[i].kind, seq++};
    qsort(all, n, sizeof(sortable_error), compare_errors);
    PyObject *ans = PyTuple_New(n);
    if (ans != NULL) {
        for (uint32_t i = 0; i < n; i++) {
            PyObject *e = kg_make_parse_error(p->ts, all[i].offset, (kg_error_kind)all[i].kind);
            if (e == NULL) { Py_CLEAR(ans); break; }
            PyTuple_SET_ITEM(ans, i, e);
        }
    }
    PyMem_Free(all);
    return ans;
}

static PyObject *
make_node_list(kg_parser *p, uint32_t pos, uint32_t end, PyObject *encoding) {
    kg_NodeList *self = PyObject_New(kg_NodeList, p->state->NodeList_type);
    if (self == NULL) return NULL;
    self->stream = (kg_TokenStream *)Py_NewRef(p->ts);
    self->encoding = Py_NewRef(encoding);
    self->items = PyList_AsTuple(p->nodes);
    self->errors = self->items ? collect_errors(p, pos, end) : NULL;
    if (self->errors == NULL) { Py_DECREF(self); return NULL; }
    return (PyObject *)self;
}

/* ---------------------------------------------------------------------------
 * Entry points
 * ------------------------------------------------------------------------- */

typedef struct {
    kg_TokenStream *ts; /* new reference */
    uint32_t first, last, pos, end;
    PyObject *encoding; /* borrowed */
} parse_input;

/* Accept str, or a Block if allow_block, as the input to parse. With
 * with_encoding the format has an extra keyword only "encoding" argument. */
static bool
get_input(PyObject *module, PyObject *args, PyObject *kwds, const char *fmt, bool allow_block, bool with_encoding,
          parse_input *in) {
    static char *kwlist[] = {"", "comments", NULL};
    static char *kwlist_with_encoding[] = {"", "comments", "encoding", NULL};
    PyObject *source;
    int comments = 1;
    in->encoding = Py_None;
    if (with_encoding) {
        if (!PyArg_ParseTupleAndKeywords(args, kwds, fmt, kwlist_with_encoding, &source, &comments, &in->encoding)) return false;
        if (in->encoding != Py_None && !PyUnicode_Check(in->encoding)) {
            PyErr_SetString(PyExc_TypeError, "encoding must be a str or None");
            return false;
        }
    } else if (!PyArg_ParseTupleAndKeywords(args, kwds, fmt, kwlist, &source, &comments)) return false;
    kg_module_state *state = kg_get_module_state(module);
    if (allow_block && Py_IS_TYPE(source, state->Block_type)) {
        kg_Group *b = (kg_Group *)source;
        const kg_token *t = b->stream->arr.tokens + b->idx;
        in->ts = (kg_TokenStream *)Py_NewRef(b->stream);
        in->first = b->idx + 1;
        in->last = (t->flags & KG_TF_UNCLOSED) ? in->ts->arr.count : t->match;
        in->pos = t->end;
        in->end = range_end_offset(in->ts, in->last);
        return true;
    }
    if (!PyUnicode_Check(source)) {
        PyErr_Format(PyExc_TypeError, "expected str%s, got %.200s", allow_block ? " or Block" : "", Py_TYPE(source)->tp_name);
        return false;
    }
    in->ts = kg_tokenize_to_stream(module, source, comments != 0);
    if (in->ts == NULL) return false;
    in->first = 0;
    in->last = in->ts->arr.count;
    in->pos = 0;
    in->end = in->ts->len;
    return true;
}

static bool
parser_init(kg_parser *p, PyObject *module, kg_TokenStream *ts) {
    memset(p, 0, sizeof(*p));
    p->ts = ts;
    p->state = kg_get_module_state(module);
    p->nodes = PyList_New(0);
    return p->nodes != NULL;
}

static PyObject *
parse_list(PyObject *module, PyObject *args, PyObject *kwds, const char *fmt, int mode) {
    parse_input in;
    if (!get_input(module, args, kwds, fmt, mode != 0, mode == 0, &in)) return NULL;
    kg_parser p;
    PyObject *ans = NULL;
    if (parser_init(&p, module, in.ts)) {
        bool ok = mode == 2 ? consume_block_contents(&p, in.first, in.last)
                            : consume_rule_list(&p, in.first, in.last, mode == 0);
        if (ok) ans = make_node_list(&p, in.pos, in.end, in.encoding);
    }
    parser_free(&p);
    Py_DECREF(in.ts);
    return ans;
}

PyObject *
kg_parse_stylesheet(PyObject *module, PyObject *args, PyObject *kwds) {
    return parse_list(module, args, kwds, "O|$pO:parse_stylesheet", 0);
}

PyObject *
kg_parse_rule_list(PyObject *module, PyObject *args, PyObject *kwds) {
    return parse_list(module, args, kwds, "O|$p:parse_rule_list", 1);
}

PyObject *
kg_parse_block_contents(PyObject *module, PyObject *args, PyObject *kwds) {
    return parse_list(module, args, kwds, "O|$p:parse_block_contents", 2);
}

PyObject *
kg_parse_one_rule(PyObject *module, PyObject *args, PyObject *kwds) {
    parse_input in;
    if (!get_input(module, args, kwds, "O|$p:parse_one_rule", false, false, &in)) return NULL;
    kg_parser p;
    PyObject *ans = NULL;
    kg_TokenStream *ts = in.ts;
    if (parser_init(&p, module, ts)) {
        const uint32_t count = ts->arr.count;
        uint32_t i = skip_ignorable(&p, 0, count), next = count;
        if (i >= count) ans = kg_make_parse_error(ts, ts->len, KG_ERR_EMPTY);
        else {
            PyObject *rule = ttype(&p, i) == KG_TOK_AT_KEYWORD ? consume_at_rule(&p, i, count, &next)
                                                               : consume_qualified_rule(&p, i, count, false, false, &next);
            if (rule == Py_None) {
                Py_DECREF(rule);
                ans = kg_make_parse_error(ts, ts->arr.tokens[i].start, KG_ERR_INVALID_RULE);
            } else if (rule != NULL) {
                uint32_t j = skip_ignorable(&p, next, count);
                if (j < count) {
                    Py_DECREF(rule);
                    ans = kg_make_parse_error(ts, ts->arr.tokens[j].start, KG_ERR_EXTRA_INPUT);
                } else ans = rule;
            }
        }
    }
    parser_free(&p);
    Py_DECREF(ts);
    return ans;
}

PyObject *
kg_parse_one_declaration(PyObject *module, PyObject *args, PyObject *kwds) {
    parse_input in;
    if (!get_input(module, args, kwds, "O|$p:parse_one_declaration", false, false, &in)) return NULL;
    kg_parser p;
    PyObject *ans = NULL;
    kg_TokenStream *ts = in.ts;
    if (parser_init(&p, module, ts)) {
        const uint32_t count = ts->arr.count;
        uint32_t i = skip_ignorable(&p, 0, count), next;
        if (i >= count) ans = kg_make_parse_error(ts, ts->len, KG_ERR_EMPTY);
        else {
            ans = consume_declaration(&p, i, count, &next);
            if (ans == Py_None) {
                Py_DECREF(ans);
                ans = kg_make_parse_error(ts, ts->arr.tokens[i].start, KG_ERR_INVALID_DECLARATION);
            }
        }
    }
    parser_free(&p);
    Py_DECREF(ts);
    return ans;
}

/* ---------------------------------------------------------------------------
 * Node types
 * ------------------------------------------------------------------------- */

static void
Node_dealloc(PyObject *o) {
    kg_Node *self = (kg_Node *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->stream);
    Py_XDECREF(self->name);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static PyObject *
Node_source_text(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Node *self = (kg_Node *)o;
    return PyUnicode_Substring(self->stream->source, self->pos, self->end);
}

static PyObject *
Node_line(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Node *self = (kg_Node *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->pos, &line, &col)) return NULL;
    return PyLong_FromSsize_t(line);
}

static PyObject *
Node_column(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Node *self = (kg_Node *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->pos, &line, &col)) return NULL;
    return PyLong_FromSsize_t(col);
}

static PyObject *
Node_values(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Node *self = (kg_Node *)o;
    return kg_cvl_new(self->stream, self->first, self->last, self->text_pos, self->text_end);
}

static PyObject *
Node_block(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Node *self = (kg_Node *)o;
    if (self->block == KG_NO_BLOCK) Py_RETURN_NONE;
    return kg_group_new(self->stream, self->block);
}

static PyObject *
Node_repr(PyObject *o) {
    kg_Node *self = (kg_Node *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, self->pos, &line, &col)) return NULL;
    const char *tname = strrchr(Py_TYPE(o)->tp_name, '.') + 1;
    if (self->name == NULL) return PyUnicode_FromFormat("<%s at %zd:%zd>", tname, line, col);
    return PyUnicode_FromFormat("<%s %R%s at %zd:%zd>", tname, self->name, self->important ? " !important" : "", line, col);
}

#define COMMON_NODE_MEMBERS \
    {"pos", Py_T_UINT, offsetof(kg_Node, pos), Py_READONLY, "Offset of the start of this node in the source"}, \
    {"end", Py_T_UINT, offsetof(kg_Node, end), Py_READONLY, "Offset just past the end of this node in the source"}, \
    {"stream", Py_T_OBJECT_EX, offsetof(kg_Node, stream), Py_READONLY, "The TokenStream this node belongs to"}

#define COMMON_NODE_GETSET \
    {"source_text", Node_source_text, NULL, "The exact source text of this node", NULL}, \
    {"line", Node_line, NULL, "1-based line number of the start of this node", NULL}, \
    {"column", Node_column, NULL, "1-based column (in code points) of the start of this node", NULL}

#define NODE_SPEC(cname, slots_name) { \
    .name = "kgcss." #cname, \
    .basicsize = sizeof(kg_Node), \
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION, \
    .slots = slots_name, \
}

static PyMemberDef AtRule_members[] = {
    COMMON_NODE_MEMBERS,
    {"name", Py_T_OBJECT_EX, offsetof(kg_Node, name), Py_READONLY, "The unescaped name, without the @"},
    {NULL, 0, 0, 0, NULL}
};
static PyGetSetDef AtRule_getset[] = {
    COMMON_NODE_GETSET,
    {"prelude", Node_values, NULL, "The component values between the name and the block or semicolon", NULL},
    {"block", Node_block, NULL, "The {} Block, or None for at-rules ended by a semicolon or EOF", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};
static PyType_Slot AtRule_slots[] = {
    {Py_tp_dealloc, Node_dealloc}, {Py_tp_repr, Node_repr},
    {Py_tp_members, AtRule_members}, {Py_tp_getset, AtRule_getset},
    {Py_tp_doc, (void *)"An at-rule such as @media or @import. The block, if any, is not parsed further."},
    {0, NULL}
};
static PyType_Spec AtRule_spec = NODE_SPEC(AtRule, AtRule_slots);

static PyMemberDef QualifiedRule_members[] = {COMMON_NODE_MEMBERS, {NULL, 0, 0, 0, NULL}};
static PyGetSetDef QualifiedRule_getset[] = {
    COMMON_NODE_GETSET,
    {"prelude", Node_values, NULL, "The component values before the block, usually a selector list", NULL},
    {"block", Node_block, NULL, "The {} Block", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};
static PyType_Slot QualifiedRule_slots[] = {
    {Py_tp_dealloc, Node_dealloc}, {Py_tp_repr, Node_repr},
    {Py_tp_members, QualifiedRule_members}, {Py_tp_getset, QualifiedRule_getset},
    {Py_tp_doc, (void *)"A qualified rule, such as a style rule: a prelude and a {} block. "
                        "Use parse_block_contents() on the block to get its declarations and nested rules."},
    {0, NULL}
};
static PyType_Spec QualifiedRule_spec = NODE_SPEC(QualifiedRule, QualifiedRule_slots);

static PyMemberDef Declaration_members[] = {
    COMMON_NODE_MEMBERS,
    {"name", Py_T_OBJECT_EX, offsetof(kg_Node, name), Py_READONLY, "The unescaped property name, case preserved"},
    {"important", Py_T_BOOL, offsetof(kg_Node, important), Py_READONLY, "True if the declaration has !important"},
    {NULL, 0, 0, 0, NULL}
};
static PyGetSetDef Declaration_getset[] = {
    COMMON_NODE_GETSET,
    {"value", Node_values, NULL,
     "The value as component values, without surrounding whitespace and comments and without !important", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};
static PyType_Slot Declaration_slots[] = {
    {Py_tp_dealloc, Node_dealloc}, {Py_tp_repr, Node_repr},
    {Py_tp_members, Declaration_members}, {Py_tp_getset, Declaration_getset},
    {Py_tp_doc, (void *)"A declaration: name, value and the !important flag."},
    {0, NULL}
};
static PyType_Spec Declaration_spec = NODE_SPEC(Declaration, Declaration_slots);

/* ---------------------------------------------------------------------------
 * NodeList
 * ------------------------------------------------------------------------- */

static void
NodeList_dealloc(PyObject *o) {
    kg_NodeList *self = (kg_NodeList *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->stream);
    Py_XDECREF(self->items);
    Py_XDECREF(self->errors);
    Py_XDECREF(self->encoding);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static Py_ssize_t
NodeList_len(PyObject *o) { return PyTuple_GET_SIZE(((kg_NodeList *)o)->items); }

static PyObject *
NodeList_item(PyObject *o, Py_ssize_t i) {
    kg_NodeList *self = (kg_NodeList *)o;
    if (i < 0 || i >= PyTuple_GET_SIZE(self->items)) {
        PyErr_SetString(PyExc_IndexError, "node index out of range");
        return NULL;
    }
    return Py_NewRef(PyTuple_GET_ITEM(self->items, i));
}

static PyObject *
NodeList_repr(PyObject *o) {
    kg_NodeList *self = (kg_NodeList *)o;
    return PyUnicode_FromFormat("<NodeList of %zd nodes and %zd errors>", PyTuple_GET_SIZE(self->items),
                                PyTuple_GET_SIZE(self->errors));
}

static PyMemberDef NodeList_members[] = {
    {"stream", Py_T_OBJECT_EX, offsetof(kg_NodeList, stream), Py_READONLY, "The TokenStream that was parsed"},
    {"errors", Py_T_OBJECT_EX, offsetof(kg_NodeList, errors), Py_READONLY,
     "Tokenizer and syntax errors in the parsed range, as a tuple of ParseError ordered by offset"},
    {"encoding", Py_T_OBJECT_EX, offsetof(kg_NodeList, encoding), Py_READONLY,
     "For stylesheets parsed from bytes, the name of the encoding used to decode them, otherwise None"},
    {NULL, 0, 0, 0, NULL}
};

static PyType_Slot NodeList_slots[] = {
    {Py_tp_dealloc, NodeList_dealloc},
    {Py_tp_repr, NodeList_repr},
    {Py_tp_members, NodeList_members},
    {Py_sq_length, NodeList_len},
    {Py_sq_item, NodeList_item},
    {Py_tp_doc, (void *)"An immutable sequence of parsed nodes (AtRule, QualifiedRule, Declaration, and COMMENT "
                        "Token), with the errors found while parsing."},
    {0, NULL}
};

static PyType_Spec NodeList_spec = {
    .name = "kgcss.NodeList",
    .basicsize = sizeof(kg_NodeList),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = NodeList_slots,
};

/* ---------------------------------------------------------------------------
 * Module integration
 * ------------------------------------------------------------------------- */

int
kg_syntax_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
#define T(name) \
    state->name##_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &name##_spec, NULL); \
    if (state->name##_type == NULL || PyModule_AddType(module, state->name##_type) != 0) return -1;
    T(AtRule) T(QualifiedRule) T(Declaration) T(NodeList)
#undef T
    return 0;
}

int
kg_syntax_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->AtRule_type);
    Py_VISIT(state->QualifiedRule_type);
    Py_VISIT(state->Declaration_type);
    Py_VISIT(state->NodeList_type);
    return 0;
}

void
kg_syntax_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->AtRule_type);
    Py_CLEAR(state->QualifiedRule_type);
    Py_CLEAR(state->Declaration_type);
    Py_CLEAR(state->NodeList_type);
}
