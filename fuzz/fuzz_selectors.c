/*
 * fuzz_selectors.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * libFuzzer harness for the selector parser (csrc/selectors.c). Build and
 * run it with tools/fuzz.py --target selectors. The whole extension is
 * compiled into the fuzzer and registered as the built-in module
 * kgcss._kgcss, so the Python package (from PYTHONPATH) uses it. Each input,
 * decoded as UTF-8, is parsed as a top level and as a nested selector list.
 * Either the result is a tuple of CompiledSelector whose nodes satisfy the
 * tree invariants, or SelectorParseError is raised with a position inside
 * the text. Anything else aborts.
 */

#include "kgcss.h"
#include <stdio.h>
#include <stdlib.h>

#define CHECK(cond) do { if (!(cond)) { \
    fprintf(stderr, "%s:%d: invariant failed: %s\n", __FILE__, __LINE__, #cond); \
    if (PyErr_Occurred()) PyErr_Print(); \
    abort(); } } while (0)

PyMODINIT_FUNC PyInit__kgcss(void);
int LLVMFuzzerInitialize(int *argc, char ***argv);
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

static PyObject *parse_selectors, *parse_error;

int
LLVMFuzzerInitialize(int *argc, char ***argv) {
    (void)argc; (void)argv;
    CHECK(PyImport_AppendInittab("kgcss._kgcss", PyInit__kgcss) == 0);
    Py_InitializeEx(0);
    PyObject *m = PyImport_ImportModule("kgcss._kgcss");
    CHECK(m != NULL);
    /* The module must be the one compiled into this binary, not an extension from PYTHONPATH */
    PyObject *file = PyObject_GetAttrString(m, "__file__");
    CHECK(file == NULL);
    PyErr_Clear();
    parse_selectors = PyObject_GetAttrString(m, "parse_selectors");
    parse_error = PyObject_GetAttrString(m, "SelectorParseError");
    CHECK(parse_selectors != NULL && parse_error != NULL);
    Py_DECREF(m);
    return 0;
}

static long
as_long(PyObject *tuple, Py_ssize_t i) {
    long v = PyLong_AsLong(PyTuple_GET_ITEM(tuple, i));
    CHECK(!(v == -1 && PyErr_Occurred()));
    return v;
}

static void
check_compiled(PyObject *compiled, Py_ssize_t text_len) {
    PyObject *start_o = PyObject_GetAttrString(compiled, "start"), *end_o = PyObject_GetAttrString(compiled, "end");
    CHECK(start_o != NULL && end_o != NULL);
    const long start = PyLong_AsLong(start_o), end = PyLong_AsLong(end_o);
    Py_DECREF(start_o); Py_DECREF(end_o);
    CHECK(0 <= start && start <= end && end <= text_len);
    PyObject *nodes = PyObject_CallMethod(compiled, "nodes", NULL);
    CHECK(nodes != NULL && PyTuple_Check(nodes));
    const Py_ssize_t n = PyTuple_GET_SIZE(nodes);
    CHECK(n >= 2);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *node = PyTuple_GET_ITEM(nodes, i);
        CHECK(PyTuple_Check(node) && PyTuple_GET_SIZE(node) == 11);
        const long kind = as_long(node, 0), s = as_long(node, 3), e = as_long(node, 4), sub = as_long(node, 5);
        CHECK(kind >= 1 && kind <= 13);
        CHECK(0 <= s && s <= e && e <= end - start);
        CHECK(sub > i && sub <= n);
        /* Children are inside their parent */
        for (Py_ssize_t k = i + 1; k < sub; k++) {
            PyObject *child = PyTuple_GET_ITEM(nodes, k);
            CHECK(as_long(child, 5) <= sub);
        }
    }
    CHECK(as_long(PyTuple_GET_ITEM(nodes, 0), 0) == 1);  /* a complex selector */
    CHECK(as_long(PyTuple_GET_ITEM(nodes, 0), 5) == n);
    Py_DECREF(nodes);
    PyObject *spec = PyObject_CallMethod(compiled, "specificity", NULL);
    CHECK(spec != NULL && PyTuple_Check(spec) && PyTuple_GET_SIZE(spec) == 3);
    Py_DECREF(spec);
}

int
LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size > (1u << 16)) return 0;
    PyObject *text = PyUnicode_DecodeUTF8((const char *)data, (Py_ssize_t)size, "replace");
    CHECK(text != NULL);
    const Py_ssize_t len = PyUnicode_GET_LENGTH(text);
    for (int nested = 0; nested < 2; nested++) {
        PyObject *result = PyObject_CallFunctionObjArgs(parse_selectors, text, nested ? Py_True : Py_False, NULL);
        if (result == NULL) {
            CHECK(PyErr_ExceptionMatches(parse_error));
            PyObject *exc = PyErr_GetRaisedException();
            PyObject *args = PyObject_GetAttrString(exc, "args");
            CHECK(args != NULL && PyTuple_Check(args) && PyTuple_GET_SIZE(args) == 3);
            const long pos = as_long(args, 2);
            CHECK(0 <= pos && pos <= len);
            Py_DECREF(args);
            Py_DECREF(exc);
            continue;
        }
        CHECK(PyTuple_Check(result));
        for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(result); i++) check_compiled(PyTuple_GET_ITEM(result, i), len);
        Py_DECREF(result);
    }
    Py_DECREF(text);
    return 0;
}
