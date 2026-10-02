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
 * Test program to demonstrate thread data race bug
 *
 * This program creates multiple threads that increment a shared counter
 * without proper synchronization, causing a data race that leads to
 * incorrect results.
 */

#define _GNU_SOURCE  /* Required for sched_getcpu() */

#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <unistd.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <sched.h>

#define INCREMENTS_PER_THREAD 100000
#define DEFAULT_NUM_THREADS 4
#define MAX_THREADS 1024

/* Shared counter - this will be accessed by multiple threads without locking */
static volatile uint64_t counter = 0;

/* Thread function that increments the counter */
void *increment_counter(void *arg) {
    int thread_id = *(int *)arg;
    int cpu_id;
    int prev_cpu = -1;
    int cpu_changes = 0;

    cpu_id = sched_getcpu();
    printf("Thread %d: Starting increments on vCPU %d\n", thread_id, cpu_id);
    prev_cpu = cpu_id;

    for (int i = 0; i < INCREMENTS_PER_THREAD; i++) {
        /* Data race: multiple threads read-modify-write without synchronization */
        counter++;

        /* Check if we migrated to a different CPU */
        cpu_id = sched_getcpu();
        if (cpu_id != prev_cpu) {
            cpu_changes++;
            if (cpu_changes <= 5) {  /* Limit output to first 5 migrations */
                printf("Thread %d: Migrated from vCPU %d to vCPU %d (at increment %d)\n",
                       thread_id, prev_cpu, cpu_id, i);
            }
            prev_cpu = cpu_id;
        }
    }

    if (cpu_changes > 5) {
        printf("Thread %d: (and %d more CPU migrations...)\n",
               thread_id, cpu_changes - 5);
    }

    cpu_id = sched_getcpu();
    printf("Thread %d: Finished increments on vCPU %d (total migrations: %d)\n",
           thread_id, cpu_id, cpu_changes);
    return NULL;
}

static void usage(const char *prog_name) {
    fprintf(stderr, "Usage: %s [num_threads]\n", prog_name);
    fprintf(stderr, "  num_threads: Number of threads to create (default: %d, max: %d)\n",
            DEFAULT_NUM_THREADS, MAX_THREADS);
    fprintf(stderr, "\nExample: %s 8\n", prog_name);
}

int main(int argc, char *argv[]) {
    int num_threads = DEFAULT_NUM_THREADS;
    pthread_t *threads;
    int *thread_ids;
    uint64_t expected_value;
    char *endptr;
    long parsed_threads;

    /* Parse command line argument */
    if (argc > 1) {
        if (strcmp(argv[1], "-h") == 0 || strcmp(argv[1], "--help") == 0) {
            usage(argv[0]);
            return 0;
        }

        errno = 0;
        parsed_threads = strtol(argv[1], &endptr, 10);

        if (errno != 0 || *endptr != '\0' || parsed_threads <= 0) {
            fprintf(stderr, "Error: Invalid number of threads: %s\n", argv[1]);
            usage(argv[0]);
            return 1;
        }

        if (parsed_threads > MAX_THREADS) {
            fprintf(stderr, "Error: Number of threads (%ld) exceeds maximum (%d)\n",
                    parsed_threads, MAX_THREADS);
            return 1;
        }

        num_threads = (int)parsed_threads;
    }

    /* Allocate arrays for threads */
    threads = malloc(num_threads * sizeof(pthread_t));
    thread_ids = malloc(num_threads * sizeof(int));

    if (!threads || !thread_ids) {
        perror("malloc");
        free(threads);
        free(thread_ids);
        return 1;
    }

    printf("=== Thread Data Race Test ===\n");
    printf("Creating %d threads, each incrementing %d times\n",
           num_threads, INCREMENTS_PER_THREAD);
    printf("Expected final value: %lu\n",
           (unsigned long)((uint64_t)num_threads * INCREMENTS_PER_THREAD));
    printf("Main thread running on vCPU %d\n", sched_getcpu());
    printf("\n");

    /* Create threads */
    for (int i = 0; i < num_threads; i++) {
        thread_ids[i] = i;
        if (pthread_create(&threads[i], NULL, increment_counter, &thread_ids[i]) != 0) {
            perror("pthread_create");
            free(threads);
            free(thread_ids);
            return 1;
        }
    }

    /* Wait for all threads to complete */
    for (int i = 0; i < num_threads; i++) {
        if (pthread_join(threads[i], NULL) != 0) {
            perror("pthread_join");
            free(threads);
            free(thread_ids);
            return 1;
        }
    }

    expected_value = (uint64_t)num_threads * INCREMENTS_PER_THREAD;

    /* Clean up */
    free(threads);
    free(thread_ids);

    printf("\n=== Results ===\n");
    printf("Expected counter value: %lu\n", (unsigned long)expected_value);
    printf("Actual counter value:   %lu\n", (unsigned long)counter);

    if (counter == expected_value) {
        printf("SUCCESS: Counter matches expected value (race condition may not have occurred)\n");
        return 0;
    } else {
        printf("FAILURE: Counter does not match expected value!\n");
        printf("         This demonstrates the data race bug.\n");
        printf("         Lost increments: %ld\n",
               (long)(expected_value - counter));
        return 1;
    }
}

