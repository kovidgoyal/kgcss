/*
 * fuzz_tokenizer.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * libFuzzer harness for the C tokenizer (csrc/tokenizer.c). Build and run it
 * with tools/fuzz.py. Each input is tokenized as PEP 393 kind 1, 2 and 4 data
 * (the raw bytes reinterpreted), with and without comments, and the token
 * array invariants are checked. Token values are built with kg_unescape(),
 * which needs a Python interpreter, initialised once.
 */

#include "tokens.h"
#include <stdio.h>
#include <stdlib.h>

#define CHECK(cond) do { if (!(cond)) { \
    fprintf(stderr, "%s:%d: invariant failed: %s\n", __FILE__, __LINE__, #cond); abort(); } } while (0)

int LLVMFuzzerInitialize(int *argc, char ***argv);
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int
LLVMFuzzerInitialize(int *argc, char ***argv) {
    (void)argc; (void)argv;
    Py_InitializeEx(0);
    return 0;
}

static bool
closes(uint8_t opener, uint8_t closer) {
    switch (opener) {
        case KG_TOK_LEFT_CURLY: return closer == KG_TOK_RIGHT_CURLY;
        case KG_TOK_LEFT_SQUARE: return closer == KG_TOK_RIGHT_SQUARE;
        default: return closer == KG_TOK_RIGHT_PAREN;
    }
}

static void
check_values(int kind, const void *data, const kg_token *t) {
    PyObject *v = NULL;
    switch (t->type) {
        case KG_TOK_IDENT: case KG_TOK_FUNCTION: case KG_TOK_AT_KEYWORD: case KG_TOK_HASH: case KG_TOK_URL:
            v = kg_unescape(kind, data, t->vstart, t->vend, false);
            break;
        case KG_TOK_STRING:
            v = kg_unescape(kind, data, t->vstart, t->vend, true);
            break;
        case KG_TOK_DIMENSION:
            v = kg_unescape(kind, data, t->vend, t->end, false);
            break;
        default:
            return;
    }
    CHECK(v != NULL);
    for (Py_ssize_t i = 0; i < PyUnicode_GET_LENGTH(v); i++) {
        Py_UCS4 c = PyUnicode_READ_CHAR(v, i);
        CHECK(c != 0 && !kg_is_surrogate(c) && c <= 0x10FFFF);
    }
    Py_DECREF(v);
}

static void
check(int kind, const void *data, uint32_t len) {
    for (int keep_comments = 0; keep_comments < 2; keep_comments++) {
        kg_token_array arr = {0};
        CHECK(kg_tokenize(kind, data, len, keep_comments, &arr));
        uint32_t pos = 0;
        /* Stack of open groups, to check that matches nest properly */
        uint32_t *stack = malloc(((size_t)arr.count + 1) * sizeof(uint32_t));
        CHECK(stack != NULL);
        uint32_t depth = 0;
        for (uint32_t i = 0; i < arr.count; i++) {
            const kg_token *t = arr.tokens + i;
            CHECK(t->type >= KG_TOK_IDENT && t->type < KG_TOK__COUNT);
            CHECK(t->start < t->end && t->end <= len);
            CHECK(t->start <= t->vstart && t->vstart <= t->vend && t->vend <= t->end);
            if (keep_comments) CHECK(t->start == pos);
            else CHECK(t->start >= pos && t->type != KG_TOK_COMMENT);
            pos = t->end;
            while (depth && arr.tokens[stack[depth - 1]].match == i) depth--;
            if (kg_is_opener(t->type)) {
                if (t->flags & KG_TF_UNCLOSED) CHECK(t->match == arr.count);
                else {
                    CHECK(t->match > i && t->match < arr.count);
                    CHECK(closes(t->type, arr.tokens[t->match].type));
                }
                /* A group must end no later than the group containing it */
                if (depth) CHECK(t->match <= arr.tokens[stack[depth - 1]].match);
                stack[depth++] = i;
            }
            check_values(kind, data, t);
        }
        free(stack);
        if (keep_comments) CHECK(pos == len);
        for (uint32_t i = 0; i < arr.error_count; i++) {
            CHECK(arr.errors[i].offset <= len);
            CHECK(arr.errors[i].kind < KG_ERR__COUNT);
        }
        kg_token_array_free(&arr);
    }
    uint32_t *lines = NULL, count = 0;
    CHECK(kg_build_line_table(kind, data, len, &lines, &count));
    CHECK(count >= 1 && lines[0] == 0);
    for (uint32_t i = 1; i < count; i++) CHECK(lines[i] > lines[i - 1] && lines[i] <= len);
    PyMem_RawFree(lines);
}

int
LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size > (1u << 20)) return 0;
    check(PyUnicode_1BYTE_KIND, data, (uint32_t)size);

    const size_t n2 = size / 2, n4 = size / 4;
    Py_UCS2 *ucs2 = malloc(n2 * sizeof(Py_UCS2) + 1);
    Py_UCS4 *ucs4 = malloc(n4 * sizeof(Py_UCS4) + 1);
    if (ucs2 == NULL || ucs4 == NULL) abort();
    memcpy(ucs2, data, n2 * sizeof(Py_UCS2));
    check(PyUnicode_2BYTE_KIND, ucs2, (uint32_t)n2);
    /* Python strings never hold code points above U+10FFFF */
    for (size_t i = 0; i < n4; i++) {
        uint32_t c;
        memcpy(&c, data + i * 4, 4);
        ucs4[i] = c % 0x110000;
    }
    check(PyUnicode_4BYTE_KIND, ucs4, (uint32_t)n4);
    free(ucs2);
    free(ucs4);
    return 0;
}
