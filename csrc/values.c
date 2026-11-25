/*
 * values.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Component values (CSS Syntax §5): Block and Function groups, and the
 * ComponentValueList sequence, all lightweight views over a TokenStream. The
 * nesting was already resolved by the tokenizer (kg_token.match), so no code
 * here recurses, and arbitrarily deep input cannot overflow the C stack.
 *
 * Thread safety: all objects are immutable after creation.
 */

#include "objects.h"
#include <stddef.h>

/* ---------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------- */

PyObject *
kg_group_new(kg_TokenStream *ts, uint32_t idx) {
    kg_module_state *state = kg_state_for((PyObject *)ts);
    const kg_token *t = ts->arr.tokens + idx;
    const bool is_function = t->type == KG_TOK_FUNCTION;
    kg_Group *self = PyObject_New(kg_Group, is_function ? state->Function_type : state->Block_type);
    if (self == NULL) return NULL;
    self->stream = (kg_TokenStream *)Py_NewRef(ts);
    self->idx = idx;
    self->end = (t->flags & KG_TF_UNCLOSED) ? ts->len : ts->arr.tokens[t->match].end;
    self->name = NULL;
    if (is_function && (self->name = kg_range_value(ts, t, t->vstart, t->vend, false)) == NULL) {
        Py_DECREF(self);
        return NULL;
    }
    return (PyObject *)self;
}

/* Create the Token, Block or Function starting at token idx */
PyObject *
kg_make_item(kg_TokenStream *ts, uint32_t idx) {
    if (kg_is_opener(ts->arr.tokens[idx].type)) return kg_group_new(ts, idx);
    return kg_make_token(ts, idx);
}

/* A list of the component values in tokens [first, last), covering source
 * text [pos, end). [first, last) must not split a block. */
PyObject *
kg_cvl_new(kg_TokenStream *ts, uint32_t first, uint32_t last, uint32_t pos, uint32_t end) {
    kg_module_state *state = kg_state_for((PyObject *)ts);
    Py_ssize_t n = 0;
    for (uint32_t i = first; i < last; i = kg_next_sibling(ts, i)) n++;
    kg_ComponentValueList *self = PyObject_NewVar(kg_ComponentValueList, state->ComponentValueList_type, n);
    if (self == NULL) return NULL;
    self->stream = (kg_TokenStream *)Py_NewRef(ts);
    self->pos = pos;
    self->end = end;
    n = 0;
    for (uint32_t i = first; i < last; i = kg_next_sibling(ts, i)) self->items[n++] = i;
    assert(n == Py_SIZE(self));
    return (PyObject *)self;
}

/* ---------------------------------------------------------------------------
 * Block and Function
 * ------------------------------------------------------------------------- */

static void
Group_dealloc(PyObject *o) {
    kg_Group *self = (kg_Group *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->stream);
    Py_XDECREF(self->name);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static inline const kg_token *
group_opener(const kg_Group *self) { return self->stream->arr.tokens + self->idx; }

static PyObject *
Group_type(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    return Py_NewRef(PyTuple_GET_ITEM(kg_state_for(o)->token_types, group_opener(self)->type));
}

static PyObject *
Group_pos(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    return PyLong_FromUnsignedLong(group_opener((kg_Group *)o)->start);
}

static PyObject *
Group_closed(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    return PyBool_FromLong(!(group_opener((kg_Group *)o)->flags & KG_TF_UNCLOSED));
}

static PyObject *
Group_source_text(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    return PyUnicode_Substring(self->stream->source, group_opener(self)->start, self->end);
}

static PyObject *
Group_line(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, group_opener(self)->start, &line, &col)) return NULL;
    return PyLong_FromSsize_t(line);
}

static PyObject *
Group_column(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, group_opener(self)->start, &line, &col)) return NULL;
    return PyLong_FromSsize_t(col);
}

/* The contents of the group, between the opening and closing tokens */
static PyObject *
Group_contents(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    kg_TokenStream *ts = self->stream;
    const kg_token *t = group_opener(self);
    if (t->flags & KG_TF_UNCLOSED) return kg_cvl_new(ts, self->idx + 1, ts->arr.count, t->end, ts->len);
    return kg_cvl_new(ts, self->idx + 1, t->match, t->end, ts->arr.tokens[t->match].start);
}

static PyObject *
Block_opener(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_Group *self = (kg_Group *)o;
    const kg_token *t = group_opener(self);
    return PyUnicode_Substring(self->stream->source, t->start, t->end);
}

static PyObject *
Group_repr(PyObject *o) {
    kg_Group *self = (kg_Group *)o;
    Py_ssize_t line, col;
    if (!kg_line_col(self->stream, group_opener(self)->start, &line, &col)) return NULL;
    if (self->name != NULL) return PyUnicode_FromFormat("<Function %R at %zd:%zd>", self->name, line, col);
    static const char *names[] = {[KG_TOK_LEFT_CURLY] = "{}", [KG_TOK_LEFT_SQUARE] = "[]", [KG_TOK_LEFT_PAREN] = "()"};
    return PyUnicode_FromFormat("<Block %s at %zd:%zd>", names[group_opener(self)->type], line, col);
}

#define COMMON_GROUP_GETSET \
    {"type", Group_type, NULL, "LEFT_CURLY, LEFT_SQUARE or LEFT_PAREN for blocks, FUNCTION for functions", NULL}, \
    {"pos", Group_pos, NULL, "Offset of the opening token in the source", NULL}, \
    {"closed", Group_closed, NULL, "False if the end of input was reached before the closing token", NULL}, \
    {"source_text", Group_source_text, NULL, "The exact source text, from the opening token to the closing one", NULL}, \
    {"line", Group_line, NULL, "1-based line number of the opening token", NULL}, \
    {"column", Group_column, NULL, "1-based column (in code points) of the opening token", NULL}

#define COMMON_GROUP_MEMBERS \
    {"end", Py_T_UINT, offsetof(kg_Group, end), Py_READONLY, \
     "Offset just past the closing token, or the length of the source if unclosed"}, \
    {"stream", Py_T_OBJECT_EX, offsetof(kg_Group, stream), Py_READONLY, "The TokenStream this belongs to"}

static PyGetSetDef Block_getset[] = {
    COMMON_GROUP_GETSET,
    {"content", Group_contents, NULL, "The component values inside the block, as a ComponentValueList", NULL},
    {"opener", Block_opener, NULL, "The opening character: '{', '[' or '('", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyMemberDef Block_members[] = {COMMON_GROUP_MEMBERS, {NULL, 0, 0, 0, NULL}};

static PyType_Slot Block_slots[] = {
    {Py_tp_dealloc, Group_dealloc},
    {Py_tp_repr, Group_repr},
    {Py_tp_getset, Block_getset},
    {Py_tp_members, Block_members},
    {Py_tp_doc, (void *)"A {}, [] or () block: a simple block in CSS Syntax terms."},
    {0, NULL}
};

static PyType_Spec Block_spec = {
    .name = "kgcss.Block",
    .basicsize = sizeof(kg_Group),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = Block_slots,
};

static PyGetSetDef Function_getset[] = {
    COMMON_GROUP_GETSET,
    {"arguments", Group_contents, NULL, "The component values inside the parentheses, as a ComponentValueList", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyMemberDef Function_members[] = {
    COMMON_GROUP_MEMBERS,
    {"name", Py_T_OBJECT_EX, offsetof(kg_Group, name), Py_READONLY, "The unescaped function name"},
    {NULL, 0, 0, 0, NULL}
};

static PyType_Slot Function_slots[] = {
    {Py_tp_dealloc, Group_dealloc},
    {Py_tp_repr, Group_repr},
    {Py_tp_getset, Function_getset},
    {Py_tp_members, Function_members},
    {Py_tp_doc, (void *)"A function such as rgb(...): a name and its arguments."},
    {0, NULL}
};

static PyType_Spec Function_spec = {
    .name = "kgcss.Function",
    .basicsize = sizeof(kg_Group),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = Function_slots,
};

/* ---------------------------------------------------------------------------
 * ComponentValueList
 * ------------------------------------------------------------------------- */

static void
CVL_dealloc(PyObject *o) {
    kg_ComponentValueList *self = (kg_ComponentValueList *)o;
    PyTypeObject *tp = Py_TYPE(o);
    Py_XDECREF(self->stream);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static Py_ssize_t
CVL_len(PyObject *o) { return Py_SIZE(o); }

static PyObject *
CVL_item(PyObject *o, Py_ssize_t i) {
    kg_ComponentValueList *self = (kg_ComponentValueList *)o;
    if (i < 0 || i >= Py_SIZE(o)) {
        PyErr_SetString(PyExc_IndexError, "component value index out of range");
        return NULL;
    }
    return kg_make_item(self->stream, self->items[i]);
}

static PyObject *
CVL_text(PyObject *o, void *closure) {
    KG_UNUSED(closure);
    kg_ComponentValueList *self = (kg_ComponentValueList *)o;
    return PyUnicode_Substring(self->stream->source, self->pos, self->end);
}

static PyObject *
CVL_repr(PyObject *o) {
    kg_ComponentValueList *self = (kg_ComponentValueList *)o;
    PyObject *text = CVL_text(o, NULL);
    if (text == NULL) return NULL;
    if (PyUnicode_GET_LENGTH(text) > 40) {
        PyObject *head = PyUnicode_Substring(text, 0, 37);
        Py_SETREF(text, head == NULL ? NULL : PyUnicode_FromFormat("%U...", head));
        Py_XDECREF(head);
        if (text == NULL) return NULL;
    }
    PyObject *ans = PyUnicode_FromFormat("<ComponentValueList of %zd items: %R>", Py_SIZE(self), text);
    Py_DECREF(text);
    return ans;
}

static PyMemberDef CVL_members[] = {
    {"pos", Py_T_UINT, offsetof(kg_ComponentValueList, pos), Py_READONLY, "Offset of the start of this list in the source"},
    {"end", Py_T_UINT, offsetof(kg_ComponentValueList, end), Py_READONLY, "Offset just past the end of this list"},
    {"stream", Py_T_OBJECT_EX, offsetof(kg_ComponentValueList, stream), Py_READONLY, "The TokenStream this belongs to"},
    {NULL, 0, 0, 0, NULL}
};

static PyGetSetDef CVL_getset[] = {
    {"text", CVL_text, NULL, "The exact source text covered by this list, including any comments", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot CVL_slots[] = {
    {Py_tp_dealloc, CVL_dealloc},
    {Py_tp_repr, CVL_repr},
    {Py_tp_members, CVL_members},
    {Py_tp_getset, CVL_getset},
    {Py_sq_length, CVL_len},
    {Py_sq_item, CVL_item},
    {Py_tp_doc, (void *)"An immutable sequence of component values (Token, Block or Function). "
                        "Items are created on access."},
    {0, NULL}
};

static PyType_Spec CVL_spec = {
    .name = "kgcss.ComponentValueList",
    .basicsize = offsetof(kg_ComponentValueList, items),
    .itemsize = sizeof(uint32_t),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION,
    .slots = CVL_slots,
};

/* ---------------------------------------------------------------------------
 * Parsing entry points
 * ------------------------------------------------------------------------- */

static kg_TokenStream *
parse_args(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames, const char *fname) {
    PyObject *source;
    bool comments;
    if (!kg_parse_source_args(args, nargs, kwnames, fname, &source, &comments, NULL)) return NULL;
    if (!PyUnicode_Check(source)) {
        PyErr_Format(PyExc_TypeError, "%s() argument must be str, not %.200s", fname, Py_TYPE(source)->tp_name);
        return NULL;
    }
    return kg_tokenize_to_stream(module, source, comments);
}

PyObject *
kg_parse_component_value_list(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {
    kg_TokenStream *ts = parse_args(module, args, nargs, kwnames, "parse_component_value_list");
    if (ts == NULL) return NULL;
    PyObject *ans = kg_cvl_new(ts, 0, ts->arr.count, 0, ts->len);
    Py_DECREF(ts);
    return ans;
}

PyObject *
kg_parse_one_component_value(PyObject *module, PyObject *const *args, Py_ssize_t nargs, PyObject *kwnames) {
    kg_TokenStream *ts = parse_args(module, args, nargs, kwnames, "parse_one_component_value");
    if (ts == NULL) return NULL;
    const uint32_t count = ts->arr.count;
    PyObject *ans;
    uint32_t i = 0;
    while (i < count && kg_is_ignorable(ts, i)) i = kg_next_sibling(ts, i);
    if (i >= count) {
        ans = kg_make_parse_error(ts, ts->len, KG_ERR_EMPTY);
    } else {
        uint32_t j = kg_next_sibling(ts, i);
        while (j < count && kg_is_ignorable(ts, j)) j = kg_next_sibling(ts, j);
        ans = j < count ? kg_make_parse_error(ts, ts->arr.tokens[j].start, KG_ERR_EXTRA_INPUT) : kg_make_item(ts, i);
    }
    Py_DECREF(ts);
    return ans;
}

/* ---------------------------------------------------------------------------
 * Module integration
 * ------------------------------------------------------------------------- */

int
kg_values_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
#define T(name, spec) \
    state->name##_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &spec, NULL); \
    if (state->name##_type == NULL || PyModule_AddType(module, state->name##_type) != 0) return -1;
    T(Block, Block_spec) T(Function, Function_spec) T(ComponentValueList, CVL_spec)
#undef T
    return 0;
}

int
kg_values_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->Block_type);
    Py_VISIT(state->Function_type);
    Py_VISIT(state->ComponentValueList_type);
    return 0;
}

void
kg_values_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->Block_type);
    Py_CLEAR(state->Function_type);
    Py_CLEAR(state->ComponentValueList_type);
}
