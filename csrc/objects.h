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

typedef struct {
    PyObject_VAR_HEAD
    kg_TokenStream *stream;
    uint32_t pos, end; /* source range covered by this list */
    uint32_t items[];  /* token index of each top level item, Py_SIZE() of them */
} kg_ComponentValueList;

/* Syntax tree nodes, see syntax.c */
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
    PyObject *items;    /* list, never mutated after creation */
    PyObject *errors;   /* tuple of ParseError */
    PyObject *encoding; /* str or None */
} kg_NodeList;

/* The fields of kgcss.cssom.declarations.Property, see properties.c */
typedef struct {
    PyObject_HEAD
    PyObject *name, *value, *priority, *literal_priority, *node, *parent, *parsed;
    char value_from_node, dirty;
} kg_PropertyData;

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
/* Parse the arguments (source, /, *, comments=True) for METH_FASTCALL | METH_KEYWORDS
 * functions, plus a keyword only encoding=None if encoding is not NULL */
bool kg_parse_source_args(PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames, const char *fname,
                          PyObject **source, bool *comments, PyObject **encoding);

/* values.c */
int kg_values_init(PyObject *module);
int kg_values_traverse(PyObject *module, visitproc visit, void *arg);
void kg_values_clear(PyObject *module);
PyObject *kg_parse_component_value_list(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_parse_one_component_value(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_cvl_new(kg_TokenStream *ts, uint32_t first, uint32_t last, uint32_t pos, uint32_t end);
PyObject *kg_make_item(kg_TokenStream *ts, uint32_t idx);
PyObject *kg_group_new(kg_TokenStream *ts, uint32_t idx);

/* properties.c */
int kg_properties_init(PyObject *module);
int kg_properties_traverse(PyObject *module, visitproc visit, void *arg);
void kg_properties_clear(PyObject *module);
PyObject *kg_build_properties(PyObject *module, PyObject *const *args, Py_ssize_t nargs);

/* selectors.c */
int kg_selectors_init(PyObject *module);
int kg_selectors_traverse(PyObject *module, visitproc visit, void *arg);
void kg_selectors_clear(PyObject *module);
PyObject *kg_parse_selectors(PyObject *module, PyObject *const *args, Py_ssize_t nargs);

/* select.c */
int kg_select_init(PyObject *module);
int kg_select_traverse(PyObject *module, visitproc visit, void *arg);
void kg_select_clear(PyObject *module);

/* color.c */
int kg_color_init(PyObject *module);
int kg_color_traverse(PyObject *module, visitproc visit, void *arg);
void kg_color_clear(PyObject *module);
PyObject *kg_parse_rgba(PyObject *module, PyObject *src);
PyObject *kg_parse_color_raw(PyObject *module, PyObject *src);
PyObject *kg_color_to_rgba(PyObject *module, PyObject *const *args, Py_ssize_t nargs);

/* urls.c */
PyObject *kg_find_urls(PyObject *module, PyObject *const *args, Py_ssize_t nargs);

/* writer.c */
PyObject *kg_write_values(PyObject *module, PyObject *const *args, Py_ssize_t nargs);
PyObject *kg_write_declarations(PyObject *module, PyObject *const *args, Py_ssize_t nargs);

/* syntax.c */
int kg_syntax_init(PyObject *module);
int kg_syntax_traverse(PyObject *module, visitproc visit, void *arg);
void kg_syntax_clear(PyObject *module);
PyObject *kg_parse_stylesheet(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_parse_rule_list(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_parse_one_rule(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_parse_block_contents(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
PyObject *kg_parse_one_declaration(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames);
