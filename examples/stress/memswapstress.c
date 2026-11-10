/*
 * Copyright 2026 Katteli Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * Memory and swap stress test: memstress.c one layer down, through swap.
 *
 * Allocates more memory than the VM has RAM, so the kernel has to swap:
 *   - Creates a swap file on the root filesystem
 *   - Allocates 150% of RAM across several threads
 *   - Exercises swap-out: kswapd reclaims and writes to swap
 *   - Exercises swap-in: a page fault reads back from swap
 *   - Exercises the swap cache: re-reads a recently swapped page
 *   - madvise patterns that interact with swap
 *
 * Swap is disk I/O as well as memory management, so a replay has to
 * reproduce both for the run to match its recording.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <sys/swap.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sched.h>

#define PAGE_SIZE       4096
#define MB              (1024UL * 1024)

#define DEFAULT_MEM_PERCENT     150 /* use 150% of RAM — forces swap */
#define DEFAULT_NUM_THREADS     4
#define DEFAULT_ITERATIONS      5
#define SWAP_FILE_PATH          "/swapfile"
#define SWAP_SIZE_MB            128

static unsigned long total_mb;
static int num_threads;
static int iterations;

static unsigned long detect_total_mb(void)
{
    FILE *f;
    char line[256];
    unsigned long mem_kb = 0;

    f = fopen("/proc/meminfo", "r");
    if (!f)
        return 256;

    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "MemTotal: %lu kB", &mem_kb) == 1)
            break;
    }
    fclose(f);

    return mem_kb / 1024;
}

/*
 * Write a minimal swap header (linux/swap.h format).
 * This replaces mkswap — no external binary needed.
 */
#define SWAP_MAGIC      "SWAPSPACE2"
#define SWAP_MAGIC_LEN  10
#define SWAP_HDR_SIZE   PAGE_SIZE

struct swap_header {
    char            bootbits[1024];
    unsigned int    version;
    unsigned int    last_page;
    unsigned int    nr_badpages;
    unsigned char   sws_uuid[16];
    unsigned char   sws_volume[16];
    unsigned int    padding[117];
    unsigned int    badpages[1];
};

static int format_swap(int fd, size_t size)
{
    char page[PAGE_SIZE];
    struct swap_header *hdr = (struct swap_header *)page;
    unsigned int last_page = (unsigned int)(size / PAGE_SIZE) - 1;

    memset(page, 0, PAGE_SIZE);
    hdr->version = 1;
    hdr->last_page = last_page;
    hdr->nr_badpages = 0;

    /* Magic at end of first page */
    memcpy(page + PAGE_SIZE - SWAP_MAGIC_LEN, SWAP_MAGIC, SWAP_MAGIC_LEN);

    if (pwrite(fd, page, PAGE_SIZE, 0) != PAGE_SIZE)
        return -1;

    return 0;
}

/*
 * Set up swap file on root filesystem.
 * Returns 0 on success, -1 on failure.
 */
static int setup_swap(void)
{
    int fd;
    size_t swap_size = SWAP_SIZE_MB * MB;

    printf("Setting up %d MB swap file at %s\n", SWAP_SIZE_MB, SWAP_FILE_PATH);

    fd = open(SWAP_FILE_PATH, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        perror("open swap file");
        return -1;
    }

    /* Allocate space — must not create holes (swapon rejects sparse files) */
    if (fallocate(fd, 0, 0, swap_size) < 0) {
        /* Fallback: write zeros to allocate blocks */
        char zeros[PAGE_SIZE];
        memset(zeros, 0, PAGE_SIZE);
        for (size_t i = 0; i < swap_size; i += PAGE_SIZE) {
            if (write(fd, zeros, PAGE_SIZE) != PAGE_SIZE) {
                perror("write swap file");
                close(fd);
                return -1;
            }
        }
    }

    /* Write swap header (replaces mkswap) */
    if (format_swap(fd, swap_size) < 0) {
        perror("format swap header");
        close(fd);
        return -1;
    }

    close(fd);

    /* Enable swap */
    if (swapon(SWAP_FILE_PATH, 0) < 0) {
        perror("swapon");
        return -1;
    }

    printf("Swap enabled: %d MB\n\n", SWAP_SIZE_MB);
    return 0;
}

static void teardown_swap(void)
{
    swapoff(SWAP_FILE_PATH);
    unlink(SWAP_FILE_PATH);
}

/*
 * Phase 1: Allocate and touch — forces pages into memory, eventually
 * pushing older pages to swap when total exceeds RAM.
 */
static void phase_alloc_touch(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) {
        printf("  thread %d: mmap(%zu MB) failed: %s\n",
               tid, size / MB, strerror(errno));
        return;
    }

    /* Touch every page — will force swap-out of cold pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i ^ tid);

    printf("  thread %d: alloc+touch %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 2: Working set churn — allocate, touch, then re-read earlier
 * pages. Forces swap-in of pages that were swapped out.
 */
static void phase_swap_churn(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch all pages — will swap out earlier pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i ^ tid ^ 0xAB);

    /* Now read back from the beginning — forces swap-in */
    volatile char sink = 0;
    for (i = 0; i < size; i += PAGE_SIZE)
        sink += mem[i];

    /* Write again — forces swap-out of pages swapped back in */
    for (i = 0; i < size; i += PAGE_SIZE * 2)
        mem[i] = (char)(i ^ tid ^ 0xCD);

    printf("  thread %d: swap churn %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 3: madvise with swap — MADV_FREE pages that may be in swap.
 */
static void phase_madvise_swap(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch all pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i + tid);

    /* MADV_FREE first half — may already be partially swapped */
    madvise((void *)mem, size / 2, MADV_FREE);

    /* Touch second half again to trigger more swap */
    for (i = size / 2; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i ^ 0xEE);

    /* MADV_COLD second half */
    madvise((void *)(mem + size / 2), size / 2, MADV_COLD);

    /* Read everything back — swap-in for freed/cold pages */
    volatile char sink = 0;
    for (i = 0; i < size; i += PAGE_SIZE)
        sink += mem[i];

    printf("  thread %d: madvise+swap %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 4: COW with swap pressure — fork while memory is overcommitted.
 */
static void phase_cow_swap(int tid, size_t size)
{
    volatile char *mem;
    pid_t pid;
    size_t i;

    if (tid != 0)
        return;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch all pages — some will be swapped */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)i;

    pid = fork();
    if (pid == 0) {
        /* Child: write to every 4th page — COW + swap-in */
        for (i = 0; i < size; i += PAGE_SIZE * 4)
            mem[i] = (char)(i ^ 0xFF);
        _exit(0);
    } else if (pid > 0) {
        int status;
        waitpid(pid, &status, 0);
        printf("  thread %d: COW+swap %zu MB done (child=%d)\n",
               tid, size / MB, WEXITSTATUS(status));
    }

    munmap((void *)mem, size);
}

struct thread_arg {
    int tid;
    size_t per_thread_size;
    int iteration;
};

static void *worker(void *arg)
{
    struct thread_arg *ta = arg;
    int tid = ta->tid;
    size_t sz = ta->per_thread_size;
    int cpu = sched_getcpu();

    printf("  thread %d on vCPU %d (iter %d, %zu MB)\n",
           tid, cpu, ta->iteration, sz / MB);

    phase_alloc_touch(tid, sz);
    phase_swap_churn(tid, sz);
    phase_madvise_swap(tid, sz / 2);
    phase_cow_swap(tid, sz / 4);

    return NULL;
}

static void usage(const char *prog)
{
    unsigned long total = detect_total_mb();

    fprintf(stderr,
        "Usage: %s [total_mb [threads [iterations]]]\n"
        "  total_mb:   memory to stress in MB (default: %d%% of RAM)\n"
        "  threads:    worker threads (default: %d)\n"
        "  iterations: repeat count (default: %d)\n"
        "\n"
        "  Detected: %lu MB total RAM → default %lu MB (>RAM, forces swap)\n",
        prog, DEFAULT_MEM_PERCENT, DEFAULT_NUM_THREADS, DEFAULT_ITERATIONS,
        total, total * DEFAULT_MEM_PERCENT / 100);
}

int main(int argc, char *argv[])
{
    num_threads = DEFAULT_NUM_THREADS;
    iterations = DEFAULT_ITERATIONS;

    /* Auto-detect: use 150% of total RAM to force swap */
    total_mb = detect_total_mb() * DEFAULT_MEM_PERCENT / 100;
    if (total_mb == 0)
        total_mb = 128;

    if (argc > 1) {
        if (strcmp(argv[1], "-h") == 0 || strcmp(argv[1], "--help") == 0) {
            usage(argv[0]);
            return 0;
        }
        total_mb = strtoul(argv[1], NULL, 10);
    }
    if (argc > 2)
        num_threads = atoi(argv[2]);
    if (argc > 3)
        iterations = atoi(argv[3]);

    if (total_mb == 0 || num_threads <= 0 || iterations <= 0) {
        usage(argv[0]);
        return 1;
    }

    printf("=== Memory + Swap Stress Test ===\n");
    printf("RAM: %lu MB, Target: %lu MB (%d%% of RAM)\n",
           detect_total_mb(), total_mb, DEFAULT_MEM_PERCENT);
    printf("Threads: %d, Iterations: %d\n\n", num_threads, iterations);

    /* Set up swap file */
    if (setup_swap() < 0) {
        printf("FAILURE: could not set up swap\n");
        return 1;
    }

    size_t per_thread = (total_mb * MB) / num_threads;

    for (int iter = 0; iter < iterations; iter++) {
        printf("--- Iteration %d/%d ---\n", iter + 1, iterations);

        pthread_t *threads = calloc(num_threads, sizeof(pthread_t));
        struct thread_arg *args = calloc(num_threads, sizeof(struct thread_arg));

        if (!threads || !args) {
            perror("calloc");
            free(threads);
            free(args);
            teardown_swap();
            return 1;
        }

        int created = 0;
        for (int t = 0; t < num_threads; t++) {
            args[t].tid = t;
            args[t].per_thread_size = per_thread;
            args[t].iteration = iter;
            if (pthread_create(&threads[t], NULL, worker, &args[t]) != 0) {
                perror("pthread_create");
                break;
            }
            created++;
        }

        for (int t = 0; t < created; t++)
            pthread_join(threads[t], NULL);

        free(threads);
        free(args);

        if (created < num_threads) {
            printf("FAILURE: only %d/%d threads created\n",
                   created, num_threads);
            teardown_swap();
            return 1;
        }

        printf("--- Iteration %d complete ---\n\n", iter + 1);
    }

    teardown_swap();

    printf("=== Memory + Swap Stress Test PASSED ===\n");
    return 0;
}
