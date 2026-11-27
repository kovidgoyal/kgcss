/*
 * fuzz_linter.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * libFuzzer harness for the C code of the linter (csrc/postcss.c,
 * csrc/linter.c, csrc/value_parser.c and csrc/records.c). Build and run it
 * with tools/fuzz.py --target linter. The whole extension is compiled into
 * the fuzzer and registered as the built-in module kgcss._kgcss, so the
 * Python package (from PYTHONPATH) uses it. Each input, decoded as UTF-8,
 * goes to check() of tests/linter_c_checks.py, which compares every C
 * function of the linter with the Python version it ports, and to
 * kgcss.lint(), which must not raise for text without deep nesting. Any
 * exception aborts.
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

static PyObject *check, *lint;

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
    Py_DECREF(m);
    PyObject *checks = PyImport_ImportModule("tests.linter_c_checks");
    CHECK(checks != NULL);
    check = PyObject_GetAttrString(checks, "check");
    CHECK(check != NULL);
    Py_DECREF(checks);
    PyObject *kgcss = PyImport_ImportModule("kgcss");
    CHECK(kgcss != NULL);
    lint = PyObject_GetAttrString(kgcss, "lint");
    CHECK(lint != NULL);
    Py_DECREF(kgcss);
    return 0;
}

/* The deepest nesting of brackets, which deep enough makes lint() raise, as it did before the C code */
static Py_ssize_t
nesting(PyObject *text) {
    Py_ssize_t depth = 0, max = 0;
    for (Py_ssize_t i = 0; i < PyUnicode_GET_LENGTH(text); i++) {
        const Py_UCS4 c = PyUnicode_READ_CHAR(text, i);
        if (c == '{' || c == '(' || c == '[') { if (++depth > max) max = depth; }
        else if ((c == '}' || c == ')' || c == ']') && depth) depth--;
    }
    return max;
}

int
LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size > (1u << 16)) return 0;
    PyObject *text = PyUnicode_DecodeUTF8((const char *)data, (Py_ssize_t)size, "replace");
    CHECK(text != NULL);
    PyObject *r = PyObject_CallOneArg(check, text);
    CHECK(r != NULL);
    Py_DECREF(r);
    if (nesting(text) < 100) {
        r = PyObject_CallOneArg(lint, text);
        CHECK(r != NULL);
        Py_DECREF(r);
    }
    Py_DECREF(text);
    return 0;
}
