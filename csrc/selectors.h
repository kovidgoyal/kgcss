/*
 * selectors.h
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * The compiled form of selectors, shared by the parser (selectors.c) and
 * the matcher (select.c).
 */

#pragma once

#include "objects.h"

/* Keep in sync with NodeKind in src/kgcss/selectors.py */
typedef enum {
    SN_COMPLEX = 1, SN_COMPOUND, SN_TYPE, SN_ID, SN_CLASS, SN_ATTRIBUTE, SN_PSEUDO_CLASS, SN_SELECTOR_PSEUDO,
    SN_NTH, SN_LANG, SN_FUNCTIONAL_PSEUDO, SN_PSEUDO_ELEMENT, SN_NESTING,
} sn_kind;

/* Combinators, in SN_COMPOUND.op: the combinator before the compound */
enum { C_NONE = 0, C_DESCENDANT, C_CHILD, C_NEXT_SIBLING, C_SUBSEQUENT_SIBLING };
/* Attribute operators, in SN_ATTRIBUTE.op */
enum { A_EXISTS = 0, A_EQUALS, A_INCLUDES, A_DASH, A_PREFIX, A_SUFFIX, A_SUBSTRING };
/* Flags */
enum {
    F_CASE_I = 1, F_CASE_S = 2,          /* SN_ATTRIBUTE */
    F_LEGACY = 1, F_FUNCTIONAL = 2,      /* SN_PSEUDO_ELEMENT */
    F_HAS_OF = 1,                        /* SN_NTH */
    F_RELATIVE = 1,                      /* SN_COMPLEX: starts with an explicit combinator */
};

typedef struct {
    uint8_t kind, op, flags;
    uint32_t start, end;        /* span in the text */
    uint32_t subtree_end;       /* index just past the last node of this subtree */
    int32_t a, b;               /* SN_NTH */
    PyObject *name, *value, *ns; /* owned references, or NULL */
} sn_node;

/* A specificity (a, b, c) */
typedef struct { uint32_t a, b, c; } spec_t;

typedef struct {
    PyObject_HEAD
    sn_node *nodes;
    uint32_t n;
    uint32_t start, end;    /* span in the parsed text; node spans are relative to start */
    spec_t specificity;     /* with & counting as :scope */
    bool has_nesting;
    int32_t pseudo_element; /* index of the first pseudo-element of the last compound, or -1 */
} kg_CompiledSelector;

/* selectors.c */
PyObject *kg_ascii_lower(PyObject *s);
