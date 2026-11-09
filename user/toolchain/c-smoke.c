/* Host-built artifact first; board execution requires a new libc checkpoint. */
#define _GNU_SOURCE 1
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
_Static_assert(sizeof(void *) == 4, "RV32 pointers required");
_Static_assert(sizeof(long) == 4, "ILP32 required");
_Static_assert(sizeof(time_t) == 8, "time64 libc required");
static volatile unsigned int seed = 0xc5;
static const char *volatile labels[] = { "C5 C LIBC", "C5 C LIBC" };
static int transform(int n) { return n * 3 + 1; }
static int (*volatile operation)(int) = transform;
int main(int argc, char **argv)
{
    struct timespec now;
    char *memory = malloc(128);
    int fd, result = 0;
    if (!memory) return 10;
    if (argc < 1 || !argv || !argv[0]) result = 19;
    memset(memory, 0xa5, 128);
    if (operation(7) != 22 || memory[127] != (char)0xa5) result = 11;
    if (snprintf(memory, 128, "%s pid=%ld argc=%d", labels[seed & 1], (long)getpid(), argc) <= 0) result = 12;
    if (!strstr(memory, "C5 C LIBC pid=")) result = 13;
    errno = 0;
    fd = open("/c5-deliberately-absent", O_RDONLY);
    if (fd >= 0) { close(fd); result = 14; }
    else if (errno != ENOENT) result = 15;
    if (clock_gettime(CLOCK_MONOTONIC, &now) || now.tv_sec < 0) result = 16;
    if (printf("%s result=%d argv0=%s\n", memory, result,
               argc > 0 && argv && argv[0] ? argv[0] : "missing") < 0) result = 17;
    if (fflush(stdout)) result = 18;
    free(memory);
    return result;
}
