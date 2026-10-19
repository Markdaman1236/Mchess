/* Minimal ndbm for Sjeng's binary opening book (newbook.c) on Windows.
 * dbm_open(name) reads and writes "<name>.db" as a flat list of records;
 * the whole table lives in memory and is written back by dbm_close when
 * it was opened for writing. */
#ifndef SJENG_WIN_NDBM_H
#define SJENG_WIN_NDBM_H
#include <stddef.h>
typedef struct { void *dptr; size_t dsize; } datum;
typedef struct dbm_table DBM;
#define DBM_INSERT 0
#define DBM_REPLACE 1
DBM *dbm_open(const char *name, int flags, int mode);
void dbm_close(DBM *db);
datum dbm_fetch(DBM *db, datum key);
int dbm_store(DBM *db, datum key, datum content, int mode);
int dbm_delete(DBM *db, datum key);
datum dbm_firstkey(DBM *db);
datum dbm_nextkey(DBM *db);
#endif
