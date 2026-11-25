/*
 * module.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Module definition for kgcss._kgcss. Uses multi-phase initialisation
 * (PEP 489) and per-module state so that it works with sub-interpreters and
 * declares that it does not need the GIL (PEP 703).
 */

#include "kgcss.h"

static PyMethodDef module_methods[] = {
    {"serialize_string", kg_serialize_string, METH_O,
     "serialize_string(text: str) -> str\n\n"
     "Serialize text as a CSS string, including the surrounding double quotes, "
     "following the CSSOM 'serialize a string' algorithm."},
    {"serialize_identifier", kg_serialize_identifier, METH_O,
     "serialize_identifier(text: str) -> str\n\n"
     "Serialize text as a CSS identifier, escaping characters as needed, "
     "following the CSSOM 'serialize an identifier' algorithm."},
    {NULL, NULL, 0, NULL}
};

static int
module_exec(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->empty_string = PyUnicode_FromStringAndSize("", 0);
    if (state->empty_string == NULL) return -1;
    if (PyModule_AddIntConstant(module, "GIL_DISABLED_BUILD", KG_GIL_DISABLED_BUILD) != 0) return -1;
    return 0;
}

static int
module_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->empty_string);
    return 0;
}

static int
module_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->empty_string);
    return 0;
}

static void
module_free(void *module) {
    module_clear((PyObject *)module);
}

static PyModuleDef_Slot module_slots[] = {
    {Py_mod_exec, (void *)module_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL}
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT,
    .m_name = "kgcss._kgcss",
    .m_doc = "C backend for kgcss. Not a public API, use the kgcss package instead.",
    .m_size = sizeof(kg_module_state),
    .m_methods = module_methods,
    .m_slots = module_slots,
    .m_traverse = module_traverse,
    .m_clear = module_clear,
    .m_free = module_free,
};

PyMODINIT_FUNC
PyInit__kgcss(void) {
    return PyModuleDef_Init(&module_def);
}
