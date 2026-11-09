/* SPDX-License-Identifier: GPL-2.0 */
#ifndef C5_FLASH_ABI_H
#define C5_FLASH_ABI_H
#define C5_SERVICE_BASE 0x40808000u
#define C5_REQUEST_BASE 0x4080a000u
#define C5_SERVICE_BYTES 0x6000u
#define C5_DATA_BASE 0x400000u
#define C5_DATA_BYTES 0x400000u
#define C5_WRITE_KEY 0xc5f1a501u
#define C5_FLASH_ID 0x174020u
#define C5_OP_ID 1u
#define C5_OP_READ 2u
#define C5_OP_STATUS 3u
#define C5_OP_PROGRAM 4u
#define C5_OP_ERASE 5u
struct c5_request {
    unsigned int op, offset, length, key;
    int result;
    unsigned int data[16];
};
static inline int c5_range_valid(unsigned int offset, unsigned int length)
{
    return length && offset >= C5_DATA_BASE && offset < C5_DATA_BASE+C5_DATA_BYTES &&
           length <= C5_DATA_BASE+C5_DATA_BYTES-offset;
}
static inline int c5_request_valid(const struct c5_request *r)
{
    switch (r->op) {
    case C5_OP_ID: case C5_OP_STATUS:
        return r->length==0 && r->offset==0;
    case C5_OP_READ:
        return r->length<=64 && c5_range_valid(r->offset,r->length);
    case C5_OP_PROGRAM:
        return r->key==C5_WRITE_KEY && r->length<=64 &&
               c5_range_valid(r->offset,r->length) &&
               r->length<=256-(r->offset&255);
    case C5_OP_ERASE:
        return r->key==C5_WRITE_KEY && r->length==4096 &&
               !(r->offset&4095) && c5_range_valid(r->offset,4096);
    default: return 0;
    }
}
#endif
