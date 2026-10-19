/* config.h for building Apple's Sjeng 11.2 with MSVC on Windows. */
#define HAVE_FTIME 1
#define HAVE_INTTYPES_H 1
#define HAVE_LIBM 1
#define HAVE_STDINT_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define HAVE_STRSTR 1
#define HAVE_SYS_STAT_H 1
#define HAVE_SYS_TIMEB_H 1
#define HAVE_SYS_TYPES_H 1
/* No HAVE_SELECT: utils.c then uses its _WIN32 PeekNamedPipe input code. */
#define PACKAGE "Sjeng-Free"
#define RETSIGTYPE void
#define STDC_HEADERS 1
#define VERSION "11.2"
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif
#include <string.h>
#define strcasecmp _stricmp
#define strncasecmp _strnicmp
#ifdef _MSC_VER
#define inline __inline
#endif
