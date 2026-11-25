/*
 * tokenizer_impl.h
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * Template body of the tokenizer, following CSS Syntax Level 3 §4.
 * Included by tokenizer.c once per PEP 393 kind, with CHAR_T set to the
 * code unit type and FN(name) adding a kind-specific suffix.
 *
 * The input is never preprocessed (that would shift offsets): CRLF, CR and
 * FF are treated as newlines, and NUL and surrogates as U+FFFD, on the fly.
 *
 * Nothing in here touches Python objects except FN(unescape), so FN(run)
 * can be called without an attached thread state.
 */

#ifndef CHAR_T
#error "CHAR_T must be defined before including tokenizer_impl.h"
#endif

#define DATA ((const CHAR_T *)s->data)

static inline uint32_t
FN(peek)(const kg_tokenizer *s, uint32_t n) {
    return (s->pos + n < s->len) ? (uint32_t)DATA[s->pos + n] : KG_EOF;
}

static inline bool
FN(needs_processing)(uint32_t c) {
    return c == 0 || (sizeof(CHAR_T) > 1 && kg_is_surrogate(c));
}

/* Length of the whitespace at pos + n, treating CRLF as one unit. 0 if none. */
static inline uint32_t
FN(ws_len)(const kg_tokenizer *s, uint32_t n) {
    uint32_t c = FN(peek)(s, n);
    if (c == '\r' && FN(peek)(s, n + 1) == '\n') return 2;
    return kg_is_whitespace(c) ? 1 : 0;
}

static inline bool
FN(valid_escape_at)(const kg_tokenizer *s, uint32_t n) {
    return FN(peek)(s, n) == '\\' && !kg_is_newline(FN(peek)(s, n + 1));
}

static inline bool
FN(starts_ident_at)(const kg_tokenizer *s, uint32_t n) {
    uint32_t c = FN(peek)(s, n);
    if (c == '-') {
        uint32_t c2 = FN(peek)(s, n + 1);
        return kg_is_ident_start(c2) || c2 == '-' || FN(valid_escape_at)(s, n + 1);
    }
    if (kg_is_ident_start(c)) return true;
    return c == '\\' && FN(valid_escape_at)(s, n);
}

static inline bool
FN(starts_number_at)(const kg_tokenizer *s, uint32_t n) {
    uint32_t c = FN(peek)(s, n);
    if (c == '+' || c == '-') {
        uint32_t c2 = FN(peek)(s, n + 1);
        if (kg_is_digit(c2)) return true;
        return c2 == '.' && kg_is_digit(FN(peek)(s, n + 2));
    }
    if (c == '.') return kg_is_digit(FN(peek)(s, n + 1));
    return kg_is_digit(c);
}

static inline void
FN(consume_whitespace)(kg_tokenizer *s) {
    while (s->pos < s->len && kg_is_whitespace(DATA[s->pos])) s->pos++;
}

/* Called with pos just after a backslash that starts a valid escape. */
static void
FN(consume_escape)(kg_tokenizer *s) {
    uint32_t c = FN(peek)(s, 0);
    if (kg_is_hex_digit(c)) {
        uint32_t n = 0;
        while (n < 6 && kg_is_hex_digit(FN(peek)(s, 0))) { s->pos++; n++; }
        s->pos += FN(ws_len)(s, 0);
    } else if (c == KG_EOF) {
        kg_add_error(s, s->pos - 1, KG_ERR_EOF_IN_ESCAPE);
    } else s->pos++;
}

static void
FN(consume_ident_sequence)(kg_tokenizer *s, uint8_t *flags) {
    while (s->pos < s->len) {
        uint32_t c = DATA[s->pos];
        if (kg_is_ident_char(c)) {
            if (FN(needs_processing)(c)) *flags |= KG_TF_NEEDS_PROCESSING;
            s->pos++;
        } else if (c == '\\' && !kg_is_newline(FN(peek)(s, 1))) {
            s->pos++;
            FN(consume_escape)(s);
            *flags |= KG_TF_NEEDS_PROCESSING;
        } else break;
    }
}

/* Decode the code point at *i in [*, end), advancing *i. Returns KG_EOF for
 * input that produces nothing (an escaped newline or a backslash at the end
 * of a string). Mirrors the escape rules of the tokenizer. */
static inline uint32_t
FN(decode_next)(const CHAR_T *d, uint32_t *i, uint32_t end, bool in_string) {
    uint32_t c = d[*i];
    if (c != '\\') {
        (*i)++;
        return FN(needs_processing)(c) ? KG_REPLACEMENT_CHAR : c;
    }
    (*i)++;
    if (*i >= end) return in_string ? KG_EOF : KG_REPLACEMENT_CHAR;
    c = d[*i];
    if (kg_is_newline(c)) {
        (*i)++;
        if (c == '\r' && *i < end && d[*i] == '\n') (*i)++;
        return KG_EOF;
    }
    if (kg_is_hex_digit(c)) {
        uint32_t v = 0, n = 0;
        while (n < 6 && *i < end && kg_is_hex_digit(d[*i])) { v = v * 16 + kg_hex_value(d[*i]); (*i)++; n++; }
        if (*i < end && kg_is_whitespace(d[*i])) {
            if (d[*i] == '\r' && *i + 1 < end && d[*i + 1] == '\n') (*i)++;
            (*i)++;
        }
        return (v == 0 || kg_is_surrogate(v) || v > 0x10FFFF) ? KG_REPLACEMENT_CHAR : v;
    }
    (*i)++;
    return FN(needs_processing)(c) ? KG_REPLACEMENT_CHAR : c;
}

static PyObject *
FN(unescape)(const CHAR_T *d, uint32_t start, uint32_t end, bool in_string) {
    Py_ssize_t n = 0;
    uint32_t maxchar = 0;
    for (uint32_t i = start; i < end;) {
        uint32_t c = FN(decode_next)(d, &i, end, in_string);
        if (c == KG_EOF) continue;
        n++;
        if (c > maxchar) maxchar = c;
    }
    PyObject *ans = PyUnicode_New(n, maxchar);
    if (ans == NULL) return NULL;
    const int okind = PyUnicode_KIND(ans);
    void *odata = PyUnicode_DATA(ans);
    Py_ssize_t o = 0;
    for (uint32_t i = start; i < end;) {
        uint32_t c = FN(decode_next)(d, &i, end, in_string);
        if (c != KG_EOF) PyUnicode_WRITE(okind, odata, o++, c);
    }
    assert(o == n);
    return ans;
}

/* Whether the (possibly escaped) ident in [start, end) is "url", ASCII case-insensitively */
static bool
FN(name_is_url)(const CHAR_T *d, uint32_t start, uint32_t end) {
    static const char url[] = "url";
    unsigned n = 0;
    for (uint32_t i = start; i < end;) {
        uint32_t c = FN(decode_next)(d, &i, end, false);
        if (c == KG_EOF) continue;
        if (n >= 3) return false;
        if (c >= 'A' && c <= 'Z') c |= 0x20;
        if (c != (uint32_t)url[n]) return false;
        n++;
    }
    return n == 3;
}

/* Called with pos just after the opening quote */
static void
FN(consume_string)(kg_tokenizer *s, uint32_t start, uint32_t quote) {
    uint8_t flags = 0;
    const uint32_t vstart = s->pos;
    while (true) {
        if (s->pos >= s->len) {
            kg_add_error(s, s->pos, KG_ERR_EOF_IN_STRING);
            kg_add_token(s, KG_TOK_STRING, start, s->pos, vstart, s->pos, flags | KG_TF_UNCLOSED);
            return;
        }
        uint32_t c = DATA[s->pos];
        if (c == quote) {
            s->pos++;
            kg_add_token(s, KG_TOK_STRING, start, s->pos, vstart, s->pos - 1, flags);
            return;
        }
        if (kg_is_newline(c)) {
            kg_add_error(s, s->pos, KG_ERR_NEWLINE_IN_STRING);
            kg_add_token(s, KG_TOK_BAD_STRING, start, s->pos, vstart, s->pos, flags);
            return;
        }
        if (c == '\\') {
            flags |= KG_TF_NEEDS_PROCESSING;
            s->pos++;
            uint32_t n = FN(peek)(s, 0);
            if (n == KG_EOF) continue;
            if (kg_is_newline(n)) s->pos += FN(ws_len)(s, 0);
            else FN(consume_escape)(s);
            continue;
        }
        if (FN(needs_processing)(c)) flags |= KG_TF_NEEDS_PROCESSING;
        s->pos++;
    }
}

static void
FN(consume_numeric)(kg_tokenizer *s, uint32_t start) {
    uint8_t flags = KG_TF_IS_INTEGER;
    uint32_t c = FN(peek)(s, 0);
    if (c == '+' || c == '-') s->pos++;
    while (kg_is_digit(FN(peek)(s, 0))) s->pos++;
    if (FN(peek)(s, 0) == '.' && kg_is_digit(FN(peek)(s, 1))) {
        s->pos += 2;
        while (kg_is_digit(FN(peek)(s, 0))) s->pos++;
        flags &= (uint8_t)~KG_TF_IS_INTEGER;
    }
    c = FN(peek)(s, 0);
    if (c == 'e' || c == 'E') {
        uint32_t c1 = FN(peek)(s, 1);
        uint32_t skip = 0;
        if (kg_is_digit(c1)) skip = 2;
        else if ((c1 == '+' || c1 == '-') && kg_is_digit(FN(peek)(s, 2))) skip = 3;
        if (skip) {
            s->pos += skip;
            while (kg_is_digit(FN(peek)(s, 0))) s->pos++;
            flags &= (uint8_t)~KG_TF_IS_INTEGER;
        }
    }
    const uint32_t number_end = s->pos;
    if (FN(starts_ident_at)(s, 0)) {
        FN(consume_ident_sequence)(s, &flags);
        kg_add_token(s, KG_TOK_DIMENSION, start, s->pos, start, number_end, flags);
    } else if (FN(peek)(s, 0) == '%') {
        s->pos++;
        kg_add_token(s, KG_TOK_PERCENTAGE, start, s->pos, start, number_end, flags);
    } else kg_add_token(s, KG_TOK_NUMBER, start, s->pos, start, number_end, flags);
}

static void
FN(consume_bad_url_remnants)(kg_tokenizer *s) {
    while (s->pos < s->len) {
        uint32_t c = DATA[s->pos];
        if (c == ')') { s->pos++; return; }
        if (c == '\\' && !kg_is_newline(FN(peek)(s, 1))) {
            s->pos++;
            FN(consume_escape)(s);
        } else s->pos++;
    }
}

/* Called with pos after "url(" and any leading whitespace consumed by the caller */
static void
FN(consume_url)(kg_tokenizer *s, uint32_t start) {
    uint8_t flags = 0;
    FN(consume_whitespace)(s);
    const uint32_t vstart = s->pos;
    while (true) {
        if (s->pos >= s->len) {
            kg_add_error(s, s->pos, KG_ERR_EOF_IN_URL);
            kg_add_token(s, KG_TOK_URL, start, s->pos, vstart, s->pos, flags | KG_TF_UNCLOSED);
            return;
        }
        uint32_t c = DATA[s->pos];
        if (c == ')') {
            s->pos++;
            kg_add_token(s, KG_TOK_URL, start, s->pos, vstart, s->pos - 1, flags);
            return;
        }
        if (kg_is_whitespace(c)) {
            const uint32_t vend = s->pos;
            FN(consume_whitespace)(s);
            c = FN(peek)(s, 0);
            if (c == ')') {
                s->pos++;
                kg_add_token(s, KG_TOK_URL, start, s->pos, vstart, vend, flags);
            } else if (c == KG_EOF) {
                kg_add_error(s, s->pos, KG_ERR_EOF_IN_URL);
                kg_add_token(s, KG_TOK_URL, start, s->pos, vstart, vend, flags | KG_TF_UNCLOSED);
            } else {
                kg_add_error(s, s->pos, KG_ERR_BAD_URL);
                FN(consume_bad_url_remnants)(s);
                kg_add_token(s, KG_TOK_BAD_URL, start, s->pos, start, s->pos, 0);
            }
            return;
        }
        if (c == '"' || c == '\'' || c == '(' || kg_is_non_printable(c)) {
            kg_add_error(s, s->pos, KG_ERR_BAD_URL);
            FN(consume_bad_url_remnants)(s);
            kg_add_token(s, KG_TOK_BAD_URL, start, s->pos, start, s->pos, 0);
            return;
        }
        if (c == '\\') {
            if (kg_is_newline(FN(peek)(s, 1))) {
                kg_add_error(s, s->pos, KG_ERR_INVALID_ESCAPE);
                FN(consume_bad_url_remnants)(s);
                kg_add_token(s, KG_TOK_BAD_URL, start, s->pos, start, s->pos, 0);
                return;
            }
            s->pos++;
            FN(consume_escape)(s);
            flags |= KG_TF_NEEDS_PROCESSING;
            continue;
        }
        if (FN(needs_processing)(c)) flags |= KG_TF_NEEDS_PROCESSING;
        s->pos++;
    }
}

static void
FN(consume_ident_like)(kg_tokenizer *s, uint32_t start) {
    uint8_t flags = 0;
    FN(consume_ident_sequence)(s, &flags);
    const uint32_t name_end = s->pos;
    if (FN(peek)(s, 0) != '(') {
        kg_add_token(s, KG_TOK_IDENT, start, s->pos, start, name_end, flags);
        return;
    }
    s->pos++;
    if (name_end - start >= 3 && FN(name_is_url)(DATA, start, name_end)) {
        /* While the next two code points are whitespace, consume the next one */
        uint32_t w;
        while ((w = FN(ws_len)(s, 0)) && FN(ws_len)(s, w)) s->pos += w;
        uint32_t c = FN(peek)(s, 0);
        w = FN(ws_len)(s, 0);
        if (w) c = FN(peek)(s, w);
        if (c != '"' && c != '\'') {
            FN(consume_url)(s, start);
            return;
        }
    }
    kg_add_token(s, KG_TOK_FUNCTION, start, s->pos, start, name_end, flags);
}

static void
FN(consume_comment)(kg_tokenizer *s, uint32_t start) {
    s->pos += 2;
    const uint32_t vstart = s->pos;
    while (s->pos + 1 < s->len) {
        if (DATA[s->pos] == '*' && DATA[s->pos + 1] == '/') {
            s->pos += 2;
            if (s->keep_comments) kg_add_token(s, KG_TOK_COMMENT, start, s->pos, vstart, s->pos - 2, 0);
            return;
        }
        s->pos++;
    }
    s->pos = s->len;
    kg_add_error(s, start, KG_ERR_EOF_IN_COMMENT);
    if (s->keep_comments) kg_add_token(s, KG_TOK_COMMENT, start, s->pos, vstart, s->pos, KG_TF_UNCLOSED);
}

static inline void
FN(single)(kg_tokenizer *s, kg_token_type type, uint32_t start, uint32_t length) {
    s->pos += length;
    kg_add_token(s, type, start, s->pos, start, s->pos, 0);
}

static void
FN(run)(kg_tokenizer *s) {
    while (s->pos < s->len && !s->oom) {
        const uint32_t start = s->pos;
        const uint32_t c = DATA[start];
        switch (c) {
            case ' ': case '\t': case '\n': case '\r': case '\f':
                FN(consume_whitespace)(s);
                kg_add_token(s, KG_TOK_WHITESPACE, start, s->pos, start, s->pos, 0);
                break;
            case '"': case '\'':
                s->pos++;
                FN(consume_string)(s, start, c);
                break;
            case '#':
                if (kg_is_ident_char(FN(peek)(s, 1)) || FN(valid_escape_at)(s, 1)) {
                    s->pos++;
                    uint8_t flags = FN(starts_ident_at)(s, 0) ? KG_TF_HASH_ID : 0;
                    FN(consume_ident_sequence)(s, &flags);
                    kg_add_token(s, KG_TOK_HASH, start, s->pos, start + 1, s->pos, flags);
                } else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '(': FN(single)(s, KG_TOK_LEFT_PAREN, start, 1); break;
            case ')': FN(single)(s, KG_TOK_RIGHT_PAREN, start, 1); break;
            case '[': FN(single)(s, KG_TOK_LEFT_SQUARE, start, 1); break;
            case ']': FN(single)(s, KG_TOK_RIGHT_SQUARE, start, 1); break;
            case '{': FN(single)(s, KG_TOK_LEFT_CURLY, start, 1); break;
            case '}': FN(single)(s, KG_TOK_RIGHT_CURLY, start, 1); break;
            case ',': FN(single)(s, KG_TOK_COMMA, start, 1); break;
            case ':': FN(single)(s, KG_TOK_COLON, start, 1); break;
            case ';': FN(single)(s, KG_TOK_SEMICOLON, start, 1); break;
            case '+': case '.':
                if (FN(starts_number_at)(s, 0)) FN(consume_numeric)(s, start);
                else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '-':
                if (FN(starts_number_at)(s, 0)) FN(consume_numeric)(s, start);
                else if (FN(peek)(s, 1) == '-' && FN(peek)(s, 2) == '>') FN(single)(s, KG_TOK_CDC, start, 3);
                else if (FN(starts_ident_at)(s, 0)) FN(consume_ident_like)(s, start);
                else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '<':
                if (FN(peek)(s, 1) == '!' && FN(peek)(s, 2) == '-' && FN(peek)(s, 3) == '-')
                    FN(single)(s, KG_TOK_CDO, start, 4);
                else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '@':
                if (FN(starts_ident_at)(s, 1)) {
                    uint8_t flags = 0;
                    s->pos++;
                    FN(consume_ident_sequence)(s, &flags);
                    kg_add_token(s, KG_TOK_AT_KEYWORD, start, s->pos, start + 1, s->pos, flags);
                } else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '\\':
                if (FN(valid_escape_at)(s, 0)) FN(consume_ident_like)(s, start);
                else {
                    kg_add_error(s, start, KG_ERR_INVALID_ESCAPE);
                    FN(single)(s, KG_TOK_DELIM, start, 1);
                }
                break;
            case '/':
                if (FN(peek)(s, 1) == '*') FN(consume_comment)(s, start);
                else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
            case '0': case '1': case '2': case '3': case '4':
            case '5': case '6': case '7': case '8': case '9':
                FN(consume_numeric)(s, start);
                break;
            default:
                if (kg_is_ident_start(c)) FN(consume_ident_like)(s, start);
                else FN(single)(s, KG_TOK_DELIM, start, 1);
                break;
        }
    }
}

/* Offsets at which each line starts. CRLF, CR, LF and FF end a line. */
static bool
FN(build_line_table)(const CHAR_T *d, uint32_t len, uint32_t **out, uint32_t *count) {
    uint32_t cap = 64, n = 0;
    uint32_t *lines = PyMem_RawMalloc(cap * sizeof(uint32_t));
    if (lines == NULL) return false;
    lines[n++] = 0;
    for (uint32_t i = 0; i < len; i++) {
        const CHAR_T c = d[i];
        if (c != '\n' && c != '\r' && c != '\f') continue;
        if (c == '\r' && i + 1 < len && d[i + 1] == '\n') i++;
        if (n >= cap) {
            cap *= 2;
            uint32_t *nl = PyMem_RawRealloc(lines, cap * sizeof(uint32_t));
            if (nl == NULL) { PyMem_RawFree(lines); return false; }
            lines = nl;
        }
        lines[n++] = i + 1;
    }
    *out = lines;
    *count = n;
    return true;
}

#undef DATA
