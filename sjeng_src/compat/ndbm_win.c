/* See ndbm.h. Open addressing hash table of byte-string keys. */
#define _CRT_SECURE_NO_WARNINGS
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ndbm.h"

typedef struct {
    unsigned char *key, *val;
    size_t klen, vlen;
    int used;            /* 0 empty, 1 live, 2 deleted */
} slot_t;

struct dbm_table {
    char path[1024];
    int writable, dirty;
    size_t cap, live, filled, iter;
    slot_t *slots;
};

static uint32_t hash_bytes(const unsigned char *p, size_t n)
{
    uint32_t h = 2166136261u;
    while (n--) { h ^= *p++; h *= 16777619u; }
    return h;
}

static slot_t *find(DBM *db, const void *key, size_t klen, int for_insert)
{
    size_t mask = db->cap - 1, i = hash_bytes(key, klen) & mask;
    slot_t *tomb = NULL;
    for (;;) {
        slot_t *s = &db->slots[i];
        if (s->used == 0) return for_insert ? (tomb ? tomb : s) : NULL;
        if (s->used == 2) { if (!tomb) tomb = s; }
        else if (s->klen == klen && !memcmp(s->key, key, klen)) return s;
        i = (i + 1) & mask;
    }
}

static void put(DBM *db, const void *key, size_t klen, const void *val, size_t vlen);

static void grow(DBM *db)
{
    slot_t *old = db->slots;
    size_t oldcap = db->cap, i;
    db->cap = oldcap ? oldcap * 2 : 1024;
    db->slots = calloc(db->cap, sizeof(slot_t));
    db->live = db->filled = 0;
    for (i = 0; i < oldcap; i++)
        if (old[i].used == 1) {
            put(db, old[i].key, old[i].klen, old[i].val, old[i].vlen);
            free(old[i].key);
            free(old[i].val);
        }
    free(old);
}

static void put(DBM *db, const void *key, size_t klen, const void *val, size_t vlen)
{
    slot_t *s;
    if ((db->filled + 1) * 4 >= db->cap * 3) grow(db);
    s = find(db, key, klen, 1);
    if (s->used == 1) {
        free(s->val);
    } else {
        if (s->used == 0) db->filled++;
        s->key = malloc(klen ? klen : 1);
        memcpy(s->key, key, klen);
        s->klen = klen;
        s->used = 1;
        db->live++;
    }
    s->val = malloc(vlen ? vlen : 1);
    memcpy(s->val, val, vlen);
    s->vlen = vlen;
}

DBM *dbm_open(const char *name, int flags, int mode)
{
    DBM *db = calloc(1, sizeof(DBM));
    FILE *f;
    (void)mode;
    snprintf(db->path, sizeof db->path, "%s.db", name);
    db->writable = (flags & (O_RDWR | O_WRONLY)) != 0;
    grow(db);
    if (flags & O_TRUNC) { db->dirty = 1; return db; }
    f = fopen(db->path, "rb");
    if (!f) {
        if (flags & O_CREAT) { db->dirty = 1; return db; }
        free(db->slots);
        free(db);
        return NULL;
    }
    for (;;) {
        uint32_t klen, vlen;
        unsigned char *k, *v;
        if (fread(&klen, 4, 1, f) != 1 || fread(&vlen, 4, 1, f) != 1) break;
        k = malloc(klen ? klen : 1);
        v = malloc(vlen ? vlen : 1);
        if (fread(k, 1, klen, f) != klen || fread(v, 1, vlen, f) != vlen) { free(k); free(v); break; }
        put(db, k, klen, v, vlen);
        free(k);
        free(v);
    }
    fclose(f);
    return db;
}

void dbm_close(DBM *db)
{
    size_t i;
    if (!db) return;
    if (db->writable && db->dirty) {
        FILE *f = fopen(db->path, "wb");
        if (f) {
            for (i = 0; i < db->cap; i++)
                if (db->slots[i].used == 1) {
                    uint32_t klen = (uint32_t)db->slots[i].klen, vlen = (uint32_t)db->slots[i].vlen;
                    fwrite(&klen, 4, 1, f);
                    fwrite(&vlen, 4, 1, f);
                    fwrite(db->slots[i].key, 1, klen, f);
                    fwrite(db->slots[i].val, 1, vlen, f);
                }
            fclose(f);
        }
    }
    for (i = 0; i < db->cap; i++)
        if (db->slots[i].used == 1) { free(db->slots[i].key); free(db->slots[i].val); }
    free(db->slots);
    free(db);
}

datum dbm_fetch(DBM *db, datum key)
{
    datum out = { NULL, 0 };
    slot_t *s = find(db, key.dptr, key.dsize, 0);
    if (s && s->used == 1) { out.dptr = s->val; out.dsize = s->vlen; }
    return out;
}

int dbm_store(DBM *db, datum key, datum content, int mode)
{
    slot_t *s = find(db, key.dptr, key.dsize, 0);
    if (s && s->used == 1 && mode == DBM_INSERT) return 1;
    put(db, key.dptr, key.dsize, content.dptr, content.dsize);
    db->dirty = 1;
    return 0;
}

int dbm_delete(DBM *db, datum key)
{
    slot_t *s = find(db, key.dptr, key.dsize, 0);
    if (!s || s->used != 1) return -1;
    free(s->key);
    free(s->val);
    s->key = s->val = NULL;
    s->used = 2;
    db->live--;
    db->dirty = 1;
    return 0;
}

static datum scan(DBM *db)
{
    datum out = { NULL, 0 };
    while (db->iter < db->cap) {
        slot_t *s = &db->slots[db->iter++];
        if (s->used == 1) { out.dptr = s->key; out.dsize = s->klen; break; }
    }
    return out;
}

datum dbm_firstkey(DBM *db) { db->iter = 0; return scan(db); }
datum dbm_nextkey(DBM *db) { return scan(db); }
