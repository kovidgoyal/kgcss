/*
 * urls.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Finding the URLs in component values: a port of find_urls() in
 * src/kgcss/cssom/urls.py, which remains the reference implementation. It
 * works on the token records directly, so finding the few URLs of a large
 * sheet does not create an object for every token.
 *
 * Nesting is handled with an explicit stack. Only immutable objects are
 * read, so this is safe to run concurrently without the GIL.
 */

#include "objects.h"

typedef enum { FN_OTHER = 0, FN_IMAGE_SET } fn_kind;
typedef enum { PENDING_NONE = 0, PENDING_IMPORT, PENDING_NAMESPACE } pending_kind;

typedef struct {
    uint32_t cur, end;
    fn_kind function;
} u_frame;

/* Whether the value [a, b) of token t is ASCII and, ignoring ASCII case, equal to the lowercase name.
 * Matches _lower(value) == name in urls.py, as _lower() only lowercases ASCII text. */
static bool
value_is(const kg_TokenStream *ts, const kg_token *t, uint32_t a, uint32_t b, const char *name, bool *ok) {
    *ok = true;
    if (!(t->flags & KG_TF_NEEDS_PROCESSING)) {
        size_t n = strlen(name);
        if (b - a != n) return false;
        for (size_t i = 0; i < n; i++) {
            Py_UCS4 c = PyUnicode_READ(ts->kind, ts->data, a + i);
            if (c >= 'A' && c <= 'Z') c += 32;
            if (c != (Py_UCS4)(unsigned char)name[i]) return false;
        }
        return true;
    }
    /* Escaped: compare the unescaped value */
    PyObject *v = kg_range_value((kg_TokenStream *)ts, t, a, b, false);
    if (v == NULL) { *ok = false; return false; }
    bool ans = PyUnicode_IS_ASCII(v) && (size_t)PyUnicode_GET_LENGTH(v) == strlen(name);
    for (Py_ssize_t i = 0; ans && i < PyUnicode_GET_LENGTH(v); i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        if (c >= 'A' && c <= 'Z') c += 32;
        if (c != (Py_UCS4)(unsigned char)name[i]) ans = false;
    }
    Py_DECREF(v);
    return ans;
}

static bool
add_span(PyObject *spans, kg_TokenStream *ts, uint32_t start, uint32_t end, const kg_token *value_token,
         PyObject *kind) {
    PyObject *url = kg_range_value(ts, value_token, value_token->vstart, value_token->vend,
                                   value_token->type == KG_TOK_STRING);
    if (url == NULL) return false;
    PyObject *t = Py_BuildValue("(IINO)", start, end, url, kind);
    if (t == NULL) return false;
    int r = PyList_Append(spans, t);
    Py_DECREF(t);
    return r == 0;
}

/* The single significant argument of the function at idx, if it is a string, else 0 */
static uint32_t
single_string_argument(const kg_TokenStream *ts, uint32_t idx) {
    const kg_token *t = ts->arr.tokens + idx;
    const uint32_t end = (t->flags & KG_TF_UNCLOSED) ? ts->arr.count : t->match;
    uint32_t found = 0, count = 0;
    for (uint32_t i = idx + 1; i < end; i = kg_next_sibling(ts, i)) {
        if (kg_is_ignorable(ts, i)) continue;
        if (++count > 1) return 0;
        found = i;
    }
    return (count == 1 && ts->arr.tokens[found].type == KG_TOK_STRING) ? found : 0;
}

static bool
find(kg_TokenStream *ts, uint32_t first, uint32_t last, bool top_level_rules, PyObject *spans, PyObject *kind_url,
     PyObject *kind_string) {
    size_t cap = 16, depth = 0;
    u_frame *stack = PyMem_Malloc(cap * sizeof(u_frame));
    if (stack == NULL) { PyErr_NoMemory(); return false; }
    stack[depth++] = (u_frame){.cur = first, .end = last, .function = FN_OTHER};
    pending_kind pending = PENDING_NONE;
    bool ok = true;
#define PUSH(c, e, f) do { \
    if (depth == cap) { \
        u_frame *n_ = PyMem_Realloc(stack, 2 * cap * sizeof(u_frame)); \
        if (n_ == NULL) { PyErr_NoMemory(); ok = false; goto done; } \
        stack = n_; cap *= 2; \
    } \
    stack[depth++] = (u_frame){.cur = (c), .end = (e), .function = (f)}; \
} while (0)
#define CHECK(x) do { if (!(x)) { ok = false; goto done; } } while (0)
    while (depth) {
        u_frame *f = stack + depth - 1;
        if (f->cur >= f->end) { depth--; continue; }
        const uint32_t idx = f->cur;
        f->cur = kg_next_sibling(ts, idx);
        const fn_kind function = f->function;
        const bool at_top = top_level_rules && depth == 1;
        const kg_token *t = ts->arr.tokens + idx;
        const uint8_t type = t->type;
        if (type == KG_TOK_FUNCTION) {
            if (pending == PENDING_NAMESPACE && at_top) continue;
            bool vok;
            bool is_url = value_is(ts, t, t->vstart, t->vend, "url", &vok);
            CHECK(vok);
            uint32_t s;
            if (is_url && (s = single_string_argument(ts, idx))) {
                CHECK(add_span(spans, ts, t->start, kg_item_end(ts, idx), ts->arr.tokens + s, kind_url));
                if (at_top) pending = PENDING_NONE;
                continue;
            }
            bool image_set = value_is(ts, t, t->vstart, t->vend, "image-set", &vok);
            CHECK(vok);
            if (!image_set) {
                image_set = value_is(ts, t, t->vstart, t->vend, "-webkit-image-set", &vok);
                CHECK(vok);
            }
            const uint32_t end = (t->flags & KG_TF_UNCLOSED) ? ts->arr.count : t->match;
            PUSH(idx + 1, end, image_set ? FN_IMAGE_SET : FN_OTHER);
            continue;
        }
        if (kg_is_opener(type)) {
            if (at_top) pending = PENDING_NONE;
            const uint32_t end = (t->flags & KG_TF_UNCLOSED) ? ts->arr.count : t->match;
            PUSH(idx + 1, end, FN_OTHER);
            continue;
        }
        if (at_top) {
            if (type == KG_TOK_AT_KEYWORD) {
                bool vok;
                if (value_is(ts, t, t->vstart, t->vend, "import", &vok)) pending = PENDING_IMPORT;
                else {
                    CHECK(vok);
                    pending = value_is(ts, t, t->vstart, t->vend, "namespace", &vok) ? PENDING_NAMESPACE : PENDING_NONE;
                }
                CHECK(vok);
                continue;
            }
            if (type == KG_TOK_SEMICOLON) { pending = PENDING_NONE; continue; }
            if (pending == PENDING_NAMESPACE) continue;
            if (pending == PENDING_IMPORT && type == KG_TOK_STRING) {
                CHECK(add_span(spans, ts, t->start, t->end, t, kind_string));
                pending = PENDING_NONE;
                continue;
            }
            if (pending == PENDING_IMPORT && type != KG_TOK_WHITESPACE && type != KG_TOK_COMMENT && type != KG_TOK_URL) {
                pending = PENDING_NONE;
            }
        }
        if (type == KG_TOK_URL) {
            CHECK(add_span(spans, ts, t->start, t->end, t, kind_url));
            if (at_top) pending = PENDING_NONE;
        } else if (type == KG_TOK_STRING && function == FN_IMAGE_SET) {
            CHECK(add_span(spans, ts, t->start, t->end, t, kind_string));
        }
    }
#undef PUSH
#undef CHECK
done:
    PyMem_Free(stack);
    return ok;
}

PyObject *
kg_find_urls(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 2 || !Py_IS_TYPE(args[0], state->ComponentValueList_type)) {
        PyErr_SetString(PyExc_TypeError, "find_urls() takes a ComponentValueList and a bool");
        return NULL;
    }
    const int top_level_rules = PyObject_IsTrue(args[1]);
    if (top_level_rules < 0) return NULL;
    kg_ComponentValueList *cvl = (kg_ComponentValueList *)args[0];
    kg_TokenStream *ts = cvl->stream;
    const Py_ssize_t n = Py_SIZE(cvl);
    const uint32_t first = n ? cvl->items[0] : 0, last = n ? kg_next_sibling(ts, cvl->items[n - 1]) : 0;
    PyObject *spans = PyList_New(0), *kind_url = PyUnicode_InternFromString("url"),
             *kind_string = PyUnicode_InternFromString("string");
    if (spans == NULL || kind_url == NULL || kind_string == NULL ||
        !find(ts, first, last, top_level_rules, spans, kind_url, kind_string)) {
        Py_CLEAR(spans);
    }
    Py_XDECREF(kind_url);
    Py_XDECREF(kind_string);
    return spans;
}
