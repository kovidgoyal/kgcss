/*
 * tokenizer.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Dispatch over PEP 393 kinds to the template tokenizer in tokenizer_impl.h,
 * plus management of the output arrays.
 */

#include "tokens.h"

typedef struct {
    const void *data;
    uint32_t len, pos;
    kg_token_array *out;
    bool keep_comments;
    bool oom;
} kg_tokenizer;

static bool
grow(void **buf, uint32_t *capacity, size_t item_size, uint32_t min_capacity) {
    uint64_t cap = *capacity ? *capacity : 16;
    while (cap < min_capacity) cap *= 2;
    if (cap > UINT32_MAX) cap = UINT32_MAX;
    void *nb = PyMem_RawRealloc(*buf, (size_t)cap * item_size);
    if (nb == NULL) return false;
    *buf = nb;
    *capacity = (uint32_t)cap;
    return true;
}

static inline void
kg_add_token(kg_tokenizer *s, kg_token_type type, uint32_t start, uint32_t end, uint32_t vstart, uint32_t vend, uint8_t flags) {
    kg_token_array *a = s->out;
    if (KG_UNLIKELY(a->count >= a->capacity)) {
        if (!grow((void **)&a->tokens, &a->capacity, sizeof(kg_token), a->count + 1)) { s->oom = true; return; }
    }
    kg_token *t = a->tokens + a->count++;
    t->start = start; t->end = end; t->vstart = vstart; t->vend = vend;
    t->match = 0; t->type = (uint8_t)type; t->flags = flags;
}

static void
kg_add_error(kg_tokenizer *s, uint32_t offset, kg_error_kind kind) {
    kg_token_array *a = s->out;
    if (a->error_count >= a->error_capacity) {
        if (!grow((void **)&a->errors, &a->error_capacity, sizeof(kg_error), a->error_count + 1)) { s->oom = true; return; }
    }
    kg_error *e = a->errors + a->error_count++;
    e->offset = offset; e->kind = (uint32_t)kind;
}

#define FN(name) name##_ucs1
#define CHAR_T Py_UCS1
#include "tokenizer_impl.h"
#undef CHAR_T
#undef FN

#define FN(name) name##_ucs2
#define CHAR_T Py_UCS2
#include "tokenizer_impl.h"
#undef CHAR_T
#undef FN

#define FN(name) name##_ucs4
#define CHAR_T Py_UCS4
#include "tokenizer_impl.h"
#undef CHAR_T
#undef FN

bool
kg_tokenize(int kind, const void *data, uint32_t len, bool keep_comments, kg_token_array *out) {
    kg_tokenizer s = {.data = data, .len = len, .out = out, .keep_comments = keep_comments};
    /* Most CSS produces roughly one token per 3-5 code points */
    uint32_t estimate = len / 4 + 16;
    if (!grow((void **)&out->tokens, &out->capacity, sizeof(kg_token), estimate)) return false;
    switch (kind) {
        case PyUnicode_1BYTE_KIND: run_ucs1(&s); break;
        case PyUnicode_2BYTE_KIND: run_ucs2(&s); break;
        default: run_ucs4(&s); break;
    }
    return !s.oom;
}

void
kg_token_array_free(kg_token_array *arr) {
    PyMem_RawFree(arr->tokens);
    PyMem_RawFree(arr->errors);
    memset(arr, 0, sizeof(*arr));
}

PyObject *
kg_unescape(int kind, const void *data, uint32_t start, uint32_t end, bool in_string) {
    switch (kind) {
        case PyUnicode_1BYTE_KIND: return unescape_ucs1(data, start, end, in_string);
        case PyUnicode_2BYTE_KIND: return unescape_ucs2(data, start, end, in_string);
        default: return unescape_ucs4(data, start, end, in_string);
    }
}

bool
kg_build_line_table(int kind, const void *data, uint32_t len, uint32_t **lines, uint32_t *count) {
    switch (kind) {
        case PyUnicode_1BYTE_KIND: return build_line_table_ucs1(data, len, lines, count);
        case PyUnicode_2BYTE_KIND: return build_line_table_ucs2(data, len, lines, count);
        default: return build_line_table_ucs4(data, len, lines, count);
    }
}
