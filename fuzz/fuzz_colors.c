/*
 * fuzz_colors.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * libFuzzer harness for the color parser (csrc/color.c). Build and run it
 * with tools/fuzz.py --target colors. The whole extension is compiled into
 * the fuzzer and registered as the built-in module kgcss._kgcss. Each input,
 * decoded as UTF-8, must give None, 'currentColor' or an RGBA of four floats
 * in 0..1. ASCII input must give the same result in upper case, since
 * everything in a color is ASCII case-insensitive. Anything else aborts.
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

static PyObject *parse_rgba, *rgba_type;

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
    parse_rgba = PyObject_GetAttrString(m, "parse_rgba");
    rgba_type = PyObject_GetAttrString(m, "RGBA");
    CHECK(parse_rgba != NULL && rgba_type != NULL);
    Py_DECREF(m);
    return 0;
}

static PyObject *
check(PyObject *text) {
    PyObject *ans = PyObject_CallOneArg(parse_rgba, text);
    CHECK(ans != NULL);
    if (ans == Py_None) return ans;
    if (PyUnicode_Check(ans)) {
        CHECK(PyUnicode_CompareWithASCIIString(ans, "currentColor") == 0);
        return ans;
    }
    CHECK(Py_IS_TYPE(ans, (PyTypeObject *)rgba_type) && PyTuple_GET_SIZE(ans) == 4);
    for (Py_ssize_t i = 0; i < 4; i++) {
        PyObject *f = PyTuple_GET_ITEM(ans, i);
        CHECK(PyFloat_Check(f));
        const double v = PyFloat_AS_DOUBLE(f);
        CHECK(0 <= v && v <= 1);
    }
    return ans;
}

int
LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size > (1u << 16)) return 0;
    PyObject *text = PyUnicode_DecodeUTF8((const char *)data, (Py_ssize_t)size, "replace");
    CHECK(text != NULL);
    PyObject *ans = check(text);
    if (PyUnicode_IS_ASCII(text)) {
        char *upper = malloc(size + 1);
        CHECK(upper != NULL);
        for (size_t i = 0; i < size; i++) upper[i] = (char)Py_TOUPPER(data[i]);
        PyObject *utext = PyUnicode_DecodeASCII(upper, (Py_ssize_t)size, "strict");
        free(upper);
        CHECK(utext != NULL);
        PyObject *uans = check(utext);
        const int eq = PyObject_RichCompareBool(ans, uans, Py_EQ);
        if (eq != 1) {
            fprintf(stderr, "case changes the result: %s\n", PyUnicode_AsUTF8(text));
            abort();
        }
        Py_DECREF(uans);
        Py_DECREF(utext);
    }
    Py_DECREF(ans);
    Py_DECREF(text);
    return 0;
}
