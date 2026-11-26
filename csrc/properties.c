/*
 * properties.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * PropertyData, the C base of kgcss.cssom.declarations.Property, which holds
 * its fields, and build_properties(), which creates the properties of a
 * parsed declaration block. Reading every declaration of a large sheet
 * creates tens of thousands of properties, so this is a hot path.
 *
 * The properties created by build_properties() are not tracked by the
 * garbage collector. That is safe because a Property cannot be part of a
 * reference cycle: its only reference back to its container, parent, is a
 * weak reference, and nothing else it refers to (strings, a syntax node, a
 * PropertyValue) refers back to it. Tracking tens of thousands of objects
 * that can never be garbage makes every collection slower, which for large
 * sheets cost as much as creating the objects.
 */

#include "objects.h"
#include <structmember.h>

static int
PropertyData_traverse(PyObject *o, visitproc visit, void *arg) {
    kg_PropertyData *self = (kg_PropertyData *)o;
    Py_VISIT(Py_TYPE(o));
    Py_VISIT(self->name);
    Py_VISIT(self->value);
    Py_VISIT(self->priority);
    Py_VISIT(self->literal_priority);
    Py_VISIT(self->node);
    Py_VISIT(self->parent);
    Py_VISIT(self->parsed);
    return 0;
}

static int
PropertyData_clear(PyObject *o) {
    kg_PropertyData *self = (kg_PropertyData *)o;
    Py_CLEAR(self->name);
    Py_CLEAR(self->value);
    Py_CLEAR(self->priority);
    Py_CLEAR(self->literal_priority);
    Py_CLEAR(self->node);
    Py_CLEAR(self->parent);
    Py_CLEAR(self->parsed);
    return 0;
}

static void
PropertyData_dealloc(PyObject *o) {
    PyTypeObject *tp = Py_TYPE(o);
    PyObject_GC_UnTrack(o);
    PropertyData_clear(o);
    tp->tp_free(o);
    Py_DECREF(tp);
}

#define M(name, field, type) {name, type, offsetof(kg_PropertyData, field), 0, NULL}
static PyMemberDef PropertyData_members[] = {
    M("_name", name, Py_T_OBJECT_EX),
    M("_value", value, Py_T_OBJECT_EX),
    M("_priority", priority, Py_T_OBJECT_EX),
    M("_literal_priority", literal_priority, Py_T_OBJECT_EX),
    M("_node", node, Py_T_OBJECT_EX),
    M("_parent", parent, Py_T_OBJECT_EX),
    M("_parsed", parsed, Py_T_OBJECT_EX),
    M("_value_from_node", value_from_node, Py_T_BOOL),
    M("_dirty", dirty, Py_T_BOOL),
    {NULL, 0, 0, 0, NULL},
};
#undef M

static PyType_Slot PropertyData_slots[] = {
    {Py_tp_dealloc, PropertyData_dealloc},
    {Py_tp_traverse, PropertyData_traverse},
    {Py_tp_clear, PropertyData_clear},
    {Py_tp_members, PropertyData_members},
    {Py_tp_new, PyType_GenericNew},
    {Py_tp_doc, (void *)"The fields of a Property. Not a public API, use kgcss.Property."},
    {0, NULL},
};

static PyType_Spec PropertyData_spec = {
    .name = "kgcss.PropertyData",
    .basicsize = sizeof(kg_PropertyData),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = PropertyData_slots,
};

/* The name of a property: lowercased, unless it is a custom property or not ASCII */
static PyObject *
property_name(PyObject *name) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(name);
    if (!PyUnicode_IS_ASCII(name)) return Py_NewRef(name);
    const Py_UCS1 *s = PyUnicode_1BYTE_DATA(name);
    if (n >= 2 && s[0] == '-' && s[1] == '-') return Py_NewRef(name);
    Py_ssize_t i = 0;
    while (i < n && !(s[i] >= 'A' && s[i] <= 'Z')) i++;
    if (i == n) return Py_NewRef(name);
    PyObject *ans = PyUnicode_New(n, 127);
    if (ans == NULL) return NULL;
    Py_UCS1 *d = PyUnicode_1BYTE_DATA(ans);
    for (i = 0; i < n; i++) d[i] = (s[i] >= 'A' && s[i] <= 'Z') ? s[i] + 32 : s[i];
    return ans;
}

static PyObject *
build(kg_module_state *state, PyObject *items, PyTypeObject *cls, PyObject *parent) {
    const Py_ssize_t n = PyList_GET_SIZE(items);
    PyObject *out = PyList_New(n);
    if (out == NULL) return NULL;
    bool others = false;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyList_GET_ITEM(items, i);
        if (!Py_IS_TYPE(item, state->Declaration_type)) {
            others = true;
            PyList_SET_ITEM(out, i, Py_NewRef(item));
            continue;
        }
        const kg_Node *node = (const kg_Node *)item;
        kg_PropertyData *p = (kg_PropertyData *)cls->tp_alloc(cls, 0);
        if (p == NULL) { Py_DECREF(out); return NULL; }
        /* Not shared yet, so it is safe to fill without locking */
        p->name = property_name(node->name);
        if (p->name == NULL) { Py_DECREF(p); Py_DECREF(out); return NULL; }
        p->value = Py_NewRef(Py_None);  /* read from the node on first access */
        p->priority = Py_NewRef(node->important ? state->str_important : state->empty_string);
        p->literal_priority = Py_NewRef(Py_None);
        p->node = Py_NewRef(item);
        p->parent = Py_NewRef(parent);
        p->parsed = Py_NewRef(Py_None);
        p->value_from_node = 1;
        p->dirty = 0;
        /* See the comment at the top of this file */
        PyObject_GC_UnTrack(p);
        PyList_SET_ITEM(out, i, (PyObject *)p);
    }
    PyObject *ans = PyTuple_Pack(2, out, others ? Py_True : Py_False);
    Py_DECREF(out);
    return ans;
}

PyObject *
kg_build_properties(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 3) {
        PyErr_SetString(PyExc_TypeError, "build_properties() takes exactly 3 arguments");
        return NULL;
    }
    PyObject *nodes = args[0], *parent = args[2];
    if (!PyType_Check(args[1]) || !PyType_IsSubtype((PyTypeObject *)args[1], state->PropertyData_type)) {
        PyErr_SetString(PyExc_TypeError, "build_properties() needs a subclass of PropertyData");
        return NULL;
    }
    PyTypeObject *cls = (PyTypeObject *)args[1];
    if (cls->tp_basicsize != state->PropertyData_type->tp_basicsize) {
        /* Instances must be exactly the fields of PropertyData, as they are filled here */
        PyErr_SetString(PyExc_TypeError, "build_properties() needs a subclass of PropertyData without extra slots");
        return NULL;
    }
    PyObject *items;
    if (Py_IS_TYPE(nodes, state->NodeList_type)) items = ((kg_NodeList *)nodes)->items;
    else if (PyList_CheckExact(nodes)) items = nodes;
    else {
        PyErr_Format(PyExc_TypeError, "build_properties() expects a NodeList or a list, not %.200s", Py_TYPE(nodes)->tp_name);
        return NULL;
    }
    PyObject *ans;
    Py_BEGIN_CRITICAL_SECTION(items);
    ans = build(state, items, cls, parent);
    Py_END_CRITICAL_SECTION();
    return ans;
}

int
kg_properties_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->PropertyData_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &PropertyData_spec, NULL);
    if (state->PropertyData_type == NULL) return -1;
    return PyModule_AddType(module, state->PropertyData_type);
}

int
kg_properties_traverse(PyObject *module, visitproc visit, void *arg) {
    Py_VISIT(kg_get_module_state(module)->PropertyData_type);
    return 0;
}

void
kg_properties_clear(PyObject *module) {
    Py_CLEAR(kg_get_module_state(module)->PropertyData_type);
}
