/*
 * value_parser.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * ValueNode and the C version of parse() of the linter's port of
 * postcss-value-parser, kgcss/linter/_value_parser.py, where python_parse()
 * is the reference: tests/test_linter_value_parser_c.py compares them. The
 * quirks of the JavaScript are kept: an unclosed string or url() gets its
 * missing quote or parenthesis added to the text, and the loop still ends at
 * the length of the text as it was.
 */

#include "kgcss.h"
#include "objects.h"
#include <structmember.h>

typedef struct {
    PyObject_HEAD
    PyObject *type, *value, *before, *after, *quote, *nodes;
    Py_ssize_t source_index, source_end_index;
    char unclosed;
} kg_ValueNode;

static int
ValueNode_traverse(PyObject *o, visitproc visit, void *arg) {
    kg_ValueNode *self = (kg_ValueNode *)o;
    Py_VISIT(Py_TYPE(o));
    Py_VISIT(self->nodes);
    return 0;
}

static int
ValueNode_clear(PyObject *o) {
    kg_ValueNode *self = (kg_ValueNode *)o;
    Py_CLEAR(self->type);
    Py_CLEAR(self->value);
    Py_CLEAR(self->before);
    Py_CLEAR(self->after);
    Py_CLEAR(self->quote);
    Py_CLEAR(self->nodes);
    return 0;
}

static void
ValueNode_dealloc(PyObject *o) {
    PyTypeObject *tp = Py_TYPE(o);
    PyObject_GC_UnTrack(o);
    Py_TRASHCAN_BEGIN(o, ValueNode_dealloc)
    ValueNode_clear(o);
    tp->tp_free(o);
    Py_DECREF(tp);
    Py_TRASHCAN_END
}

/* A node with the defaults of ValueNode(type, source_index, value), stealing type and value */
static kg_ValueNode *
new_value_node(kg_module_state *state, PyObject *type, Py_ssize_t source_index, PyObject *value) {
    PyTypeObject *tp = state->ValueNode_type;
    if (type == NULL || value == NULL) goto error;
    kg_ValueNode *self = (kg_ValueNode *)tp->tp_alloc(tp, 0);
    if (self == NULL) goto error;
    self->type = type;
    self->value = value;
    self->source_index = self->source_end_index = source_index;
    self->before = Py_NewRef(state->empty_string);
    self->after = Py_NewRef(state->empty_string);
    self->quote = Py_NewRef(state->empty_string);
    self->nodes = Py_NewRef(Py_None);
    self->unclosed = 0;
    return self;
error:
    Py_XDECREF(type);
    Py_XDECREF(value);
    return NULL;
}

static PyObject *
ValueNode_new(PyTypeObject *tp, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"type", "source_index", "value", NULL};
    PyObject *type, *value = NULL;
    Py_ssize_t source_index;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "Un|U:ValueNode", kwlist, &type, &source_index, &value)) return NULL;
    kg_module_state *state = (kg_module_state *)PyType_GetModuleState(tp);
    if (state == NULL) return NULL;
    return (PyObject *)new_value_node(state, Py_NewRef(type), source_index,
                                      Py_NewRef(value ? value : state->empty_string));
}

static PyObject *
ValueNode_repr(PyObject *o) {
    kg_ValueNode *self = (kg_ValueNode *)o;
    PyObject *type = NULL, *value = NULL, *ans = NULL;
    Py_BEGIN_CRITICAL_SECTION(o);
    type = Py_XNewRef(self->type);
    value = Py_XNewRef(self->value);
    Py_END_CRITICAL_SECTION();
    if (type != NULL && value != NULL) {
        ans = PyUnicode_FromFormat("ValueNode(%S, %R, %zd, %zd)", type, value, self->source_index,
                                   self->source_end_index);
    } else {
        PyErr_SetString(PyExc_AttributeError, "a field of the ValueNode is deleted");
    }
    Py_XDECREF(type);
    Py_XDECREF(value);
    return ans;
}

#define M(name, field, type) {name, type, offsetof(kg_ValueNode, field), 0, NULL}
static PyMemberDef ValueNode_members[] = {
    M("type", type, Py_T_OBJECT_EX),
    M("value", value, Py_T_OBJECT_EX),
    M("before", before, Py_T_OBJECT_EX),
    M("after", after, Py_T_OBJECT_EX),
    M("quote", quote, Py_T_OBJECT_EX),
    M("nodes", nodes, Py_T_OBJECT_EX),
    M("source_index", source_index, Py_T_PYSSIZET),
    M("source_end_index", source_end_index, Py_T_PYSSIZET),
    M("unclosed", unclosed, Py_T_BOOL),
    {NULL, 0, 0, 0, NULL},
};
#undef M

static PyType_Slot ValueNode_slots[] = {
    {Py_tp_new, ValueNode_new},
    {Py_tp_dealloc, ValueNode_dealloc},
    {Py_tp_traverse, ValueNode_traverse},
    {Py_tp_clear, ValueNode_clear},
    {Py_tp_members, ValueNode_members},
    {Py_tp_repr, ValueNode_repr},
    {Py_tp_doc, (void *)"ValueNode(type, source_index, value='')\n\nA node of the linter's port of "
                        "postcss-value-parser: what it gives as a plain object. Not a public API."},
    {0, NULL},
};

static PyType_Spec ValueNode_spec = {
    .name = "kgcss.ValueNode",
    .basicsize = sizeof(kg_ValueNode),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | Py_TPFLAGS_IMMUTABLETYPE,
    .slots = ValueNode_slots,
};

/* parse() {{{ */

typedef struct {
    kg_module_state *state;
    Py_UCS4 *text;
    Py_ssize_t len;  /* the length of text, which may grow */
    Py_ssize_t cap;
    PyObject *types[8];
} vparser;

enum { VT_WORD, VT_STRING, VT_DIV, VT_SPACE, VT_COMMENT, VT_FUNCTION, VT_UNICODE_RANGE, VT_ROOT };
static const char *const VT_NAMES[8] = {"word", "string", "div", "space", "comment", "function", "unicode-range",
                                        "root"};

/* _code(): -1 out of range */
static long
code_at(const vparser *p, Py_ssize_t i) {
    return (i >= 0 && i < p->len) ? (long)p->text[i] : -1;
}

static bool
le32(long code) {
    return code >= 0 && code <= 32;
}

/* text[a:b] with Python's slice semantics for 0 <= a */
static PyObject *
slice(const vparser *p, Py_ssize_t a, Py_ssize_t b) {
    if (b > p->len) b = p->len;
    if (a > b) a = b;
    return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, p->text + a, b - a);
}

/* value += c: the text grows once at most, as the loop then ends */
static int
grow(vparser *p, Py_UCS4 c) {
    if (p->len >= p->cap) {
        PyErr_SetString(PyExc_SystemError, "value_parse(): the text grew more than expected");
        return -1;
    }
    p->text[p->len++] = c;
    return 0;
}

static kg_ValueNode *
node(vparser *p, int type, Py_ssize_t source_index, PyObject *value) {
    return new_value_node(p->state, Py_NewRef(p->types[type]), source_index, value);
}

static bool
is_type(const vparser *p, PyObject *n, int type) {
    return ((kg_ValueNode *)n)->type == p->types[type];
}

/* text.find(c, start), -1 if not found */
static Py_ssize_t
find_char(const vparser *p, Py_UCS4 c, Py_ssize_t start) {
    for (Py_ssize_t i = start < 0 ? 0 : start; i < p->len; i++) {
        if (p->text[i] == c) return i;
    }
    return -1;
}

/* Whether the character at i is escaped by an odd number of backslashes */
static bool
escaped_at(const vparser *p, Py_ssize_t i) {
    bool escape = false;
    while (code_at(p, i - 1) == '\\') {
        i--;
        escape = !escape;
    }
    return escape;
}

/* RE_UNICODE_RANGE.match(token[2:]) for token[0] in 'uU' and token[1] == '+' */
static bool
is_unicode_range(const Py_UCS4 *t, Py_ssize_t n) {
    if (n < 3 || (t[0] != 'u' && t[0] != 'U') || t[1] != '+') return false;
    for (Py_ssize_t i = 2; i < n; i++) {
        const Py_UCS4 c = t[i];
        if (!((c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F') || (c >= '0' && c <= '9') || c == '?' || c == '-'))
            return false;
    }
    return true;
}

/* Whether parent is a calc() function: in_calc() */
static bool
in_calc(const vparser *p, PyObject *parent) {
    if (parent == NULL || !is_type(p, parent, VT_FUNCTION)) return false;
    return PyUnicode_EqualToUTF8(((kg_ValueNode *)parent)->value, "calc");
}

static int
append(PyObject *list, kg_ValueNode *n) {
    if (n == NULL) return -1;
    const int r = PyList_Append(list, (PyObject *)n);
    Py_DECREF(n);
    return r;
}

static PyObject *
parse_value(vparser *p, Py_ssize_t max_) {
    kg_ValueNode *root = node(p, VT_ROOT, 0, Py_NewRef(p->state->empty_string));
    PyObject *stack = PyList_New(0), *tokens = PyList_New(0), *ans = NULL;
    PyObject *before = Py_NewRef(p->state->empty_string), *after = Py_NewRef(p->state->empty_string);
    PyObject *name = Py_NewRef(p->state->empty_string);
    PyObject *parent = NULL;  /* borrowed: the stack holds it */
    if (root == NULL || stack == NULL || tokens == NULL) goto end;
    Py_SETREF(root->nodes, Py_NewRef(tokens));
    if (PyList_Append(stack, (PyObject *)root) != 0) goto end;
    Py_ssize_t pos = 0, balanced = 0;
    long code = code_at(p, pos);
#define SET(var, val) do { PyObject *v_ = (val); if (v_ == NULL) goto end; Py_SETREF(var, v_); } while (0)
    while (pos < max_) {
        if (le32(code)) {  /* whitespace */
            Py_ssize_t nxt = pos;
            do {
                nxt++;
                code = code_at(p, nxt);
            } while (le32(code));
            PyObject *token = slice(p, pos, nxt);
            if (token == NULL) goto end;
            const Py_ssize_t n = PyList_GET_SIZE(tokens);
            kg_ValueNode *prev = n ? (kg_ValueNode *)PyList_GET_ITEM(tokens, n - 1) : NULL;
            if (code == ')' && balanced) {
                Py_SETREF(after, token);
            } else if (prev != NULL && is_type(p, (PyObject *)prev, VT_DIV)) {
                prev->source_end_index += PyUnicode_GET_LENGTH(token);
                Py_SETREF(prev->after, token);
            } else if (code == ',' || code == ':' ||
                       (code == '/' && code_at(p, nxt + 1) != '*' &&
                        (parent == NULL || (is_type(p, parent, VT_FUNCTION) &&
                                            !PyUnicode_EqualToUTF8(((kg_ValueNode *)parent)->value, "calc"))))) {
                Py_SETREF(before, token);
            } else {
                kg_ValueNode *sp = node(p, VT_SPACE, pos, token);
                if (sp == NULL) goto end;
                sp->source_end_index = nxt;
                if (append(tokens, sp) != 0) goto end;
            }
            pos = nxt;
        } else if (code == '\'' || code == '"') {  /* quotes */
            Py_ssize_t nxt = pos;
            const Py_UCS4 quote = (Py_UCS4)code;
            kg_ValueNode *n = node(p, VT_STRING, pos, Py_NewRef(p->state->empty_string));
            if (n == NULL) goto end;
            SET(n->quote, PyUnicode_FromOrdinal(quote));
            while (true) {
                bool escape = false;
                nxt = find_char(p, quote, nxt + 1);
                if (nxt != -1) {
                    escape = escaped_at(p, nxt);
                } else {
                    if (grow(p, quote) != 0) { Py_DECREF(n); goto end; }
                    nxt = p->len - 1;
                    n->unclosed = 1;
                }
                if (!escape) break;
            }
            SET(n->value, slice(p, pos + 1, nxt));
            n->source_end_index = n->unclosed ? nxt : nxt + 1;
            if (append(tokens, n) != 0) goto end;
            pos = nxt + 1;
            code = code_at(p, pos);
        } else if (code == '/' && code_at(p, pos + 1) == '*') {  /* comments */
            Py_ssize_t nxt = -1;
            for (Py_ssize_t i = pos; i + 1 < p->len; i++) {
                if (p->text[i] == '*' && p->text[i + 1] == '/') { nxt = i; break; }
            }
            kg_ValueNode *n = node(p, VT_COMMENT, pos, Py_NewRef(p->state->empty_string));
            if (n == NULL) goto end;
            n->source_end_index = nxt + 2;
            if (nxt == -1) {
                n->unclosed = 1;
                nxt = p->len;
                n->source_end_index = nxt;
            }
            PyObject *v = slice(p, pos + 2, nxt);
            if (v == NULL) { Py_DECREF(n); goto end; }
            Py_SETREF(n->value, v);
            if (append(tokens, n) != 0) goto end;
            pos = nxt + 2;
            code = code_at(p, pos);
        } else if ((code == '/' || code == '*') && in_calc(p, parent)) {  /* an operation within calc() */
            kg_ValueNode *n = node(p, VT_WORD, pos - PyUnicode_GET_LENGTH(before), slice(p, pos, pos + 1));
            if (n == NULL) goto end;
            n->source_end_index = pos + 1;
            if (append(tokens, n) != 0) goto end;
            pos++;
            code = code_at(p, pos);
        } else if (code == '/' || code == ',' || code == ':') {  /* dividers */
            kg_ValueNode *n = node(p, VT_DIV, pos - PyUnicode_GET_LENGTH(before), slice(p, pos, pos + 1));
            if (n == NULL) goto end;
            n->source_end_index = pos + 1;
            Py_SETREF(n->before, before);
            before = Py_NewRef(p->state->empty_string);
            if (append(tokens, n) != 0) goto end;
            pos++;
            code = code_at(p, pos);
        } else if (code == '(') {  /* open parenthesis */
            Py_ssize_t nxt = pos;
            do {
                nxt++;
                code = code_at(p, nxt);
            } while (le32(code));
            const Py_ssize_t open_pos = pos;
            kg_ValueNode *n = node(p, VT_FUNCTION, pos - PyUnicode_GET_LENGTH(name), Py_NewRef(name));
            if (n == NULL) goto end;
            PyObject *b = slice(p, open_pos + 1, nxt);
            if (b == NULL) { Py_DECREF(n); goto end; }
            Py_SETREF(n->before, b);
            pos = nxt;
            if (PyUnicode_EqualToUTF8(name, "url") && code != '\'' && code != '"') {
                nxt--;
                while (true) {
                    bool escape = false;
                    nxt = find_char(p, ')', nxt + 1);
                    if (nxt != -1) {
                        escape = escaped_at(p, nxt);
                    } else {
                        if (grow(p, ')') != 0) { Py_DECREF(n); goto end; }
                        nxt = p->len - 1;
                        n->unclosed = 1;
                    }
                    if (!escape) break;
                }
                Py_ssize_t ws_pos = nxt;
                do {
                    ws_pos--;
                    code = code_at(p, ws_pos);
                } while (le32(code));
                PyObject *nodes = PyList_New(0);
                if (nodes == NULL) { Py_DECREF(n); goto end; }
                Py_SETREF(n->nodes, nodes);
                if (open_pos < ws_pos) {
                    if (pos != ws_pos + 1) {
                        kg_ValueNode *w = node(p, VT_WORD, pos, slice(p, pos, ws_pos + 1));
                        if (w == NULL) { Py_DECREF(n); goto end; }
                        w->source_end_index = ws_pos + 1;
                        if (append(nodes, w) != 0) { Py_DECREF(n); goto end; }
                    }
                    if (n->unclosed && ws_pos + 1 != nxt) {
                        Py_SETREF(n->after, Py_NewRef(p->state->empty_string));
                        kg_ValueNode *s = node(p, VT_SPACE, ws_pos + 1, slice(p, ws_pos + 1, nxt));
                        if (s == NULL) { Py_DECREF(n); goto end; }
                        s->source_end_index = nxt;
                        if (append(nodes, s) != 0) { Py_DECREF(n); goto end; }
                    } else {
                        PyObject *a = slice(p, ws_pos + 1, nxt);
                        if (a == NULL) { Py_DECREF(n); goto end; }
                        Py_SETREF(n->after, a);
                        n->source_end_index = nxt;
                    }
                } else {
                    Py_SETREF(n->after, Py_NewRef(p->state->empty_string));
                }
                pos = nxt + 1;
                n->source_end_index = n->unclosed ? nxt : pos;
                code = code_at(p, pos);
                if (append(tokens, n) != 0) goto end;
            } else {
                balanced++;
                n->source_end_index = pos + 1;
                PyObject *nodes = PyList_New(0);
                if (nodes == NULL) { Py_DECREF(n); goto end; }
                Py_SETREF(n->nodes, nodes);
                if (PyList_Append(tokens, (PyObject *)n) != 0 || PyList_Append(stack, (PyObject *)n) != 0) {
                    Py_DECREF(n);
                    goto end;
                }
                Py_SETREF(tokens, Py_NewRef(nodes));
                parent = (PyObject *)n;
                Py_DECREF(n);
            }
            SET(name, Py_NewRef(p->state->empty_string));
        } else if (code == ')' && balanced) {  /* close parenthesis */
            pos++;
            code = code_at(p, pos);
            kg_ValueNode *pn = (kg_ValueNode *)parent;
            pn->source_end_index += PyUnicode_GET_LENGTH(after);
            Py_SETREF(pn->after, after);
            after = Py_NewRef(p->state->empty_string);
            balanced--;
            const Py_ssize_t top = PyList_GET_SIZE(stack) - 1;
            ((kg_ValueNode *)PyList_GET_ITEM(stack, top))->source_end_index = pos;
            if (PyList_SetSlice(stack, top, top + 1, NULL) != 0) goto end;
            parent = PyList_GET_ITEM(stack, balanced);
            Py_SETREF(tokens, Py_NewRef(((kg_ValueNode *)parent)->nodes));
        } else {  /* words */
            Py_ssize_t nxt = pos;
            const bool calc = in_calc(p, parent);
            while (true) {
                if (code == '\\') nxt++;
                nxt++;
                code = code_at(p, nxt);
                if (!(nxt < max_) || le32(code) || code == '\'' || code == '"' || code == ',' || code == ':' ||
                    code == '/' || code == '(' || (code == '*' && calc) || (code == ')' && balanced)) break;
            }
            PyObject *token = slice(p, pos, nxt);
            if (token == NULL) goto end;
            if (code == '(') {
                Py_SETREF(name, token);
            } else {
                const Py_ssize_t end = nxt > p->len ? p->len : nxt;
                const int type = is_unicode_range(p->text + pos, end - pos) ? VT_UNICODE_RANGE : VT_WORD;
                kg_ValueNode *n = node(p, type, pos, token);
                if (n == NULL) goto end;
                n->source_end_index = nxt;
                if (append(tokens, n) != 0) goto end;
            }
            pos = nxt;
        }
    }
#undef SET
    for (Py_ssize_t i = PyList_GET_SIZE(stack) - 1; i > 0; i--) {
        kg_ValueNode *n = (kg_ValueNode *)PyList_GET_ITEM(stack, i);
        n->unclosed = 1;
        n->source_end_index = p->len;
    }
    ans = Py_NewRef(root->nodes);
end:
    Py_XDECREF(root);
    Py_XDECREF(stack);
    Py_XDECREF(tokens);
    Py_XDECREF(before);
    Py_XDECREF(after);
    Py_XDECREF(name);
    return ans;
}

PyObject *
kg_value_parse(PyObject *module, PyObject *value) {
    if (!PyUnicode_Check(value)) {
        PyErr_Format(PyExc_TypeError, "value_parse() expects a str, not %.200s", Py_TYPE(value)->tp_name);
        return NULL;
    }
    vparser p = {.state = kg_get_module_state(module)};
    const Py_ssize_t n = PyUnicode_GET_LENGTH(value);
    /* Room for a quote or parenthesis that is added */
    p.cap = n + 2;
    p.text = PyMem_Malloc((size_t)p.cap * sizeof(Py_UCS4));
    if (p.text == NULL) return PyErr_NoMemory();
    if (PyUnicode_AsUCS4(value, p.text, p.cap, 0) == NULL) { PyMem_Free(p.text); return NULL; }
    p.len = n;
    PyObject *ans = NULL;
    for (int i = 0; i < 8; i++) {
        if ((p.types[i] = PyUnicode_InternFromString(VT_NAMES[i])) == NULL) goto end;
    }
    ans = parse_value(&p, n);
end:
    for (int i = 0; i < 8; i++) Py_XDECREF(p.types[i]);
    PyMem_Free(p.text);
    return ans;
}

/* }}} */

int
kg_value_parser_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->ValueNode_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &ValueNode_spec, NULL);
    if (state->ValueNode_type == NULL) return -1;
    return PyModule_AddType(module, state->ValueNode_type);
}

int
kg_value_parser_traverse(PyObject *module, visitproc visit, void *arg) {
    Py_VISIT(kg_get_module_state(module)->ValueNode_type);
    return 0;
}

void
kg_value_parser_clear(PyObject *module) {
    Py_CLEAR(kg_get_module_state(module)->ValueNode_type);
}
