/*
 * color.c
 * Copyright (C) 2026 Kovid Goyal <kovid at kovidgoyal.net>
 *
 * Distributed under terms of the BSD 3-Clause license.
 *
 * CSS Color Module Level 4 colors.
 *
 * Ported from calibre's src/tinycss/color.c. A color is parsed in two steps:
 * first into a kg_color, which keeps the color space and the specified
 * channels, then converted to sRGB and clipped to 0..1.
 *
 * The parser reads the string directly, without the tokenizer, for speed:
 * the editor calls it on every highlight pass. It accepts only ASCII without
 * backslashes. The rare input with escapes or non-ASCII characters (which
 * can only be valid inside comments) is tokenized, and rebuilt as plain
 * ASCII from the decoded tokens, then parsed the same way.
 */

#include "kgcss.h"
#include "objects.h"
#include "tokens.h"
#include <math.h>
#include <stdlib.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define arraysz(x) (sizeof(x) / sizeof((x)[0]))

typedef enum {
    KG_CS_RGB,          /* the legacy sRGB forms: names, hex, rgb(), hsl(), hwb(); channels 0..255 */
    KG_CS_LAB, KG_CS_LCH, KG_CS_OKLAB, KG_CS_OKLCH,
    KG_CS_SRGB, KG_CS_SRGB_LINEAR, KG_CS_DISPLAY_P3, KG_CS_A98_RGB, KG_CS_PROPHOTO_RGB, KG_CS_REC2020,
    KG_CS_XYZ_D50, KG_CS_XYZ_D65,
} kg_color_space;

typedef struct {
    kg_color_space space;
    double c[3], alpha;
    /* Bits 0 to 2: the channel is none, bit 3: the alpha is none. The value is then 0. */
    unsigned none;
} kg_color;

typedef enum { KG_COLOR_INVALID, KG_COLOR_OK, KG_COLOR_CURRENT } kg_color_result;

/* ---------------------------------------------------------------------------
 * Named colors
 * ------------------------------------------------------------------------- */

typedef struct {
    const char *name;
    unsigned char r, g, b;
} color_keyword;

/* Sorted by name, for bsearch(). The CSS 3 names; rebeccapurple and transparent are handled separately. */
static const color_keyword color_keywords[] = {
    {"aliceblue", 240, 248, 255},
    {"antiquewhite", 250, 235, 215},
    {"aqua", 0, 255, 255},
    {"aquamarine", 127, 255, 212},
    {"azure", 240, 255, 255},
    {"beige", 245, 245, 220},
    {"bisque", 255, 228, 196},
    {"black", 0, 0, 0},
    {"blanchedalmond", 255, 235, 205},
    {"blue", 0, 0, 255},
    {"blueviolet", 138, 43, 226},
    {"brown", 165, 42, 42},
    {"burlywood", 222, 184, 135},
    {"cadetblue", 95, 158, 160},
    {"chartreuse", 127, 255, 0},
    {"chocolate", 210, 105, 30},
    {"coral", 255, 127, 80},
    {"cornflowerblue", 100, 149, 237},
    {"cornsilk", 255, 248, 220},
    {"crimson", 220, 20, 60},
    {"cyan", 0, 255, 255},
    {"darkblue", 0, 0, 139},
    {"darkcyan", 0, 139, 139},
    {"darkgoldenrod", 184, 134, 11},
    {"darkgray", 169, 169, 169},
    {"darkgreen", 0, 100, 0},
    {"darkgrey", 169, 169, 169},
    {"darkkhaki", 189, 183, 107},
    {"darkmagenta", 139, 0, 139},
    {"darkolivegreen", 85, 107, 47},
    {"darkorange", 255, 140, 0},
    {"darkorchid", 153, 50, 204},
    {"darkred", 139, 0, 0},
    {"darksalmon", 233, 150, 122},
    {"darkseagreen", 143, 188, 143},
    {"darkslateblue", 72, 61, 139},
    {"darkslategray", 47, 79, 79},
    {"darkslategrey", 47, 79, 79},
    {"darkturquoise", 0, 206, 209},
    {"darkviolet", 148, 0, 211},
    {"deeppink", 255, 20, 147},
    {"deepskyblue", 0, 191, 255},
    {"dimgray", 105, 105, 105},
    {"dimgrey", 105, 105, 105},
    {"dodgerblue", 30, 144, 255},
    {"firebrick", 178, 34, 34},
    {"floralwhite", 255, 250, 240},
    {"forestgreen", 34, 139, 34},
    {"fuchsia", 255, 0, 255},
    {"gainsboro", 220, 220, 220},
    {"ghostwhite", 248, 248, 255},
    {"gold", 255, 215, 0},
    {"goldenrod", 218, 165, 32},
    {"gray", 128, 128, 128},
    {"green", 0, 128, 0},
    {"greenyellow", 173, 255, 47},
    {"grey", 128, 128, 128},
    {"honeydew", 240, 255, 240},
    {"hotpink", 255, 105, 180},
    {"indianred", 205, 92, 92},
    {"indigo", 75, 0, 130},
    {"ivory", 255, 255, 240},
    {"khaki", 240, 230, 140},
    {"lavender", 230, 230, 250},
    {"lavenderblush", 255, 240, 245},
    {"lawngreen", 124, 252, 0},
    {"lemonchiffon", 255, 250, 205},
    {"lightblue", 173, 216, 230},
    {"lightcoral", 240, 128, 128},
    {"lightcyan", 224, 255, 255},
    {"lightgoldenrodyellow", 250, 250, 210},
    {"lightgray", 211, 211, 211},
    {"lightgreen", 144, 238, 144},
    {"lightgrey", 211, 211, 211},
    {"lightpink", 255, 182, 193},
    {"lightsalmon", 255, 160, 122},
    {"lightseagreen", 32, 178, 170},
    {"lightskyblue", 135, 206, 250},
    {"lightslategray", 119, 136, 153},
    {"lightslategrey", 119, 136, 153},
    {"lightsteelblue", 176, 196, 222},
    {"lightyellow", 255, 255, 224},
    {"lime", 0, 255, 0},
    {"limegreen", 50, 205, 50},
    {"linen", 250, 240, 230},
    {"magenta", 255, 0, 255},
    {"maroon", 128, 0, 0},
    {"mediumaquamarine", 102, 205, 170},
    {"mediumblue", 0, 0, 205},
    {"mediumorchid", 186, 85, 211},
    {"mediumpurple", 147, 112, 219},
    {"mediumseagreen", 60, 179, 113},
    {"mediumslateblue", 123, 104, 238},
    {"mediumspringgreen", 0, 250, 154},
    {"mediumturquoise", 72, 209, 204},
    {"mediumvioletred", 199, 21, 133},
    {"midnightblue", 25, 25, 112},
    {"mintcream", 245, 255, 250},
    {"mistyrose", 255, 228, 225},
    {"moccasin", 255, 228, 181},
    {"navajowhite", 255, 222, 173},
    {"navy", 0, 0, 128},
    {"oldlace", 253, 245, 230},
    {"olive", 128, 128, 0},
    {"olivedrab", 107, 142, 35},
    {"orange", 255, 165, 0},
    {"orangered", 255, 69, 0},
    {"orchid", 218, 112, 214},
    {"palegoldenrod", 238, 232, 170},
    {"palegreen", 152, 251, 152},
    {"paleturquoise", 175, 238, 238},
    {"palevioletred", 219, 112, 147},
    {"papayawhip", 255, 239, 213},
    {"peachpuff", 255, 218, 185},
    {"peru", 205, 133, 63},
    {"pink", 255, 192, 203},
    {"plum", 221, 160, 221},
    {"powderblue", 176, 224, 230},
    {"purple", 128, 0, 128},
    {"red", 255, 0, 0},
    {"rosybrown", 188, 143, 143},
    {"royalblue", 65, 105, 225},
    {"saddlebrown", 139, 69, 19},
    {"salmon", 250, 128, 114},
    {"sandybrown", 244, 164, 96},
    {"seagreen", 46, 139, 87},
    {"seashell", 255, 245, 238},
    {"sienna", 160, 82, 45},
    {"silver", 192, 192, 192},
    {"skyblue", 135, 206, 235},
    {"slateblue", 106, 90, 205},
    {"slategray", 112, 128, 144},
    {"slategrey", 112, 128, 144},
    {"snow", 255, 250, 250},
    {"springgreen", 0, 255, 127},
    {"steelblue", 70, 130, 180},
    {"tan", 210, 180, 140},
    {"teal", 0, 128, 128},
    {"thistle", 216, 191, 216},
    {"tomato", 255, 99, 71},
    {"turquoise", 64, 224, 208},
    {"violet", 238, 130, 238},
    {"wheat", 245, 222, 179},
    {"white", 255, 255, 255},
    {"whitesmoke", 245, 245, 245},
    {"yellow", 255, 255, 0},
    {"yellowgreen", 154, 205, 50},
};

static int
compare_keyword(const void *key, const void *item) {
    return strcmp((const char *)key, ((const color_keyword *)item)->name);
}

/* ---------------------------------------------------------------------------
 * The scanner, over ASCII
 * ------------------------------------------------------------------------- */

typedef struct {
    const unsigned char *s;
    Py_ssize_t pos, len;
} scanner;

static inline bool is_space(unsigned char ch) { return ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r' || ch == '\f'; }
static inline bool is_digit(unsigned char ch) { return '0' <= ch && ch <= '9'; }
static inline bool
is_nmchar(unsigned char ch) {
    return ch == '_' || ch == '-' || ('a' <= ch && ch <= 'z') || ('A' <= ch && ch <= 'Z') || is_digit(ch);
}
static inline bool
is_name_start(unsigned char ch) {
    return ch == '-' || ch == '_' || ('a' <= ch && ch <= 'z') || ('A' <= ch && ch <= 'Z');
}
static inline int
hex_value(unsigned char ch) {
    if (is_digit(ch)) return ch - '0';
    if ('a' <= ch && ch <= 'f') return ch - 'a' + 10;
    if ('A' <= ch && ch <= 'F') return ch - 'A' + 10;
    return -1;
}
static inline unsigned char
peek(const scanner *p, Py_ssize_t offset) {
    return p->pos + offset < p->len ? p->s[p->pos + offset] : 0;
}

/* Skip whitespace and comments. A comment left open runs to the end of the input. */
static void
skip_ignorable(scanner *p) {
    while (p->pos < p->len) {
        const unsigned char ch = p->s[p->pos];
        if (is_space(ch)) { p->pos++; continue; }
        if (ch != '/' || peek(p, 1) != '*') break;
        Py_ssize_t i = p->pos + 2;
        while (i + 1 < p->len && !(p->s[i] == '*' && p->s[i + 1] == '/')) i++;
        p->pos = i + 1 < p->len ? i + 2 : p->len;
    }
}

/* A name, lowercased into buf. False if it is empty or too long. */
static bool
read_name(scanner *p, char *buf, size_t bufsz) {
    const Py_ssize_t start = p->pos;
    while (p->pos < p->len && is_nmchar(p->s[p->pos])) p->pos++;
    const Py_ssize_t n = p->pos - start;
    if (n < 1 || (size_t)n >= bufsz) return false;
    for (Py_ssize_t i = 0; i < n; i++) buf[i] = (char)Py_TOLOWER(p->s[start + i]);
    buf[n] = 0;
    return true;
}

/* ---------------------------------------------------------------------------
 * Function arguments
 * ------------------------------------------------------------------------- */

typedef enum { A_NUMBER, A_PERCENTAGE, A_DIMENSION, A_IDENT, A_COMMA, A_SLASH } arg_type;

typedef struct {
    arg_type type;
    double value;
    char name[16];  /* the lowercased unit of a dimension, or the ident */
} color_arg;

#define MAX_ARGS 16

/* A number token of CSS Syntax 3, then an optional % or unit */
static bool
read_number(scanner *p, color_arg *a) {
    const Py_ssize_t start = p->pos;
    Py_ssize_t digits = 0;
    char buf[128];
    if (peek(p, 0) == '+' || peek(p, 0) == '-') p->pos++;
    while (is_digit(peek(p, 0))) { p->pos++; digits++; }
    if (peek(p, 0) == '.' && is_digit(peek(p, 1))) {
        p->pos++;
        while (is_digit(peek(p, 0))) { p->pos++; digits++; }
    }
    if (!digits) return false;
    const unsigned char e = peek(p, 0);
    if (e == 'e' || e == 'E') {
        if (is_digit(peek(p, 1))) p->pos++;
        else if ((peek(p, 1) == '+' || peek(p, 1) == '-') && is_digit(peek(p, 2))) p->pos += 2;
        while (is_digit(peek(p, 0))) p->pos++;
    }
    const Py_ssize_t sz = p->pos - start;
    const bool sign = p->s[start] == '+' || p->s[start] == '-';
    if (digits == sz - sign && digits <= 15) {
        /* An integer, as most rgb() arguments are: exact as a double */
        double v = 0;
        for (Py_ssize_t i = start + sign; i < p->pos; i++) v = v * 10 + (p->s[i] - '0');
        a->value = p->s[start] == '-' ? -v : v;
    } else {
        if (sz >= (Py_ssize_t)sizeof(buf)) return false;
        memcpy(buf, p->s + start, sz);
        buf[sz] = 0;
        /* Not strtod(), which depends on the locale: Qt sets it from the environment */
        a->value = PyOS_string_to_double(buf, NULL, NULL);
        if (a->value == -1.0 && PyErr_Occurred()) { PyErr_Clear(); return false; }
        if (!isfinite(a->value)) return false;
    }
    if (peek(p, 0) == '%') {
        p->pos++;
        a->type = A_PERCENTAGE;
    } else if (is_name_start(peek(p, 0))) {
        if (!read_name(p, a->name, sizeof(a->name))) return false;
        a->type = A_DIMENSION;
    } else a->type = A_NUMBER;
    return true;
}

/* The arguments of a function, after its (. The end of the input closes it. */
static bool
read_args(scanner *p, color_arg *args, unsigned *count) {
    unsigned n = 0;
    while (true) {
        skip_ignorable(p);
        if (p->pos >= p->len) break;
        const unsigned char ch = p->s[p->pos];
        if (ch == ')') { p->pos++; break; }
        if (n >= MAX_ARGS) return false;
        color_arg *a = args + n++;
        if (ch == ',') { a->type = A_COMMA; p->pos++; }
        else if (ch == '/') { a->type = A_SLASH; p->pos++; }
        else if (is_digit(ch) || ch == '.' || ch == '+' || (ch == '-' && (is_digit(peek(p, 1)) || peek(p, 1) == '.'))) {
            if (!read_number(p, a)) return false;
        } else if (is_name_start(ch)) {
            if (!read_name(p, a->name, sizeof(a->name))) return false;
            if (peek(p, 0) == '(') return false;  /* calc(), var() and other functions */
            a->type = A_IDENT;
        } else return false;
    }
    *count = n;
    return true;
}

static inline bool is_none(const color_arg *a) { return a->type == A_IDENT && strcmp(a->name, "none") == 0; }

typedef enum { HUE, PERCENT_OR_NUMBER, PERCENT_ONLY } arg_kind;

/* An argument as a number, with 100% == pct_ref. Hues are in degrees. none is 0 and sets *none. */
static bool
resolve(const color_arg *a, arg_kind kind, double pct_ref, bool allow_none, double *ans, bool *none) {
    *none = false;
    if (is_none(a)) {
        *ans = 0;
        *none = true;
        return allow_none;
    }
    switch (a->type) {
        case A_NUMBER:
            if (kind == PERCENT_ONLY) return false;
            *ans = a->value;
            return true;
        case A_PERCENTAGE:
            if (kind == HUE) return false;
            *ans = a->value / 100. * pct_ref;
            return true;
        case A_DIMENSION:
            if (kind != HUE) return false;
            if (strcmp(a->name, "deg") == 0) *ans = a->value;
            else if (strcmp(a->name, "grad") == 0) *ans = a->value * 0.9;
            else if (strcmp(a->name, "rad") == 0) *ans = a->value * 180. / M_PI;
            else if (strcmp(a->name, "turn") == 0) *ans = a->value * 360.;
            else return false;
            return true;
        default: return false;
    }
}

static inline double clip01(double x) { return fmin(1, fmax(0, x)); }
static inline double clamp(double x, double lo, double hi) { return fmin(hi, fmax(lo, x)); }

/* A hue in degrees, in [0, 360) */
static double
normalized_hue(double h) {
    h = fmod(h, 360);
    if (h < 0) h += 360;
    /* -0 and the rounding of a tiny negative h to 360 */
    return h == 0 || h >= 360 ? 0 : h;
}

/* ---------------------------------------------------------------------------
 * hsl() and hwb(), to sRGB
 * ------------------------------------------------------------------------- */

static double
hue_to_rgb(double m1, double m2, double h) {
    if (h < 0) h += 1;
    if (h > 1) h -= 1;
    if (h * 6 < 1) return m1 + (m2 - m1) * h * 6;
    if (h * 2 < 1) return m2;
    if (h * 3 < 2) return m1 + (m2 - m1) * (2. / 3 - h) * 6;
    return m1;
}

/* Saturation and lightness are fractions. They are not clamped: the result is clipped at the end. */
static void
hsl_to_rgb(double hue, double saturation, double lightness, double rgb[3]) {
    hue = fmod(hue / 360, 1.0);
    if (hue < 0) hue += 1.0;
    const double m2 = lightness <= 0.5 ? lightness * (saturation + 1) : lightness + saturation - lightness * saturation;
    const double m1 = lightness * 2 - m2;
    rgb[0] = hue_to_rgb(m1, m2, hue + 1. / 3);
    rgb[1] = hue_to_rgb(m1, m2, hue);
    rgb[2] = hue_to_rgb(m1, m2, hue - 1. / 3);
}

static void
hwb_to_rgb(double hue, double white, double black, double rgb[3]) {
    if (white + black >= 1) {
        rgb[0] = rgb[1] = rgb[2] = white / (white + black);
        return;
    }
    hsl_to_rgb(hue, 1, 0.5, rgb);
    for (unsigned i = 0; i < 3; i++) rgb[i] = rgb[i] * (1 - white - black) + white;
}

/* ---------------------------------------------------------------------------
 * Functions
 * ------------------------------------------------------------------------- */

typedef enum { F_RGB, F_HSL, F_HWB, F_LAB, F_LCH, F_OKLAB, F_OKLCH, F_COLOR } color_function;

static const struct { const char *name; kg_color_space space; } predefined_spaces[] = {
    {"srgb", KG_CS_SRGB}, {"srgb-linear", KG_CS_SRGB_LINEAR}, {"display-p3", KG_CS_DISPLAY_P3},
    {"a98-rgb", KG_CS_A98_RGB}, {"prophoto-rgb", KG_CS_PROPHOTO_RGB}, {"rec2020", KG_CS_REC2020},
    {"xyz", KG_CS_XYZ_D65}, {"xyz-d50", KG_CS_XYZ_D50}, {"xyz-d65", KG_CS_XYZ_D65},
};

/* The color function with this lowercased name, or -1 */
static int
function_from_name(const char *name) {
    if (strcmp(name, "rgb") == 0 || strcmp(name, "rgba") == 0) return F_RGB;
    if (strcmp(name, "hsl") == 0 || strcmp(name, "hsla") == 0) return F_HSL;
    if (strcmp(name, "hwb") == 0) return F_HWB;
    if (strcmp(name, "lab") == 0) return F_LAB;
    if (strcmp(name, "lch") == 0) return F_LCH;
    if (strcmp(name, "oklab") == 0) return F_OKLAB;
    if (strcmp(name, "oklch") == 0) return F_OKLCH;
    if (strcmp(name, "color") == 0) return F_COLOR;
    return -1;
}

/* A color function. p is at the (. Not inlined: its large frame would slow down the common case of
 * text that is not a color. */
static Py_NO_INLINE bool
parse_function(scanner *p, color_function func, kg_color *out) {
    p->pos++;
    color_arg args[MAX_ARGS];
    unsigned nargs = 0;
    if (!read_args(p, args, &nargs)) return false;
    skip_ignorable(p);
    if (p->pos != p->len) return false;

    /* The channels and the optional alpha */
    const color_arg *channels[4], *alpha = NULL;
    unsigned nchannels = 0;
    const bool legacy = nargs > 1 && args[1].type == A_COMMA;
    if (legacy) {
        /* rgb(r, g, b[, a]) and hsl(h, s, l[, a]), without none */
        if (func != F_RGB && func != F_HSL) return false;
        if (nargs != 5 && nargs != 7) return false;
        for (unsigned i = 0; i < nargs; i++) {
            if ((i % 2 == 1) != (args[i].type == A_COMMA)) return false;
            if (i % 2 == 0) {
                if (args[i].type == A_IDENT || args[i].type == A_SLASH) return false;
                if (i < 6) channels[nchannels++] = args + i;
                else alpha = args + i;
            }
        }
    } else {
        unsigned i = 0;
        for (; i < nargs && args[i].type != A_SLASH; i++) {
            if (args[i].type == A_COMMA || nchannels >= arraysz(channels)) return false;
            channels[nchannels++] = args + i;
        }
        if (i < nargs) {
            if (nargs != i + 2 || args[i + 1].type == A_COMMA || args[i + 1].type == A_SLASH) return false;
            alpha = args + i + 1;
        }
    }
    out->none = 0;
    out->alpha = 1;
    bool none;
    if (alpha != NULL) {
        if (!resolve(alpha, PERCENT_OR_NUMBER, 1, !legacy, &out->alpha, &none)) return false;
        out->alpha = clip01(out->alpha);
        if (none) out->none |= 8;
    }
    if (func == F_COLOR) {
        if (nchannels < 1 || channels[0]->type != A_IDENT) return false;
        bool found = false;
        for (unsigned i = 0; i < arraysz(predefined_spaces) && !found; i++) {
            if (strcmp(channels[0]->name, predefined_spaces[i].name) == 0) {
                out->space = predefined_spaces[i].space;
                found = true;
            }
        }
        if (!found) return false;
        memmove(channels, channels + 1, (--nchannels) * sizeof(channels[0]));
    }
    if (nchannels != 3) return false;
    double *c = out->c;
#define R(idx, kind, ref) \
    if (!resolve(channels[idx], kind, ref, !legacy, c + idx, &none)) return false; \
    if (none) out->none |= 1u << (idx);
    switch (func) {
        case F_RGB:
            if (legacy) {
                /* All numbers or all percentages */
                const arg_type t = channels[0]->type;
                if ((t != A_NUMBER && t != A_PERCENTAGE) || channels[1]->type != t || channels[2]->type != t) return false;
            }
            for (unsigned i = 0; i < 3; i++) {
                R(i, PERCENT_OR_NUMBER, 255);
                c[i] = clamp(c[i], 0, 255);
            }
            out->space = KG_CS_RGB;
            break;
        case F_HSL: {
            R(0, HUE, 0);
            R(1, legacy ? PERCENT_ONLY : PERCENT_OR_NUMBER, 100);
            R(2, legacy ? PERCENT_ONLY : PERCENT_OR_NUMBER, 100);
            /* A negative saturation is clamped to zero */
            double rgb[3];
            hsl_to_rgb(c[0], fmax(0, c[1]) / 100, c[2] / 100, rgb);
            for (unsigned i = 0; i < 3; i++) c[i] = clamp(rgb[i] * 255, 0, 255);
            out->space = KG_CS_RGB;
        } break;
        case F_HWB: {
            R(0, HUE, 0);
            R(1, PERCENT_OR_NUMBER, 100);
            R(2, PERCENT_OR_NUMBER, 100);
            double rgb[3];
            hwb_to_rgb(c[0], c[1] / 100, c[2] / 100, rgb);
            for (unsigned i = 0; i < 3; i++) c[i] = clamp(rgb[i] * 255, 0, 255);
            out->space = KG_CS_RGB;
        } break;
        case F_LAB:
            R(0, PERCENT_OR_NUMBER, 100);
            R(1, PERCENT_OR_NUMBER, 125);
            R(2, PERCENT_OR_NUMBER, 125);
            c[0] = clamp(c[0], 0, 100);
            out->space = KG_CS_LAB;
            break;
        case F_LCH:
            R(0, PERCENT_OR_NUMBER, 100);
            R(1, PERCENT_OR_NUMBER, 150);
            R(2, HUE, 0);
            c[0] = clamp(c[0], 0, 100);
            c[1] = fmax(0, c[1]);
            if (!(out->none & 4)) c[2] = normalized_hue(c[2]);
            out->space = KG_CS_LCH;
            break;
        case F_OKLAB:
            R(0, PERCENT_OR_NUMBER, 1);
            R(1, PERCENT_OR_NUMBER, 0.4);
            R(2, PERCENT_OR_NUMBER, 0.4);
            c[0] = clip01(c[0]);
            out->space = KG_CS_OKLAB;
            break;
        case F_OKLCH:
            R(0, PERCENT_OR_NUMBER, 1);
            R(1, PERCENT_OR_NUMBER, 0.4);
            R(2, HUE, 0);
            c[0] = clip01(c[0]);
            c[1] = fmax(0, c[1]);
            if (!(out->none & 4)) c[2] = normalized_hue(c[2]);
            out->space = KG_CS_OKLCH;
            break;
        case F_COLOR:
            for (unsigned i = 0; i < 3; i++) { R(i, PERCENT_OR_NUMBER, 1); }
            break;
    }
#undef R
    /* The legacy sRGB forms have no none: it is 0 */
    if (out->space == KG_CS_RGB) out->none = 0;
    return true;
}

static bool
parse_hash(scanner *p, kg_color *out) {
    const Py_ssize_t start = ++p->pos;
    while (p->pos < p->len && is_nmchar(p->s[p->pos])) p->pos++;
    const Py_ssize_t n = p->pos - start;
    skip_ignorable(p);
    if (p->pos != p->len) return false;
    if (n != 3 && n != 4 && n != 6 && n != 8) return false;
    int v[8];
    for (Py_ssize_t i = 0; i < n; i++) if ((v[i] = hex_value(p->s[start + i])) < 0) return false;
    const bool short_form = n < 6;
    double channels[4] = {0, 0, 0, 255};
    for (unsigned i = 0; i < (unsigned)(short_form ? n : n / 2); i++) {
        channels[i] = short_form ? v[i] * 17 : v[2 * i] * 16 + v[2 * i + 1];
    }
    out->space = KG_CS_RGB;
    out->none = 0;
    for (unsigned i = 0; i < 3; i++) out->c[i] = channels[i];
    out->alpha = channels[3] / 255.;
    return true;
}

static void
set_rgb(kg_color *out, double r, double g, double b, double alpha) {
    out->space = KG_CS_RGB;
    out->none = 0;
    out->c[0] = r; out->c[1] = g; out->c[2] = b;
    out->alpha = alpha;
}

/* Parse ASCII text without backslashes as a color */
static kg_color_result
parse_ascii(const unsigned char *s, Py_ssize_t len, kg_color *out) {
    scanner p = {.s = s, .pos = 0, .len = len};
    skip_ignorable(&p);
    const unsigned char ch = peek(&p, 0);
    if (ch == '#') return parse_hash(&p, out) ? KG_COLOR_OK : KG_COLOR_INVALID;
    if (!is_name_start(ch)) return KG_COLOR_INVALID;
    char name[32];
    if (!read_name(&p, name, sizeof(name))) return KG_COLOR_INVALID;
    if (peek(&p, 0) == '(') {
        const int func = function_from_name(name);
        return func >= 0 && parse_function(&p, (color_function)func, out) ? KG_COLOR_OK : KG_COLOR_INVALID;
    }
    skip_ignorable(&p);
    if (p.pos != p.len) return KG_COLOR_INVALID;
    if (strcmp(name, "currentcolor") == 0) return KG_COLOR_CURRENT;
    if (strcmp(name, "transparent") == 0) { set_rgb(out, 0, 0, 0, 0); return KG_COLOR_OK; }
    if (strcmp(name, "rebeccapurple") == 0) { set_rgb(out, 102, 51, 153, 1); return KG_COLOR_OK; }
    const color_keyword *kw = bsearch(name, color_keywords, arraysz(color_keywords), sizeof(color_keywords[0]), compare_keyword);
    if (kw == NULL) return KG_COLOR_INVALID;
    set_rgb(out, kw->r, kw->g, kw->b, 1);
    return KG_COLOR_OK;
}

/* ---------------------------------------------------------------------------
 * Text with escapes or non-ASCII characters
 * ------------------------------------------------------------------------- */

typedef struct {
    unsigned char *s;
    size_t len, cap;
} abuf;

static bool
abuf_put(abuf *b, const char *s, size_t n) {
    if (b->len + n > b->cap) {
        size_t cap = (b->cap ? b->cap * 2 : 64);
        while (cap < b->len + n) cap *= 2;
        unsigned char *ns = PyMem_Realloc(b->s, cap);
        if (ns == NULL) return false;
        b->s = ns;
        b->cap = cap;
    }
    memcpy(b->s + b->len, s, n);
    b->len += n;
    return true;
}

/* Append the decoded text of [start, end) of the source. False if it is not a plain ASCII name
 * (a letter, then letters, digits and -), or hex digits for a hash; *err on memory errors. */
static bool
put_decoded(abuf *b, int kind, const void *data, const kg_token *t, uint32_t start, uint32_t end, bool hex, bool *err) {
    char tmp[64];
    size_t n = 0;
    if (t->flags & KG_TF_NEEDS_PROCESSING) {
        PyObject *s = kg_unescape(kind, data, start, end, false);
        if (s == NULL) { *err = true; return false; }
        const bool ok = PyUnicode_IS_ASCII(s) && (size_t)PyUnicode_GET_LENGTH(s) < sizeof(tmp);
        if (ok) {
            n = PyUnicode_GET_LENGTH(s);
            memcpy(tmp, PyUnicode_1BYTE_DATA(s), n);
        }
        Py_DECREF(s);
        if (!ok) return false;
    } else {
        if (end - start >= sizeof(tmp)) return false;
        for (uint32_t i = start; i < end; i++) {
            const Py_UCS4 c = PyUnicode_READ(kind, data, i);
            if (c > 127) return false;
            tmp[n++] = (char)c;
        }
    }
    if (n == 0) return false;
    for (size_t i = 0; i < n; i++) {
        const unsigned char c = (unsigned char)tmp[i];
        const bool letter = ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z');
        if (hex ? hex_value(c) < 0 : !(letter || (i > 0 && (is_digit(c) || c == '-')))) return false;
    }
    if (!abuf_put(b, tmp, n)) { *err = true; return false; }
    return true;
}

/* Rebuild the text as ASCII from its tokens, with names decoded and comments as spaces. Returns
 * KG_COLOR_INVALID if it cannot be a color, -1 with an exception set on errors. */
static int
parse_tokens(PyObject *src, kg_color *out) {
    const Py_ssize_t len = PyUnicode_GET_LENGTH(src);
    if (len > UINT32_MAX / 2) return KG_COLOR_INVALID;
    const int kind = PyUnicode_KIND(src);
    const void *data = PyUnicode_DATA(src);
    kg_token_array tokens = {0};
    abuf b = {0};
    int ans = KG_COLOR_INVALID;
    bool err = false;
    if (!kg_tokenize(kind, data, (uint32_t)len, true, &tokens)) { PyErr_NoMemory(); return -1; }
    for (uint32_t i = 0; i < tokens.count; i++) {
        const kg_token *t = tokens.tokens + i;
        bool ok = true;
        switch (t->type) {
            case KG_TOK_WHITESPACE: case KG_TOK_COMMENT: ok = abuf_put(&b, " ", 1); err = !ok; break;
            case KG_TOK_IDENT: ok = put_decoded(&b, kind, data, t, t->vstart, t->vend, false, &err); break;
            case KG_TOK_FUNCTION:
                ok = put_decoded(&b, kind, data, t, t->vstart, t->vend, false, &err);
                if (ok) { ok = abuf_put(&b, "(", 1); err = !ok; }
                break;
            case KG_TOK_HASH:
                ok = abuf_put(&b, "#", 1);
                err = !ok;
                if (ok) ok = put_decoded(&b, kind, data, t, t->vstart, t->vend, true, &err);
                break;
            case KG_TOK_NUMBER: case KG_TOK_PERCENTAGE: case KG_TOK_DIMENSION: {
                /* The number itself is always plain ASCII */
                char num[128];
                if (t->vend - t->vstart >= sizeof(num)) { ok = false; break; }
                for (uint32_t k = t->vstart; k < t->vend; k++) num[k - t->vstart] = (char)PyUnicode_READ(kind, data, k);
                ok = abuf_put(&b, num, t->vend - t->vstart);
                err = !ok;
                if (ok && t->type == KG_TOK_PERCENTAGE) { ok = abuf_put(&b, "%", 1); err = !ok; }
                /* The unit is not decoded by the tokenizer: it is [vend, end) of the source */
                if (ok && t->type == KG_TOK_DIMENSION) {
                    kg_token unit = *t;
                    unit.flags = KG_TF_NEEDS_PROCESSING;
                    ok = put_decoded(&b, kind, data, &unit, t->vend, t->end, false, &err);
                }
            } break;
            case KG_TOK_COMMA: ok = abuf_put(&b, ",", 1); err = !ok; break;
            case KG_TOK_RIGHT_PAREN: ok = abuf_put(&b, ")", 1); err = !ok; break;
            case KG_TOK_DELIM: {
                const Py_UCS4 c = PyUnicode_READ(kind, data, t->start);
                ok = c == '/';
                if (ok) { ok = abuf_put(&b, "/", 1); err = !ok; }
            } break;
            default: ok = false;
        }
        if (!ok) goto end;
    }
    ans = parse_ascii(b.s, (Py_ssize_t)b.len, out);
end:
    PyMem_Free(b.s);
    kg_token_array_free(&tokens);
    if (err) {
        if (!PyErr_Occurred()) PyErr_NoMemory();
        return -1;
    }
    return ans;
}

/* Parse a str as a color: a kg_color_result, or -1 with an exception set */
static int
parse_color(PyObject *src, kg_color *out) {
    if (PyUnicode_IS_ASCII(src)) {
        const unsigned char *s = PyUnicode_1BYTE_DATA(src);
        const Py_ssize_t len = PyUnicode_GET_LENGTH(src);
        if (memchr(s, '\\', len) == NULL) return parse_ascii(s, len, out);
    }
    return parse_tokens(src, out);
}

/* ---------------------------------------------------------------------------
 * Conversion to sRGB. Matrices and transfer functions from the sample code of
 * CSS Color 4.
 * ------------------------------------------------------------------------- */

static void
mat3_mul(const double m[3][3], const double v[3], double out[3]) {
    for (unsigned i = 0; i < 3; i++) out[i] = m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2];
}

static const double XYZ_D65_to_lin_sRGB[3][3] = {
    {12831. / 3959., -329. / 214., -1974. / 3959.},
    {-851781. / 878810., 1648619. / 878810., 36519. / 878810.},
    {705. / 12673., -2585. / 12673., 705. / 667.},
};
static const double D50_to_D65[3][3] = {
    {0.955473421488075, -0.02309845494876471, 0.06325924320057072},
    {-0.0283697093338637, 1.0099953980813041, 0.021041441191917323},
    {0.012314014864481998, -0.020507649298898964, 1.330365926242124},
};
static const double lin_P3_to_XYZ[3][3] = {
    {608311. / 1250200., 189793. / 714400., 198249. / 1000160.},
    {35783. / 156275., 247089. / 357200., 198249. / 2500400.},
    {0., 32229. / 714400., 5220557. / 5000800.},
};
static const double lin_a98rgb_to_XYZ[3][3] = {
    {573536. / 994567., 263643. / 1420810., 187206. / 994567.},
    {591459. / 1989134., 6239551. / 9945670., 374412. / 4972835.},
    {53769. / 1989134., 351524. / 4972835., 4929758. / 4972835.},
};
static const double lin_ProPhoto_to_XYZ_D50[3][3] = {
    {0.79776664490064230, 0.13518129740053308, 0.03134773412839220},
    {0.28807482881940130, 0.71183523424187300, 0.00008993693872564},
    {0.0, 0.0, 0.82510460251046020},
};
static const double lin_2020_to_XYZ[3][3] = {
    {63426534. / 99577255., 20160776. / 139408157., 47086771. / 278816314.},
    {26158966. / 99577255., 472592308. / 697040785., 8267143. / 139408157.},
    {0., 19567812. / 697040785., 295819943. / 278816314.},
};
static const double OKLab_to_LMS[3][3] = {
    {1.0, 0.3963377773761749, 0.2158037573099136},
    {1.0, -0.1055613458156586, -0.0638541728258133},
    {1.0, -0.0894841775298119, -1.2914855480194092},
};
static const double LMS_to_XYZ[3][3] = {
    {1.2268798758459243, -0.5578149944602171, 0.2813910456659647},
    {-0.0405757452148008, 1.1122868032803170, -0.0717110580655164},
    {-0.0763729366746601, -0.4214933324022432, 1.5869240198367816},
};

static double
srgb_to_linear(double v) {
    const double a = fabs(v);
    return a <= 0.04045 ? v / 12.92 : copysign(pow((a + 0.055) / 1.055, 2.4), v);
}

static double
linear_to_srgb(double v) {
    const double a = fabs(v);
    return a > 0.0031308 ? copysign(1.055 * pow(a, 1 / 2.4) - 0.055, v) : 12.92 * v;
}

static double a98_to_linear(double v) { return copysign(pow(fabs(v), 563. / 256.), v); }

static double
prophoto_to_linear(double v) {
    const double a = fabs(v);
    return a <= 16. / 512. ? v / 16. : copysign(pow(a, 1.8), v);
}

static double
rec2020_to_linear(double v) {
    const double alpha = 1.09929682680944, beta = 0.018053968510807;
    const double a = fabs(v);
    return a < beta * 4.5 ? v / 4.5 : copysign(pow((a + alpha - 1) / alpha, 1 / 0.45), v);
}

static void
xyz_d65_to_srgb(const double xyz[3], double rgb[3]) {
    double lin[3];
    mat3_mul(XYZ_D65_to_lin_sRGB, xyz, lin);
    for (unsigned i = 0; i < 3; i++) rgb[i] = linear_to_srgb(lin[i]);
}

static void
xyz_d50_to_srgb(const double xyz[3], double rgb[3]) {
    double d65[3];
    mat3_mul(D50_to_D65, xyz, d65);
    xyz_d65_to_srgb(d65, rgb);
}

static void
lab_to_srgb(double L, double a, double b, double rgb[3]) {
    const double kappa = 24389. / 27., epsilon = 216. / 24389.;
    const double white[3] = {0.3457 / 0.3585, 1.0, (1.0 - 0.3457 - 0.3585) / 0.3585};
    const double f1 = (L + 16) / 116, f0 = a / 500 + f1, f2 = f1 - b / 200;
    double xyz[3] = {
        f0 * f0 * f0 > epsilon ? f0 * f0 * f0 : (116 * f0 - 16) / kappa,
        L > kappa * epsilon ? f1 * f1 * f1 : L / kappa,
        f2 * f2 * f2 > epsilon ? f2 * f2 * f2 : (116 * f2 - 16) / kappa,
    };
    for (unsigned i = 0; i < 3; i++) xyz[i] *= white[i];
    xyz_d50_to_srgb(xyz, rgb);
}

static void
oklab_to_srgb(double L, double a, double b, double rgb[3]) {
    double lab[3] = {L, a, b}, lms[3], xyz[3];
    mat3_mul(OKLab_to_LMS, lab, lms);
    for (unsigned i = 0; i < 3; i++) lms[i] = lms[i] * lms[i] * lms[i];
    mat3_mul(LMS_to_XYZ, lms, xyz);
    xyz_d65_to_srgb(xyz, rgb);
}

static void
linear_space_to_srgb(const double c[3], double (*transfer)(double), const double matrix[3][3], bool d50, double rgb[3]) {
    double lin[3], xyz[3];
    for (unsigned i = 0; i < 3; i++) lin[i] = transfer(c[i]);
    mat3_mul(matrix, lin, xyz);
    if (d50) xyz_d50_to_srgb(xyz, rgb);
    else xyz_d65_to_srgb(xyz, rgb);
}

/* The color in sRGB, each channel clipped to 0..1 */
static void
to_srgb(const kg_color *k, double rgba[4]) {
    const double *c = k->c;
    switch (k->space) {
        case KG_CS_RGB: for (unsigned i = 0; i < 3; i++) rgba[i] = c[i] / 255; break;
        case KG_CS_LAB: lab_to_srgb(c[0], c[1], c[2], rgba); break;
        case KG_CS_LCH: {
            const double h = c[2] * M_PI / 180.;
            lab_to_srgb(c[0], c[1] * cos(h), c[1] * sin(h), rgba);
        } break;
        case KG_CS_OKLAB: oklab_to_srgb(c[0], c[1], c[2], rgba); break;
        case KG_CS_OKLCH: {
            const double h = c[2] * M_PI / 180.;
            oklab_to_srgb(c[0], c[1] * cos(h), c[1] * sin(h), rgba);
        } break;
        case KG_CS_SRGB: for (unsigned i = 0; i < 3; i++) rgba[i] = c[i]; break;
        case KG_CS_SRGB_LINEAR: for (unsigned i = 0; i < 3; i++) rgba[i] = linear_to_srgb(c[i]); break;
        case KG_CS_DISPLAY_P3: linear_space_to_srgb(c, srgb_to_linear, lin_P3_to_XYZ, false, rgba); break;
        case KG_CS_A98_RGB: linear_space_to_srgb(c, a98_to_linear, lin_a98rgb_to_XYZ, false, rgba); break;
        case KG_CS_PROPHOTO_RGB: linear_space_to_srgb(c, prophoto_to_linear, lin_ProPhoto_to_XYZ_D50, true, rgba); break;
        case KG_CS_REC2020: linear_space_to_srgb(c, rec2020_to_linear, lin_2020_to_XYZ, false, rgba); break;
        case KG_CS_XYZ_D50: xyz_d50_to_srgb(c, rgba); break;
        case KG_CS_XYZ_D65: xyz_d65_to_srgb(c, rgba); break;
    }
    rgba[3] = k->alpha;
    /* Outside the sRGB gamut, clip; NaN cannot happen for finite input, but would become 0 */
    for (unsigned i = 0; i < 4; i++) rgba[i] = isnan(rgba[i]) ? 0 : clip01(rgba[i]);
}

/* ---------------------------------------------------------------------------
 * Python
 * ------------------------------------------------------------------------- */

static PyStructSequence_Field RGBA_fields[] = {
    {"red", "0 to 1"}, {"green", "0 to 1"}, {"blue", "0 to 1"}, {"alpha", "0 to 1"}, {NULL, NULL},
};

static PyStructSequence_Desc RGBA_desc = {
    .name = "kgcss.RGBA",
    .doc = "A color in sRGB: red, green, blue and alpha, each a float from 0 to 1.",
    .fields = RGBA_fields,
    .n_in_sequence = 4,
};

static PyObject *new_rgba(kg_module_state *state, const kg_color *k);

PyObject *
kg_parse_rgba(PyObject *module, PyObject *src) {
    if (!PyUnicode_Check(src)) {
        PyErr_Format(PyExc_TypeError, "parse_rgba() argument must be str, not %T", src);
        return NULL;
    }
    kg_color k;
    switch (parse_color(src, &k)) {
        case -1: return NULL;
        case KG_COLOR_INVALID: Py_RETURN_NONE;
        case KG_COLOR_CURRENT: return Py_NewRef(kg_get_module_state(module)->str_currentColor);
        default: break;
    }
    return new_rgba(kg_get_module_state(module), &k);
}

/* The names of the color spaces, as Color.space, in the order of kg_color_space */
static const char *space_names[] = {
    "rgb", "lab", "lch", "oklab", "oklch", "srgb", "srgb-linear", "display-p3", "a98-rgb", "prophoto-rgb", "rec2020",
    "xyz-d50", "xyz-d65",
};

static PyObject *
new_rgba(kg_module_state *state, const kg_color *k) {
    double rgba[4];
    to_srgb(k, rgba);
    /* Not PyStructSequence_New(), which looks up the field counts in the type's dict on every call. All
     * four fields are in the sequence, so the object is exactly a tuple of size 4. */
    PyObject *ans = state->RGBA_type->tp_alloc(state->RGBA_type, 4);
    if (ans == NULL) return NULL;
    for (Py_ssize_t i = 0; i < 4; i++) {
        PyObject *f = PyFloat_FromDouble(rgba[i]);
        if (f == NULL) { Py_DECREF(ans); return NULL; }
        PyTuple_SET_ITEM(ans, i, f);
    }
    return ans;
}

/* parse_color_raw(text) -> (space, c0, c1, c2, alpha) | 'currentColor' | None, with None for none */
PyObject *
kg_parse_color_raw(PyObject *module, PyObject *src) {
    if (!PyUnicode_Check(src)) {
        PyErr_Format(PyExc_TypeError, "parse_color() argument must be str, not %T", src);
        return NULL;
    }
    kg_color k;
    switch (parse_color(src, &k)) {
        case -1: return NULL;
        case KG_COLOR_INVALID: Py_RETURN_NONE;
        case KG_COLOR_CURRENT: return Py_NewRef(kg_get_module_state(module)->str_currentColor);
        default: break;
    }
    kg_module_state *state = kg_get_module_state(module);
    PyObject *ans = PyTuple_New(5);
    if (ans == NULL) return NULL;
    PyTuple_SET_ITEM(ans, 0, Py_NewRef(PyTuple_GET_ITEM(state->color_space_names, k.space)));
    for (unsigned i = 0; i < 4; i++) {
        PyObject *v = (k.none & (1u << i)) ? Py_NewRef(Py_None) : PyFloat_FromDouble(i < 3 ? k.c[i] : k.alpha);
        if (v == NULL) { Py_DECREF(ans); return NULL; }
        PyTuple_SET_ITEM(ans, i + 1, v);
    }
    return ans;
}

static bool
as_channel(PyObject *o, double *ans, unsigned bit, unsigned *none) {
    if (o == Py_None) {
        *ans = 0;
        *none |= bit;
        return true;
    }
    *ans = PyFloat_AsDouble(o);
    if (*ans == -1.0 && PyErr_Occurred()) return false;
    if (!isfinite(*ans)) {
        PyErr_SetString(PyExc_ValueError, "color channels must be finite");
        return false;
    }
    return true;
}

/* color_to_rgba(space, c0, c1, c2, alpha) -> RGBA. None is none, which counts as 0. */
PyObject *
kg_color_to_rgba(PyObject *module, PyObject *const *args, Py_ssize_t nargs) {
    if (nargs != 5 || !PyUnicode_Check(args[0])) {
        PyErr_SetString(PyExc_TypeError, "color_to_rgba(space: str, c0, c1, c2, alpha)");
        return NULL;
    }
    kg_module_state *state = kg_get_module_state(module);
    kg_color k = {.none = 0};
    bool found = false;
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(state->color_space_names) && !found; i++) {
        if (PyUnicode_Compare(args[0], PyTuple_GET_ITEM(state->color_space_names, i)) == 0) {
            k.space = (kg_color_space)i;
            found = true;
        }
    }
    if (!found) {
        PyErr_Format(PyExc_ValueError, "unknown color space: %R", args[0]);
        return NULL;
    }
    for (unsigned i = 0; i < 4; i++) {
        if (!as_channel(args[i + 1], i < 3 ? k.c + i : &k.alpha, 1u << i, &k.none)) return NULL;
    }
    return new_rgba(state, &k);
}

int
kg_color_init(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    state->RGBA_type = PyStructSequence_NewType(&RGBA_desc);
    if (state->RGBA_type == NULL) return -1;
    if (PyModule_AddObjectRef(module, "RGBA", (PyObject *)state->RGBA_type) != 0) return -1;
    state->str_currentColor = PyUnicode_InternFromString("currentColor");
    if (state->str_currentColor == NULL) return -1;
    state->color_space_names = PyTuple_New(arraysz(space_names));
    if (state->color_space_names == NULL) return -1;
    for (Py_ssize_t i = 0; i < (Py_ssize_t)arraysz(space_names); i++) {
        PyObject *name = PyUnicode_InternFromString(space_names[i]);
        if (name == NULL) return -1;
        PyTuple_SET_ITEM(state->color_space_names, i, name);
    }
    return 0;
}

int
kg_color_traverse(PyObject *module, visitproc visit, void *arg) {
    kg_module_state *state = kg_get_module_state(module);
    Py_VISIT(state->RGBA_type);
    Py_VISIT(state->str_currentColor);
    Py_VISIT(state->color_space_names);
    return 0;
}

void
kg_color_clear(PyObject *module) {
    kg_module_state *state = kg_get_module_state(module);
    Py_CLEAR(state->RGBA_type);
    Py_CLEAR(state->str_currentColor);
    Py_CLEAR(state->color_space_names);
}
