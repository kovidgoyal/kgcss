/*
 * writer.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the GPL3 license.
 *
 * The token writer of the CSSOM serializer, working directly on the token
 * records of a ComponentValueList, so no Token objects are created. This is
 * a port of TokenWriter and write_values() in src/kgcss/cssom/serializer.py,
 * which remain the reference implementation: every decision here mirrors one
 * there, and property tests check that both give the same output.
 *
 * Nesting is handled with an explicit stack, so deep input cannot overflow
 * the C stack. Only immutable objects are read, so this is safe to run
 * concurrently without the GIL.
 */

#include "objects.h"
#include <math.h>

/* Keep in sync with the WRITE_* flags in src/kgcss/cssom/serializer.py */
enum {
    W_NORMALIZE = 1u << 0,
    W_SELECTOR = 1u << 1,
    W_VERBATIM = 1u << 2,
    W_ATTRIBUTE_SAFE = 1u << 3,
    W_KEEP_COMMENTS = 1u << 4,
    /* Return everything written, rather than the significant part (TokenWriter.write()) */
    W_RAW = 1u << 5,
};

typedef enum { WS_OK = 0, WS_ERROR, WS_UNSAFE, WS_FALLBACK } w_status;

typedef struct {
    uint32_t cur, end;  /* token index of the next item, and of the end */
    Py_UCS4 closer, kind;
} w_frame;

typedef struct {
    const kg_TokenStream *ts;
    int kind;
    const void *src;
    bool normalize, selector, value_mode, attribute_safe, keep_comments;
    /* Output */
    Py_UCS4 *buf;
    size_t len, cap;
    Py_UCS4 small[256];
    size_t piece_start;  /* where the last piece written starts: out[-1] in the Python version */
    /* State, named as in the Python version */
    bool pending_space, at_start, need_newline, last_needs_newline, dropped_comment, last_text_nonempty;
    bool have_first;
    size_t first_significant, significant_end;
    /* Stack */
    w_frame *frames;
    size_t depth, frames_cap;
    w_frame small_frames[16];
} w_state;

/* ---------------------------------------------------------------------------
 * Output buffer
 * ------------------------------------------------------------------------- */

static bool
reserve(w_state *w, size_t extra) {
    if (KG_LIKELY(w->len + extra <= w->cap)) return true;
    size_t cap = w->cap * 2;
    if (cap < w->len + extra) cap = w->len + extra + 64;
    Py_UCS4 *n = PyMem_Malloc(cap * sizeof(Py_UCS4));
    if (n == NULL) { PyErr_NoMemory(); return false; }
    memcpy(n, w->buf, w->len * sizeof(Py_UCS4));
    if (w->buf != w->small) PyMem_Free(w->buf);
    w->buf = n;
    w->cap = cap;
    return true;
}

static inline bool
put(w_state *w, Py_UCS4 c) {
    if (!reserve(w, 1)) return false;
    w->buf[w->len++] = c;
    return true;
}

static bool
put_ascii(w_state *w, const char *s) {
    size_t n = strlen(s);
    if (!reserve(w, n)) return false;
    for (size_t i = 0; i < n; i++) w->buf[w->len++] = (Py_UCS4)(unsigned char)s[i];
    return true;
}

static inline Py_UCS4
src_at(const w_state *w, uint32_t i) { return PyUnicode_READ(w->kind, w->src, i); }

static bool
put_source(w_state *w, uint32_t start, uint32_t end) {
    if (!reserve(w, end - start)) return false;
    for (uint32_t i = start; i < end; i++) w->buf[w->len++] = src_at(w, i);
    return true;
}

/* A run of characters, from the source or from a str created for an unescaped value */
typedef struct {
    int kind;
    const void *data;
    Py_ssize_t start, end;
    PyObject *owned;
} w_chars;

static inline Py_UCS4
ch_at(const w_chars *c, Py_ssize_t i) { return PyUnicode_READ(c->kind, c->data, i); }

/* The value of [a, b) of token t: unescaped if needed, as for Token.value */
static bool
token_chars(w_state *w, const kg_token *t, uint32_t a, uint32_t b, bool in_string, w_chars *out) {
    out->owned = NULL;
    if (t->flags & KG_TF_NEEDS_PROCESSING) {
        out->owned = kg_unescape(w->kind, w->src, a, b, in_string);
        if (out->owned == NULL) return false;
        out->kind = PyUnicode_KIND(out->owned);
        out->data = PyUnicode_DATA(out->owned);
        out->start = 0;
        out->end = PyUnicode_GET_LENGTH(out->owned);
    } else {
        out->kind = w->kind;
        out->data = w->src;
        out->start = a;
        out->end = b;
    }
    return true;
}

static inline void
chars_free(w_chars *c) { Py_CLEAR(c->owned); }

static bool
put_hex_escape(w_state *w, Py_UCS4 ch) {
    char buf[16];
    snprintf(buf, sizeof buf, "\\%x ", (unsigned)ch);
    return put_ascii(w, buf);
}

static inline bool
is_control(Py_UCS4 ch) { return (ch >= 0x1 && ch <= 0x1f) || ch == 0x7f; }

/* serialize_string(), or serialize_string_single_quoted() */
static bool
put_string(w_state *w, const w_chars *c, bool single_quoted) {
    if (!reserve(w, (size_t)(c->end - c->start) + 2)) return false;
    const Py_UCS4 quote = single_quoted ? '\'' : '"';
    w->buf[w->len++] = quote;
    for (Py_ssize_t i = c->start; i < c->end; i++) {
        Py_UCS4 ch = ch_at(c, i);
        bool ok;
        if (ch == 0) ok = put(w, 0xfffd);
        else if (is_control(ch)) ok = put_hex_escape(w, ch);
        else if (ch == quote || ch == '\\') ok = put(w, '\\') && put(w, ch);
        else if (single_quoted && ch == '"') ok = put_ascii(w, "\\22 ");
        else ok = put(w, ch);
        if (!ok) return false;
    }
    return put(w, quote);
}

static inline bool
url_unsafe(Py_UCS4 c) {
    return c < 0x20 || c == 0x7f || c == ' ' || c == '"' || c == '\'' || c == '(' || c == ')' || c == '\\';
}

/* format_url() */
static bool
put_url(w_state *w, const w_chars *c, bool attribute_safe) {
    bool plain = c->end > c->start;
    for (Py_ssize_t i = c->start; plain && i < c->end; i++) if (url_unsafe(ch_at(c, i))) plain = false;
    if (!put_ascii(w, "url(")) return false;
    if (plain) {
        if (!reserve(w, (size_t)(c->end - c->start))) return false;
        for (Py_ssize_t i = c->start; i < c->end; i++) w->buf[w->len++] = ch_at(c, i);
    } else if (!put_string(w, c, attribute_safe)) return false;
    return put(w, ')');
}

static inline bool
is_hex(Py_UCS4 c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }

/* HEX_ESCAPE_AT_END.search() on buf[lo, hi): an unescaped backslash and 1 to 6 hex digits at the end */
static bool
hex_escape_at_end(const Py_UCS4 *buf, size_t lo, size_t hi) {
    size_t i = hi;
    unsigned h = 0;
    while (i > lo && h < 7 && is_hex(buf[i - 1])) { i--; h++; }
    if (h == 0 || h > 6 || i == lo || buf[i - 1] != '\\') return false;
    size_t n = 0;
    while (i > lo && buf[i - 1] == '\\') { i--; n++; }
    return n % 2 == 1;
}

/* ---------------------------------------------------------------------------
 * emit() and space()
 * ------------------------------------------------------------------------- */

/* The part of emit() before the text is written */
static w_status
pre_emit(w_state *w, bool significant, bool starts_with_newline) {
    if (w->dropped_comment && significant) {
        w->dropped_comment = false;
        /* Deciding whether a separator is needed means tokenizing: leave it to the Python version */
        if (w->last_text_nonempty) return WS_FALLBACK;
    }
    if (w->need_newline) {
        w->need_newline = false;
        if (!starts_with_newline) {
            if (!put(w, '\n')) return WS_ERROR;
            w->pending_space = false;
        }
    }
    if (significant && !w->have_first) {
        w->have_first = true;
        w->first_significant = w->len;
    }
    w->piece_start = w->len;
    return WS_OK;
}

/* The part of emit() after the text is written */
static inline void
post_emit(w_state *w, bool significant) {
    if (significant) {
        w->significant_end = w->len;
        w->last_needs_newline = false;
        w->last_text_nonempty = true;
    } else w->last_text_nonempty = false;
}

static w_status
emit_char(w_state *w, Py_UCS4 c, bool significant) {
    w_status s = pre_emit(w, significant, kg_is_newline(c));
    if (s != WS_OK) return s;
    if (!put(w, c)) return WS_ERROR;
    post_emit(w, significant);
    return WS_OK;
}

static bool
space(w_state *w) {
    w->last_text_nonempty = false;
    /* A hex escape at the end of the previous token would absorb a single space */
    bool twice = w->len > 0 && hex_escape_at_end(w->buf, w->piece_start, w->len);
    w->piece_start = w->len;
    return put(w, ' ') && (!twice || put(w, ' '));
}

static inline bool
ends_with_space(const w_state *w) { return w->len > 0 && w->buf[w->len - 1] == ' '; }

/* ---------------------------------------------------------------------------
 * Tokens
 * ------------------------------------------------------------------------- */

static inline bool
is_escapable(uint8_t type) {
    return type == KG_TOK_IDENT || type == KG_TOK_AT_KEYWORD || type == KG_TOK_HASH || type == KG_TOK_DIMENSION;
}

static inline bool
is_lone_backslash(const w_state *w, const kg_token *t) {
    return t->type == KG_TOK_DELIM && t->end - t->start == 1 && src_at(w, t->start) == '\\';
}

static inline bool
could_merge(Py_UCS4 c, bool after) {
    if (c >= 0x80) return true;
    if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) return true;
    return c == '-' || c == '_' || c == '\\' || c == '.' || c == '+' || (after && c == '%');
}

/* one_line() on buf[from:] */
static void
one_line(w_state *w, size_t from) {
    size_t o = from;
    for (size_t i = from; i < w->len; i++) {
        Py_UCS4 c = w->buf[i];
        if (c == '\r' && i + 1 < w->len && w->buf[i + 1] == '\n') { w->buf[o++] = ' '; i++; }
        else w->buf[o++] = kg_is_newline(c) ? ' ' : c;
    }
    w->len = o;
}

static bool
has_quote_or_newline(const w_state *w, size_t from) {
    for (size_t i = from; i < w->len; i++) {
        Py_UCS4 c = w->buf[i];
        if (c == '"' || kg_is_newline(c)) return true;
    }
    return false;
}

/* buf[from:].replace('\\"', '\\22 ') */
static bool
hex_escape_quotes(w_state *w, size_t from) {
    size_t count = 0;
    for (size_t i = from; i + 1 < w->len; i++) if (w->buf[i] == '\\' && w->buf[i + 1] == '"') { count++; i++; }
    if (!count) return true;
    size_t n = w->len - from;
    Py_UCS4 *copy = PyMem_Malloc(n * sizeof(Py_UCS4));
    if (copy == NULL) { PyErr_NoMemory(); return false; }
    memcpy(copy, w->buf + from, n * sizeof(Py_UCS4));
    w->len = from;
    bool ok = reserve(w, n + 2 * count);
    for (size_t i = 0; ok && i < n; i++) {
        if (copy[i] == '\\' && i + 1 < n && copy[i + 1] == '"') {
            ok = put_ascii(w, "\\22 ");
            i++;
        } else ok = put(w, copy[i]);
    }
    PyMem_Free(copy);
    return ok;
}

/* verbatim_text() */
static bool
put_verbatim_text(w_state *w, const kg_token *t) {
    if (t->flags & KG_TF_UNCLOSED) {
        w_chars c;
        bool ok;
        switch (t->type) {
            case KG_TOK_STRING:
                if (!token_chars(w, t, t->vstart, t->vend, true, &c)) return false;
                ok = put_string(w, &c, false);
                chars_free(&c);
                return ok;
            case KG_TOK_URL:
                if (!token_chars(w, t, t->vstart, t->vend, false, &c)) return false;
                ok = put_url(w, &c, false);
                chars_free(&c);
                return ok;
            case KG_TOK_COMMENT:
                return put_source(w, t->start, t->end) && put_ascii(w, "*/");
            case KG_TOK_BAD_URL:
                return put_source(w, t->start, t->end) && put(w, ')');
            default:
                break;
        }
    }
    if (is_escapable(t->type)) {
        uint32_t n = 0;
        for (uint32_t i = t->end; i > t->start && src_at(w, i - 1) == '\\'; i--) n++;
        if (n % 2) {
            /* A backslash at the end of the input is an escape for U+FFFD. Anything written
             * after it would be escaped instead, so use the explicit form. */
            return put_source(w, t->start, t->end - 1) && put_ascii(w, "\\fffd ");
        }
    }
    return put_source(w, t->start, t->end);
}

/* verbatim_token() */
static w_status
put_verbatim_token(w_state *w, const kg_token *t) {
    size_t from = w->len;
    if (!put_verbatim_text(w, t)) return WS_ERROR;
    if (w->attribute_safe) {
        if (is_escapable(t->type)) one_line(w, from);
        if (t->type == KG_TOK_BAD_STRING || is_lone_backslash(w, t) || has_quote_or_newline(w, from)) return WS_UNSAFE;
    }
    return WS_OK;
}

static const char *const length_units[] = {
    "px", "em", "rem", "ex", "rex", "ch", "rch", "cap", "rcap", "ic", "ric", "lh", "rlh", "pt", "pc", "in", "cm",
    "mm", "q", "vw", "vh", "vi", "vb", "vmin", "vmax", "svw", "svh", "lvw", "lvh", "dvw", "dvh", NULL,
};

static bool
is_length_unit(const w_chars *c) {
    Py_ssize_t n = c->end - c->start;
    if (n < 1 || n > 4) return false;
    char lower[8];
    for (Py_ssize_t i = 0; i < n; i++) {
        Py_UCS4 ch = ch_at(c, c->start + i);
        if (ch >= 0x80) return false;
        lower[i] = (char)((ch >= 'A' && ch <= 'Z') ? ch + 32 : ch);
    }
    lower[n] = 0;
    for (const char *const *u = length_units; *u; u++) if (strcmp(*u, lower) == 0) return true;
    return false;
}

/* format_number() */
static bool
put_number(w_state *w, const kg_token *t, bool top_level) {
    const uint32_t a = t->vstart, b = t->vend;
    const bool plus = src_at(w, a) == '+';
    bool zero, positive;
    /* What to write for the number: the representation, or text, or for integers the digits from digit_start */
    bool use_rep = false;
    char text[64];
    text[0] = 0;
    uint32_t digit_start = 0;
    bool negative_int = false;
    if (t->flags & KG_TF_IS_INTEGER) {
        uint32_t i = a;
        if (src_at(w, i) == '+' || src_at(w, i) == '-') { negative_int = src_at(w, i) == '-'; i++; }
        while (i < b && src_at(w, i) == '0') i++;
        zero = i == b;
        positive = !zero && !negative_int;
        digit_start = i;
    } else {
        char stack_buf[128];
        char *rep = (b - a < sizeof stack_buf) ? stack_buf : PyMem_Malloc(b - a + 1);
        if (rep == NULL) { PyErr_NoMemory(); return false; }
        for (uint32_t i = a; i < b; i++) rep[i - a] = (char)src_at(w, i);
        rep[b - a] = 0;
        double v = PyOS_string_to_double(rep, NULL, NULL);
        if (rep != stack_buf) PyMem_Free(rep);
        if (v == -1.0 && PyErr_Occurred()) return false;
        zero = v == 0;
        positive = v > 0;
        if (isinf(v) || isnan(v)) use_rep = true;
        else if (zero) strcpy(text, "0");
        else if (v == floor(v) && fabs(v) < 1e16) snprintf(text, sizeof text, "%.0f", v);
        else {
            char *r = PyOS_double_to_string(v, 'r', 0, Py_DTSF_ADD_DOT_0, NULL);
            if (r == NULL) return false;
            if (strchr(r, 'e') != NULL || strlen(r) >= sizeof text) use_rep = true;
            else strcpy(text, r);
            PyMem_Free(r);
        }
    }
    if (t->type == KG_TOK_DIMENSION) {
        /* A unit that starts like an exponent (1e1e3 is 1e1 with the unit e3) would join a rewritten
         * number: 10e3 is a number. Keep the number as written then. */
        const uint32_t u = t->vend, end = t->end;
        const Py_UCS4 c0 = src_at(w, u), c1 = u + 1 < end ? src_at(w, u + 1) : 0, c2 = u + 2 < end ? src_at(w, u + 2) : 0;
        if ((c0 == 'e' || c0 == 'E') && (kg_is_digit(c1) || ((c1 == '+' || c1 == '-') && kg_is_digit(c2)))) {
            use_rep = true;
        }
    }
    if (t->type == KG_TOK_DIMENSION && zero && top_level && !use_rep) {
        w_chars unit;
        if (!token_chars(w, t, t->vend, t->end, false, &unit)) return false;
        bool length = is_length_unit(&unit);
        chars_free(&unit);
        if (length) return put(w, '0');
    }
    if (use_rep) {
        if (!put_source(w, a, b)) return false;
    } else {
        /* The number was rewritten, so a + sign must be added back */
        if (plus && positive && !put(w, '+')) return false;
        if (t->flags & KG_TF_IS_INTEGER) {
            if (zero) { if (!put(w, '0')) return false; }
            else if ((negative_int && !put(w, '-')) || !put_source(w, digit_start, b)) return false;
        } else if (!put_ascii(w, text)) return false;
    }
    if (t->type == KG_TOK_PERCENTAGE) return put(w, '%');
    if (t->type == KG_TOK_DIMENSION) return put_source(w, b, t->end);
    return true;
}

/* shorten_color(): true if a shorter form was written */
static bool
put_short_color(w_state *w, const kg_token *t, bool *written) {
    *written = false;
    if (t->flags & KG_TF_NEEDS_PROCESSING) return true;
    uint32_t n = t->vend - t->vstart;
    if ((n != 6 && n != 8) || t->vstart != t->start + 1 || t->vend != t->end) return true;
    for (uint32_t i = t->vstart; i < t->vend; i += 2) {
        Py_UCS4 c = src_at(w, i);
        if (!is_hex(c) || c != src_at(w, i + 1)) return true;
    }
    if (!put(w, '#')) return false;
    for (uint32_t i = t->vstart; i < t->vend; i += 2) if (!put(w, src_at(w, i))) return false;
    *written = true;
    return true;
}

/* normalized_token() */
static w_status
put_normalized_token(w_state *w, const kg_token *t, bool top_level, bool isolated) {
    const bool safe = w->attribute_safe;
    if (safe && (t->type == KG_TOK_BAD_STRING || is_lone_backslash(w, t))) return WS_UNSAFE;
    w_chars c;
    bool ok;
    switch (t->type) {
        case KG_TOK_STRING:
            if (!token_chars(w, t, t->vstart, t->vend, true, &c)) return WS_ERROR;
            ok = put_string(w, &c, safe);
            chars_free(&c);
            return ok ? WS_OK : WS_ERROR;
        case KG_TOK_URL:
            if (!token_chars(w, t, t->vstart, t->vend, false, &c)) return WS_ERROR;
            ok = put_url(w, &c, safe);
            chars_free(&c);
            return ok ? WS_OK : WS_ERROR;
        case KG_TOK_NUMBER: case KG_TOK_PERCENTAGE: case KG_TOK_DIMENSION:
            if (w->value_mode && isolated) return put_number(w, t, top_level) ? WS_OK : WS_ERROR;
            break;
        case KG_TOK_HASH:
            if (w->value_mode) {
                bool written;
                if (!put_short_color(w, t, &written)) return WS_ERROR;
                if (written) return WS_OK;
            }
            break;
        default:
            break;
    }
    size_t from = w->len;
    if (!put_verbatim_text(w, t)) return WS_ERROR;
    if (safe) {
        if (is_escapable(t->type)) one_line(w, from);
        /* A " can only be an escape here (strings were handled above): use the hex form */
        if (!hex_escape_quotes(w, from)) return WS_ERROR;
        if (has_quote_or_newline(w, from)) return WS_UNSAFE;
    }
    return WS_OK;
}

/* ---------------------------------------------------------------------------
 * Functions and blocks
 * ------------------------------------------------------------------------- */

static inline uint32_t
group_end(const kg_TokenStream *ts, const kg_token *t) {
    return (t->flags & KG_TF_UNCLOSED) ? ts->arr.count : t->match;
}

/* url_function(): if the function at idx is url("..."), the index of the string token, else 0 */
static bool
url_string_argument(w_state *w, uint32_t idx, uint32_t *string_idx) {
    const kg_TokenStream *ts = w->ts;
    const kg_token *t = ts->arr.tokens + idx;
    *string_idx = 0;
    w_chars name;
    if (!token_chars(w, t, t->vstart, t->vend, false, &name)) return false;
    bool is_url = name.end - name.start == 3;
    static const char url[] = "url";
    for (Py_ssize_t i = 0; is_url && i < 3; i++) {
        Py_UCS4 ch = ch_at(&name, name.start + i);
        if (ch >= 'A' && ch <= 'Z') ch += 32;
        if (ch != (Py_UCS4)url[i]) is_url = false;
    }
    chars_free(&name);
    if (!is_url) return true;
    uint32_t found = 0, count = 0;
    for (uint32_t i = idx + 1, end = group_end(ts, t); i < end; i = kg_next_sibling(ts, i)) {
        if (kg_is_ignorable(ts, i)) continue;
        if (++count > 1) return true;
        found = i;
    }
    if (count == 1 && ts->arr.tokens[found].type == KG_TOK_STRING) *string_idx = found;
    return true;
}

/* Write the function name, as in TokenWriter.write() */
static bool
put_function_name(w_state *w, const kg_token *t) {
    size_t from = w->len;
    if (!put_source(w, t->start, t->end)) return false;
    if (w->attribute_safe) one_line(w, from);
    if (w->normalize) {
        while (w->len > from && kg_is_whitespace(w->buf[w->len - 1])) w->len--;
        if (!(t->flags & KG_TF_NEEDS_PROCESSING)) {
            bool ascii = true;
            for (uint32_t i = t->vstart; ascii && i < t->vend; i++) if (src_at(w, i) >= 0x80) ascii = false;
            bool custom = t->vend - t->vstart >= 2 && src_at(w, t->vstart) == '-' && src_at(w, t->vstart + 1) == '-';
            if (ascii && !custom) {
                for (size_t i = from; i < w->len; i++) {
                    Py_UCS4 c = w->buf[i];
                    if (c >= 'A' && c <= 'Z') w->buf[i] = c + 32;
                }
            }
        }
    }
    return true;
}

static bool
push(w_state *w, uint32_t cur, uint32_t end, Py_UCS4 closer, Py_UCS4 kind) {
    if (w->depth == w->frames_cap) {
        size_t cap = w->frames_cap * 2;
        w_frame *n = PyMem_Malloc(cap * sizeof(w_frame));
        if (n == NULL) { PyErr_NoMemory(); return false; }
        memcpy(n, w->frames, w->depth * sizeof(w_frame));
        if (w->frames != w->small_frames) PyMem_Free(w->frames);
        w->frames = n;
        w->frames_cap = cap;
    }
    w->frames[w->depth++] = (w_frame){.cur = cur, .end = end, .closer = closer, .kind = kind};
    return true;
}

/* ---------------------------------------------------------------------------
 * The main loop: TokenWriter.write()
 * ------------------------------------------------------------------------- */

#define CHECK(expr) do { w_status s_ = (expr); if (s_ != WS_OK) return s_; } while (0)
#define CHECK_BOOL(expr) do { if (!(expr)) return WS_ERROR; } while (0)

static w_status
emit_token(w_state *w, const kg_token *t, bool (*writer)(w_state *, const kg_token *)) {
    CHECK(pre_emit(w, true, false));
    CHECK_BOOL(writer(w, t));
    post_emit(w, true);
    return WS_OK;
}

/* Write the component values in tokens [first, last), which must not split a block */
static w_status
write_all(w_state *w, uint32_t first, uint32_t last) {
    const kg_TokenStream *ts = w->ts;
    CHECK_BOOL(push(w, first, last, 0, 0));
    while (w->depth) {
        w_frame *f = w->frames + w->depth - 1;
        const bool top_level = w->depth == 1;
        if (f->cur >= f->end) {
            Py_UCS4 closer = f->closer;
            w->depth--;
            if (closer) {
                CHECK(emit_char(w, closer, true));
                w->pending_space = w->at_start = false;
            }
            continue;
        }
        const uint32_t idx = f->cur;
        f->cur = kg_next_sibling(ts, idx);
        const Py_UCS4 kind = f->kind;
        const kg_token *t = ts->arr.tokens + idx;
        const uint8_t type = t->type;

        if (!kg_is_opener(type)) {
            if (type == KG_TOK_WHITESPACE) {
                if (w->normalize) {
                    if (w->need_newline) CHECK(emit_char(w, '\n', false));
                    else w->pending_space = true;
                } else if (w->attribute_safe) {
                    /* One line in attribute-safe mode, where nothing needs a newline */
                    if (!ends_with_space(w)) CHECK(emit_char(w, ' ', false));
                } else {
                    CHECK(pre_emit(w, false, kg_is_newline(src_at(w, t->start))));
                    CHECK_BOOL(put_source(w, t->start, t->end));
                    post_emit(w, false);
                }
                continue;
            }
            const bool is_comment = type == KG_TOK_COMMENT;
            if (is_comment && !w->keep_comments) {
                if (w->selector) w->dropped_comment = true;
                else if (w->normalize) w->pending_space = true;
                else if (!ends_with_space(w)) CHECK(emit_char(w, ' ', false));
                continue;
            }
            bool isolated = false;
            if (w->normalize && !is_comment) {
                /* Rewriting a number changes its first and last characters, which could merge with
                 * neighbours that touch it: only do it when that cannot happen */
                bool left_ok = w->at_start || w->pending_space || w->len == 0 || !could_merge(w->buf[w->len - 1], false);
                bool right_merges = t->end < ts->len && could_merge(src_at(w, t->end), true);
                isolated = left_ok && !right_merges;
            }
            if (w->normalize) {
                if (type == KG_TOK_COMMA) {
                    CHECK(emit_char(w, ',', true));
                    /* A space after commas, except inside attribute selectors */
                    w->pending_space = w->value_mode || kind != '[';
                    w->at_start = false;
                    continue;
                }
                const Py_UCS4 d = type == KG_TOK_DELIM ? src_at(w, t->start) : 0;
                if (w->selector && (d == '>' || d == '+' || d == '~') && kind != '[') {
                    if (!w->at_start) CHECK_BOOL(space(w));
                    CHECK(emit_char(w, d, true));
                    w->pending_space = true;
                    w->at_start = false;
                    continue;
                }
                const bool in_attribute_operator = w->selector && kind == '[' && type == KG_TOK_DELIM;
                if (w->pending_space && !w->at_start && !in_attribute_operator && !w->need_newline) CHECK_BOOL(space(w));
                CHECK(pre_emit(w, true, false));
                if (is_comment) {
                    CHECK_BOOL(put_ascii(w, "/*") && put_source(w, t->vstart, t->vend) && put_ascii(w, "*/"));
                } else CHECK(put_normalized_token(w, t, top_level, isolated));
                post_emit(w, true);
                if (in_attribute_operator) {
                    /* No spaces around the operators of attribute selectors */
                    w->pending_space = false;
                    w->at_start = true;
                    continue;
                }
            } else {
                CHECK(pre_emit(w, true, false));
                if (is_comment) {
                    CHECK_BOOL(put_ascii(w, "/*") && put_source(w, t->vstart, t->vend) && put_ascii(w, "*/"));
                } else CHECK(put_verbatim_token(w, t));
                post_emit(w, true);
            }
            w->pending_space = w->at_start = false;
            w->need_newline = w->last_needs_newline = type == KG_TOK_BAD_STRING || is_lone_backslash(w, t);
            continue;
        }

        /* A function or block */
        if (w->normalize && w->pending_space && !w->at_start && !w->need_newline) CHECK_BOOL(space(w));
        const uint32_t end = group_end(ts, t);
        if (type == KG_TOK_FUNCTION) {
            uint32_t string_idx;
            CHECK_BOOL(url_string_argument(w, idx, &string_idx));
            if (string_idx) {
                const kg_token *s = ts->arr.tokens + string_idx;
                if (w->normalize || (t->flags & KG_TF_UNCLOSED) || (s->flags & KG_TF_UNCLOSED)) {
                    w_chars c;
                    CHECK(pre_emit(w, true, false));
                    CHECK_BOOL(token_chars(w, s, s->vstart, s->vend, true, &c));
                    bool ok;
                    if (w->normalize) ok = put_url(w, &c, w->attribute_safe);
                    else ok = put_ascii(w, "url(") && put_string(w, &c, false) && put(w, ')');
                    chars_free(&c);
                    CHECK_BOOL(ok);
                    post_emit(w, true);
                    w->pending_space = w->at_start = false;
                    continue;
                }
            }
            CHECK(emit_token(w, t, put_function_name));
            CHECK_BOOL(push(w, idx + 1, end, ')', '('));
        } else {
            const Py_UCS4 opener = src_at(w, t->start);
            CHECK(emit_char(w, opener, true));
            CHECK_BOOL(push(w, idx + 1, end, opener == '{' ? '}' : opener == '[' ? ']' : ')', opener));
        }
        w->pending_space = false;
        w->at_start = true;
    }
    return WS_OK;
}

/* ---------------------------------------------------------------------------
 * Entry points
 * ------------------------------------------------------------------------- */

static void
w_init(w_state *w) {
    w->buf = w->small;
    w->cap = sizeof w->small / sizeof w->small[0];
    w->frames = w->small_frames;
    w->frames_cap = sizeof w->small_frames / sizeof w->small_frames[0];
}

static void
w_free(w_state *w) {
    if (w->buf != w->small) PyMem_Free(w->buf);
    if (w->frames != w->small_frames) PyMem_Free(w->frames);
    PyMem_Free(w);
}

static w_state *
w_new(void) {
    w_state *w = PyMem_Calloc(1, sizeof(w_state));
    if (w == NULL) { PyErr_NoMemory(); return NULL; }
    w_init(w);
    return w;
}

/* Get ready to write tokens of ts, keeping the allocated buffers */
static void
w_begin(w_state *w, const kg_TokenStream *ts, unsigned long flags) {
    w->ts = ts;
    w->kind = ts->kind;
    w->src = ts->data;
    const bool verbatim = flags & W_VERBATIM;
    w->normalize = (flags & W_NORMALIZE) && !verbatim;
    w->selector = flags & W_SELECTOR;
    w->value_mode = !verbatim && !w->selector;
    w->attribute_safe = flags & W_ATTRIBUTE_SAFE;
    w->keep_comments = (flags & W_KEEP_COMMENTS) && !w->attribute_safe;
    w->len = w->piece_start = w->depth = 0;
    w->pending_space = w->need_newline = w->last_needs_newline = w->dropped_comment = false;
    w->last_text_nonempty = w->have_first = false;
    w->first_significant = w->significant_end = 0;
    w->at_start = true;
}

/* Write tokens [first, last) and find the text to return in buf[*start, *end) */
static w_status
w_write(w_state *w, uint32_t first, uint32_t last, unsigned long flags, size_t *start, size_t *end) {
    w_status status = write_all(w, first, last);
    if (status != WS_OK) return status;
    *start = 0;
    *end = w->len;
    if (flags & W_RAW) return WS_OK;
    /* Only whitespace from whitespace tokens is trimmed, never the space ending an escape */
    const size_t s = w->have_first ? w->first_significant : w->len;
    w->len = w->significant_end > s ? w->significant_end : s;
    /* The canonical form ends a final hex escape with its space, otherwise whatever is
     * written next would be read back as part of the escape */
    if (hex_escape_at_end(w->buf, s, w->len) && !put(w, ' ')) return WS_ERROR;
    /* A bad string or lone backslash is ended by a newline, which must stay */
    if (w->last_needs_newline && !put(w, '\n')) return WS_ERROR;
    *start = s;
    *end = w->len;
    return WS_OK;
}

PyObject *
kg_write_values(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 2) {
        PyErr_SetString(PyExc_TypeError, "write_values() takes exactly 2 arguments");
        return NULL;
    }
    if (!Py_IS_TYPE(args[0], state->ComponentValueList_type)) {
        PyErr_Format(PyExc_TypeError, "write_values() expects a ComponentValueList, not %.200s", Py_TYPE(args[0])->tp_name);
        return NULL;
    }
    const unsigned long flags = PyLong_AsUnsignedLong(args[1]);
    if (flags == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    const kg_ComponentValueList *cvl = (const kg_ComponentValueList *)args[0];
    const kg_TokenStream *ts = cvl->stream;
    const Py_ssize_t n = Py_SIZE(cvl);
    /* The items of a list are the siblings from the first to the last */
    const uint32_t first = n ? cvl->items[0] : 0, last = n ? kg_next_sibling(ts, cvl->items[n - 1]) : 0;

    w_state *w = w_new();
    if (w == NULL) return NULL;
    w_begin(w, ts, flags);
    PyObject *ans = NULL;
    size_t start, end;
    switch (w_write(w, first, last, flags, &start, &end)) {
        case WS_UNSAFE: ans = Py_NewRef(Py_None); break;
        case WS_FALLBACK: ans = Py_NewRef(Py_NotImplemented); break;
        case WS_OK: ans = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, w->buf + start, (Py_ssize_t)(end - start)); break;
        case WS_ERROR: break;
    }
    w_free(w);
    return ans;
}

static bool
put_str(w_state *w, PyObject *s) {
    const Py_ssize_t n = PyUnicode_GET_LENGTH(s);
    if (!reserve(w, (size_t)n)) return false;
    const int kind = PyUnicode_KIND(s);
    const void *data = PyUnicode_DATA(s);
    for (Py_ssize_t i = 0; i < n; i++) w->buf[w->len++] = PyUnicode_READ(kind, data, i);
    return true;
}

/* Strong references to the fields of a property used for writing it. Taken together under a
 * critical section on the property, as another thread could be assigning them (the member
 * descriptors of PropertyData use the same critical section). */
typedef struct {
    PyObject *name, *value, *priority, *node;
    bool value_from_node;
} p_fields;

static void
get_fields(kg_PropertyData *p, p_fields *f) {
    Py_BEGIN_CRITICAL_SECTION(p);
    f->name = Py_XNewRef(p->name);
    f->value = Py_XNewRef(p->value);
    f->priority = Py_XNewRef(p->priority);
    f->node = Py_XNewRef(p->node);
    f->value_from_node = p->value_from_node;
    Py_END_CRITICAL_SECTION();
}

static void
release_fields(p_fields *f) {
    Py_XDECREF(f->name);
    Py_XDECREF(f->value);
    Py_XDECREF(f->priority);
    Py_XDECREF(f->node);
}

/* The line for one property, as property_text() in serializer.py, appended to line. Returns
 * WS_FALLBACK for properties that the Python version must handle. */
static w_status
property_line(PyObject *module, kg_module_state *state, w_state *w, w_state *line, const p_fields *p, unsigned long flags) {
    if (p->name == NULL || p->priority == NULL || !PyUnicode_Check(p->name) || !PyUnicode_Check(p->priority)) {
        return WS_FALLBACK;
    }
    const kg_TokenStream *ts;
    uint32_t first, last;
    kg_TokenStream *owned = NULL;
    if (p->value_from_node && p->node != NULL && Py_IS_TYPE(p->node, state->Declaration_type)) {
        const kg_Node *node = (const kg_Node *)p->node;
        ts = node->stream;
        first = node->first;
        last = node->last;
    } else {
        if (p->value == NULL || !PyUnicode_Check(p->value)) return WS_FALLBACK;
        /* As parse_component_value_list(prop.value) */
        if ((owned = kg_tokenize_to_stream(module, p->value, true)) == NULL) return WS_ERROR;
        ts = owned;
        first = 0;
        last = owned->arr.count;
    }
    /* Custom property values are written exactly, apart from closing what is unclosed */
    const Py_ssize_t name_len = PyUnicode_GET_LENGTH(p->name);
    const bool custom = name_len >= 2 && PyUnicode_READ_CHAR(p->name, 0) == '-' && PyUnicode_READ_CHAR(p->name, 1) == '-';
    if (custom) flags |= W_VERBATIM;
    w_begin(w, ts, flags);
    size_t start, end;
    w_status status = w_write(w, first, last, flags, &start, &end);
    if (status != WS_OK) { Py_XDECREF(owned); return status; }
    /* Names are stored unescaped, so they are escaped as needed */
    PyObject *name = kg_serialize_identifier(module, p->name);
    bool ok = name != NULL && put_str(line, name);
    Py_XDECREF(name);
    ok = ok && put_ascii(line, end > start ? ": " : ":") && reserve(line, end - start);
    if (ok) {
        memcpy(line->buf + line->len, w->buf + start, (end - start) * sizeof(Py_UCS4));
        line->len += end - start;
    }
    if (ok && PyUnicode_GET_LENGTH(p->priority)) ok = put_ascii(line, " !important");
    Py_XDECREF(owned);
    return ok && put(line, ';') ? WS_OK : WS_ERROR;
}

PyObject *
kg_write_declarations(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    kg_module_state *state = kg_get_module_state(module);
    if (nargs != 3 || !PyList_Check(args[0]) || !PyLong_Check(args[1]) || !PyUnicode_Check(args[2])) {
        PyErr_SetString(PyExc_TypeError, "write_declarations() takes a list, an int and a str");
        return NULL;
    }
    PyObject *items = args[0], *indent = args[2];
    const unsigned long flags = PyLong_AsUnsignedLong(args[1]);
    if (flags == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    /* Only the pretty, normalized format, with !important written in lowercase */
    if (!(flags & W_NORMALIZE) || (flags & (W_SELECTOR | W_VERBATIM | W_ATTRIBUTE_SAFE | W_RAW))) {
        return Py_NewRef(Py_NotImplemented);
    }
    PyObject *ans = PyList_New(0);
    w_state *w = w_new(), *line = w_new();
    if (ans == NULL || w == NULL || line == NULL) goto error;
    w_status status = WS_OK;
    Py_BEGIN_CRITICAL_SECTION(items);
    for (Py_ssize_t i = 0; status == WS_OK && i < PyList_GET_SIZE(items); i++) {
        /* A strong reference: the critical section can be suspended by what is called below */
        PyObject *item = Py_NewRef(PyList_GET_ITEM(items, i));
        line->len = 0;
        if (!put_str(line, indent)) status = WS_ERROR;
        else if (PyObject_TypeCheck(item, state->PropertyData_type)) {
            p_fields fields;
            get_fields((kg_PropertyData *)item, &fields);
            status = property_line(module, state, w, line, &fields, flags);
            release_fields(&fields);
        } else if (!(flags & W_KEEP_COMMENTS)) {
            Py_DECREF(item);
            continue;
        } else {
            /* A comment */
            PyObject *text = PyObject_GetAttrString(item, "text");
            if (text == NULL) status = WS_ERROR;
            else if (!PyUnicode_Check(text)) {
                PyErr_SetString(PyExc_TypeError, "comment text must be a str");
                status = WS_ERROR;
            } else if (!(put_ascii(line, "/*") && put_str(line, text) && put_ascii(line, "*/"))) status = WS_ERROR;
            Py_XDECREF(text);
        }
        Py_DECREF(item);
        if (status != WS_OK) break;
        PyObject *s = PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, line->buf, (Py_ssize_t)line->len);
        if (s == NULL || PyList_Append(ans, s) != 0) { Py_XDECREF(s); status = WS_ERROR; break; }
        Py_DECREF(s);
    }
    Py_END_CRITICAL_SECTION();
    if (status == WS_FALLBACK) { Py_CLEAR(ans); ans = Py_NewRef(Py_NotImplemented); }
    else if (status != WS_OK) goto error;
    w_free(w);
    w_free(line);
    return ans;
error:
    Py_XDECREF(ans);
    if (w) w_free(w);
    if (line) w_free(line);
    return NULL;
}
