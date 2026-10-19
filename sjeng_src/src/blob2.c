#ifdef _WIN32
/* Windows build: windows.h must come before squares.h defines D8, D9, ... */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#undef FALSE
#undef TRUE
#endif
/* Compile this file for a faster Sjeng */

/* e.g. gcc -O9 -lgdbm -o sjeng blob2.c */

#include "rcfile.c"
#include "attacks.c"
#include "book.c"
#include "crazy.c"
#include "epd.c"
#include "learn.c"
#include "newbook.c"
#include "partner.c"
#include "moves.c"
#include "ttable.c"
#include "ecache.c"
#include "draw.c"
#include "see.c"
#include "search.c"
#include "seval.c"
#include "neval.c"
#include "eval.c"
#include "sjeng.c"
#include "utils.c"
#include "proof.c"
#include "leval.c"
#include "segtb.c"

