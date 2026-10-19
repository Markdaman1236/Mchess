/* Stand-in for <machine/endian.h>: Windows on x86-64 and ARM64 is little endian. */
#ifndef SJENG_WIN_ENDIAN_H
#define SJENG_WIN_ENDIAN_H
#define __DARWIN_LITTLE_ENDIAN 1234
#define __DARWIN_BIG_ENDIAN 4321
#define __DARWIN_BYTE_ORDER __DARWIN_LITTLE_ENDIAN
#endif
