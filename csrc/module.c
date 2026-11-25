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
#include "objects.h"

static PyMethodDef module_methods[] = {
    {"serialize_string", kg_serialize_string, METH_O,
     "serialize_string(text: str) -> str\n\n"
     "Serialize text as a CSS string, including the surrounding double quotes, "
     "following the CSSOM 'serialize a string' algorithm."},
    {"serialize_identifier", kg_serialize_identifier, METH_O,
     "serialize_identifier(text: str) -> str\n\n"
     "Serialize text as a CSS identifier, escaping characters as needed, "
     "following the CSSOM 'serialize an identifier' algorithm."},
    {"tokenize", (PyCFunction)(void (*)(void))kg_tokenize_py, METH_FASTCALL | METH_KEYWORDS,
     "tokenize(source: str, /, *, comments: bool = True) -> TokenStream\n\n"
     "Tokenize source following CSS Syntax Level 3. Never raises for bad CSS, errors are recorded "
     "in TokenStream.errors. With comments=False, comment tokens are left out."},
    {"parse_component_value_list", (PyCFunction)(void (*)(void))kg_parse_component_value_list,
     METH_FASTCALL | METH_KEYWORDS,
     "parse_component_value_list(source: str, /, *, comments: bool = True) -> ComponentValueList\n\n"
     "Parse source into a list of component values (CSS Syntax 'parse a list of component values')."},
    {"parse_one_component_value", (PyCFunction)(void (*)(void))kg_parse_one_component_value,
     METH_FASTCALL | METH_KEYWORDS,
     "parse_one_component_value(source: str, /, *, comments: bool = True) -> Token | Block | Function | ParseError\n\n"
     "Parse source as exactly one component value, ignoring surrounding whitespace and comments. "
     "Returns a ParseError of kind 'empty' or 'extra-input' if there is not exactly one."},
    {"build_properties", (PyCFunction)(void (*)(void))kg_build_properties, METH_FASTCALL,
     "build_properties(nodes: NodeList | list, cls: type[PropertyData], parent: weakref | None, /) -> tuple[list, bool]\n\n"
     "Create a cls instance for every Declaration in nodes, other items are kept as they are. Returns the list and "
     "whether it has other items. See block_from_nodes() in kgcss.cssom.declarations."},
    {"find_urls", (PyCFunction)(void (*)(void))kg_find_urls, METH_FASTCALL,
     "find_urls(values: ComponentValueList, top_level_rules: bool, /) -> list[tuple[int, int, str, str]]\n\n"
     "The C version of find_urls() in kgcss.cssom.urls: (start, end, url, kind) for every URL."},
    {"write_declarations", (PyCFunction)(void (*)(void))kg_write_declarations, METH_FASTCALL,
     "write_declarations(items: list[Property | Comment], flags: int, indent: str, /) -> list[str] | NotImplemented\n\n"
     "The lines of declaration_lines() in kgcss.cssom.serializer, each starting with indent, for the pretty and "
     "normalized format. NotImplemented for anything the Python version must handle."},
    {"write_values", (PyCFunction)(void (*)(void))kg_write_values, METH_FASTCALL,
     "write_values(values: ComponentValueList, flags: int, /) -> str | None | NotImplemented\n\n"
     "The C version of kgcss.cssom.serializer.TokenWriter, see write_values() there. Returns None for "
     "values that cannot be written attribute-safe, NotImplemented when the Python version must be used."},
    {"parse_stylesheet", (PyCFunction)(void (*)(void))kg_parse_stylesheet, METH_FASTCALL | METH_KEYWORDS,
     "parse_stylesheet(source: str, /, *, comments: bool = True, encoding: str | None = None) -> NodeList\n\n"
     "Parse a stylesheet into at-rules and qualified rules (CSS Syntax 'parse a stylesheet'). CDO/CDC are ignored between rules. With comments=True, top level comments are included as COMMENT tokens. encoding is recorded as NodeList.encoding; use kgcss.parse_stylesheet() to parse bytes."},
    {"parse_rule_list", (PyCFunction)(void (*)(void))kg_parse_rule_list, METH_FASTCALL | METH_KEYWORDS,
     "parse_rule_list(source: str | Block, /, *, comments: bool = True) -> NodeList\n\n"
     "Parse a list of rules. Like parse_stylesheet() but CDO/CDC are not special (CSS Syntax 2021 'parse a list of rules')."},
    {"parse_block_contents", (PyCFunction)(void (*)(void))kg_parse_block_contents, METH_FASTCALL | METH_KEYWORDS,
     "parse_block_contents(source: str | Block, /, *, comments: bool = True) -> NodeList\n\n"
     "Parse declarations and nested rules, as in a style rule's block or a style attribute (CSS Syntax 'parse a block\'s contents'). A top level } ends the input."},
    {"parse_one_rule", (PyCFunction)(void (*)(void))kg_parse_one_rule, METH_FASTCALL | METH_KEYWORDS,
     "parse_one_rule(source: str, /, *, comments: bool = True) -> AtRule | QualifiedRule | ParseError\n\n"
     "Parse exactly one rule, surrounded by optional whitespace and comments."},
    {"parse_one_declaration", (PyCFunction)(void (*)(void))kg_parse_one_declaration, METH_FASTCALL | METH_KEYWORDS,
     "parse_one_declaration(source: str, /, *, comments: bool = True) -> Declaration | ParseError\n\n"
     "Parse one declaration (CSS Syntax 'parse a declaration'). Input after a top level ; is ignored."},
    {NULL, NULL, 0, NULL}
};

static int
module_exec(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->empty_string = PyUnicode_FromStringAndSize("", 0);
    if (state->empty_string == NULL) return -1;
    state->str_important = PyUnicode_InternFromString("important");
    if (state->str_important == NULL) return -1;
    if (PyModule_AddIntConstant(module, "GIL_DISABLED_BUILD", KG_GIL_DISABLED_BUILD) != 0) return -1;
    if (kg_types_init(module) != 0) return -1;
    if (kg_values_init(module) != 0) return -1;
    if (kg_properties_init(module) != 0) return -1;
    return kg_syntax_init(module);
}

static int
module_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->empty_string);
    Py_VISIT(state->str_important);
    if (kg_properties_traverse(module, visit, arg) != 0) return -1;
    if (kg_types_traverse(module, visit, arg) != 0) return -1;
    if (kg_values_traverse(module, visit, arg) != 0) return -1;
    return kg_syntax_traverse(module, visit, arg);
}

static int
module_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->empty_string);
    Py_CLEAR(state->str_important);
    kg_properties_clear(module);
    kg_types_clear(module);
    kg_values_clear(module);
    kg_syntax_clear(module);
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
