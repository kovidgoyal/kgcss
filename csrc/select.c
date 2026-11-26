/*
 * select.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * Matching selectors against element trees.
 *
 * Snapshot walks an ElementTree-style tree (lxml, html5-parser, or the
 * standard library) once and records each element in compact arrays: tree
 * links, document order, the local name, id and classes as atoms (interned
 * strings, as integers), and the attributes. Indexes map tag, id and class
 * atoms to elements. Matching then works on these arrays only, so it can run
 * without the GIL.
 *
 * Matching is right to left: candidates for the rightmost compound selector
 * come from an index where possible, and the combinators are followed with
 * an explicit stack, so long selectors cannot overflow the C stack. Where a
 * descendant or subsequent sibling combinator makes it backtrack, a bitset
 * remembers where the selector to the left is known to fail, which keeps
 * patterns like "a b c d" linear in the size of the tree.
 *
 * Thread safety: a Snapshot is immutable after construction, except for
 * caches filled on first use (text content for :empty, and the map from
 * elements to their index), which are filled inside a critical section on
 * the snapshot and only read afterwards.
 */

#include "selectors.h"

#define NONE UINT32_MAX
#define ABSENT (UINT32_MAX - 1)  /* an atom for a name that occurs nowhere in the document */

/* Atoms that are always present, at these indices */
enum { ATOM_ID = 0, ATOM_CLASS, ATOM_HREF, ATOM_A, ATOM_AREA, ATOM_LINK, ATOM_LANG, ATOM_XML_LANG, FIXED_ATOMS };
static const char *const fixed_atoms[FIXED_ATOMS] = {"id", "class", "href", "a", "area", "link", "lang", "xml:lang"};

typedef struct {
    uint32_t parent, prev, next, first_child, subtree_end;
    uint32_t sibling_index, sibling_count, child_count;
    uint32_t name, id;
    uint32_t class_start, class_count, attr_start, attr_count;
    uint8_t has_content;  /* for :empty, valid once content_done */
} snap_el;

/* The kinds of language attributes, for :lang() */
enum { LANG_NONE = 0, LANG_PLAIN, LANG_XML };

typedef struct {
    uint32_t name;
    uint8_t lang;     /* LANG_PLAIN for lang without a namespace, LANG_XML for xml:lang */
    PyObject *value;  /* str */
} snap_attr;

typedef struct {
    uint32_t *off, *items;  /* the elements with atom a are items[off[a]:off[a+1]], in document order */
} snap_index;

typedef struct {
    PyObject_HEAD
    uint32_t n, n_classes, n_attrs, n_atoms;
    PyObject **elements;
    snap_el *els;
    uint32_t *classes;
    snap_attr *attrs;
    PyObject *atoms;   /* dict: str -> int */
    snap_index by_name, by_id, by_class;
    bool quirks;
    /* Filled on first use, inside a critical section */
    bool content_done;
    PyObject *index_map;  /* dict: element -> index */
    uint32_t *type_index, *type_count;  /* position among the siblings with the same name, and their number */
    PyObject **lang;      /* the language of each element, borrowed from attrs, or NULL */
    bool lang_done;
} kg_Snapshot;

/* ---------------------------------------------------------------------------
 * Building the snapshot
 * ------------------------------------------------------------------------- */

typedef struct {
    kg_Snapshot *s;
    size_t cap_el, cap_elements, cap_classes, cap_attrs;
    PyObject *tag_atoms;   /* raw tag or attribute name -> atom of its lowercased local name */
    PyObject *str_tag, *str_items, *str_getparent, *str_iter;  /* interned names */
} builder;

static bool
grow(void **ptr, size_t *cap, size_t need, size_t size) {
    if (need <= *cap) return true;
    size_t cap2 = *cap ? *cap * 2 : 64;
    while (cap2 < need) cap2 *= 2;
    void *p = PyMem_Realloc(*ptr, cap2 * size);
    if (p == NULL) { PyErr_NoMemory(); return false; }
    *ptr = p;
    *cap = cap2;
    return true;
}

/* The atom for an interned string, adding it if needed */
static uint32_t
atom_of(kg_Snapshot *s, PyObject *text) {
    PyObject *v = PyDict_GetItemWithError(s->atoms, text);
    if (v != NULL) return (uint32_t)PyLong_AsUnsignedLong(v);
    if (PyErr_Occurred()) return NONE;
    PyObject *num = PyLong_FromUnsignedLong(s->n_atoms);
    if (num == NULL) return NONE;
    int r = PyDict_SetItem(s->atoms, text, num);
    Py_DECREF(num);
    return r == 0 ? s->n_atoms++ : NONE;
}

/* The atom of the ASCII lowercased local name of a tag or attribute name ({ns}local) */
static uint32_t
name_atom(builder *b, PyObject *raw) {
    PyObject *v = PyDict_GetItemWithError(b->tag_atoms, raw);
    if (v != NULL) return (uint32_t)PyLong_AsUnsignedLong(v);
    if (PyErr_Occurred()) return NONE;
    PyObject *local = raw;
    Py_ssize_t len = PyUnicode_GET_LENGTH(raw);
    if (len && PyUnicode_READ_CHAR(raw, 0) == '{') {
        Py_ssize_t close = PyUnicode_FindChar(raw, '}', 0, len, 1);
        if (close >= 0) local = PyUnicode_Substring(raw, close + 1, len);
        else Py_INCREF(local);
    } else Py_INCREF(local);
    if (local == NULL) return NONE;
    PyObject *lower = kg_ascii_lower(local);
    Py_DECREF(local);
    if (lower == NULL) return NONE;
    uint32_t a = atom_of(b->s, lower);
    Py_DECREF(lower);
    if (a == NONE) return NONE;
    PyObject *num = PyLong_FromUnsignedLong(a);
    if (num == NULL || PyDict_SetItem(b->tag_atoms, raw, num) != 0) { Py_XDECREF(num); return NONE; }
    Py_DECREF(num);
    return a;
}

/* The atom of an id or class: lowercased in quirks mode */
static uint32_t
value_atom(kg_Snapshot *s, PyObject *text) {
    if (!s->quirks) return atom_of(s, text);
    PyObject *lower = kg_ascii_lower(text);
    if (lower == NULL) return NONE;
    uint32_t a = atom_of(s, lower);
    Py_DECREF(lower);
    return a;
}

static inline bool
is_ascii_ws(Py_UCS4 c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f'; }

static bool
add_classes(builder *b, snap_el *el, PyObject *value) {
    kg_Snapshot *s = b->s;
    const Py_ssize_t len = PyUnicode_GET_LENGTH(value);
    Py_ssize_t i = 0;
    el->class_start = s->n_classes;
    while (i < len) {
        while (i < len && is_ascii_ws(PyUnicode_READ_CHAR(value, i))) i++;
        Py_ssize_t start = i;
        while (i < len && !is_ascii_ws(PyUnicode_READ_CHAR(value, i))) i++;
        if (i == start) break;
        PyObject *cls = PyUnicode_Substring(value, start, i);
        if (cls == NULL) return false;
        uint32_t a = value_atom(s, cls);
        Py_DECREF(cls);
        if (a == NONE || !grow((void **)&s->classes, &b->cap_classes, s->n_classes + 1, sizeof(uint32_t))) return false;
        s->classes[s->n_classes++] = a;
    }
    el->class_count = s->n_classes - el->class_start;
    return true;
}

/* Record element e, a child of parent (NONE for the root) */
static bool
add_element(builder *b, PyObject *e, PyObject *tag, uint32_t parent) {
    kg_Snapshot *s = b->s;
    if (!grow((void **)&s->els, &b->cap_el, s->n + 1, sizeof(snap_el))) return false;
    if (!grow((void **)&s->elements, &b->cap_elements, s->n + 1, sizeof(PyObject *))) return false;
    const uint32_t idx = s->n;
    snap_el *el = s->els + idx;
    memset(el, 0, sizeof *el);
    el->parent = parent;
    el->prev = el->next = el->first_child = NONE;
    el->id = NONE;
    if ((el->name = name_atom(b, tag)) == NONE) return false;
    s->elements[idx] = Py_NewRef(e);
    s->n++;
    if (parent != NONE) {
        snap_el *p = s->els + parent;
        el->sibling_index = p->child_count++;
        if (p->first_child == NONE) p->first_child = idx;
    }
    PyObject *items = PyObject_CallMethodNoArgs(e, b->str_items);
    if (items == NULL) return false;
    /* A tuple of our own: hashing the names and values can run Python code, which must not be able to free
     * the pairs we are reading by changing the list items() returned */
    PyObject *seq = PySequence_Tuple(items);
    Py_DECREF(items);
    if (seq == NULL) return false;
    el = s->els + idx;
    el->attr_start = s->n_attrs;
    bool ok = true;
    for (Py_ssize_t i = 0; ok && i < PyTuple_GET_SIZE(seq); i++) {
        PyObject *pair = PyTuple_GET_ITEM(seq, i);
        if (!PyTuple_Check(pair) || PyTuple_GET_SIZE(pair) != 2) continue;
        PyObject *k = PyTuple_GET_ITEM(pair, 0), *v = PyTuple_GET_ITEM(pair, 1);
        if (!PyUnicode_Check(k) || !PyUnicode_Check(v)) continue;
        uint32_t a = name_atom(b, k);
        if (a == NONE || !grow((void **)&s->attrs, &b->cap_attrs, s->n_attrs + 1, sizeof(snap_attr))) { ok = false; break; }
        uint8_t lang = LANG_NONE;
        if (a == ATOM_XML_LANG) lang = LANG_XML;  /* xml:lang without namespace processing, as in lxml.html */
        else if (a == ATOM_LANG) {
            /* lang without a namespace, or in the XML namespace: in other namespaces, it is not a language */
            if (PyUnicode_READ_CHAR(k, 0) != '{') lang = LANG_PLAIN;
            else if (PyUnicode_CompareWithASCIIString(k, "{http://www.w3.org/XML/1998/namespace}lang") == 0) lang = LANG_XML;
        }
        s->attrs[s->n_attrs++] = (snap_attr){.name = a, .lang = lang, .value = Py_NewRef(v)};
        if (a == ATOM_ID && el->id == NONE) {
            if ((el->id = value_atom(s, v)) == NONE) ok = false;
        } else if (a == ATOM_CLASS && el->class_count == 0) {
            ok = add_classes(b, el, v);
        }
    }
    Py_DECREF(seq);
    el->attr_count = s->n_attrs - el->attr_start;
    return ok;
}

typedef struct { PyObject *iter; uint32_t idx, last_child; } walk_frame;

typedef struct { PyObject *element; uint32_t idx, last_child; } lxml_frame;

/* For lxml: one iteration over the whole tree in document order, with each element's parent from
 * getparent(), is much faster than iterating the children of every element */
static bool
walk_lxml(builder *b, PyObject *root) {
    kg_Snapshot *s = b->s;
    PyObject *it = PyObject_CallMethodNoArgs(root, b->str_iter);
    if (it == NULL) return false;
    size_t cap = 64, depth = 0;
    lxml_frame *stack = PyMem_Malloc(cap * sizeof(lxml_frame));
    if (stack == NULL) { Py_DECREF(it); PyErr_NoMemory(); return false; }
    bool ok = true;
    PyObject *e;
    while (ok && (e = PyIter_Next(it)) != NULL) {
        PyObject *tag = PyObject_GetAttr(e, b->str_tag);
        if (tag == NULL) { Py_DECREF(e); ok = false; break; }
        if (PyUnicode_Check(tag)) {
            uint32_t parent = NONE;
            if (depth) {
                PyObject *p = PyObject_CallMethodNoArgs(e, b->str_getparent);
                if (p == NULL) ok = false;
                else {
                    /* Elements of the stack whose subtree has ended */
                    while (depth && stack[depth - 1].element != p) {
                        depth--;
                        s->els[stack[depth].idx].subtree_end = s->n;
                    }
                    Py_DECREF(p);
                    if (!depth) {
                        PyErr_SetString(PyExc_ValueError, "The tree changed while it was being read");
                        ok = false;
                    } else parent = stack[depth - 1].idx;
                }
            }
            const uint32_t idx = s->n;
            if (ok && (depth == 0) != (idx == 0)) {
                PyErr_SetString(PyExc_ValueError, "The tree changed while it was being read");
                ok = false;
            }
            ok = ok && add_element(b, e, tag, parent);
            if (ok && depth) {
                lxml_frame *f = stack + depth - 1;
                if (f->last_child != NONE) { s->els[f->last_child].next = idx; s->els[idx].prev = f->last_child; }
                f->last_child = idx;
            }
            if (ok && depth == cap) {
                lxml_frame *ns = PyMem_Realloc(stack, cap * 2 * sizeof(lxml_frame));
                if (ns == NULL) { PyErr_NoMemory(); ok = false; }
                else { stack = ns; cap *= 2; }
            }
            /* The elements array holds a reference, so the borrowed pointer stays valid */
            if (ok) stack[depth++] = (lxml_frame){s->elements[idx], idx, NONE};
        }
        Py_DECREF(tag);
        Py_DECREF(e);
    }
    if (ok && PyErr_Occurred()) ok = false;
    while (depth) { depth--; s->els[stack[depth].idx].subtree_end = s->n; }
    PyMem_Free(stack);
    Py_DECREF(it);
    return ok;
}

static bool
walk(builder *b, PyObject *root) {
    kg_Snapshot *s = b->s;
    int is_lxml = PyObject_HasAttrWithError(root, b->str_getparent);
    if (is_lxml < 0) return false;
    if (is_lxml) {
        if (!walk_lxml(b, root)) return false;
        goto counts;
    }
    PyObject *tag = PyObject_GetAttr(root, b->str_tag);
    if (tag == NULL) return false;
    if (!PyUnicode_Check(tag)) {
        Py_DECREF(tag);
        PyErr_SetString(PyExc_TypeError, "The root must be an element");
        return false;
    }
    bool ok = add_element(b, root, tag, NONE);
    Py_DECREF(tag);
    if (!ok) return false;
    size_t cap = 64, depth = 0;
    walk_frame *stack = PyMem_Malloc(cap * sizeof(walk_frame));
    if (stack == NULL) { PyErr_NoMemory(); return false; }
    PyObject *it = PyObject_GetIter(root);
    if (it == NULL) { PyMem_Free(stack); return false; }
    stack[depth++] = (walk_frame){it, 0, NONE};
    while (ok && depth) {
        walk_frame *f = stack + depth - 1;
        PyObject *child = PyIter_Next(f->iter);
        if (child == NULL) {
            if (PyErr_Occurred()) { ok = false; break; }
            s->els[f->idx].subtree_end = s->n;
            Py_DECREF(f->iter);
            depth--;
            continue;
        }
        PyObject *ctag = PyObject_GetAttr(child, b->str_tag);
        if (ctag == NULL) { Py_DECREF(child); ok = false; break; }
        if (PyUnicode_Check(ctag)) {
            /* An element: comments, processing instructions and entities have other tags */
            const uint32_t idx = s->n;
            ok = add_element(b, child, ctag, f->idx);
            if (ok) {
                if (f->last_child != NONE) { s->els[f->last_child].next = idx; s->els[idx].prev = f->last_child; }
                f->last_child = idx;
                PyObject *cit = PyObject_GetIter(child);
                if (cit == NULL) ok = false;
                else if (depth == cap) {
                    walk_frame *ns = PyMem_Realloc(stack, cap * 2 * sizeof(walk_frame));
                    if (ns == NULL) { Py_DECREF(cit); PyErr_NoMemory(); ok = false; }
                    else { stack = ns; cap *= 2; }
                }
                if (ok) stack[depth++] = (walk_frame){cit, idx, NONE};
            }
        }
        Py_DECREF(ctag);
        Py_DECREF(child);
    }
    while (depth) Py_DECREF(stack[--depth].iter);
    PyMem_Free(stack);
    if (!ok) return false;
counts:
    for (uint32_t i = 0; i < s->n; i++) {
        snap_el *el = s->els + i;
        el->sibling_count = el->parent == NONE ? 1 : s->els[el->parent].child_count;
    }
    return true;
}

/* An index from atoms to elements, by counting sort */
static bool
build_index(kg_Snapshot *s, snap_index *index, uint32_t count_of(const kg_Snapshot *, uint32_t, uint32_t *)) {
    index->off = PyMem_Calloc(s->n_atoms + 1, sizeof(uint32_t));
    if (index->off == NULL) { PyErr_NoMemory(); return false; }
    uint32_t total = 0;
    for (uint32_t i = 0; i < s->n; i++) total += count_of(s, i, NULL);
    /* The atom of each entry, and its element */
    uint32_t *atoms = PyMem_Malloc((total ? total : 1) * sizeof(uint32_t));
    if (atoms == NULL) { PyErr_NoMemory(); return false; }
    uint32_t *owner = PyMem_Malloc((total ? total : 1) * sizeof(uint32_t));
    if (owner == NULL) { PyMem_Free(atoms); PyErr_NoMemory(); return false; }
    uint32_t pos = 0;
    for (uint32_t i = 0; i < s->n; i++) {
        uint32_t k = count_of(s, i, atoms + pos);
        for (uint32_t j = 0; j < k; j++) owner[pos + j] = i;
        pos += k;
    }
    for (uint32_t j = 0; j < total; j++) index->off[atoms[j] + 1]++;
    for (uint32_t a = 0; a < s->n_atoms; a++) index->off[a + 1] += index->off[a];
    index->items = PyMem_Malloc((total ? total : 1) * sizeof(uint32_t));
    uint32_t *fill = PyMem_Malloc((s->n_atoms + 1) * sizeof(uint32_t));
    if (index->items == NULL || fill == NULL) {
        PyMem_Free(atoms); PyMem_Free(owner); PyMem_Free(fill);
        PyErr_NoMemory();
        return false;
    }
    memcpy(fill, index->off, (s->n_atoms + 1) * sizeof(uint32_t));
    for (uint32_t j = 0; j < total; j++) index->items[fill[atoms[j]]++] = owner[j];
    PyMem_Free(atoms); PyMem_Free(owner); PyMem_Free(fill);
    return true;
}

/* The atoms of element i for each index: the number of them, written to out if not NULL */
static uint32_t
name_atoms(const kg_Snapshot *s, uint32_t i, uint32_t *out) {
    if (out) out[0] = s->els[i].name;
    return 1;
}

static uint32_t
id_atoms(const kg_Snapshot *s, uint32_t i, uint32_t *out) {
    if (s->els[i].id == NONE) return 0;
    if (out) out[0] = s->els[i].id;
    return 1;
}

static uint32_t
class_atoms(const kg_Snapshot *s, uint32_t i, uint32_t *out) {
    const snap_el *el = s->els + i;
    /* An element can have the same class twice: index it once */
    uint32_t k = 0;
    for (uint32_t j = 0; j < el->class_count; j++) {
        const uint32_t a = s->classes[el->class_start + j];
        bool seen = false;
        for (uint32_t m = 0; m < j && !seen; m++) seen = s->classes[el->class_start + m] == a;
        if (!seen) { if (out) out[k] = a; k++; }
    }
    return k;
}

static void
Snapshot_dealloc(PyObject *o) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    PyTypeObject *tp = Py_TYPE(o);
    PyObject_GC_UnTrack(o);
    for (uint32_t i = 0; i < s->n; i++) Py_XDECREF(s->elements[i]);
    for (uint32_t i = 0; i < s->n_attrs; i++) Py_XDECREF(s->attrs[i].value);
    PyMem_Free(s->elements); PyMem_Free(s->els); PyMem_Free(s->classes); PyMem_Free(s->attrs);
    snap_index *indexes[] = {&s->by_name, &s->by_id, &s->by_class};
    for (size_t i = 0; i < 3; i++) { PyMem_Free(indexes[i]->off); PyMem_Free(indexes[i]->items); }
    Py_XDECREF(s->atoms);
    Py_XDECREF(s->index_map);
    PyMem_Free(s->type_index); PyMem_Free(s->type_count); PyMem_Free(s->lang);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static int
Snapshot_traverse(PyObject *o, visitproc visit, void *arg) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    Py_VISIT(Py_TYPE(o));
    for (uint32_t i = 0; i < s->n; i++) Py_VISIT(s->elements[i]);
    Py_VISIT(s->index_map);
    return 0;
}

static int
Snapshot_clear(PyObject *o) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    for (uint32_t i = 0; i < s->n; i++) Py_CLEAR(s->elements[i]);
    Py_CLEAR(s->index_map);
    /* No elements left: every index is out of range from now on */
    s->n = 0;
    return 0;
}

static PyObject *
Snapshot_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"root", "quirks_mode", NULL};
    PyObject *root;
    int quirks = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|p", kwlist, &root, &quirks)) return NULL;
    kg_Snapshot *s = (kg_Snapshot *)type->tp_alloc(type, 0);
    if (s == NULL) return NULL;
    s->quirks = quirks;
    builder b = {.s = s};
    bool ok = (s->atoms = PyDict_New()) != NULL && (b.tag_atoms = PyDict_New()) != NULL;
    ok = ok && (b.str_tag = PyUnicode_InternFromString("tag")) && (b.str_items = PyUnicode_InternFromString("items")) &&
         (b.str_getparent = PyUnicode_InternFromString("getparent")) && (b.str_iter = PyUnicode_InternFromString("iter"));
    for (int i = 0; ok && i < FIXED_ATOMS; i++) {
        PyObject *t = PyUnicode_InternFromString(fixed_atoms[i]);
        ok = t != NULL && atom_of(s, t) == (uint32_t)i;
        Py_XDECREF(t);
    }
    ok = ok && walk(&b, root);
    Py_XDECREF(b.tag_atoms);
    Py_XDECREF(b.str_tag); Py_XDECREF(b.str_items); Py_XDECREF(b.str_getparent); Py_XDECREF(b.str_iter);
    ok = ok && build_index(s, &s->by_name, name_atoms) && build_index(s, &s->by_id, id_atoms) &&
         build_index(s, &s->by_class, class_atoms);
    if (!ok) { Py_DECREF(s); return NULL; }
    return (PyObject *)s;
}

/* :empty needs text, which is read from the elements on first use. Reading it runs Python code (text and
 * tail can be properties), which lets other threads run, and on free-threaded builds suspends the critical
 * section. So the result is computed into a private array, and published, with the flag, under the critical
 * section only when complete, by the first thread to finish. Matching reads it only after seeing the flag. */
static bool
ensure_content(kg_Snapshot *s) {
    bool done;
    Py_BEGIN_CRITICAL_SECTION(s);
    done = s->content_done;
    Py_END_CRITICAL_SECTION();
    if (done) return true;
    uint8_t *content = PyMem_Calloc(s->n ? s->n : 1, 1);
    if (content == NULL) { PyErr_NoMemory(); return false; }
    bool ok = true;
    for (uint32_t i = 0; ok && i < s->n; i++) {
        if (s->els[i].first_child != NONE) { content[i] = 1; continue; }
        PyObject *e = Py_NewRef(s->elements[i]);
        PyObject *text = PyObject_GetAttrString(e, "text");
        if (text == NULL) { ok = false; Py_DECREF(e); break; }
        content[i] = text != Py_None && PyObject_IsTrue(text) == 1;
        Py_DECREF(text);
        /* The tails of comments and processing instructions are text too */
        PyObject *it = content[i] ? NULL : PyObject_GetIter(e);
        Py_DECREF(e);
        if (!content[i] && it == NULL) { ok = false; break; }
        PyObject *child;
        while (it != NULL && (child = PyIter_Next(it)) != NULL) {
            PyObject *tail = PyObject_GetAttrString(child, "tail");
            Py_DECREF(child);
            if (tail == NULL) { ok = false; break; }
            if (tail != Py_None && PyObject_IsTrue(tail) == 1) content[i] = 1;
            Py_DECREF(tail);
            if (content[i]) break;
        }
        if (it != NULL && PyErr_Occurred()) ok = false;
        Py_XDECREF(it);
    }
    if (ok) {
        Py_BEGIN_CRITICAL_SECTION(s);
        if (!s->content_done) {
            for (uint32_t i = 0; i < s->n; i++) s->els[i].has_content = content[i];
            s->content_done = true;
        }
        Py_END_CRITICAL_SECTION();
    }
    PyMem_Free(content);
    return ok;
}

/* The positions of elements among their siblings with the same name, for the of-type pseudo-classes */
static bool
ensure_types(kg_Snapshot *s) {
    bool ok = true;
    Py_BEGIN_CRITICAL_SECTION(s);
    if (s->type_index == NULL) {
        uint32_t *index = PyMem_Malloc((s->n ? s->n : 1) * sizeof(uint32_t));
        uint32_t *count = PyMem_Malloc((s->n ? s->n : 1) * sizeof(uint32_t));
        uint32_t *counter = PyMem_Calloc(s->n_atoms ? s->n_atoms : 1, sizeof(uint32_t));
        if (!index || !count || !counter) {
            PyMem_Free(index); PyMem_Free(count); PyMem_Free(counter);
            PyErr_NoMemory();
            ok = false;
        } else {
            for (uint32_t i = 0; i < s->n; i++) {
                /* Each group of siblings, from its first element */
                const snap_el *el = s->els + i;
                if (el->prev != NONE) continue;
                for (uint32_t c = i; c != NONE; c = s->els[c].next) index[c] = counter[s->els[c].name]++;
                for (uint32_t c = i; c != NONE; c = s->els[c].next) count[c] = counter[s->els[c].name];
                for (uint32_t c = i; c != NONE; c = s->els[c].next) counter[s->els[c].name] = 0;
            }
            PyMem_Free(counter);
            s->type_index = index;
            s->type_count = count;
        }
    }
    Py_END_CRITICAL_SECTION();
    return ok;
}

/* The language of each element: from its own xml:lang, else its own lang, else its parent's */
static bool
ensure_lang(kg_Snapshot *s) {
    bool ok = true;
    Py_BEGIN_CRITICAL_SECTION(s);
    if (!s->lang_done) {
        PyObject **lang = PyMem_Calloc(s->n ? s->n : 1, sizeof(PyObject *));
        if (lang == NULL) { PyErr_NoMemory(); ok = false; }
        else {
            for (uint32_t i = 0; i < s->n; i++) {
                const snap_el *el = s->els + i;
                PyObject *plain = NULL, *xml = NULL;
                for (uint32_t k = 0; k < el->attr_count; k++) {
                    const snap_attr *a = s->attrs + el->attr_start + k;
                    if (a->lang == LANG_XML && xml == NULL) xml = a->value;
                    else if (a->lang == LANG_PLAIN && plain == NULL) plain = a->value;
                }
                /* Document order puts parents first */
                lang[i] = xml ? xml : (plain ? plain : (el->parent == NONE ? NULL : lang[el->parent]));
            }
            s->lang = lang;
            s->lang_done = true;
        }
    }
    Py_END_CRITICAL_SECTION();
    return ok;
}

/* ---------------------------------------------------------------------------
 * Preparing a compiled selector for a snapshot
 * ------------------------------------------------------------------------- */

typedef enum {
    P_NONE = 0, P_ROOT, P_SCOPE, P_ANY_LINK, P_EMPTY, P_FIRST_CHILD, P_LAST_CHILD, P_ONLY_CHILD,
    P_FIRST_OF_TYPE, P_LAST_OF_TYPE, P_ONLY_OF_TYPE,
    P_IGNORED,  /* an inappropriate pseudo-class or pseudo-element, ignored: matches everything */
    P_IS, P_NOT, P_HAS,
    P_NTH_CHILD, P_NTH_LAST_CHILD, P_NTH_OF_TYPE, P_NTH_LAST_OF_TYPE,
} pseudo_code;

/* Keep in sync with INAPPROPRIATE_PSEUDO_CLASSES in src/kgcss/selectors.py */
static const char *const inappropriate[] = {
    "active", "after", "disabled", "visited", "link", "before", "focus", "first-letter", "enabled", "first-line",
    "hover", "checked", "target", "focus-within", "focus-visible", "target-within", NULL,
};

static bool
in_list(PyObject *name, const char *const *list) {
    for (; *list; list++) if (PyUnicode_CompareWithASCIIString(name, *list) == 0) return true;
    return false;
}

/* Per node, for one compiled selector against one snapshot */
typedef struct {
    const kg_CompiledSelector *c;
    uint32_t *atom;
    uint8_t *code;
    uint8_t *skip;      /* complex selectors in :is() that cannot be matched, and so match nothing */
    uint8_t **memo;     /* per complex selector: bits per (compound, element) known to fail */
    /* Per node, filled during a run: for nth with 'of S', each element's position among its siblings
     * that match S (0 if it does not), and their number; for :has(), 1 or 2 when known to match or not */
    uint32_t **nth_pos, **nth_total;
    uint8_t **has_cache;
    bool needs_content, needs_types, needs_lang;
} prepared;

static void
prepared_free(prepared *p) {
    for (uint32_t i = 0; i < p->c->n; i++) {
        if (p->memo) PyMem_RawFree(p->memo[i]);
        if (p->nth_pos) PyMem_RawFree(p->nth_pos[i]);
        if (p->nth_total) PyMem_RawFree(p->nth_total[i]);
        if (p->has_cache) PyMem_RawFree(p->has_cache[i]);
    }
    PyMem_Free(p->atom); PyMem_Free(p->code); PyMem_Free(p->skip); PyMem_Free(p->memo);
    PyMem_Free(p->nth_pos); PyMem_Free(p->nth_total); PyMem_Free(p->has_cache);
}

static uint32_t
lookup_atom(kg_Snapshot *s, PyObject *name, bool lower) {
    PyObject *key = lower ? kg_ascii_lower(name) : Py_NewRef(name);
    if (key == NULL) return NONE;
    PyObject *v = PyDict_GetItemWithError(s->atoms, key);
    Py_DECREF(key);
    if (v != NULL) return (uint32_t)PyLong_AsUnsignedLong(v);
    return PyErr_Occurred() ? NONE : ABSENT;
}

/* Prepare the subtree at node i. Returns a new reference to an error message for selectors that
 * cannot be matched, Py_None if fine, or NULL with an exception set. */
static PyObject *
prepare_node(prepared *p, kg_Snapshot *s, uint32_t i, bool ignore) {
    const sn_node *nodes = p->c->nodes, *node = nodes + i;
    switch ((sn_kind)node->kind) {
        case SN_COMPLEX: case SN_COMPOUND:
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                PyObject *r = prepare_node(p, s, k, ignore);
                if (r != Py_None) return r;
                Py_DECREF(r);
            }
            Py_RETURN_NONE;
        case SN_TYPE:
            if (node->name != NULL && (p->atom[i] = lookup_atom(s, node->name, true)) == NONE) return NULL;
            if (node->name == NULL) p->atom[i] = NONE;
            Py_RETURN_NONE;
        case SN_ID: case SN_CLASS:
            if ((p->atom[i] = lookup_atom(s, node->name, s->quirks)) == NONE) return NULL;
            Py_RETURN_NONE;
        case SN_ATTRIBUTE:
            if ((p->atom[i] = lookup_atom(s, node->name, true)) == NONE) return NULL;
            Py_RETURN_NONE;
        case SN_NESTING:
            p->code[i] = P_SCOPE;
            Py_RETURN_NONE;
        case SN_PSEUDO_CLASS: {
            static const struct { const char *name; pseudo_code code; } known[] = {
                {"root", P_ROOT}, {"scope", P_SCOPE}, {"any-link", P_ANY_LINK}, {"empty", P_EMPTY},
                {"first-child", P_FIRST_CHILD}, {"last-child", P_LAST_CHILD}, {"only-child", P_ONLY_CHILD},
                {"first-of-type", P_FIRST_OF_TYPE}, {"last-of-type", P_LAST_OF_TYPE}, {"only-of-type", P_ONLY_OF_TYPE},
            };
            for (size_t k = 0; k < sizeof known / sizeof known[0]; k++) {
                if (PyUnicode_CompareWithASCIIString(node->name, known[k].name) == 0) {
                    p->code[i] = (uint8_t)known[k].code;
                    if (known[k].code == P_EMPTY) p->needs_content = true;
                    if (known[k].code >= P_FIRST_OF_TYPE && known[k].code <= P_ONLY_OF_TYPE) p->needs_types = true;
                    Py_RETURN_NONE;
                }
            }
            if (ignore && in_list(node->name, inappropriate)) { p->code[i] = P_IGNORED; Py_RETURN_NONE; }
            return PyUnicode_FromFormat("The pseudo-class :%U is not supported", node->name);
        }
        case SN_SELECTOR_PSEUDO: {
            const bool negation = PyUnicode_CompareWithASCIIString(node->name, "not") == 0;
            const bool has = PyUnicode_CompareWithASCIIString(node->name, "has") == 0;
            p->code[i] = negation ? P_NOT : (has ? P_HAS : P_IS);
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                PyObject *r = prepare_node(p, s, k, ignore);
                if (r == NULL) return NULL;
                if (r != Py_None) {
                    /* :is(), :where() and :matches() take forgiving lists: such an argument matches nothing.
                     * In :not() and :has() it is an error. */
                    if (negation || has) return r;
                    p->skip[k] = 1;
                }
                Py_DECREF(r);
            }
            Py_RETURN_NONE;
        }
        case SN_NTH: {
            static const struct { const char *name; pseudo_code code; } nth[] = {
                {"nth-child", P_NTH_CHILD}, {"nth-last-child", P_NTH_LAST_CHILD},
                {"nth-of-type", P_NTH_OF_TYPE}, {"nth-last-of-type", P_NTH_LAST_OF_TYPE},
            };
            for (size_t k = 0; k < sizeof nth / sizeof nth[0]; k++) {
                if (PyUnicode_CompareWithASCIIString(node->name, nth[k].name) == 0) p->code[i] = (uint8_t)nth[k].code;
            }
            if (p->code[i] == P_NTH_OF_TYPE || p->code[i] == P_NTH_LAST_OF_TYPE) p->needs_types = true;
            /* The selectors of 'of S': not a forgiving list */
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                PyObject *r = prepare_node(p, s, k, ignore);
                if (r != Py_None) return r;
                Py_DECREF(r);
            }
            Py_RETURN_NONE;
        }
        case SN_LANG:
            p->needs_lang = true;
            Py_RETURN_NONE;
        case SN_FUNCTIONAL_PSEUDO:
            return PyUnicode_FromFormat("The pseudo-class :%U() is not supported", node->name);
        case SN_PSEUDO_ELEMENT:
            if (!ignore || (node->flags & F_FUNCTIONAL) || !in_list(node->name, inappropriate)) {
                return PyUnicode_FromFormat("The pseudo-element ::%U is not supported", node->name);
            }
            p->code[i] = P_IGNORED;
            /* The pseudo-classes after it */
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                PyObject *r = prepare_node(p, s, k, ignore);
                if (r != Py_None) return r;
                Py_DECREF(r);
            }
            Py_RETURN_NONE;
    }
    Py_RETURN_NONE;
}

/* Returns a new reference to an error message, Py_None, or NULL with an exception set */
static PyObject *
prepare(prepared *p, kg_Snapshot *s, const kg_CompiledSelector *c, bool ignore) {
    memset(p, 0, sizeof *p);
    p->c = c;
    p->atom = PyMem_Calloc(c->n, sizeof(uint32_t));
    p->code = PyMem_Calloc(c->n, 1);
    p->skip = PyMem_Calloc(c->n, 1);
    p->memo = PyMem_Calloc(c->n, sizeof(uint8_t *));
    p->nth_pos = PyMem_Calloc(c->n, sizeof(uint32_t *));
    p->nth_total = PyMem_Calloc(c->n, sizeof(uint32_t *));
    p->has_cache = PyMem_Calloc(c->n, sizeof(uint8_t *));
    if (!p->atom || !p->code || !p->skip || !p->memo || !p->nth_pos || !p->nth_total || !p->has_cache) {
        return PyErr_NoMemory();
    }
    return prepare_node(p, s, 0, ignore);
}

/* ---------------------------------------------------------------------------
 * Matching
 * ------------------------------------------------------------------------- */

typedef struct {
    const kg_Snapshot *s;
    prepared *p;
    uint32_t scope;
    bool oom;
} mctx;

static bool match_prefix(mctx *m, uint32_t x, uint32_t limit, uint32_t e, uint32_t anchor);

static inline bool
match_complex(mctx *m, uint32_t x, uint32_t e, uint32_t anchor) { return match_prefix(m, x, NONE, e, anchor); }

static inline Py_UCS4
uch(PyObject *u, Py_ssize_t i) { return PyUnicode_READ(PyUnicode_KIND(u), PyUnicode_DATA(u), i); }

static inline Py_UCS4
fold(Py_UCS4 c, bool ci) { return (ci && c >= 'A' && c <= 'Z') ? c + 32 : c; }


/* Whether index (1-based) is a*n + b for some integer n >= 0 */
static inline bool
anb_matches(int32_t a, int32_t b, uint32_t index) {
    const int64_t d = (int64_t)index - b;
    if (a == 0) return d == 0;
    return d % a == 0 && d / a >= 0;
}

/* The positions of the siblings of e among those that match the 'of S' selectors of the nth node i */
static bool
nth_of_positions(mctx *m, uint32_t i, uint32_t e) {
    const kg_Snapshot *s = m->s;
    prepared *p = m->p;
    const sn_node *nodes = p->c->nodes;
    if (p->nth_pos[i] == NULL) {
        p->nth_pos[i] = PyMem_RawMalloc((s->n ? s->n : 1) * sizeof(uint32_t));
        p->nth_total[i] = PyMem_RawMalloc((s->n ? s->n : 1) * sizeof(uint32_t));
        if (!p->nth_pos[i] || !p->nth_total[i]) { m->oom = true; return false; }
        for (uint32_t k = 0; k < s->n; k++) p->nth_pos[i][k] = NONE;
    }
    uint32_t *pos = p->nth_pos[i], *total = p->nth_total[i];
    if (pos[e] != NONE) return true;
    const uint32_t parent = s->els[e].parent;
    const uint32_t first = parent == NONE ? e : s->els[parent].first_child;
    uint32_t count = 0;
    for (uint32_t c = first; c != NONE; c = parent == NONE ? NONE : s->els[c].next) {
        bool matches = false;
        for (uint32_t k = i + 1; k < nodes[i].subtree_end && !matches; k = nodes[k].subtree_end) {
            matches = match_complex(m, k, c, NONE);
        }
        pos[c] = matches ? ++count : 0;
    }
    for (uint32_t c = first; c != NONE; c = parent == NONE ? NONE : s->els[c].next) total[c] = count;
    return !m->oom;
}

static bool
match_nth(mctx *m, uint32_t i, uint32_t e) {
    const kg_Snapshot *s = m->s;
    const sn_node *node = m->p->c->nodes + i;
    const snap_el *el = s->els + e;
    uint32_t index;
    switch ((pseudo_code)m->p->code[i]) {
        case P_NTH_CHILD: case P_NTH_LAST_CHILD:
            if (node->flags & F_HAS_OF) {
                if (!nth_of_positions(m, i, e)) return false;
                const uint32_t pos = m->p->nth_pos[i][e];
                if (pos == 0) return false;
                index = m->p->code[i] == P_NTH_CHILD ? pos : m->p->nth_total[i][e] - pos + 1;
            } else index = m->p->code[i] == P_NTH_CHILD ? el->sibling_index + 1 : el->sibling_count - el->sibling_index;
            break;
        case P_NTH_OF_TYPE: index = s->type_index[e] + 1; break;
        case P_NTH_LAST_OF_TYPE: index = s->type_count[e] - s->type_index[e]; break;
        default: return false;
    }
    return anb_matches(node->a, node->b, index);
}

/* The next subtag of a language tag or range: [*start, *end), from pos. Returns false at the end. */
static bool
subtag(PyObject *text, Py_ssize_t *pos, Py_ssize_t *start, Py_ssize_t *end) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(text);
    if (*pos > n) return false;
    *start = *pos;
    Py_ssize_t i = *pos;
    while (i < n && uch(text, i) != '-') i++;
    *end = i;
    *pos = i + 1;
    return true;
}

static bool
subtags_equal(PyObject *a, Py_ssize_t as, Py_ssize_t ae, PyObject *b, Py_ssize_t bs, Py_ssize_t be) {
    if (ae - as != be - bs) return false;
    for (Py_ssize_t k = 0; k < ae - as; k++) if (fold(uch(a, as + k), true) != fold(uch(b, bs + k), true)) return false;
    return true;
}

/* Whether a language tag matches a language range, by extended filtering (RFC 4647, section 3.3.2):
 * fr matches fr and fr-CH, *-CH matches de-CH and de-Latn-CH. ASCII case-insensitive. */
static bool
language_matches(PyObject *tag, PyObject *range) {
    if (!PyUnicode_GET_LENGTH(tag) || !PyUnicode_GET_LENGTH(range)) return false;
    Py_ssize_t tp = 0, rp = 0, ts = 0, te = 0, rs = 0, re = 0;
    subtag(tag, &tp, &ts, &te);
    subtag(range, &rp, &rs, &re);
    const bool wildcard = re - rs == 1 && uch(range, rs) == '*';
    if (!wildcard && !subtags_equal(tag, ts, te, range, rs, re)) return false;
    bool have_tag = subtag(tag, &tp, &ts, &te);
    while (subtag(range, &rp, &rs, &re)) {
        if (re - rs == 1 && uch(range, rs) == '*') continue;
        if (re == rs) return false;  /* an empty subtag in the range, as in fr- */
        while (true) {
            if (!have_tag) return false;
            if (subtags_equal(tag, ts, te, range, rs, re)) { have_tag = subtag(tag, &tp, &ts, &te); break; }
            if (te - ts == 1) return false;  /* a singleton ends the search */
            have_tag = subtag(tag, &tp, &ts, &te);
        }
    }
    return true;
}

/* The elements that can match the compound at node c: from the index for its most selective part, in
 * document order, or every element (*all) */
static void
compound_candidates(const kg_Snapshot *s, const prepared *p, uint32_t last, const uint32_t **items, uint32_t *count,
                    bool *all) {
    const sn_node *nodes = p->c->nodes;
    const snap_index *best = NULL;
    uint32_t best_atom = 0, best_count = UINT32_MAX;
    *all = true;
    for (uint32_t k = last + 1; k < nodes[last].subtree_end; k = nodes[k].subtree_end) {
        const snap_index *index = NULL;
        switch (nodes[k].kind) {
            case SN_ID: index = &s->by_id; break;
            case SN_CLASS: index = &s->by_class; break;
            case SN_TYPE: if (p->atom[k] != NONE) index = &s->by_name; break;
            default: break;
        }
        if (index == NULL) continue;
        const uint32_t a = p->atom[k];
        if (a == ABSENT) { *all = false; *count = 0; *items = NULL; return; }
        const uint32_t n = index->off[a + 1] - index->off[a];
        if (n < best_count) { best = index; best_atom = a; best_count = n; }
    }
    if (best == NULL) return;
    *all = false;
    *items = best->items + best->off[best_atom];
    *count = best_count;
}

/* The candidates for the complex selector at node x: those of its last compound */
static void
candidates(const kg_Snapshot *s, const prepared *p, uint32_t x, const uint32_t **items, uint32_t *count, bool *all) {
    const sn_node *nodes = p->c->nodes;
    uint32_t last = x + 1;
    for (uint32_t k = x + 1; k < nodes[x].subtree_end; k = nodes[k].subtree_end) last = k;
    compound_candidates(s, p, last, items, count, all);
}

/* Whether the relative selector at node x matches an element of which anchor is the subject of :has() */
static bool
has_matches(mctx *m, uint32_t x, uint32_t anchor) {
    const kg_Snapshot *s = m->s;
    const sn_node *nodes = m->p->c->nodes;
    const uint8_t comb = nodes[x + 1].op;  /* before the first compound */
    uint32_t lo, hi;
    if (comb == C_DESCENDANT || comb == C_CHILD) { lo = anchor + 1; hi = s->els[anchor].subtree_end; }
    else {
        /* The following siblings, and their descendants */
        const uint32_t parent = s->els[anchor].parent;
        if (parent == NONE) return false;
        lo = s->els[anchor].subtree_end;
        hi = s->els[parent].subtree_end;
    }
    const uint32_t *items;
    uint32_t count;
    bool all;
    candidates(s, m->p, x, &items, &count, &all);
    if (all) {
        for (uint32_t e = lo; e < hi; e++) {
            if (match_complex(m, x, e, anchor)) return true;
            if (m->oom) return false;
        }
        return false;
    }
    /* Index lists are in document order: start at the first one in range */
    uint32_t a = 0, b = count;
    while (a < b) {
        const uint32_t mid = a + (b - a) / 2;
        if (items[mid] < lo) a = mid + 1; else b = mid;
    }
    for (uint32_t j = a; j < count && items[j] < hi; j++) {
        if (match_complex(m, x, items[j], anchor)) return true;
        if (m->oom) return false;
    }
    return false;
}

/* Whether hay[start:start+len(needle)] equals needle */
static bool
equal_at(PyObject *hay, Py_ssize_t start, PyObject *needle, bool ci) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(needle);
    if (start < 0 || start + n > PyUnicode_GET_LENGTH(hay)) return false;
    for (Py_ssize_t i = 0; i < n; i++) if (fold(uch(hay, start + i), ci) != fold(uch(needle, i), ci)) return false;
    return true;
}

static bool
attribute_matches(const sn_node *node, PyObject *v, bool ci) {
    PyObject *want = node->value;
    const Py_ssize_t vn = PyUnicode_GET_LENGTH(v), wn = want ? PyUnicode_GET_LENGTH(want) : 0;
    switch (node->op) {
        case A_EXISTS: return true;
        case A_EQUALS: return vn == wn && equal_at(v, 0, want, ci);
        case A_DASH: return equal_at(v, 0, want, ci) && (vn == wn || uch(v, wn) == '-');
        case A_PREFIX: return wn && equal_at(v, 0, want, ci);
        case A_SUFFIX: return wn && equal_at(v, vn - wn, want, ci);
        case A_SUBSTRING:
            if (!wn) return false;
            for (Py_ssize_t i = 0; i + wn <= vn; i++) if (equal_at(v, i, want, ci)) return true;
            return false;
        case A_INCLUDES: {
            if (!wn) return false;
            for (Py_ssize_t i = 0; i < wn; i++) if (is_ascii_ws(uch(want, i))) return false;
            Py_ssize_t i = 0;
            while (i < vn) {
                while (i < vn && is_ascii_ws(uch(v, i))) i++;
                Py_ssize_t start = i;
                while (i < vn && !is_ascii_ws(uch(v, i))) i++;
                if (i - start == wn && equal_at(v, start, want, ci)) return true;
            }
            return false;
        }
    }
    return false;
}

static bool
match_simple(mctx *m, uint32_t i, uint32_t e) {
    const kg_Snapshot *s = m->s;
    const sn_node *nodes = m->p->c->nodes, *node = nodes + i;
    const snap_el *el = s->els + e;
    const uint32_t atom = m->p->atom[i];
    switch ((sn_kind)node->kind) {
        case SN_TYPE: return atom == NONE || el->name == atom;
        case SN_ID: return el->id == atom;
        case SN_CLASS:
            for (uint32_t k = 0; k < el->class_count; k++) if (s->classes[el->class_start + k] == atom) return true;
            return false;
        case SN_ATTRIBUTE: {
            if (atom == ABSENT) return false;
            const bool ci = node->flags & F_CASE_I;
            for (uint32_t k = 0; k < el->attr_count; k++) {
                const snap_attr *a = s->attrs + el->attr_start + k;
                if (a->name == atom && attribute_matches(node, a->value, ci)) return true;
            }
            return false;
        }
        case SN_NESTING: return e == m->scope;
        case SN_PSEUDO_CLASS:
            switch ((pseudo_code)m->p->code[i]) {
                case P_ROOT: return el->parent == NONE;
                case P_SCOPE: return e == m->scope;
                case P_ANY_LINK: {
                    if (el->name != ATOM_A && el->name != ATOM_AREA && el->name != ATOM_LINK) return false;
                    for (uint32_t k = 0; k < el->attr_count; k++) if (s->attrs[el->attr_start + k].name == ATOM_HREF) return true;
                    return false;
                }
                case P_EMPTY: return !el->has_content;
                case P_FIRST_CHILD: return el->prev == NONE;
                case P_LAST_CHILD: return el->next == NONE;
                case P_ONLY_CHILD: return el->prev == NONE && el->next == NONE;
                case P_FIRST_OF_TYPE: return s->type_index[e] == 0;
                case P_LAST_OF_TYPE: return s->type_index[e] + 1 == s->type_count[e];
                case P_ONLY_OF_TYPE: return s->type_count[e] == 1;
                case P_IGNORED: return true;
                default: return false;
            }
        case SN_SELECTOR_PSEUDO: {
            if (m->p->code[i] == P_HAS) {
                uint8_t *cache = m->p->has_cache[i];
                if (cache == NULL && (cache = m->p->has_cache[i] = PyMem_RawCalloc(s->n ? s->n : 1, 1)) == NULL) {
                    m->oom = true;
                    return false;
                }
                if (cache[e]) return cache[e] == 1;
                bool ans = false;
                for (uint32_t k = i + 1; k < node->subtree_end && !ans; k = nodes[k].subtree_end) ans = has_matches(m, k, e);
                cache[e] = ans ? 1 : 2;
                return ans;
            }
            const bool negation = m->p->code[i] == P_NOT;
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) {
                if (m->p->skip[k]) continue;
                if (match_complex(m, k, e, NONE)) return !negation;
            }
            return negation;
        }
        case SN_NTH: return match_nth(m, i, e);
        case SN_LANG: {
            PyObject *lang = s->lang[e];
            if (lang == NULL) return false;
            for (Py_ssize_t k = 0; k < PyTuple_GET_SIZE(node->value); k++) {
                if (language_matches(lang, PyTuple_GET_ITEM(node->value, k))) return true;
            }
            return false;
        }
        case SN_PSEUDO_ELEMENT:
            /* Only the ignored ones get here: they match their originating element */
            for (uint32_t k = i + 1; k < node->subtree_end; k = nodes[k].subtree_end) if (!match_simple(m, k, e)) return false;
            return true;
        default:
            return false;
    }
}

static bool
match_compound(mctx *m, uint32_t ci, uint32_t e) {
    const sn_node *nodes = m->p->c->nodes;
    for (uint32_t k = ci + 1; k < nodes[ci].subtree_end; k = nodes[k].subtree_end) if (!match_simple(m, k, e)) return false;
    return true;
}

/* The first candidate for the compound before one that matched x, given the combinator between them */
static inline uint32_t
first_candidate(const kg_Snapshot *s, uint8_t comb, uint32_t x) {
    switch (comb) {
        case C_DESCENDANT: case C_CHILD: return s->els[x].parent;
        case C_NEXT_SIBLING: case C_SUBSEQUENT_SIBLING: return s->els[x].prev;
    }
    return NONE;
}

static inline uint32_t
next_candidate(const kg_Snapshot *s, uint8_t comb, uint32_t y) {
    switch (comb) {
        case C_DESCENDANT: return s->els[y].parent;
        case C_SUBSEQUENT_SIBLING: return s->els[y].prev;
    }
    return NONE;
}

#define MAX_STACK_COMPOUNDS 32

/* Whether the leftmost compound, matched at x, relates to anchor as the combinator before it says */
static inline bool
related_to_anchor(const kg_Snapshot *s, uint8_t comb, uint32_t anchor, uint32_t x) {
    switch (comb) {
        case C_DESCENDANT: return anchor < x && x < s->els[anchor].subtree_end;
        case C_CHILD: return s->els[x].parent == anchor;
        case C_NEXT_SIBLING: return s->els[x].prev == anchor;
        case C_SUBSEQUENT_SIBLING: return s->els[x].parent == s->els[anchor].parent && anchor < x;
    }
    return true;
}

/* Whether the complex selector at node x matches element e: all its compounds, or with limit, only
 * the first limit of them. For a relative selector (in :has()), anchor is the element its leftmost
 * compound must relate to, otherwise NONE. */
static bool
match_prefix(mctx *m, uint32_t x, uint32_t limit, uint32_t e, uint32_t anchor) {
    const sn_node *nodes = m->p->c->nodes;
    const kg_Snapshot *s = m->s;
    /* The compounds, left to right */
    uint32_t small_c[MAX_STACK_COMPOUNDS], small_f[MAX_STACK_COMPOUNDS];
    uint32_t *comp = small_c, *frames = small_f, K = 0;
    for (uint32_t k = x + 1; k < nodes[x].subtree_end; k = nodes[k].subtree_end) K++;
    /* The memo is for all the compounds, whatever the limit, so that calls can share it */
    const uint32_t all_compounds = K;
    if (K > MAX_STACK_COMPOUNDS) {
        comp = PyMem_RawMalloc(K * sizeof(uint32_t));
        frames = PyMem_RawMalloc(K * sizeof(uint32_t));
        if (!comp || !frames) { PyMem_RawFree(comp); PyMem_RawFree(frames); m->oom = true; return false; }
    }
    K = 0;
    for (uint32_t k = x + 1; k < nodes[x].subtree_end; k = nodes[k].subtree_end) comp[K++] = k;
    if (limit < K) K = limit;
    bool ans = false;
    uint32_t k = K - 1;
    const uint8_t first_comb = nodes[comp[0]].op;
    if (!match_compound(m, comp[k], e)) goto done;
    if (k == 0) { ans = anchor == NONE || related_to_anchor(s, first_comb, anchor, e); goto done; }
    frames[k] = e;
    /* Failure memo: bit (j, el) set when compounds 0..j cannot match with compound j at el. Not for
     * relative selectors, where that also depends on the anchor. */
    uint8_t *memo = anchor == NONE ? m->p->memo[x] : NULL;
    bool backtracks = false;
    for (uint32_t j = 1; j < K; j++) {
        const uint8_t comb = nodes[comp[j]].op;
        if (comb == C_DESCENDANT || comb == C_SUBSEQUENT_SIBLING) backtracks = true;
    }
    if (backtracks && memo == NULL && anchor == NONE) {
        memo = m->p->memo[x] = PyMem_RawCalloc(((size_t)all_compounds * s->n + 7) / 8, 1);
        if (memo == NULL) { m->oom = true; goto done; }
    }
#define MEMO_BIT(j, el) ((size_t)(j) * s->n + (el))
#define FAILED(j, el) (memo && (memo[MEMO_BIT(j, el) / 8] & (1u << (MEMO_BIT(j, el) % 8))))
#define MARK_FAILED(j, el) do { if (memo) memo[MEMO_BIT(j, el) / 8] |= (uint8_t)(1u << (MEMO_BIT(j, el) % 8)); } while (0)
    uint32_t cand = first_candidate(s, nodes[comp[k]].op, e);
    while (true) {
        if (cand == NONE) {
            /* No more candidates for compound k - 1: backtrack */
            if (k < K - 1) MARK_FAILED(k, frames[k]);
            k++;
            if (k >= K) break;
            cand = next_candidate(s, nodes[comp[k]].op, frames[k - 1]);
            continue;
        }
        if (!FAILED(k - 1, cand) && match_compound(m, comp[k - 1], cand) &&
            (k > 1 || anchor == NONE || related_to_anchor(s, first_comb, anchor, cand))) {
            k--;
            frames[k] = cand;
            if (k == 0) { ans = true; break; }
            cand = first_candidate(s, nodes[comp[k]].op, cand);
        } else cand = next_candidate(s, nodes[comp[k]].op, cand);
    }
#undef MEMO_BIT
#undef FAILED
#undef MARK_FAILED
done:
    if (comp != small_c) { PyMem_RawFree(comp); PyMem_RawFree(frames); }
    return ans;
}

/* The index of the lowest set bit of a non-zero word */
static inline unsigned
lowest_bit(uint64_t bits) {
#if defined(__GNUC__) || defined(__clang__)
    return (unsigned)__builtin_ctzll(bits);
#else
    unsigned n = 0;
    while (!(bits & 1)) { bits >>= 1; n++; }
    return n;
#endif
}

typedef struct {
    kg_Snapshot *s;
    prepared *ps;
    Py_ssize_t count;
    uint32_t scope, lo, hi, target;  /* target: a single element to test, or NONE */
    bool first_only;
    uint64_t *found;  /* bitset over elements */
    bool oom, any;
} run_args;

static inline void
found_if_matches(run_args *r, mctx *m, uint32_t compound, uint32_t y) {
    if (y < r->lo || y >= r->hi || (r->found[y / 64] & ((uint64_t)1 << (y % 64)))) return;
    if (match_compound(m, compound, y)) { r->found[y / 64] |= (uint64_t)1 << (y % 64); r->any = true; }
}

/* For selectors like .rare > p, whose last compound matches many elements while the one before it
 * matches few: find what matches up to the last combinator, then test the elements it leads to.
 * Returns false, doing nothing, when matching from the right is better. */
static bool
match_from_left(run_args *r, mctx *m) {
    const kg_Snapshot *s = r->s;
    const sn_node *nodes = m->p->c->nodes;
    uint32_t K = 0, last = 0, before = 0;
    for (uint32_t k = 1; k < nodes[0].subtree_end; k = nodes[k].subtree_end) { before = last; last = k; K++; }
    if (K < 2) return false;
    const uint8_t comb = nodes[last].op;
    const uint32_t *right_items, *left_items;
    uint32_t right_count, left_count;
    bool right_all, left_all;
    compound_candidates(s, m->p, last, &right_items, &right_count, &right_all);
    compound_candidates(s, m->p, before, &left_items, &left_count, &left_all);
    if (right_all) right_count = r->hi - r->lo;
    if (left_all) left_count = s->n;
    if ((uint64_t)left_count * 4 >= right_count) return false;
    uint32_t covered_end = 0, sibling_parent = NONE;
    for (uint32_t j = 0; j < left_count && !(r->first_only && r->any) && !m->oom; j++) {
        const uint32_t a = left_all ? j : left_items[j];
        if (!match_prefix(m, 0, K - 1, a, NONE)) continue;
        const snap_el *el = s->els + a;
        switch (comb) {
            case C_CHILD:
                for (uint32_t y = el->first_child; y != NONE; y = s->els[y].next) found_if_matches(r, m, last, y);
                break;
            case C_DESCENDANT:
                /* The descendants of a are those of an ancestor already done */
                if (a < covered_end) break;
                for (uint32_t y = a + 1; y < el->subtree_end; y++) found_if_matches(r, m, last, y);
                covered_end = el->subtree_end;
                break;
            case C_NEXT_SIBLING:
                if (el->next != NONE) found_if_matches(r, m, last, el->next);
                break;
            case C_SUBSEQUENT_SIBLING:
                /* The siblings after a are among those after an earlier sibling already done */
                if (el->parent != NONE && el->parent == sibling_parent) break;
                for (uint32_t y = el->next; y != NONE; y = s->els[y].next) found_if_matches(r, m, last, y);
                sibling_parent = el->parent;
                break;
        }
    }
    return true;
}

static void
run(run_args *r) {
    const kg_Snapshot *s = r->s;
    for (Py_ssize_t q = 0; q < r->count && !(r->first_only && r->any); q++) {
        mctx m = {.s = s, .p = r->ps + q, .scope = r->scope};
        /* A selector that starts with a combinator, not from a nested rule, is relative to :scope */
        const uint32_t anchor = (r->ps[q].c->nodes[0].flags & F_RELATIVE) ? r->scope : NONE;
        if (r->target != NONE) {
            if (match_complex(&m, 0, r->target, anchor)) r->any = true;
        } else if (anchor == NONE && match_from_left(r, &m)) {
            /* Done */
        } else {
            const uint32_t *items = NULL;
            uint32_t count = 0;
            bool all;
            candidates(s, r->ps + q, 0, &items, &count, &all);
            const uint32_t n = all ? r->hi - r->lo : count;
            for (uint32_t j = 0; j < n && !(r->first_only && r->any); j++) {
                const uint32_t e = all ? r->lo + j : items[j];
                if (e < r->lo || e >= r->hi || (r->found[e / 64] & ((uint64_t)1 << (e % 64)))) continue;
                if (match_complex(&m, 0, e, anchor)) { r->found[e / 64] |= (uint64_t)1 << (e % 64); r->any = true; }
                if (m.oom) break;
            }
        }
        if (m.oom) { r->oom = true; return; }
    }
}

/* match(selectors, ignore_inappropriate, scope, root, target, first_only)
 * selectors: a sequence of CompiledSelector. root: index of the element whose subtree is searched.
 * target: index of a single element to test, or -1. Returns the list of matching elements in document
 * order, or with first_only or a target, a bool. Raises MatchError(message) for selectors that cannot
 * be matched. */
static PyObject *
Snapshot_match(PyObject *o, PyObject *args) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    kg_module_state *state = PyType_GetModuleState(Py_TYPE(o));
    PyObject *selectors;
    int ignore, first_only;
    long scope, root, target;
    if (!PyArg_ParseTuple(args, "Oplllp", &selectors, &ignore, &scope, &root, &target, &first_only)) return NULL;
    if (scope < 0 || (uint32_t)scope >= s->n || root < 0 || (uint32_t)root >= s->n || target >= (long)s->n) {
        PyErr_SetString(PyExc_IndexError, "element index out of range");
        return NULL;
    }
    /* A tuple of our own: the selectors must stay alive while they are used, which includes Python code run
     * while preparing and the time the GIL is released, so they cannot be borrowed from the caller's list */
    PyObject *seq = PySequence_Tuple(selectors);
    if (seq == NULL) return NULL;
    const Py_ssize_t count = PyTuple_GET_SIZE(seq);
    prepared *ps = PyMem_Calloc(count ? count : 1, sizeof(prepared));
    if (ps == NULL) { Py_DECREF(seq); return PyErr_NoMemory(); }
    PyObject *ans = NULL;
    bool needs_content = false, needs_types = false, needs_lang = false;
    Py_ssize_t prepared_count = 0;
    for (Py_ssize_t q = 0; q < count; q++) {
        PyObject *c = PyTuple_GET_ITEM(seq, q);
        if (!Py_IS_TYPE(c, state->CompiledSelector_type)) {
            PyErr_SetString(PyExc_TypeError, "selectors must be CompiledSelector objects");
            goto end;
        }
        PyObject *err = prepare(ps + q, s, (const kg_CompiledSelector *)c, ignore);
        prepared_count = q + 1;
        if (err == NULL) goto end;
        if (err != Py_None) { PyErr_SetObject(state->MatchError, err); Py_DECREF(err); goto end; }
        Py_DECREF(err);
        needs_content |= ps[q].needs_content;
        needs_types |= ps[q].needs_types;
        needs_lang |= ps[q].needs_lang;
    }
    /* These check their flags under the critical section, which also orders the reads of what they built */
    if (needs_content && !ensure_content(s)) goto end;
    if (needs_types && !ensure_types(s)) goto end;
    if (needs_lang && !ensure_lang(s)) goto end;
    run_args r = {
        .s = s, .ps = ps, .count = count, .scope = (uint32_t)scope, .lo = (uint32_t)root,
        .hi = s->els[root].subtree_end, .target = target < 0 ? NONE : (uint32_t)target, .first_only = first_only,
    };
    r.found = PyMem_RawCalloc((s->n + 63) / 64, sizeof(uint64_t));
    if (r.found == NULL) { PyErr_NoMemory(); goto end; }
    /* Matching only reads the snapshot, so other threads can run meanwhile */
    Py_BEGIN_ALLOW_THREADS
    run(&r);
    Py_END_ALLOW_THREADS
    if (r.oom) PyErr_NoMemory();
    else if (first_only || r.target != NONE) ans = Py_NewRef(r.any ? Py_True : Py_False);
    else {
        ans = PyList_New(0);
        /* A word at a time: usually few elements match */
        for (uint32_t w = r.lo / 64; ans != NULL && r.any && w <= (r.hi - 1) / 64; w++) {
            for (uint64_t bits = r.found[w]; bits && ans != NULL; bits &= bits - 1) {
                const uint32_t e = w * 64 + (uint32_t)lowest_bit(bits);
                if (e >= r.lo && e < r.hi && PyList_Append(ans, s->elements[e]) != 0) Py_CLEAR(ans);
            }
        }
    }
    PyMem_RawFree(r.found);
end:
    for (Py_ssize_t q = 0; q < prepared_count; q++) prepared_free(ps + q);
    PyMem_Free(ps);
    Py_DECREF(seq);
    return ans;
}

/* index_of(element) -> int: the index of an element of the snapshot. Raises KeyError. */
static PyObject *
Snapshot_index_of(PyObject *o, PyObject *element) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    PyObject *map = NULL;
    Py_BEGIN_CRITICAL_SECTION(s);
    map = Py_XNewRef(s->index_map);
    Py_END_CRITICAL_SECTION();
    if (map == NULL) {
        /* Built outside the critical section: hashing elements runs Python code. Another thread may build
         * one at the same time; the first to finish is kept. */
        map = PyDict_New();
        for (uint32_t i = 0; map != NULL && i < s->n; i++) {
            PyObject *num = PyLong_FromUnsignedLong(i);
            if (num == NULL || PyDict_SetItem(map, s->elements[i], num) != 0) Py_CLEAR(map);
            Py_XDECREF(num);
        }
        if (map == NULL) return NULL;
        Py_BEGIN_CRITICAL_SECTION(s);
        if (s->index_map == NULL) s->index_map = Py_NewRef(map);
        else Py_SETREF(map, Py_NewRef(s->index_map));
        Py_END_CRITICAL_SECTION();
    }
    PyObject *ans = NULL;
    if (PyDict_GetItemRef(map, element, &ans) == 0) PyErr_SetObject(PyExc_KeyError, element);
    Py_DECREF(map);
    return ans;
}

static PyObject *
Snapshot_get(PyObject *o, void *closure) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    if (closure == NULL) return PyLong_FromUnsignedLong(s->n);
    return PyBool_FromLong(s->quirks);
}

static PyObject *
Snapshot_element(PyObject *o, PyObject *arg) {
    kg_Snapshot *s = (kg_Snapshot *)o;
    Py_ssize_t i = PyLong_AsSsize_t(arg);
    if (i == -1 && PyErr_Occurred()) return NULL;
    if (i < 0 || (size_t)i >= s->n) { PyErr_SetString(PyExc_IndexError, "element index out of range"); return NULL; }
    return Py_NewRef(s->elements[i]);
}

static PyMethodDef Snapshot_methods[] = {
    {"match", Snapshot_match, METH_VARARGS,
     "match(selectors, ignore_inappropriate, scope, root, target, first_only)\n\n"
     "Match CompiledSelectors, see kgcss.matching.Select."},
    {"index_of", Snapshot_index_of, METH_O, "index_of(element) -> int"},
    {"element", Snapshot_element, METH_O, "element(index) -> the element at this index, in document order"},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Snapshot_getset[] = {
    {"count", Snapshot_get, NULL, "The number of elements", NULL},
    {"quirks_mode", Snapshot_get, NULL, "True if ids and classes are ASCII case-insensitive", (void *)1},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyType_Slot Snapshot_slots[] = {
    {Py_tp_new, Snapshot_new},
    {Py_tp_dealloc, Snapshot_dealloc},
    {Py_tp_traverse, Snapshot_traverse},
    {Py_tp_clear, Snapshot_clear},
    {Py_tp_methods, Snapshot_methods},
    {Py_tp_getset, Snapshot_getset},
    {Py_tp_doc, (void *)"Snapshot(root, quirks_mode=False): the elements of a tree, for matching. Not a public API, "
                        "see kgcss.matching.Select."},
    {0, NULL}
};

static PyType_Spec Snapshot_spec = {
    .name = "kgcss.Snapshot",
    .basicsize = sizeof(kg_Snapshot),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_HAVE_GC,
    .slots = Snapshot_slots,
};

int
kg_select_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->Snapshot_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &Snapshot_spec, NULL);
    if (state->Snapshot_type == NULL || PyModule_AddType(module, state->Snapshot_type) != 0) return -1;
    state->MatchError = PyErr_NewExceptionWithDoc(
        "kgcss._kgcss.MatchError", "Raised by Snapshot.match(). Not a public API: kgcss raises ExpressionError.",
        PyExc_ValueError, NULL);
    if (state->MatchError == NULL) return -1;
    return PyModule_AddObjectRef(module, "MatchError", state->MatchError);
}

int
kg_select_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->Snapshot_type);
    Py_VISIT(state->MatchError);
    return 0;
}

void
kg_select_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->Snapshot_type);
    Py_CLEAR(state->MatchError);
}
