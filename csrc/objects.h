/*
 * objects.h
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Layouts of the Python visible objects, shared between types.c and values.c.
 */

#pragma once

#include "tokens.h"

typedef struct {
    PyObject_HEAD
    PyObject *source;
    const void *data;
    int kind;
    uint32_t len;
    kg_token_array arr;
    /* Lazily built, guarded by a critical section on self */
    uint32_t *lines;
    uint32_t line_count;
} kg_TokenStream;

typedef struct {
    PyObject_HEAD
    kg_TokenStream *stream;
    PyObject *value;
    PyObject *unit;
    kg_token rec;
} kg_Token;

/* Block and Function */
typedef struct {
    PyObject_HEAD
    kg_TokenStream *stream;
    uint32_t idx;   /* index of the opening token ({ [ ( or FUNCTION) */
    uint32_t end;   /* source offset just past the closing token, or the end of input */
    PyObject *name; /* Function only: the unescaped name, NULL for Block */
} kg_Group;

/* Index of the token after the component value starting at idx */
static inline uint32_t
kg_next_sibling(const kg_TokenStream *ts, uint32_t idx) {
    const kg_token *t = ts->arr.tokens + idx;
    if (!kg_is_opener(t->type)) return idx + 1;
    return (t->flags & KG_TF_UNCLOSED) ? ts->arr.count : t->match + 1;
}

/* Source offset just past the component value starting at idx */
static inline uint32_t
kg_item_end(const kg_TokenStream *ts, uint32_t idx) {
    const kg_token *t = ts->arr.tokens + idx;
    if (!kg_is_opener(t->type)) return t->end;
    return (t->flags & KG_TF_UNCLOSED) ? ts->len : ts->arr.tokens[t->match].end;
}

static inline bool
kg_is_ignorable(const kg_TokenStream *ts, uint32_t idx) {
    uint8_t type = ts->arr.tokens[idx].type;
    return type == KG_TOK_WHITESPACE || type == KG_TOK_COMMENT;
}

static inline kg_module_state *
kg_state_for(PyObject *self) {
    return (kg_module_state *)PyType_GetModuleState(Py_TYPE(self));
}

/* types.c */
bool kg_line_col(kg_TokenStream *self, uint32_t offset, Py_ssize_t *line, Py_ssize_t *col);
PyObject *kg_make_token(kg_TokenStream *ts, uint32_t idx);
PyObject *kg_make_parse_error(kg_TokenStream *ts, uint32_t offset, kg_error_kind kind);
PyObject *kg_range_value(kg_TokenStream *ts, const kg_token *t, uint32_t start, uint32_t end, bool in_string);
kg_TokenStream *kg_tokenize_to_stream(PyObject *module, PyObject *source, bool comments);

/* values.c */
int kg_values_init(PyObject *module);
int kg_values_traverse(PyObject *module, visitproc visit, void *arg);
void kg_values_clear(PyObject *module);
PyObject *kg_parse_component_value_list(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_parse_one_component_value(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_cvl_new(kg_TokenStream *ts, uint32_t first, uint32_t last, uint32_t pos, uint32_t end);
PyObject *kg_make_item(kg_TokenStream *ts, uint32_t idx);
PyObject *kg_group_new(kg_TokenStream *ts, uint32_t idx);

/* syntax.c */
int kg_syntax_init(PyObject *module);
int kg_syntax_traverse(PyObject *module, visitproc visit, void *arg);
void kg_syntax_clear(PyObject *module);
PyObject *kg_parse_stylesheet(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_parse_rule_list(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_parse_one_rule(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_parse_block_contents(PyObject *module, PyObject *args, PyObject *kwds);
PyObject *kg_parse_one_declaration(PyObject *module, PyObject *args, PyObject *kwds);
