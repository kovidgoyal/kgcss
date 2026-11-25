/*
 * selectors.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * The selector parser (Selectors Level 4 and CSS Nesting, see
 * docs/plan-03-selectors.md). It works on the token records of the
 * tokenizer and produces, for each complex selector of a list, an immutable
 * CompiledSelector: a flat array of nodes in pre-order, each with its kind,
 * its span in the text and the index just past its subtree. The Python tree
 * (kgcss.selectors) is built from it on demand, and the matcher will use it
 * directly.
 *
 * Comment tokens are ignored everywhere, as CSS Syntax removes comments.
 * Whitespace is significant: it is the descendant combinator. Functional
 * pseudo-classes nest at most MAX_DEPTH deep, so recursion is bounded.
 *
 * CompiledSelector objects are immutable, so they can be shared between
 * threads.
 */

#include "objects.h"
#include <stdarg.h>

/* Keep in sync with NodeKind in src/kgcss/selectors.py */
typedef enum {
    SN_COMPLEX = 1, SN_COMPOUND, SN_TYPE, SN_ID, SN_CLASS, SN_ATTRIBUTE, SN_PSEUDO_CLASS, SN_SELECTOR_PSEUDO,
    SN_NTH, SN_LANG, SN_FUNCTIONAL_PSEUDO, SN_PSEUDO_ELEMENT, SN_NESTING,
} sn_kind;

/* Combinators, in SN_COMPOUND.op: the combinator before the compound */
enum { C_NONE = 0, C_DESCENDANT, C_CHILD, C_NEXT_SIBLING, C_SUBSEQUENT_SIBLING };
/* Attribute operators, in SN_ATTRIBUTE.op */
enum { A_EXISTS = 0, A_EQUALS, A_INCLUDES, A_DASH, A_PREFIX, A_SUFFIX, A_SUBSTRING };
/* Flags */
enum {
    F_CASE_I = 1, F_CASE_S = 2,          /* SN_ATTRIBUTE */
    F_LEGACY = 1, F_FUNCTIONAL = 2,      /* SN_PSEUDO_ELEMENT */
    F_HAS_OF = 1,                        /* SN_NTH */
    F_RELATIVE = 1,                      /* SN_COMPLEX: starts with an explicit combinator */
};

typedef struct {
    uint8_t kind, op, flags;
    uint32_t start, end;        /* span in the text */
    uint32_t subtree_end;       /* index just past the last node of this subtree */
    int32_t a, b;               /* SN_NTH */
    PyObject *name, *value, *ns; /* owned references, or NULL */
} sn_node;

typedef enum { M_TOP, M_NESTED, M_RELATIVE, M_INNER } list_mode;

#define MAX_DEPTH 32
#define NO_TOKEN UINT32_MAX

typedef struct {
    kg_TokenStream *ts;
    const kg_token *tok;
    uint32_t count;
    sn_node *nodes;
    size_t n, cap;
    int depth;
    bool in_has;
    const char *context;    /* the functional pseudo-class we are in, for messages */
    /* The first error */
    bool failed, python_error;
    char msg[160];
    uint32_t got;           /* token the error is about, NO_TOKEN for the end of the range */
    bool plain;             /* the message says it all: no ", got ..." */
    uint32_t err_pos;
} sp;

/* ---------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------- */

static inline Py_UCS4
ch(const sp *p, uint32_t pos) { return PyUnicode_READ(p->ts->kind, p->ts->data, pos); }

static inline bool
is_delim(const sp *p, uint32_t i, uint32_t end, Py_UCS4 c) {
    return i < end && p->tok[i].type == KG_TOK_DELIM && ch(p, p->tok[i].start) == c;
}

static inline bool
is_type(const sp *p, uint32_t i, uint32_t end, uint8_t type) { return i < end && p->tok[i].type == type; }

static inline uint32_t
skip_comments(const sp *p, uint32_t i, uint32_t end) {
    while (i < end && p->tok[i].type == KG_TOK_COMMENT) i++;
    return i;
}

static inline uint32_t
skip_ws(const sp *p, uint32_t i, uint32_t end) {
    while (i < end && (p->tok[i].type == KG_TOK_WHITESPACE || p->tok[i].type == KG_TOK_COMMENT)) i++;
    return i;
}

/* The text offset of token i, or of the end of a range ending at end */
static inline uint32_t
pos_of(const sp *p, uint32_t i, uint32_t end) {
    if (i < end) return p->tok[i].start;
    return end < p->count ? p->tok[end].start : p->ts->len;
}

/* Index of the component value after i, within a range ending at end */
static inline uint32_t
next_item(const sp *p, uint32_t i, uint32_t end) {
    uint32_t n = kg_next_sibling(p->ts, i);
    return n < end ? n : end;
}

/* Record an error about token i of a range ending at end. Always returns false. */
static bool fail(sp *p, uint32_t i, uint32_t end, const char *fmt, ...) __attribute__((format(printf, 4, 5)));

static bool
fail(sp *p, uint32_t i, uint32_t end, const char *fmt, ...) {
    if (p->failed) return false;
    p->failed = true;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(p->msg, sizeof p->msg, fmt, ap);
    va_end(ap);
    p->err_pos = pos_of(p, i, end);
    /* The token at end is the one closing the range, such as ) or a comma */
    p->got = i < end ? i : (end < p->count ? end : NO_TOKEN);
    return false;
}

/* fail() for messages that need no ", got ..." */
static bool fail_plain(sp *p, uint32_t i, uint32_t end, const char *fmt, ...) __attribute__((format(printf, 4, 5)));

static bool
fail_plain(sp *p, uint32_t i, uint32_t end, const char *fmt, ...) {
    if (p->failed) return false;
    va_list ap;
    va_start(ap, fmt);
    char buf[sizeof p->msg];
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    fail(p, i, end, "%s", buf);
    p->plain = true;
    return false;
}

static bool
python_failed(sp *p) {
    p->failed = p->python_error = true;
    return false;
}

static PyObject *
value_of(sp *p, const kg_token *t, uint32_t a, uint32_t b, bool in_string) {
    PyObject *v = kg_range_value(p->ts, t, a, b, in_string);
    if (v == NULL) python_failed(p);
    return v;
}

/* A new reference to s with ASCII letters lowercased */
static PyObject *
ascii_lower(PyObject *s) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(s);
    const int kind = PyUnicode_KIND(s);
    const void *data = PyUnicode_DATA(s);
    Py_ssize_t i = 0;
    for (; i < n; i++) { Py_UCS4 c = PyUnicode_READ(kind, data, i); if (c >= 'A' && c <= 'Z') break; }
    if (i == n) return Py_NewRef(s);
    PyObject *ans = PyUnicode_New(n, PyUnicode_MAX_CHAR_VALUE(s));
    if (ans == NULL) return NULL;
    const int okind = PyUnicode_KIND(ans);
    void *odata = PyUnicode_DATA(ans);
    for (i = 0; i < n; i++) {
        Py_UCS4 c = PyUnicode_READ(kind, data, i);
        PyUnicode_WRITE(okind, odata, i, (c >= 'A' && c <= 'Z') ? c + 32 : c);
    }
    return ans;
}

/* Whether the ASCII string s equals the value of [a, b) of token t, ignoring ASCII case */
static bool
equals_ci(sp *p, const kg_token *t, uint32_t a, uint32_t b, const char *s) {
    const size_t n = strlen(s);
    if (!(t->flags & KG_TF_NEEDS_PROCESSING)) {
        if (b - a != n) return false;
        for (size_t i = 0; i < n; i++) {
            Py_UCS4 c = ch(p, a + (uint32_t)i);
            if (c >= 'A' && c <= 'Z') c += 32;
            if (c != (Py_UCS4)(unsigned char)s[i]) return false;
        }
        return true;
    }
    PyObject *v = kg_range_value(p->ts, t, a, b, false);
    if (v == NULL) { PyErr_Clear(); return false; }
    bool ans = (size_t)PyUnicode_GET_LENGTH(v) == n;
    for (Py_ssize_t i = 0; ans && i < (Py_ssize_t)n; i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != (Py_UCS4)(unsigned char)s[i]) ans = false;
    }
    Py_DECREF(v);
    return ans;
}

static uint32_t
add_node(sp *p, uint8_t kind, uint32_t start, uint32_t end) {
    if (p->n == p->cap) {
        size_t cap = p->cap ? p->cap * 2 : 32;
        sn_node *nn = PyMem_Realloc(p->nodes, cap * sizeof(sn_node));
        if (nn == NULL) { PyErr_NoMemory(); python_failed(p); return NO_TOKEN; }
        p->nodes = nn;
        p->cap = cap;
    }
    sn_node *node = p->nodes + p->n;
    memset(node, 0, sizeof *node);
    node->kind = kind;
    node->start = start;
    node->end = end;
    node->subtree_end = (uint32_t)p->n + 1;
    return (uint32_t)p->n++;
}

static void
free_nodes(sn_node *nodes, size_t from, size_t to) {
    for (size_t i = from; i < to; i++) {
        Py_CLEAR(nodes[i].name);
        Py_CLEAR(nodes[i].value);
        Py_CLEAR(nodes[i].ns);
    }
}

static inline void
close_node(sp *p, uint32_t idx, uint32_t end) {
    p->nodes[idx].subtree_end = (uint32_t)p->n;
    p->nodes[idx].end = end;
}

/* The source text of [a, b) without surrounding whitespace */
static PyObject *
trimmed_text(sp *p, uint32_t a, uint32_t b) {
    while (a < b && kg_is_whitespace(ch(p, a))) a++;
    while (b > a && kg_is_whitespace(ch(p, b - 1))) b--;
    PyObject *ans = PyUnicode_Substring(p->ts->source, a, b);
    if (ans == NULL) python_failed(p);
    return ans;
}

/* ---------------------------------------------------------------------------
 * An+B (CSS Syntax 3, section 6)
 * ------------------------------------------------------------------------- */

static int32_t
clamp(int64_t v) { return v > INT32_MAX ? INT32_MAX : (v < INT32_MIN ? INT32_MIN : (int32_t)v); }

/* The value of the number in [a, b), an integer with an optional sign, saturated */
static int64_t
integer_of(const sp *p, uint32_t a, uint32_t b, bool *has_sign) {
    int64_t v = 0;
    bool negative = false;
    *has_sign = false;
    if (a < b && (ch(p, a) == '+' || ch(p, a) == '-')) { *has_sign = true; negative = ch(p, a) == '-'; a++; }
    for (; a < b; a++) { if (v < ((int64_t)1 << 40)) v = v * 10 + (int64_t)(ch(p, a) - '0'); }
    return negative ? -v : v;
}

/* If the unescaped value of [a, b) of t is prefix followed by one or more ASCII digits, their value */
static bool
prefix_digits(sp *p, const kg_token *t, uint32_t a, uint32_t b, const char *prefix, int64_t *out) {
    PyObject *v = kg_range_value(p->ts, t, a, b, false);
    if (v == NULL) { PyErr_Clear(); return false; }
    const Py_ssize_t n = PyUnicode_GET_LENGTH(v), plen = (Py_ssize_t)strlen(prefix);
    bool ok = n > plen;
    for (Py_ssize_t i = 0; ok && i < plen; i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        if (c >= 'A' && c <= 'Z') c += 32;
        ok = c == (Py_UCS4)(unsigned char)prefix[i];
    }
    int64_t val = 0;
    for (Py_ssize_t i = plen; ok && i < n; i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        ok = c >= '0' && c <= '9';
        if (ok && val < ((int64_t)1 << 40)) val = val * 10 + (int64_t)(c - '0');
    }
    Py_DECREF(v);
    if (ok) *out = val;
    return ok;
}

static inline bool
is_integer_token(const kg_token *t) { return t->type == KG_TOK_NUMBER && (t->flags & KG_TF_IS_INTEGER); }

/* Parse An+B from the tokens [start, end). On failure, *bad is the token where it went wrong. */
static bool
parse_anb(sp *p, uint32_t start, uint32_t end, int32_t *A, int32_t *B, uint32_t *bad) {
    /* The significant tokens, and whether whitespace came before each */
    uint32_t idx[6];
    bool ws_before[6];
    int n = 0;
    bool ws = false;
    for (uint32_t i = start; i < end; i++) {
        uint8_t type = p->tok[i].type;
        if (type == KG_TOK_COMMENT) continue;
        if (type == KG_TOK_WHITESPACE) { ws = true; continue; }
        if (n == 6) { *bad = i; return false; }
        idx[n] = i; ws_before[n] = ws; n++;
        ws = false;
    }
    *bad = n ? idx[0] : end;
    if (n == 0) return false;
    int k = 0;  /* tokens used so far */
    int64_t a = 0, b = 0, d;
    const kg_token *t = p->tok + idx[0];
    enum { REST_B, REST_SIGNLESS, REST_NONE } rest;
    int sign = 1;
    if (t->type == KG_TOK_IDENT && equals_ci(p, t, t->vstart, t->vend, "odd")) { a = 2; b = 1; k = 1; rest = REST_NONE; }
    else if (t->type == KG_TOK_IDENT && equals_ci(p, t, t->vstart, t->vend, "even")) { a = 2; b = 0; k = 1; rest = REST_NONE; }
    else if (is_integer_token(t)) { bool s; b = integer_of(p, t->vstart, t->vend, &s); k = 1; rest = REST_NONE; }
    else if (t->type == KG_TOK_DIMENSION && (t->flags & KG_TF_IS_INTEGER)) {
        bool s;
        a = integer_of(p, t->vstart, t->vend, &s);
        k = 1;
        if (equals_ci(p, t, t->vend, t->end, "n")) rest = REST_B;
        else if (equals_ci(p, t, t->vend, t->end, "n-")) rest = REST_SIGNLESS;
        else if (prefix_digits(p, t, t->vend, t->end, "n-", &d)) { b = -d; rest = REST_NONE; }
        else return false;
    } else {
        /* +n..., n..., -n... */
        int first = 0;
        if (t->type == KG_TOK_DELIM && ch(p, t->start) == '+') {
            if (n < 2 || ws_before[1]) return false;
            first = 1;
        }
        const kg_token *id = p->tok + idx[first];
        if (id->type != KG_TOK_IDENT) { *bad = idx[first]; return false; }
        k = first + 1;
        if (!first && (ch(p, id->vstart) == '-')) {
            /* -n, -n-, -n-<digits> */
            sign = -1;
            if (equals_ci(p, id, id->vstart, id->vend, "-n")) rest = REST_B;
            else if (equals_ci(p, id, id->vstart, id->vend, "-n-")) rest = REST_SIGNLESS;
            else if (prefix_digits(p, id, id->vstart, id->vend, "-n-", &d)) { b = -d; rest = REST_NONE; }
            else return false;
        } else {
            if (equals_ci(p, id, id->vstart, id->vend, "n")) rest = REST_B;
            else if (equals_ci(p, id, id->vstart, id->vend, "n-")) rest = REST_SIGNLESS;
            else if (prefix_digits(p, id, id->vstart, id->vend, "n-", &d)) { b = -d; rest = REST_NONE; }
            else return false;
        }
        a = sign;
    }
    if (rest == REST_SIGNLESS) {
        if (k >= n) { *bad = end; return false; }
        const kg_token *t2 = p->tok + idx[k];
        bool s;
        int64_t v = is_integer_token(t2) ? integer_of(p, t2->vstart, t2->vend, &s) : 0;
        if (!is_integer_token(t2) || s) { *bad = idx[k]; return false; }
        b = -v;
        k++;
    } else if (rest == REST_B && k < n) {
        const kg_token *t2 = p->tok + idx[k];
        bool s;
        if (is_integer_token(t2)) {
            int64_t v = integer_of(p, t2->vstart, t2->vend, &s);
            if (!s) { *bad = idx[k]; return false; }
            b = v;
            k++;
        } else if (t2->type == KG_TOK_DELIM && (ch(p, t2->start) == '+' || ch(p, t2->start) == '-')) {
            const int bsign = ch(p, t2->start) == '-' ? -1 : 1;
            if (k + 1 >= n) { *bad = end; return false; }
            const kg_token *t3 = p->tok + idx[k + 1];
            int64_t v = is_integer_token(t3) ? integer_of(p, t3->vstart, t3->vend, &s) : 0;
            if (!is_integer_token(t3) || s) { *bad = idx[k + 1]; return false; }
            b = bsign * v;
            k += 2;
        } else { *bad = idx[k]; return false; }
    }
    if (k < n) { *bad = idx[k]; return false; }
    *A = clamp(a);
    *B = clamp(b);
    return true;
}

/* ---------------------------------------------------------------------------
 * The parser
 * ------------------------------------------------------------------------- */

static bool parse_list(sp *p, uint32_t start, uint32_t end, list_mode mode, bool forgiving);

static const char *const legacy_pseudo_elements[] = {"before", "after", "first-line", "first-letter", NULL};

/* [ns|]name or [ns|]* at *i. ns is NULL if there is no prefix, "*" for any and "" for none. */
static bool
parse_type(sp *p, uint32_t *i, uint32_t end) {
    const uint32_t start = *i;
    PyObject *ns = NULL, *name = NULL;
    uint32_t j = start;
    const kg_token *t = p->tok + j;
    if (is_delim(p, j, end, '|')) {
        ns = PyUnicode_FromStringAndSize("", 0);
        if (ns == NULL) return python_failed(p);
        j = skip_comments(p, j + 1, end);
    } else {
        uint32_t k = skip_comments(p, j + 1, end);
        if (is_delim(p, k, end, '|')) {
            /* A namespace prefix */
            if (t->type == KG_TOK_IDENT) { if ((ns = value_of(p, t, t->vstart, t->vend, false)) == NULL) return false; }
            else if ((ns = PyUnicode_FromString("*")) == NULL) return python_failed(p);
            j = skip_comments(p, k + 1, end);
        }
    }
    if (ns != NULL || j == start) {
        /* The name, after an optional prefix */
        if (is_type(p, j, end, KG_TOK_IDENT)) {
            const kg_token *nt = p->tok + j;
            if ((name = value_of(p, nt, nt->vstart, nt->vend, false)) == NULL) { Py_XDECREF(ns); return false; }
        } else if (!is_delim(p, j, end, '*')) {
            Py_XDECREF(ns);
            return fail(p, j, end, "Expected an ident or '*' after the namespace prefix");
        }
    }
    uint32_t idx = add_node(p, SN_TYPE, p->tok[start].start, p->tok[j].end);
    if (idx == NO_TOKEN) { Py_XDECREF(ns); Py_XDECREF(name); return false; }
    p->nodes[idx].ns = ns;
    p->nodes[idx].name = name;
    *i = j + 1;
    return true;
}

static bool
parse_attribute(sp *p, uint32_t block) {
    const kg_token *bt = p->tok + block;
    if (bt->flags & KG_TF_UNCLOSED) return fail(p, p->count, p->count, "Expected ']'");
    const uint32_t end = bt->match;
    PyObject *ns = NULL, *name = NULL, *value = NULL;
    uint8_t op = A_EXISTS, flags = 0;
    uint32_t j = skip_ws(p, block + 1, end);
    if (is_delim(p, j, end, '*')) {
        uint32_t k = skip_comments(p, j + 1, end);
        if (!is_delim(p, k, end, '|')) return fail(p, k, end, "Expected '|'");
        if ((ns = PyUnicode_FromString("*")) == NULL) return python_failed(p);
        j = skip_comments(p, k + 1, end);
        if (!is_type(p, j, end, KG_TOK_IDENT)) { Py_DECREF(ns); return fail(p, j, end, "Expected an attribute name"); }
    } else if (is_delim(p, j, end, '|')) {
        if ((ns = PyUnicode_FromStringAndSize("", 0)) == NULL) return python_failed(p);
        j = skip_comments(p, j + 1, end);
        if (!is_type(p, j, end, KG_TOK_IDENT)) { Py_DECREF(ns); return fail(p, j, end, "Expected an attribute name"); }
    } else if (is_type(p, j, end, KG_TOK_IDENT)) {
        uint32_t k = skip_comments(p, j + 1, end);
        if (is_delim(p, k, end, '|') && !is_delim(p, skip_comments(p, k + 1, end), end, '=')) {
            const kg_token *t = p->tok + j;
            if ((ns = value_of(p, t, t->vstart, t->vend, false)) == NULL) return false;
            j = skip_comments(p, k + 1, end);
            if (!is_type(p, j, end, KG_TOK_IDENT)) { Py_DECREF(ns); return fail(p, j, end, "Expected an attribute name"); }
        }
    } else return fail(p, j, end, "Expected an attribute name or '*'");
    const kg_token *nt = p->tok + j;
    if ((name = value_of(p, nt, nt->vstart, nt->vend, false)) == NULL) { Py_XDECREF(ns); return false; }
    j = skip_ws(p, j + 1, end);
    bool ok = true;
    if (j < end) {
        static const char ops[] = "~|^$*";
        const char *found;
        if (is_delim(p, j, end, '=')) { op = A_EQUALS; j++; }
        else if (p->tok[j].type == KG_TOK_DELIM && ch(p, p->tok[j].start) < 128 &&
                 (found = strchr(ops, (int)ch(p, p->tok[j].start))) != NULL && *found &&
                 is_delim(p, skip_comments(p, j + 1, end), end, '=')) {
            op = (uint8_t)(A_INCLUDES + (found - ops));
            j = skip_comments(p, j + 1, end) + 1;
        } else ok = fail(p, j, end, "Expected an attribute operator");
        if (ok) {
            j = skip_ws(p, j, end);
            if (is_type(p, j, end, KG_TOK_IDENT) || is_type(p, j, end, KG_TOK_STRING)) {
                const kg_token *vt = p->tok + j;
                ok = (value = value_of(p, vt, vt->vstart, vt->vend, vt->type == KG_TOK_STRING)) != NULL;
                j = skip_ws(p, j + 1, end);
            } else ok = fail(p, j, end, "Expected a string or ident as the attribute value");
        }
        if (ok && is_type(p, j, end, KG_TOK_IDENT)) {
            const kg_token *ft = p->tok + j;
            if (equals_ci(p, ft, ft->vstart, ft->vend, "i")) { flags = F_CASE_I; j = skip_ws(p, j + 1, end); }
            else if (equals_ci(p, ft, ft->vstart, ft->vend, "s")) { flags = F_CASE_S; j = skip_ws(p, j + 1, end); }
        }
        if (ok && j < end) ok = fail(p, j, end, "Expected ']'");
    }
    uint32_t idx = ok ? add_node(p, SN_ATTRIBUTE, bt->start, p->tok[end].end) : NO_TOKEN;
    if (idx == NO_TOKEN) { Py_XDECREF(ns); Py_XDECREF(name); Py_XDECREF(value); return false; }
    sn_node *node = p->nodes + idx;
    node->ns = ns; node->name = name; node->value = value; node->op = op; node->flags = flags;
    return true;
}

/* :nth-child() and friends, starting with the colon at colon, the arguments [start, end) of the function at fn */
static bool
parse_nth(sp *p, uint32_t colon, uint32_t fn, uint32_t start, uint32_t end, PyObject *name, bool allow_of) {
    uint32_t of = end;
    if (allow_of) {
        for (uint32_t i = start; i < end; i = next_item(p, i, end)) {
            const kg_token *t = p->tok + i;
            if (t->type == KG_TOK_IDENT && equals_ci(p, t, t->vstart, t->vend, "of")) { of = i; break; }
        }
    }
    int32_t a, b;
    uint32_t bad;
    if (!parse_anb(p, start, of, &a, &b, &bad)) return fail(p, bad, of, "Expected an An+B argument");
    uint32_t idx = add_node(p, SN_NTH, p->tok[colon].start, p->tok[p->tok[fn].match].end);
    if (idx == NO_TOKEN) return false;
    p->nodes[idx].a = a;
    p->nodes[idx].b = b;
    p->nodes[idx].name = Py_NewRef(name);
    if (of < end) {
        p->nodes[idx].flags = F_HAS_OF;
        if (!parse_list(p, of + 1, end, M_INNER, false)) return false;
    }
    p->nodes[idx].subtree_end = (uint32_t)p->n;
    return true;
}

static bool
parse_lang(sp *p, uint32_t colon, uint32_t fn, uint32_t start, uint32_t end) {
    PyObject *ranges = PyList_New(0);
    if (ranges == NULL) return python_failed(p);
    uint32_t i = skip_ws(p, start, end);
    bool ok = true;
    while (ok) {
        if (!(is_type(p, i, end, KG_TOK_IDENT) || is_type(p, i, end, KG_TOK_STRING))) {
            ok = fail(p, i, end, "Expected a language range");
            break;
        }
        const kg_token *t = p->tok + i;
        PyObject *v = value_of(p, t, t->vstart, t->vend, t->type == KG_TOK_STRING);
        if (v == NULL || PyList_Append(ranges, v) != 0) { Py_XDECREF(v); ok = v == NULL ? false : python_failed(p); break; }
        Py_DECREF(v);
        i = skip_ws(p, i + 1, end);
        if (i == end) break;
        if (!is_type(p, i, end, KG_TOK_COMMA)) { ok = fail(p, i, end, "Expected ',' or ')'"); break; }
        i = skip_ws(p, i + 1, end);
    }
    PyObject *tuple = ok ? PyList_AsTuple(ranges) : NULL;
    Py_DECREF(ranges);
    if (!ok) return false;
    if (tuple == NULL) return python_failed(p);
    uint32_t idx = add_node(p, SN_LANG, p->tok[colon].start, p->tok[p->tok[fn].match].end);
    if (idx == NO_TOKEN) { Py_DECREF(tuple); return false; }
    p->nodes[idx].value = tuple;
    return true;
}

/* A pseudo-class or pseudo-element starting with the colon at *i */
static bool
parse_pseudo(sp *p, uint32_t *i, uint32_t end, list_mode mode, uint32_t *pseudo_element) {
    const uint32_t start = *i;
    uint32_t j = skip_comments(p, start + 1, end);
    bool element = false;
    if (is_type(p, j, end, KG_TOK_COLON)) { element = true; j = skip_comments(p, j + 1, end); }
    if (!(is_type(p, j, end, KG_TOK_IDENT) || is_type(p, j, end, KG_TOK_FUNCTION))) {
        return fail(p, j, end, element ? "Expected a pseudo-element name" : "Expected a pseudo-class name");
    }
    const kg_token *t = p->tok + j;
    const bool functional = t->type == KG_TOK_FUNCTION;
    if (functional && (t->flags & KG_TF_UNCLOSED)) return fail(p, p->count, p->count, "Expected ')'");
    PyObject *raw = value_of(p, t, t->vstart, t->vend, false);
    if (raw == NULL) return false;
    PyObject *name = ascii_lower(raw);
    Py_DECREF(raw);
    if (name == NULL) return python_failed(p);
    const uint32_t last = functional ? t->match : j;
    bool legacy = false;
    if (!element && !functional) {
        for (const char *const *l = legacy_pseudo_elements; *l; l++) {
            if (PyUnicode_CompareWithASCIIString(name, *l) == 0) { legacy = true; break; }
        }
    }
    bool ok = true;
    if (element || legacy) {
        if (mode == M_INNER || mode == M_RELATIVE) {
            const char *utf8 = PyUnicode_AsUTF8(name);
            if (utf8 == NULL) { Py_DECREF(name); return python_failed(p); }
            ok = fail_plain(p, start, end, "The pseudo-element ::%.60s is not allowed inside :%.40s()", utf8, p->context);
        }
        if (ok && *pseudo_element != NO_TOKEN) close_node(p, *pseudo_element, p->tok[start].start);
        uint32_t idx = ok ? add_node(p, SN_PSEUDO_ELEMENT, p->tok[start].start, p->tok[last].end) : NO_TOKEN;
        if (idx == NO_TOKEN) { Py_DECREF(name); return false; }
        p->nodes[idx].name = name;
        p->nodes[idx].flags = (uint8_t)((legacy ? F_LEGACY : 0) | (functional ? F_FUNCTIONAL : 0));
        if (functional && (p->nodes[idx].value = trimmed_text(p, t->end, p->tok[t->match].start)) == NULL) return false;
        *pseudo_element = idx;
        *i = last + 1;
        return true;
    }
    if (!functional) {
        uint32_t idx = add_node(p, SN_PSEUDO_CLASS, p->tok[start].start, t->end);
        if (idx == NO_TOKEN) { Py_DECREF(name); return false; }
        p->nodes[idx].name = name;
        *i = j + 1;
        return true;
    }
    /* A functional pseudo-class */
    const uint32_t args = j + 1, args_end = t->match;
    if (p->depth >= MAX_DEPTH) { Py_DECREF(name); return fail(p, j, end, "The selector is nested too deeply"); }
    const char *saved_context = p->context;
    const bool saved_in_has = p->in_has;
    p->depth++;
    const char *ctx = PyUnicode_AsUTF8(name);
    if (ctx == NULL) { Py_DECREF(name); p->depth--; return python_failed(p); }
    p->context = ctx;
    if (strcmp(ctx, "is") == 0 || strcmp(ctx, "where") == 0 || strcmp(ctx, "matches") == 0 ||
        strcmp(ctx, "not") == 0 || strcmp(ctx, "has") == 0) {
        const bool has = ctx[0] == 'h';
        uint32_t idx = add_node(p, SN_SELECTOR_PSEUDO, p->tok[start].start, p->tok[args_end].end);
        if (idx == NO_TOKEN) ok = false;
        else {
            p->nodes[idx].name = Py_NewRef(name);
            if (has && p->in_has) ok = fail_plain(p, start, end, ":has() is not allowed inside :has()");
            else {
                if (has) p->in_has = true;
                const bool forgiving = ctx[0] != 'n' && !has;
                ok = parse_list(p, args, args_end, has ? M_RELATIVE : M_INNER, forgiving);
            }
            if (ok) p->nodes[idx].subtree_end = (uint32_t)p->n;
        }
    } else if (strcmp(ctx, "nth-child") == 0 || strcmp(ctx, "nth-last-child") == 0) {
        ok = parse_nth(p, start, j, args, args_end, name, true);
    } else if (strcmp(ctx, "nth-of-type") == 0 || strcmp(ctx, "nth-last-of-type") == 0) {
        ok = parse_nth(p, start, j, args, args_end, name, false);
    } else if (strcmp(ctx, "lang") == 0) {
        ok = parse_lang(p, start, j, args, args_end);
    } else {
        /* Kept as text, but it must not contain anything left open or invalid */
        for (uint32_t k = args; ok && k < args_end; k++) {
            uint8_t type = p->tok[k].type;
            if (type == KG_TOK_BAD_STRING || type == KG_TOK_BAD_URL || (p->tok[k].flags & KG_TF_UNCLOSED)) {
                ok = fail(p, k, args_end, "Invalid token in the arguments of :%s()", ctx);
            }
        }
        uint32_t idx = ok ? add_node(p, SN_FUNCTIONAL_PSEUDO, p->tok[start].start, p->tok[args_end].end) : NO_TOKEN;
        if (ok && idx == NO_TOKEN) ok = false;
        if (ok) {
            p->nodes[idx].name = Py_NewRef(name);
            ok = (p->nodes[idx].value = trimmed_text(p, t->end, p->tok[args_end].start)) != NULL;
        }
    }
    p->depth--;
    p->context = saved_context;
    p->in_has = saved_in_has;
    Py_DECREF(name);
    *i = args_end + 1;
    return ok;
}

/* A compound selector at *i. *pseudo_element is set to the index of its first pseudo-element, if any. */
static bool
parse_compound(sp *p, uint32_t *i, uint32_t end, uint8_t combinator, list_mode mode, bool *has_pseudo_element) {
    const uint32_t cidx = add_node(p, SN_COMPOUND, p->tok[*i].start, p->tok[*i].start);
    if (cidx == NO_TOKEN) return false;
    p->nodes[cidx].op = combinator;
    uint32_t j = *i, last_end = p->tok[*i].start;
    uint32_t pseudo_element = NO_TOKEN;
    if (is_type(p, j, end, KG_TOK_IDENT) || is_delim(p, j, end, '*') || is_delim(p, j, end, '|')) {
        if (!parse_type(p, &j, end)) return false;
        last_end = p->tok[j - 1].end;
    }
    while (true) {
        uint32_t k = skip_comments(p, j, end);
        if (k >= end) break;
        const kg_token *t = p->tok + k;
        if (pseudo_element != NO_TOKEN && t->type != KG_TOK_COLON) break;
        if (t->type == KG_TOK_HASH) {
            if (!(t->flags & KG_TF_HASH_ID)) return fail(p, k, end, "Invalid ID selector");
            uint32_t idx = add_node(p, SN_ID, t->start, t->end);
            if (idx == NO_TOKEN || (p->nodes[idx].name = value_of(p, t, t->vstart, t->vend, false)) == NULL) return false;
            j = k + 1;
        } else if (is_delim(p, k, end, '.')) {
            uint32_t m = skip_comments(p, k + 1, end);
            if (!is_type(p, m, end, KG_TOK_IDENT)) return fail(p, m, end, "Expected a class name");
            const kg_token *nt = p->tok + m;
            uint32_t idx = add_node(p, SN_CLASS, t->start, nt->end);
            if (idx == NO_TOKEN || (p->nodes[idx].name = value_of(p, nt, nt->vstart, nt->vend, false)) == NULL) return false;
            j = m + 1;
        } else if (t->type == KG_TOK_LEFT_SQUARE) {
            if (!parse_attribute(p, k)) return false;
            j = (t->flags & KG_TF_UNCLOSED) ? end : t->match + 1;
        } else if (t->type == KG_TOK_COLON) {
            j = k;
            if (!parse_pseudo(p, &j, end, mode, &pseudo_element)) return false;
        } else if (is_delim(p, k, end, '&')) {
            if (add_node(p, SN_NESTING, t->start, t->end) == NO_TOKEN) return false;
            j = k + 1;
        } else break;
        last_end = p->tok[j - 1].end;
    }
    if (p->n == (size_t)cidx + 1) return fail(p, skip_comments(p, *i, end), end, "Expected a selector");
    if (pseudo_element != NO_TOKEN) close_node(p, pseudo_element, last_end);
    close_node(p, cidx, last_end);
    *has_pseudo_element = pseudo_element != NO_TOKEN;
    *i = j;
    return true;
}

static inline uint8_t
combinator_at(const sp *p, uint32_t i, uint32_t end) {
    if (is_delim(p, i, end, '>')) return C_CHILD;
    if (is_delim(p, i, end, '+')) return C_NEXT_SIBLING;
    if (is_delim(p, i, end, '~')) return C_SUBSEQUENT_SIBLING;
    return C_NONE;
}

/* A complex selector in [start, end), which has a significant token */
static bool
parse_complex(sp *p, uint32_t start, uint32_t end, list_mode mode, uint32_t text_start, uint32_t text_end) {
    uint32_t i = skip_ws(p, start, end);
    const uint32_t idx = add_node(p, SN_COMPLEX, text_start, text_end);
    if (idx == NO_TOKEN) return false;
    uint8_t comb = C_NONE;
    if (mode == M_RELATIVE || mode == M_NESTED) {
        if ((comb = combinator_at(p, i, end)) != C_NONE) {
            p->nodes[idx].flags = F_RELATIVE;
            i = skip_ws(p, i + 1, end);
            if (i == end) return fail(p, i, end, "Expected a selector after the combinator");
        } else if (mode == M_RELATIVE) comb = C_DESCENDANT;
    }
    while (true) {
        bool has_pseudo_element;
        if (!parse_compound(p, &i, end, comb, mode, &has_pseudo_element)) return false;
        uint32_t j = i;
        bool ws = false;
        while (j < end && (p->tok[j].type == KG_TOK_WHITESPACE || p->tok[j].type == KG_TOK_COMMENT)) {
            if (p->tok[j].type == KG_TOK_WHITESPACE) ws = true;
            j++;
        }
        if (j == end) break;
        if (has_pseudo_element) return fail(p, j, end, "Only pseudo-classes can follow a pseudo-element");
        if ((comb = combinator_at(p, j, end)) != C_NONE) {
            j = skip_ws(p, j + 1, end);
            if (j == end) return fail(p, j, end, "Expected a selector after the combinator");
        } else if (ws) comb = C_DESCENDANT;
        else return fail(p, j, end, "Expected a selector");
        i = j;
    }
    p->nodes[idx].subtree_end = (uint32_t)p->n;
    return true;
}

/* A comma separated list of complex selectors in [start, end). With forgiving, the ones that do
 * not parse are left out, as in :is() and :where(). */
static bool
parse_list(sp *p, uint32_t start, uint32_t end, list_mode mode, bool forgiving) {
    uint32_t part = start;
    while (true) {
        uint32_t part_end = part;
        while (part_end < end && p->tok[part_end].type != KG_TOK_COMMA) part_end = next_item(p, part_end, end);
        /* The span: from the first token that is not whitespace to the last one */
        uint32_t first = part;
        while (first < part_end && p->tok[first].type == KG_TOK_WHITESPACE) first++;
        uint32_t last = part_end;
        while (last > first && p->tok[last - 1].type == KG_TOK_WHITESPACE) last--;
        const bool empty = skip_ws(p, part, part_end) == part_end;
        if (empty) {
            /* :is() is fine, and in a forgiving list so are empty items */
            if (!forgiving) return fail(p, part_end, part_end, "Expected a selector");
        } else {
            const size_t saved = p->n;
            const uint32_t text_end = last > first ? kg_item_end(p->ts, last - 1) : p->tok[first].start;
            if (!parse_complex(p, part, part_end, mode, p->tok[first].start, text_end)) {
                if (!forgiving || p->python_error) return false;
                free_nodes(p->nodes, saved, p->n);
                p->n = saved;
                p->failed = false;
            }
        }
        if (part_end >= end) break;
        part = part_end + 1;
    }
    return true;
}

/* ---------------------------------------------------------------------------
 * Specificity
 * ------------------------------------------------------------------------- */

typedef struct { uint32_t a, b, c; } spec_t;

static inline uint32_t sat_add(uint32_t x, uint32_t y) { return x > UINT32_MAX - y ? UINT32_MAX : x + y; }
static inline spec_t spec_add(spec_t x, spec_t y) { return (spec_t){sat_add(x.a, y.a), sat_add(x.b, y.b), sat_add(x.c, y.c)}; }
static inline bool spec_less(spec_t x, spec_t y) {
    return x.a != y.a ? x.a < y.a : (x.b != y.b ? x.b < y.b : x.c < y.c);
}

static spec_t specificity_of(const sn_node *nodes, uint32_t i, spec_t nesting);

/* The largest specificity of the complex selectors that are children of node i */
static spec_t
max_of_children(const sn_node *nodes, uint32_t i, spec_t nesting) {
    spec_t best = {0, 0, 0};
    for (uint32_t k = i + 1; k < nodes[i].subtree_end; k = nodes[k].subtree_end) {
        spec_t s = specificity_of(nodes, k, nesting);
        if (spec_less(best, s)) best = s;
    }
    return best;
}

static spec_t
specificity_of(const sn_node *nodes, uint32_t i, spec_t nesting) {
    const sn_node *node = nodes + i;
    spec_t s = {0, 0, 0};
    switch ((sn_kind)node->kind) {
        case SN_COMPLEX: case SN_COMPOUND:
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                s = spec_add(s, specificity_of(nodes, k, nesting));
            }
            return s;
        case SN_TYPE: return (spec_t){0, 0, node->name != NULL ? 1 : 0};
        case SN_ID: return (spec_t){1, 0, 0};
        case SN_CLASS: case SN_ATTRIBUTE: case SN_PSEUDO_CLASS: case SN_LANG: case SN_FUNCTIONAL_PSEUDO:
            return (spec_t){0, 1, 0};
        case SN_SELECTOR_PSEUDO:
            if (PyUnicode_CompareWithASCIIString(node->name, "where") == 0) return s;
            return max_of_children(nodes, i, nesting);
        case SN_NTH:
            s = (spec_t){0, 1, 0};
            return (node->flags & F_HAS_OF) ? spec_add(s, max_of_children(nodes, i, nesting)) : s;
        case SN_PSEUDO_ELEMENT:
            /* It counts as a type, plus the pseudo-classes that follow it */
            s = (spec_t){0, 0, 1};
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                s = spec_add(s, specificity_of(nodes, k, nesting));
            }
            return s;
        case SN_NESTING: return nesting;
    }
    return s;
}

/* ---------------------------------------------------------------------------
 * CompiledSelector
 * ------------------------------------------------------------------------- */

typedef struct {
    PyObject_HEAD
    sn_node *nodes;
    uint32_t n;
    uint32_t start, end;    /* span in the parsed text; node spans are relative to start */
    spec_t specificity;     /* with & counting as :scope */
    bool has_nesting;
    int32_t pseudo_element; /* index of the first pseudo-element of the last compound, or -1 */
} kg_CompiledSelector;

static void
CompiledSelector_dealloc(PyObject *o) {
    kg_CompiledSelector *self = (kg_CompiledSelector *)o;
    PyTypeObject *tp = Py_TYPE(o);
    if (self->nodes) { free_nodes(self->nodes, 0, self->n); PyMem_Free(self->nodes); }
    tp->tp_free(o);
    Py_DECREF(tp);
}

static PyObject *
CompiledSelector_nodes(PyObject *o, PyObject *unused) {
    KG_UNUSED(unused);
    kg_CompiledSelector *self = (kg_CompiledSelector *)o;
    PyObject *ans = PyTuple_New(self->n);
    if (ans == NULL) return NULL;
    for (uint32_t i = 0; i < self->n; i++) {
        const sn_node *n = self->nodes + i;
        PyObject *t = Py_BuildValue("(BBBIIIiiOOO)", n->kind, n->op, n->flags, n->start - self->start,
                                    n->end - self->start, n->subtree_end, n->a, n->b, n->name ? n->name : Py_None,
                                    n->value ? n->value : Py_None, n->ns ? n->ns : Py_None);
        if (t == NULL) { Py_DECREF(ans); return NULL; }
        PyTuple_SET_ITEM(ans, i, t);
    }
    return ans;
}

static PyObject *
spec_tuple(spec_t s) { return Py_BuildValue("(III)", s.a, s.b, s.c); }

static PyObject *
CompiledSelector_specificity(PyObject *o, PyObject *const *args, Py_ssize_t nargs) {
    kg_CompiledSelector *self = (kg_CompiledSelector *)o;
    if (nargs == 0 || !self->has_nesting) return spec_tuple(self->specificity);
    spec_t nesting;
    if (nargs != 1 || !PyArg_ParseTuple(args[0], "III", &nesting.a, &nesting.b, &nesting.c)) {
        if (!PyErr_Occurred()) PyErr_SetString(PyExc_TypeError, "specificity() takes a (a, b, c) tuple");
        return NULL;
    }
    return spec_tuple(specificity_of(self->nodes, 0, nesting));
}

static PyObject *
CompiledSelector_get(PyObject *o, void *closure) {
    kg_CompiledSelector *self = (kg_CompiledSelector *)o;
    switch ((intptr_t)closure) {
        case 0: return PyLong_FromUnsignedLong(self->start);
        case 1: return PyLong_FromUnsignedLong(self->end);
        case 2: return PyBool_FromLong(self->has_nesting);
        case 3: return PyBool_FromLong(self->pseudo_element >= 0);
        case 4: return PyBool_FromLong(self->nodes[0].flags & F_RELATIVE);
    }
    Py_RETURN_NONE;
}

static PyMethodDef CompiledSelector_methods[] = {
    {"nodes", CompiledSelector_nodes, METH_NOARGS,
     "nodes() -> tuple of (kind, op, flags, start, end, subtree_end, a, b, name, value, ns), in pre-order"},
    {"specificity", (PyCFunction)(void (*)(void))CompiledSelector_specificity, METH_FASTCALL,
     "specificity(nesting=(0, 1, 0)) -> (a, b, c), with & counting as nesting"},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef CompiledSelector_getset[] = {
    {"start", CompiledSelector_get, NULL, "Start of this selector in the parsed text", (void *)0},
    {"end", CompiledSelector_get, NULL, "End of this selector in the parsed text", (void *)1},
    {"has_nesting", CompiledSelector_get, NULL, "True if the selector contains &", (void *)2},
    {"has_pseudo_element", CompiledSelector_get, NULL, "True if the selector has a pseudo-element", (void *)3},
    {"relative", CompiledSelector_get, NULL, "True if the selector starts with a combinator", (void *)4},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot CompiledSelector_slots[] = {
    {Py_tp_dealloc, CompiledSelector_dealloc},
    {Py_tp_methods, CompiledSelector_methods},
    {Py_tp_getset, CompiledSelector_getset},
    {Py_tp_doc, (void *)"A parsed complex selector. Not a public API, see kgcss.selectors."},
    {0, NULL}
};

static PyType_Spec CompiledSelector_spec = {
    .name = "kgcss.CompiledSelector",
    .basicsize = sizeof(kg_CompiledSelector),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = CompiledSelector_slots,
};

/* A CompiledSelector for the complex selector at nodes[i], taking over the references of its nodes */
static PyObject *
make_compiled(kg_module_state *state, sn_node *nodes, uint32_t i) {
    kg_CompiledSelector *self = PyObject_New(kg_CompiledSelector, state->CompiledSelector_type);
    if (self == NULL) return NULL;
    const uint32_t n = nodes[i].subtree_end - i;
    self->nodes = PyMem_Malloc(n * sizeof(sn_node));
    if (self->nodes == NULL) { self->n = 0; Py_DECREF(self); return PyErr_NoMemory(); }
    memcpy(self->nodes, nodes + i, n * sizeof(sn_node));
    for (uint32_t k = i; k < i + n; k++) nodes[k].name = nodes[k].value = nodes[k].ns = NULL;
    self->n = n;
    self->has_nesting = false;
    self->pseudo_element = -1;
    for (uint32_t k = 0; k < n; k++) {
        self->nodes[k].subtree_end -= i;
        if (self->nodes[k].kind == SN_NESTING) self->has_nesting = true;
    }
    self->start = self->nodes[0].start;
    self->end = self->nodes[0].end;
    /* The pseudo-element, which can only be in the last compound */
    uint32_t last = 1;
    for (uint32_t k = 1; k < n; k = self->nodes[k].subtree_end) last = k;
    for (uint32_t k = last + 1; k < self->nodes[last].subtree_end; k = self->nodes[k].subtree_end) {
        if (self->nodes[k].kind == SN_PSEUDO_ELEMENT) { self->pseudo_element = (int32_t)k; break; }
    }
    self->specificity = specificity_of(self->nodes, 0, (spec_t){0, 1, 0});
    return (PyObject *)self;
}

/* parse_selectors(text, nested) -> tuple[CompiledSelector, ...]. Raises SelectorParseError(message, pos). */
PyObject *
kg_parse_selectors(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 2 || !PyUnicode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "parse_selectors() takes a str and a bool");
        return NULL;
    }
    const int nested = PyObject_IsTrue(args[1]);
    if (nested < 0) return NULL;
    kg_TokenStream *ts = kg_tokenize_to_stream(module, args[0], true);
    if (ts == NULL) return NULL;
    sp p = {.ts = ts, .tok = ts->arr.tokens, .count = ts->arr.count, .context = ""};
    PyObject *ans = NULL;
    /* Text with tokenizer errors, such as an unclosed string or comment, is not a selector */
    if (ts->arr.error_count) {
        p.failed = p.plain = true;
        snprintf(p.msg, sizeof p.msg, "Invalid CSS in the selector");
        p.err_pos = ts->arr.errors[0].offset;
    }
    /* An unbalanced closing bracket is not part of any selector */
    for (uint32_t i = 0; !p.failed && i < p.count; i = kg_next_sibling(ts, i)) {
        uint8_t type = p.tok[i].type;
        if (type == KG_TOK_RIGHT_PAREN || type == KG_TOK_RIGHT_SQUARE || type == KG_TOK_RIGHT_CURLY ||
            type == KG_TOK_SEMICOLON || type == KG_TOK_LEFT_CURLY) {
            fail(&p, i, p.count, "Expected a selector");
            break;
        }
    }
    if (!p.failed) parse_list(&p, 0, p.count, nested ? M_NESTED : M_TOP, false);
    if (!p.failed) {
        Py_ssize_t count = 0;
        for (uint32_t i = 0; i < p.n; i = p.nodes[i].subtree_end) count++;
        ans = PyTuple_New(count);
        Py_ssize_t k = 0;
        for (uint32_t i = 0; ans != NULL && i < p.n; i = p.nodes[i].subtree_end) {
            PyObject *c = make_compiled(state, p.nodes, i);
            if (c == NULL) Py_CLEAR(ans);
            else PyTuple_SET_ITEM(ans, k++, c);
        }
    } else if (!p.python_error) {
        /* (message, what was found instead or None, position) */
        PyObject *got = NULL;
        if (p.plain) got = Py_NewRef(Py_None);
        else if (p.got == NO_TOKEN) got = PyUnicode_FromString("end of input");
        else {
            PyObject *text = PyUnicode_Substring(ts->source, p.tok[p.got].start, p.tok[p.got].end);
            if (text != NULL) { got = PyObject_Repr(text); Py_DECREF(text); }
        }
        /* Names in the message are truncated by bytes, which can split a UTF-8 sequence */
        PyObject *msg = got == NULL ? NULL : PyUnicode_DecodeUTF8(p.msg, (Py_ssize_t)strlen(p.msg), "replace");
        if (msg == NULL) Py_CLEAR(got);
        if (got != NULL) {
            PyObject *exc_args = Py_BuildValue("(NNI)", msg, got, p.err_pos);
            if (exc_args != NULL) {
                PyErr_SetObject(state->SelectorParseError, exc_args);
                Py_DECREF(exc_args);
            }
        }
    }
    if (p.nodes) { free_nodes(p.nodes, 0, p.n); PyMem_Free(p.nodes); }
    Py_DECREF(ts);
    return ans;
}

int
kg_selectors_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->CompiledSelector_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &CompiledSelector_spec, NULL);
    if (state->CompiledSelector_type == NULL || PyModule_AddType(module, state->CompiledSelector_type) != 0) return -1;
    state->SelectorParseError = PyErr_NewExceptionWithDoc(
        "kgcss._kgcss.SelectorParseError",
        "Raised by parse_selectors() with (message, got or None, pos). Not a public API: kgcss raises SelectorSyntaxError.",
        PyExc_ValueError, NULL);
    if (state->SelectorParseError == NULL) return -1;
    return PyModule_AddObjectRef(module, "SelectorParseError", state->SelectorParseError);
}

int
kg_selectors_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->CompiledSelector_type);
    Py_VISIT(state->SelectorParseError);
    return 0;
}

void
kg_selectors_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->CompiledSelector_type);
    Py_CLEAR(state->SelectorParseError);
}
