/* std::string as LevelDB uses it: the driver's stand-in, not LevelDB code.
 * Like libstdc++'s append, the capacity check and the copy are inline at
 * every call site and only growing is a call. */
#ifndef LEVELDB_BUF_H
#define LEVELDB_BUF_H

#include <stddef.h>
#include <string.h>

typedef struct {
	char *data;
	size_t size, cap;
} Buf;

void buf_grow(Buf *b, size_t n);
void buf_resize(Buf *b, size_t n);

static inline void buf_append(Buf *b, const char *p, size_t n)
{
	if (b->size + n > b->cap) buf_grow(b, b->size + n);
	memcpy(b->data + b->size, p, n);
	b->size += n;
}

static inline void buf_push(Buf *b, char c) { buf_append(b, &c, 1); }

#endif
