#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <yaml.h>

static uint64_t ev_hash;
static uint64_t out_hash;

static void h_ev_bytes(const unsigned char *d, size_t n) {
    for (size_t i = 0; i < n; i++) {
        ev_hash ^= (uint64_t)d[i];
        ev_hash *= (uint64_t)1099511628211ULL;
    }
}
static void h_ev_int(long long v) {
    h_ev_bytes((unsigned char *)&v, sizeof(v));
}
static void h_ev_str(const yaml_char_t *s) {
    if (s) h_ev_bytes((const unsigned char *)s, strlen((const char *)s));
    else h_ev_int(0x9e3779b9);
}
static void h_out_bytes(const unsigned char *d, size_t n) {
    for (size_t i = 0; i < n; i++) {
        out_hash ^= (uint64_t)d[i];
        out_hash *= (uint64_t)1099511628211ULL;
    }
}
static int write_handler(void *data, unsigned char *buffer, size_t size) {
    (void)data;
    h_out_bytes(buffer, size);
    return 1;
}
static void hash_event(yaml_event_t *e) {
    h_ev_int((long long)e->type);
    switch (e->type) {
    case YAML_STREAM_START_EVENT:
        h_ev_int((long long)e->data.stream_start.encoding);
        break;
    case YAML_DOCUMENT_START_EVENT:
        if (e->data.document_start.version_directive) {
            h_ev_int(e->data.document_start.version_directive->major);
            h_ev_int(e->data.document_start.version_directive->minor);
        } else h_ev_int(-1);
        {
            yaml_tag_directive_t *td = e->data.document_start.tag_directives.start;
            yaml_tag_directive_t *te = e->data.document_start.tag_directives.end;
            h_ev_int((long long)(te - td));
            for (; td != te; td++) { h_ev_str(td->handle); h_ev_str(td->prefix); }
        }
        h_ev_int(e->data.document_start.implicit);
        break;
    case YAML_DOCUMENT_END_EVENT:
        h_ev_int(e->data.document_end.implicit);
        break;
    case YAML_ALIAS_EVENT:
        h_ev_str(e->data.alias.anchor);
        break;
    case YAML_SCALAR_EVENT:
        h_ev_str(e->data.scalar.anchor);
        h_ev_str(e->data.scalar.tag);
        if (e->data.scalar.value)
            h_ev_bytes(e->data.scalar.value, e->data.scalar.length);
        else h_ev_int(0);
        h_ev_int((long long)e->data.scalar.length);
        h_ev_int(e->data.scalar.plain_implicit);
        h_ev_int(e->data.scalar.quoted_implicit);
        h_ev_int((long long)e->data.scalar.style);
        break;
    case YAML_SEQUENCE_START_EVENT:
        h_ev_str(e->data.sequence_start.anchor);
        h_ev_str(e->data.sequence_start.tag);
        h_ev_int(e->data.sequence_start.implicit);
        h_ev_int((long long)e->data.sequence_start.style);
        break;
    case YAML_SEQUENCE_END_EVENT:
        break;
    case YAML_MAPPING_START_EVENT:
        h_ev_str(e->data.mapping_start.anchor);
        h_ev_str(e->data.mapping_start.tag);
        h_ev_int(e->data.mapping_start.implicit);
        h_ev_int((long long)e->data.mapping_start.style);
        break;
    case YAML_MAPPING_END_EVENT:
        break;
    default:
        break;
    }
}
int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "oqed_input.yaml";
    int iters = argc > 2 ? atoi(argv[2]) : 8;
    if (iters <= 0) iters = 8;
    FILE *f = fopen(path, "rb");
    if (!f) { perror("fopen"); return 1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (len <= 0) { fprintf(stderr, "empty input\n"); return 1; }
    unsigned char *buf = (unsigned char *)malloc((size_t)len);
    if (!buf) return 1;
    if (fread(buf, 1, (size_t)len, f) != (size_t)len) { perror("fread"); return 1; }
    fclose(f);
    ev_hash = 14695981039346656037ULL;
    out_hash = 14695981039346656037ULL;
    for (int it = 0; it < iters; it++) {
        yaml_parser_t parser;
        yaml_emitter_t emitter;
        if (!yaml_parser_initialize(&parser)) { fprintf(stderr, "parser init\n"); return 1; }
        yaml_parser_set_input_string(&parser, buf, (size_t)len);
        if (!yaml_emitter_initialize(&emitter)) { fprintf(stderr, "emitter init\n"); return 1; }
        yaml_emitter_set_output(&emitter, write_handler, NULL);
        yaml_emitter_set_encoding(&emitter, YAML_UTF8_ENCODING);
        int done = 0;
        while (!done) {
            yaml_event_t event;
            if (!yaml_parser_parse(&parser, &event)) {
                fprintf(stderr, "parse error %d\n", parser.error);
                return 1;
            }
            hash_event(&event);
            if (event.type == YAML_STREAM_END_EVENT) done = 1;
            if (!yaml_emitter_emit(&emitter, &event)) {
                fprintf(stderr, "emit error\n");
                return 1;
            }
        }
        yaml_parser_delete(&parser);
        yaml_emitter_delete(&emitter);
    }
    free(buf);
    printf("events %016llx emitted %016llx\n", (unsigned long long)ev_hash, (unsigned long long)out_hash);
    return 0;
}
