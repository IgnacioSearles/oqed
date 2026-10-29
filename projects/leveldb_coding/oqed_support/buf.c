/* std::string as LevelDB uses it: the driver's stand-in, not LevelDB code. */
#include "buf.h"

#include <stdlib.h>

void buf_grow(Buf *b, size_t n)
{
	b->cap = n * 2 > 64 ? n * 2 : 64;
	b->data = realloc(b->data, b->cap);
}

void buf_resize(Buf *b, size_t n)
{
	if (n > b->cap) buf_grow(b, n);
	if (n > b->size) memset(b->data + b->size, 0, n - b->size);
	b->size = n;
}
