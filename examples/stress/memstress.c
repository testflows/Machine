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
 * Memory stress test: a workload for checking that a replay matches its
 * recording under heavy memory management.
 *
 * Exercises the kernel's memory management paths:
 *   - Page reclaim under memory pressure
 *   - madvise(MADV_FREE): lazily freed pages
 *   - madvise(MADV_COLD): page deactivation
 *   - Page faults: mmap, touch and munmap cycles
 *   - Copy-on-write: fork, then write in the child
 *   - All of it from several threads at once, across vCPUs
 *
 * Allocates close to the VM's memory limit so that kswapd wakes and the
 * kernel has to decide which pages to reclaim.
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
#include <sched.h>

#define PAGE_SIZE       4096
#define MB              (1024UL * 1024)

#define DEFAULT_MEM_PERCENT     80  /* use 80% of available RAM */
#define DEFAULT_NUM_THREADS     4
#define DEFAULT_ITERATIONS      10

static unsigned long total_mb;
static int num_threads;
static int iterations;

static unsigned long detect_available_mb(void)
{
    FILE *f;
    char line[256];
    unsigned long mem_kb = 0;

    f = fopen("/proc/meminfo", "r");
    if (!f)
        return 256; /* fallback */

    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "MemAvailable: %lu kB", &mem_kb) == 1)
            break;
        /* Fallback to MemTotal if MemAvailable not present */
        if (mem_kb == 0)
            sscanf(line, "MemTotal: %lu kB", &mem_kb);
    }
    fclose(f);

    return mem_kb / 1024;
}

/*
 * Phase 1: Allocate and touch pages to fill memory.
 * Forces page faults and page table creation.
 */
static void phase_alloc_touch(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) {
        printf("  thread %d: mmap(%zu MB) failed: %s\n",
               tid, size / MB, strerror(errno));
        return;
    }

    /* Touch every page to force page faults */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i ^ tid);

    /* Read every page back, so each one has been accessed */
    volatile char sink = 0;
    for (i = 0; i < size; i += PAGE_SIZE)
        sink += mem[i];

    printf("  thread %d: alloc+touch %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 2: madvise(MADV_FREE) — mark pages as lazyfree.
 * Re-touching some of them afterwards cancels the free for those pages.
 */
static void phase_madvise_free(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i + tid);

    /* Mark as free: the kernel may reclaim them without writing them out */
    if (madvise((void *)mem, size, MADV_FREE) < 0)
        printf("  thread %d: MADV_FREE failed: %s\n", tid, strerror(errno));

    /* Re-touch some pages (reclaims lazyfree, re-faults) */
    for (i = 0; i < size; i += PAGE_SIZE * 4)
        mem[i] = (char)(i ^ 0xAA);

    printf("  thread %d: MADV_FREE %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 3: madvise(MADV_COLD) — deactivate pages.
 * Reading them back afterwards makes them active again.
 */
static void phase_madvise_cold(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch all pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)(i + tid + 0x55);

    /* Mark cold: the kernel moves them to the inactive list */
    if (madvise((void *)mem, size, MADV_COLD) < 0)
        printf("  thread %d: MADV_COLD failed: %s\n", tid, strerror(errno));

    /* Touch again to re-activate */
    volatile char sink = 0;
    for (i = 0; i < size; i += PAGE_SIZE)
        sink += mem[i];

    printf("  thread %d: MADV_COLD %zu MB done\n", tid, size / MB);

    munmap((void *)mem, size);
}

/*
 * Phase 4: Rapid mmap/touch/munmap cycles.
 * Exercises page table creation and teardown, and TLB flushes.
 */
static void phase_mmap_churn(int tid, size_t size)
{
    int rounds = 20;
    size_t chunk = size / rounds;

    if (chunk < PAGE_SIZE)
        chunk = PAGE_SIZE;

    for (int r = 0; r < rounds; r++) {
        volatile char *mem = mmap(NULL, chunk, PROT_READ | PROT_WRITE,
                                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (mem == MAP_FAILED)
            continue;

        /* Touch every page */
        for (size_t i = 0; i < chunk; i += PAGE_SIZE)
            mem[i] = (char)(r ^ tid ^ i);

        munmap((void *)mem, chunk);
    }

    printf("  thread %d: mmap churn %d rounds done\n", tid, rounds);
}

/*
 * Phase 5: COW via fork + write.
 * Parent and child share pages, child writes to trigger COW faults.
 * Exercises page table copying at fork and copy-on-write page allocation.
 */
static void phase_cow_fork(int tid, size_t size)
{
    volatile char *mem;
    pid_t pid;
    size_t i;

    /* One thread forks; a fork from every thread would multiply the memory */
    if (tid != 0)
        return;

    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Parent touches all pages */
    for (i = 0; i < size; i += PAGE_SIZE)
        mem[i] = (char)i;

    pid = fork();
    if (pid == 0) {
        /* Child: write to every 4th page → COW faults */
        for (i = 0; i < size; i += PAGE_SIZE * 4)
            mem[i] = (char)(i ^ 0xFF);
        _exit(0);
    } else if (pid > 0) {
        int status;
        waitpid(pid, &status, 0);
        printf("  thread %d: COW fork %zu MB done (child status=%d)\n",
               tid, size / MB, WEXITSTATUS(status));
    }

    munmap((void *)mem, size);
}

/*
 * Phase 6: Memory pressure — allocate more than available.
 * Forces kswapd to wake up and scan the LRU lists for pages to reclaim.
 */
static void phase_pressure(int tid, size_t size)
{
    volatile char *mem;
    size_t i;

    /* Allocate a large chunk — may partially fail or trigger OOM */
    mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED)
        return;

    /* Touch pages gradually — kernel reclaims file-backed pages as we go */
    for (i = 0; i < size; i += PAGE_SIZE) {
        mem[i] = (char)(i ^ tid ^ 0xDE);

        /* Every 1000 pages, read back earlier pages to create working set */
        if (i > 1000 * PAGE_SIZE && (i / PAGE_SIZE) % 1000 == 0) {
            size_t back = i - (500 * PAGE_SIZE);
            volatile char sink = 0;
            for (size_t j = back; j < back + 100 * PAGE_SIZE; j += PAGE_SIZE)
                sink += mem[j];
        }
    }

    printf("  thread %d: pressure %zu MB done\n", tid, size / MB);

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
    phase_madvise_free(tid, sz / 2);
    phase_madvise_cold(tid, sz / 2);
    phase_mmap_churn(tid, sz / 4);
    phase_cow_fork(tid, sz / 4);
    phase_pressure(tid, sz);

    return NULL;
}

static void usage(const char *prog)
{
    unsigned long avail = detect_available_mb();

    fprintf(stderr,
        "Usage: %s [total_mb [threads [iterations]]]\n"
        "  total_mb:   total memory to stress in MB (default: %d%% of available)\n"
        "  threads:    worker threads (default: %d)\n"
        "  iterations: repeat count (default: %d)\n"
        "\n"
        "  Detected: %lu MB available → default %lu MB\n",
        prog, DEFAULT_MEM_PERCENT, DEFAULT_NUM_THREADS, DEFAULT_ITERATIONS,
        avail, avail * DEFAULT_MEM_PERCENT / 100);
}

int main(int argc, char *argv[])
{
    num_threads = DEFAULT_NUM_THREADS;
    iterations = DEFAULT_ITERATIONS;

    /* Auto-detect: use 80% of available RAM */
    total_mb = detect_available_mb() * DEFAULT_MEM_PERCENT / 100;
    if (total_mb == 0)
        total_mb = 64;

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

    size_t per_thread = (total_mb * MB) / num_threads;

    printf("=== Memory Stress Test ===\n");
    printf("Total: %lu MB, Threads: %d, Iterations: %d\n",
           total_mb, num_threads, iterations);
    printf("Per thread: %zu MB\n", per_thread / MB);
    printf("\n");

    for (int iter = 0; iter < iterations; iter++) {
        printf("--- Iteration %d/%d ---\n", iter + 1, iterations);

        pthread_t *threads = calloc(num_threads, sizeof(pthread_t));
        struct thread_arg *args = calloc(num_threads, sizeof(struct thread_arg));

        if (!threads || !args) {
            perror("calloc");
            free(threads);
            free(args);
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
            return 1;
        }

        printf("--- Iteration %d complete ---\n\n", iter + 1);
    }

    printf("=== Memory Stress Test PASSED ===\n");
    return 0;
}
