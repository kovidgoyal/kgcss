/*
 * records.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * Small mutable records for the linter's ports of PostCSS and
 * postcss-selector-parser: the raws, spaces and source positions of their
 * nodes. Linting a large sheet makes millions of them, and as instances of
 * Python classes every one was tracked by the garbage collector, whose
 * collections then took more time than the linting itself.
 *
 * These records are not tracked, which is safe because they cannot be part
 * of a reference cycle: a field holds None, an exact str, int or bool, or a
 * record of a type that is defined earlier in RECORDS below. The setters
 * enforce that, and the types cannot be subclassed.
 */

#include "kgcss.h"
#include "objects.h"

typedef enum { RF_STR, RF_INT, RF_BOOL, RF_RECORD } record_kind;
typedef enum { RD_NONE, RD_EMPTY, RD_REQUIRED } record_default;

typedef struct {
    const char *name;
    Py_ssize_t index;
    record_kind kind;
    /* For RF_RECORD: the kg_record_type of the records it holds */
    int record;
    /* Whether None is a valid value (always true for RF_STR and RF_RECORD) */
    bool nullable;
    record_default dflt;
} record_field;

typedef struct {
    const char *name;
    const record_field *fields;
    Py_ssize_t count;
    /* The number of fields that can be given as positional arguments */
    Py_ssize_t positional;
} record_def;

#define STR(i, n) {n, i, RF_STR, -1, true, RD_NONE}
#define STR_EMPTY(i, n) {n, i, RF_STR, -1, true, RD_EMPTY}
#define STR_REQUIRED(i, n) {n, i, RF_STR, -1, false, RD_REQUIRED}
#define INT_REQUIRED(i, n) {n, i, RF_INT, -1, false, RD_REQUIRED}
#define BOOL(i, n) {n, i, RF_BOOL, -1, true, RD_NONE}
#define RECORD(i, n, r) {n, i, RF_RECORD, r, true, RD_NONE}

static const record_field SelectorPosition_fields[] = {INT_REQUIRED(0, "line"), INT_REQUIRED(1, "column")};
static const record_field SelectorSource_fields[] = {
    RECORD(0, "start", KG_RECORD_SELECTOR_POSITION), RECORD(1, "end", KG_RECORD_SELECTOR_POSITION)};
static const record_field SelectorPartSpaces_fields[] = {STR(0, "before"), STR(1, "after")};
static const record_field SelectorSpaces_fields[] = {
    STR_EMPTY(0, "before"), STR_EMPTY(1, "after"),
    RECORD(2, "attribute", KG_RECORD_SELECTOR_PART_SPACES), RECORD(3, "operator", KG_RECORD_SELECTOR_PART_SPACES),
    RECORD(4, "value", KG_RECORD_SELECTOR_PART_SPACES), RECORD(5, "insensitive", KG_RECORD_SELECTOR_PART_SPACES)};
static const record_field SelectorRawSpaces_fields[] = {
    STR(0, "before"), STR(1, "after"),
    RECORD(2, "attribute", KG_RECORD_SELECTOR_PART_SPACES), RECORD(3, "operator", KG_RECORD_SELECTOR_PART_SPACES),
    RECORD(4, "value", KG_RECORD_SELECTOR_PART_SPACES), RECORD(5, "insensitive", KG_RECORD_SELECTOR_PART_SPACES),
    RECORD(6, "namespace", KG_RECORD_SELECTOR_PART_SPACES)};
static const record_field SelectorRaws_fields[] = {
    STR(0, "value"), STR(1, "namespace"), STR(2, "attribute"), STR(3, "operator"), STR(4, "insensitive_flag"),
    STR(5, "sensitive_flag"), RECORD(6, "spaces", KG_RECORD_SELECTOR_RAW_SPACES)};
static const record_field PostcssRawValue_fields[] = {STR_REQUIRED(0, "raw"), STR_REQUIRED(1, "value")};
static const record_field PostcssRaws_fields[] = {
    STR(0, "before"), STR(1, "after"), STR(2, "between"), STR(3, "after_name"), BOOL(4, "semicolon"),
    STR(5, "own_semicolon"), STR(6, "important"), STR(7, "left"), STR(8, "right"),
    RECORD(9, "selector", KG_RECORD_POSTCSS_RAW_VALUE), RECORD(10, "params", KG_RECORD_POSTCSS_RAW_VALUE),
    RECORD(11, "value", KG_RECORD_POSTCSS_RAW_VALUE)};

#undef STR
#undef STR_EMPTY
#undef STR_REQUIRED
#undef INT_REQUIRED
#undef BOOL
#undef RECORD

#define COUNT(a) ((Py_ssize_t)(sizeof(a) / sizeof(a[0])))
/* Indexed by kg_record_type: a record may only hold records of types before its own */
static const record_def RECORDS[KG_RECORD_TYPE_COUNT] = {
    {"SelectorPosition", SelectorPosition_fields, COUNT(SelectorPosition_fields), 2},
    {"SelectorSource", SelectorSource_fields, COUNT(SelectorSource_fields), 2},
    {"SelectorPartSpaces", SelectorPartSpaces_fields, COUNT(SelectorPartSpaces_fields), 2},
    {"SelectorSpaces", SelectorSpaces_fields, COUNT(SelectorSpaces_fields), 2},
    {"SelectorRawSpaces", SelectorRawSpaces_fields, COUNT(SelectorRawSpaces_fields), 2},
    {"SelectorRaws", SelectorRaws_fields, COUNT(SelectorRaws_fields), 1},
    {"PostcssRawValue", PostcssRawValue_fields, COUNT(PostcssRawValue_fields), 2},
    {"PostcssRaws", PostcssRaws_fields, COUNT(PostcssRaws_fields), 0},
};

static kg_record_type
record_type_of(kg_module_state *state, PyTypeObject *tp) {
    for (int i = 0; i < KG_RECORD_TYPE_COUNT; i++) {
        if (state->record_types[i] == tp) return (kg_record_type)i;
    }
    return KG_RECORD_TYPE_COUNT;
}

static int
check_value(kg_module_state *state, const record_field *f, PyObject *value) {
    if (value == Py_None) {
        if (f->nullable) return 0;
    } else {
        switch (f->kind) {
            case RF_STR: if (PyUnicode_CheckExact(value)) return 0; break;
            case RF_INT: if (PyLong_CheckExact(value) || PyBool_Check(value)) return 0; break;
            case RF_BOOL: if (PyBool_Check(value)) return 0; break;
            case RF_RECORD: if (Py_IS_TYPE(value, state->record_types[f->record])) return 0; break;
        }
    }
    static const char *const kinds[] = {"str", "int", "bool", NULL};
    const char *expected = f->kind == RF_RECORD ? RECORDS[f->record].name : kinds[f->kind];
    PyErr_Format(PyExc_TypeError, "%s must be %s%s, not %.200s", f->name, expected,
                 f->nullable ? " or None" : "", Py_TYPE(value)->tp_name);
    return -1;
}

static PyObject *
default_value(kg_module_state *state, const record_field *f) {
    return Py_NewRef(f->dflt == RD_EMPTY ? state->empty_string : Py_None);
}

PyObject *
kg_record_new(kg_module_state *state, kg_record_type type) {
    PyTypeObject *tp = state->record_types[type];
    kg_Record *self = (kg_Record *)tp->tp_alloc(tp, 0);
    if (self == NULL) return NULL;
    /* Required fields are None until the caller sets them */
    for (Py_ssize_t i = 0; i < RECORDS[type].count; i++) self->values[i] = default_value(state, &RECORDS[type].fields[i]);
    return (PyObject *)self;
}

static PyObject *
record_vectorcall(PyObject *type, PyObject *const *args, size_t nargsf, PyObject *kwnames) {
    PyTypeObject *tp = (PyTypeObject *)type;
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(tp);
    if (state == NULL) return NULL;
    const kg_record_type rt = record_type_of(state, tp);
    if (rt == KG_RECORD_TYPE_COUNT) {
        PyErr_SetString(PyExc_TypeError, "not a record type");
        return NULL;
    }
    const record_def *def = &RECORDS[rt];
    const Py_ssize_t nargs = PyVectorcall_NARGS(nargsf);
    if (nargs > def->positional) {
        PyErr_Format(PyExc_TypeError, "%s() takes at most %zd positional arguments (%zd given)", def->name,
                     def->positional, nargs);
        return NULL;
    }
    kg_Record *self = (kg_Record *)tp->tp_alloc(tp, 0);
    if (self == NULL) return NULL;
    for (Py_ssize_t i = 0; i < nargs; i++) {
        if (check_value(state, &def->fields[i], args[i]) != 0) goto error;
        self->values[i] = Py_NewRef(args[i]);
    }
    const Py_ssize_t nkw = kwnames == NULL ? 0 : PyTuple_GET_SIZE(kwnames);
    for (Py_ssize_t k = 0; k < nkw; k++) {
        PyObject *name = PyTuple_GET_ITEM(kwnames, k);
        const record_field *f = NULL;
        for (Py_ssize_t i = 0; i < def->count; i++) {
            if (PyUnicode_EqualToUTF8(name, def->fields[i].name)) { f = &def->fields[i]; break; }
        }
        if (f == NULL) {
            PyErr_Format(PyExc_TypeError, "%s() got an unexpected keyword argument %R", def->name, name);
            goto error;
        }
        if (self->values[f->index] != NULL) {
            PyErr_Format(PyExc_TypeError, "%s() got multiple values for argument %R", def->name, name);
            goto error;
        }
        PyObject *value = args[nargs + k];
        if (check_value(state, f, value) != 0) goto error;
        self->values[f->index] = Py_NewRef(value);
    }
    for (Py_ssize_t i = 0; i < def->count; i++) {
        if (self->values[i] != NULL) continue;
        if (def->fields[i].dflt == RD_REQUIRED) {
            PyErr_Format(PyExc_TypeError, "%s() missing required argument '%s'", def->name, def->fields[i].name);
            goto error;
        }
        self->values[i] = default_value(state, &def->fields[i]);
    }
    return (PyObject *)self;
error:
    Py_DECREF(self);
    return NULL;
}

static PyObject *
record_new(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    /* Calls that do not use vectorcall, such as type.__call__(cls, ...) */
    return PyObject_VectorcallDict((PyObject *)type, &PyTuple_GET_ITEM(args, 0), PyTuple_GET_SIZE(args), kwds);
}

static void
record_dealloc(PyObject *o) {
    PyTypeObject *tp = Py_TYPE(o);
    kg_Record *self = (kg_Record *)o;
    const Py_ssize_t n = (tp->tp_basicsize - (Py_ssize_t)sizeof(kg_Record)) / (Py_ssize_t)sizeof(PyObject *);
    for (Py_ssize_t i = 0; i < n; i++) Py_XDECREF(self->values[i]);
    tp->tp_free(o);
    Py_DECREF(tp);
}

static PyObject *
record_get(PyObject *o, void *closure) {
    const record_field *f = (const record_field *)closure;
    PyObject *ans;
    Py_BEGIN_CRITICAL_SECTION(o);
    ans = Py_NewRef(((kg_Record *)o)->values[f->index]);
    Py_END_CRITICAL_SECTION();
    return ans;
}

static int
record_set(PyObject *o, PyObject *value, void *closure) {
    const record_field *f = (const record_field *)closure;
    if (value == NULL) {
        PyErr_Format(PyExc_AttributeError, "cannot delete %s", f->name);
        return -1;
    }
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(Py_TYPE(o));
    if (state == NULL || check_value(state, f, value) != 0) return -1;
    PyObject *old;
    Py_BEGIN_CRITICAL_SECTION(o);
    old = ((kg_Record *)o)->values[f->index];
    ((kg_Record *)o)->values[f->index] = Py_NewRef(value);
    Py_END_CRITICAL_SECTION();
    Py_DECREF(old);
    return 0;
}

static PyObject *
record_repr(PyObject *o) {
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(Py_TYPE(o));
    if (state == NULL) return NULL;
    const record_def *def = &RECORDS[record_type_of(state, Py_TYPE(o))];
    PyUnicodeWriter *w = PyUnicodeWriter_Create(0);
    if (w == NULL) return NULL;
    if (PyUnicodeWriter_WriteUTF8(w, def->name, -1) < 0 || PyUnicodeWriter_WriteChar(w, '(') < 0) goto error;
    for (Py_ssize_t i = 0; i < def->count; i++) {
        PyObject *value = record_get(o, (void *)&def->fields[i]);
        if (value == NULL) goto error;
        const int r = PyUnicodeWriter_Format(w, "%s%s=%R", i ? ", " : "", def->fields[i].name, value);
        Py_DECREF(value);
        if (r < 0) goto error;
    }
    if (PyUnicodeWriter_WriteChar(w, ')') < 0) goto error;
    return PyUnicodeWriter_Finish(w);
error:
    PyUnicodeWriter_Discard(w);
    return NULL;
}

static PyObject *
record_copy(PyObject *o, PyObject *Py_UNUSED(ignored)) {
    PyTypeObject *tp = Py_TYPE(o);
    const Py_ssize_t n = (tp->tp_basicsize - (Py_ssize_t)sizeof(kg_Record)) / (Py_ssize_t)sizeof(PyObject *);
    kg_Record *ans = (kg_Record *)tp->tp_alloc(tp, 0);
    if (ans == NULL) return NULL;
    Py_BEGIN_CRITICAL_SECTION(o);
    for (Py_ssize_t i = 0; i < n; i++) ans->values[i] = Py_NewRef(((kg_Record *)o)->values[i]);
    Py_END_CRITICAL_SECTION();
    return (PyObject *)ans;
}

static PyObject *
record_deepcopy(PyObject *o, PyObject *Py_UNUSED(ignored)) {
    /* Records are acyclic and shallow (a few levels at most), so this recursion is bounded */
    PyTypeObject *tp = Py_TYPE(o);
    PyObject *ans = record_copy(o, NULL);
    if (ans == NULL) return NULL;
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(tp);
    if (state == NULL) { Py_DECREF(ans); return NULL; }
    const record_def *def = &RECORDS[record_type_of(state, tp)];
    kg_Record *r = (kg_Record *)ans;
    for (Py_ssize_t i = 0; i < def->count; i++) {
        if (def->fields[i].kind != RF_RECORD || r->values[i] == Py_None) continue;
        PyObject *copy = record_deepcopy(r->values[i], NULL);
        if (copy == NULL) { Py_DECREF(ans); return NULL; }
        Py_SETREF(r->values[i], copy);
    }
    return ans;
}

static PyMethodDef record_methods[] = {
    {"copy", record_copy, METH_NOARGS, "A shallow copy: the records it holds are shared."},
    {"deepcopy", record_deepcopy, METH_NOARGS, "A copy with copies of the records it holds."},
    {NULL, NULL, 0, NULL},
};

/* The getters and setters of each type, with the field as closure */
#define END {NULL, NULL, NULL, NULL, NULL}
static PyGetSetDef SelectorPosition_getset[] = {
    {"line", record_get, record_set, NULL, (void *)&SelectorPosition_fields[0]},
    {"column", record_get, record_set, NULL, (void *)&SelectorPosition_fields[1]}, END};
static PyGetSetDef SelectorSource_getset[] = {
    {"start", record_get, record_set, NULL, (void *)&SelectorSource_fields[0]},
    {"end", record_get, record_set, NULL, (void *)&SelectorSource_fields[1]}, END};
static PyGetSetDef SelectorPartSpaces_getset[] = {
    {"before", record_get, record_set, NULL, (void *)&SelectorPartSpaces_fields[0]},
    {"after", record_get, record_set, NULL, (void *)&SelectorPartSpaces_fields[1]}, END};
static PyGetSetDef SelectorSpaces_getset[] = {
    {"before", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[0]},
    {"after", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[1]},
    {"attribute", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[2]},
    {"operator", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[3]},
    {"value", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[4]},
    {"insensitive", record_get, record_set, NULL, (void *)&SelectorSpaces_fields[5]}, END};
static PyGetSetDef SelectorRawSpaces_getset[] = {
    {"before", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[0]},
    {"after", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[1]},
    {"attribute", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[2]},
    {"operator", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[3]},
    {"value", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[4]},
    {"insensitive", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[5]},
    {"namespace", record_get, record_set, NULL, (void *)&SelectorRawSpaces_fields[6]}, END};
static PyGetSetDef SelectorRaws_getset[] = {
    {"value", record_get, record_set, NULL, (void *)&SelectorRaws_fields[0]},
    {"namespace", record_get, record_set, NULL, (void *)&SelectorRaws_fields[1]},
    {"attribute", record_get, record_set, NULL, (void *)&SelectorRaws_fields[2]},
    {"operator", record_get, record_set, NULL, (void *)&SelectorRaws_fields[3]},
    {"insensitive_flag", record_get, record_set, NULL, (void *)&SelectorRaws_fields[4]},
    {"sensitive_flag", record_get, record_set, NULL, (void *)&SelectorRaws_fields[5]},
    {"spaces", record_get, record_set, NULL, (void *)&SelectorRaws_fields[6]}, END};
static PyGetSetDef PostcssRawValue_getset[] = {
    {"raw", record_get, record_set, NULL, (void *)&PostcssRawValue_fields[0]},
    {"value", record_get, record_set, NULL, (void *)&PostcssRawValue_fields[1]}, END};
static PyGetSetDef PostcssRaws_getset[] = {
    {"before", record_get, record_set, NULL, (void *)&PostcssRaws_fields[0]},
    {"after", record_get, record_set, NULL, (void *)&PostcssRaws_fields[1]},
    {"between", record_get, record_set, NULL, (void *)&PostcssRaws_fields[2]},
    {"after_name", record_get, record_set, NULL, (void *)&PostcssRaws_fields[3]},
    {"semicolon", record_get, record_set, NULL, (void *)&PostcssRaws_fields[4]},
    {"own_semicolon", record_get, record_set, NULL, (void *)&PostcssRaws_fields[5]},
    {"important", record_get, record_set, NULL, (void *)&PostcssRaws_fields[6]},
    {"left", record_get, record_set, NULL, (void *)&PostcssRaws_fields[7]},
    {"right", record_get, record_set, NULL, (void *)&PostcssRaws_fields[8]},
    {"selector", record_get, record_set, NULL, (void *)&PostcssRaws_fields[9]},
    {"params", record_get, record_set, NULL, (void *)&PostcssRaws_fields[10]},
    {"value", record_get, record_set, NULL, (void *)&PostcssRaws_fields[11]}, END};
#undef END

static PyGetSetDef *const GETSETS[KG_RECORD_TYPE_COUNT] = {
    SelectorPosition_getset, SelectorSource_getset, SelectorPartSpaces_getset, SelectorSpaces_getset,
    SelectorRawSpaces_getset, SelectorRaws_getset, PostcssRawValue_getset, PostcssRaws_getset,
};

/* The fields of a record, in their order, as a class attribute */
static PyObject *
field_names(const record_def *def) {
    PyObject *ans = PyTuple_New(def->count);
    if (ans == NULL) return NULL;
    for (Py_ssize_t i = 0; i < def->count; i++) {
        PyObject *name = PyUnicode_InternFromString(def->fields[i].name);
        if (name == NULL) { Py_DECREF(ans); return NULL; }
        PyTuple_SET_ITEM(ans, i, name);
    }
    return ans;
}

int
kg_records_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    for (int i = 0; i < KG_RECORD_TYPE_COUNT; i++) {
        const record_def *def = &RECORDS[i];
        for (Py_ssize_t j = 0; j < def->count; j++) {
            /* The guarantee that records cannot form cycles */
            if (def->fields[j].index != j || (def->fields[j].kind == RF_RECORD && def->fields[j].record >= i)) {
                PyErr_Format(PyExc_SystemError, "bad definition of the record %s", def->name);
                return -1;
            }
        }
        char qualname[64];
        PyOS_snprintf(qualname, sizeof(qualname), "kgcss.%s", def->name);
        PyType_Slot slots[] = {
            {Py_tp_new, record_new},
            {Py_tp_dealloc, record_dealloc},
            {Py_tp_getset, GETSETS[i]},
            {Py_tp_methods, record_methods},
            {Py_tp_repr, record_repr},
            {Py_tp_doc, (void *)"A record of the linter. Not a public API."},
            {0, NULL},
        };
        PyType_Spec spec = {
            .name = qualname,
            .basicsize = (int)(sizeof(kg_Record) + (size_t)def->count * sizeof(PyObject *)),
            .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE,
            .slots = slots,
        };
        PyTypeObject *tp = (PyTypeObject *)PyType_FromModuleAndSpec(module, &spec, NULL);
        if (tp == NULL) return -1;
        tp->tp_vectorcall = record_vectorcall;
        state->record_types[i] = tp;
        /* The type is immutable: set _fields in its dict before anyone looks it up */
        PyObject *dict = PyType_GetDict(tp), *fields = field_names(def);
        const int r = (dict == NULL || fields == NULL) ? -1 : PyDict_SetItemString(dict, "_fields", fields);
        Py_XDECREF(fields);
        Py_XDECREF(dict);
        if (r != 0) return -1;
        PyType_Modified(tp);
        if (PyModule_AddType(module, tp) != 0) return -1;
    }
    return 0;
}

int
kg_records_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    for (int i = 0; i < KG_RECORD_TYPE_COUNT; i++) Py_VISIT(state->record_types[i]);
    return 0;
}

void
kg_records_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    for (int i = 0; i < KG_RECORD_TYPE_COUNT; i++) Py_CLEAR(state->record_types[i]);
}
