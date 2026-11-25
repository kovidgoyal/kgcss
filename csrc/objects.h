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
