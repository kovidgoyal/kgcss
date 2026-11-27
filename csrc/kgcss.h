/*
 * kgcss.h
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * Shared declarations for the kgcss C extension. Every .c file in csrc/
 * must include this header before anything else.
 */

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---------------------------------------------------------------------------
 * Free-threading (no GIL) support
 *
 * On free-threaded builds (Py_GIL_DISABLED) several threads can be inside
 * this extension at the same time. Rules:
 *   - No mutable C level global or static state. Use the module state.
 *   - Treat str, bytes and tuple arguments as immutable, they are safe to read.
 *   - When reading/mutating mutable containers (list, dict) or our own mutable
 *     objects, either use the strong-reference APIs (PyList_GetItemRef,
 *     PyDict_GetItemRef) or wrap the access in Py_BEGIN_CRITICAL_SECTION /
 *     Py_END_CRITICAL_SECTION (these compile to nothing on builds with a GIL).
 * ------------------------------------------------------------------------- */

#if PY_VERSION_HEX < 0x030E0000
#error "kgcss requires Python 3.14 or newer"
#endif

#ifdef Py_GIL_DISABLED
#define KG_GIL_DISABLED_BUILD 1
#else
#define KG_GIL_DISABLED_BUILD 0
#endif

/* ---------------------------------------------------------------------------
 * Misc helpers
 * ------------------------------------------------------------------------- */

#define KG_UNUSED(x) (void)(x)
#define KG_LIKELY(x) __builtin_expect(!!(x), 1)
#define KG_UNLIKELY(x) __builtin_expect(!!(x), 0)

/* printf-style format checking, where the compiler has it */
#define KG_PRINTF_FORMAT(fmt, args) __attribute__((format(printf, fmt, args)))

#if !defined(__GNUC__) && !defined(__clang__)
#undef KG_LIKELY
#undef KG_UNLIKELY
#undef KG_PRINTF_FORMAT
#define KG_LIKELY(x) (x)
#define KG_UNLIKELY(x) (x)
#define KG_PRINTF_FORMAT(fmt, args)
#endif

/* ---------------------------------------------------------------------------
 * Per module state. Anything that would otherwise be a global (cached
 * objects, exception types, heap types) lives here.
 * ------------------------------------------------------------------------- */

/* The record types of records.c, in the order they are defined */
typedef enum {
    KG_RECORD_SELECTOR_POSITION,
    KG_RECORD_SELECTOR_SOURCE,
    KG_RECORD_SELECTOR_PART_SPACES,
    KG_RECORD_SELECTOR_SPACES,
    KG_RECORD_SELECTOR_RAW_SPACES,
    KG_RECORD_SELECTOR_RAWS,
    KG_RECORD_POSTCSS_RAW_VALUE,
    KG_RECORD_POSTCSS_RAWS,
    KG_RECORD_TYPE_COUNT
} kg_record_type;

typedef struct {
    PyObject *empty_string;
    PyObject *str_important;
    PyTypeObject *PropertyData_type;
    PyTypeObject *CompiledSelector_type;
    PyObject *SelectorParseError;
    PyTypeObject *Snapshot_type;
    PyObject *MatchError;
    PyTypeObject *RGBA_type;
    PyObject *str_currentColor;
    /* The names of the color spaces of color.c, a tuple of str */
    PyObject *color_space_names;
    PyTypeObject *TokenStream_type, *Token_type, *ParseError_type;
    PyTypeObject *Block_type, *Function_type, *ComponentValueList_type;
    PyTypeObject *AtRule_type, *QualifiedRule_type, *Declaration_type, *NodeList_type;
    /* token_types[i] is kgcss.enums.TokenType(i), item 0 is None */
    PyObject *token_types;
    /* error_kinds[i] is the kind string for kg_error_kind i */
    PyObject *error_kinds;
    PyTypeObject *record_types[KG_RECORD_TYPE_COUNT];
    PyTypeObject *PostcssNode_type;
} kg_module_state;

static inline kg_module_state *
kg_get_module_state(PyObject *module) {
    return (kg_module_state *)PyModule_GetState(module);
}

/* ---------------------------------------------------------------------------
 * Functions implemented in the various .c files
 * ------------------------------------------------------------------------- */

/* serialize.c */
PyObject *kg_serialize_string(PyObject *module, PyObject *arg);
PyObject *kg_serialize_identifier(PyObject *module, PyObject *arg);
