/*
 * tokens.h
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Token records produced by the tokenizer. See docs/plan-01-tokenizer-syntax.md.
 */

#pragma once

#include "kgcss.h"
#include <string.h>

/* Keep in sync with TokenType in src/kgcss/tokens.py (a test checks this). */
typedef enum {
    KG_TOK_IDENT = 1,
    KG_TOK_FUNCTION,
    KG_TOK_AT_KEYWORD,
    KG_TOK_HASH,
    KG_TOK_STRING,
    KG_TOK_BAD_STRING,
    KG_TOK_URL,
    KG_TOK_BAD_URL,
    KG_TOK_DELIM,
    KG_TOK_NUMBER,
    KG_TOK_PERCENTAGE,
    KG_TOK_DIMENSION,
    KG_TOK_WHITESPACE,
    KG_TOK_CDO,
    KG_TOK_CDC,
    KG_TOK_COLON,
    KG_TOK_SEMICOLON,
    KG_TOK_COMMA,
    KG_TOK_LEFT_SQUARE,
    KG_TOK_RIGHT_SQUARE,
    KG_TOK_LEFT_PAREN,
    KG_TOK_RIGHT_PAREN,
    KG_TOK_LEFT_CURLY,
    KG_TOK_RIGHT_CURLY,
    KG_TOK_COMMENT,
    KG_TOK__COUNT
} kg_token_type;

/* Token flags */
enum {
    /* The value range contains escapes, NUL or surrogates, so it must go
     * through the unescape routine rather than being a plain substring. */
    KG_TF_NEEDS_PROCESSING = 1u << 0,
    /* Numeric tokens: the number has integer type */
    KG_TF_IS_INTEGER = 1u << 1,
    /* Hash tokens: type flag is "id" (otherwise "unrestricted") */
    KG_TF_HASH_ID = 1u << 2,
    /* String, url or comment tokens terminated by EOF instead of their
     * closer, and block/function openers with no matching closing token */
    KG_TF_UNCLOSED = 1u << 3,
};

typedef struct {
    uint32_t start, end;    /* code point offsets of the exact source text */
    uint32_t vstart, vend;  /* range holding the value: name, string body, url, number part */
    /* Openers ({ [ ( and FUNCTION): index of the matching closing token, or
     * the number of tokens if unclosed. Unused for other tokens. */
    uint32_t match;
    uint8_t type;           /* kg_token_type */
    uint8_t flags;
} kg_token;

/* Keep in sync with _ERROR_MESSAGES in csrc/types.c */
typedef enum {
    KG_ERR_EOF_IN_COMMENT = 0,
    KG_ERR_EOF_IN_STRING,
    KG_ERR_NEWLINE_IN_STRING,
    KG_ERR_EOF_IN_URL,
    KG_ERR_BAD_URL,
    KG_ERR_INVALID_ESCAPE,
    KG_ERR_EOF_IN_ESCAPE,
    /* Syntax level errors, not produced by the tokenizer */
    KG_ERR_EMPTY,
    KG_ERR_EXTRA_INPUT,
    KG_ERR__COUNT
} kg_error_kind;

typedef struct {
    uint32_t offset;
    uint32_t kind;  /* kg_error_kind */
} kg_error;

/* Output of a tokenizer run. All memory is allocated with PyMem_Raw* so the
 * tokenizer can run without an attached thread state. */
typedef struct {
    kg_token *tokens;
    uint32_t count, capacity;
    kg_error *errors;
    uint32_t error_count, error_capacity;
} kg_token_array;

/* Returns false on memory allocation failure (no Python exception is set,
 * the caller must call PyErr_NoMemory() with an attached thread state). */
bool kg_tokenize(int kind, const void *data, uint32_t len, bool keep_comments, kg_token_array *out);
void kg_token_array_free(kg_token_array *arr);

/* Characters */
#define KG_EOF ((uint32_t)0xFFFFFFFFu)
#define KG_REPLACEMENT_CHAR ((uint32_t)0xFFFD)

static inline bool kg_is_opener(uint8_t type) {
    return type == KG_TOK_LEFT_CURLY || type == KG_TOK_LEFT_SQUARE || type == KG_TOK_LEFT_PAREN || type == KG_TOK_FUNCTION;
}

static inline bool kg_is_newline(uint32_t c) { return c == '\n' || c == '\r' || c == '\f'; }
static inline bool kg_is_whitespace(uint32_t c) { return c == ' ' || c == '\t' || kg_is_newline(c); }
static inline bool kg_is_digit(uint32_t c) { return c >= '0' && c <= '9'; }
static inline bool kg_is_hex_digit(uint32_t c) {
    return kg_is_digit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}
static inline uint32_t kg_hex_value(uint32_t c) {
    if (c <= '9') return c - '0';
    return (c | 0x20) - 'a' + 10;
}
static inline bool kg_is_surrogate(uint32_t c) { return c >= 0xD800 && c <= 0xDFFF; }
/* NUL and surrogates are replaced by U+FFFD during (virtual) preprocessing,
 * so they are treated as non-ASCII ident code points. */
static inline bool kg_is_ident_start(uint32_t c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || (c >= 0x80 && c != KG_EOF) || c == 0;
}
static inline bool kg_is_ident_char(uint32_t c) { return kg_is_ident_start(c) || kg_is_digit(c) || c == '-'; }
static inline bool kg_is_non_printable(uint32_t c) {
    /* NUL is excluded since it is preprocessed to U+FFFD */
    return (c >= 0x1 && c <= 0x8) || c == 0xB || (c >= 0xE && c <= 0x1F) || c == 0x7F;
}

/* Build the value of the range [start, end), resolving escapes, NUL and
 * surrogates. in_string selects string token semantics for escaped newlines
 * and a backslash at the end. Needs an attached thread state. */
PyObject *kg_unescape(int kind, const void *data, uint32_t start, uint32_t end, bool in_string);

/* Offsets of the start of every line (lines[0] == 0). Memory from
 * PyMem_RawMalloc. Returns false on allocation failure (no exception set). */
bool kg_build_line_table(int kind, const void *data, uint32_t len, uint32_t **lines, uint32_t *count);

/* types.c */
int kg_types_init(PyObject *module);
int kg_types_traverse(PyObject *module, visitproc visit, void *arg);
void kg_types_clear(PyObject *module);
PyObject *kg_tokenize_py(PyObject *module, PyObject *args, PyObject *kwds);
