Sjeng 11.2 for the Windows port of Apple Chess
==============================================

Source: Apple's copy of Sjeng in https://github.com/apple-oss-distributions/Chess
(folder "sjeng"), by Gian-Carlo Pascutto, licensed under the GNU GPL
version 2 (COPYING.txt). engine\sjeng.exe in the Chess-Windows folder is
built from the files here.

Changes from Apple's source
---------------------------
- src\blob2.c: includes <windows.h> first (Sjeng's square names such as D8
  clash with Windows headers) and undefines TRUE/FALSE afterwards.
- src\sjeng.c: the "sd" command read its number with sscanf(input, "%d"),
  which never matches "sd N", so the search depth stayed at 40 and every
  difficulty level played the same. It now reads from input + 2.
- compat\config.h: Windows replacement for the autoconf config.h.
- compat\machine\endian.h, compat\sys\fcntl.h: small stand-ins for Mac headers.
- compat\ndbm.h, compat\ndbm_win.c: a small ndbm replacement for the
  binary opening books (nbook.db, zbook.db are in its own simple format).

Building (Visual Studio Build Tools, x64 Native Tools prompt)
-------------------------------------------------------------
  cl /O2 /MT /I compat /I src /D_CRT_SECURE_NO_WARNINGS /D_CRT_NONSTDC_NO_WARNINGS ^
     /Fesjeng.exe src\blob2.c compat\ndbm_win.c ws2_32.lib

Opening books (run in the folder with sjeng.exe; PGN files are in Apple's
sjeng\books folder):
  sjeng.exe < commands   where commands is
      variant normal / book / nbook.pgn / 10000000 / quit       -> nbook.db
      variant crazyhouse / book / zhbook.pgn / 10000000 / quit  -> zbook.db
