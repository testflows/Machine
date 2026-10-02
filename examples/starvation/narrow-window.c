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
 * Short starvation in a narrow window, from Robert O'Callahan's "Deeper
 * Into Chaos", 2016.
 *
 * The producer raises `ready` for a few busy-loop iterations, then lowers
 * it for a long gap. The consumer must observe ready == 1 at least once.
 * The test fails if the consumer is starved through every window.
 *
 * Long starvation intervals miss a bug that needs only a short delay
 * inside a small window. Finding it takes frequent short ones: hold the
 * consumer often, briefly.
 *
 * Run it with 2 vCPUs, so the consumer spins while the producer opens
 * windows. With one, the producer finishes every window before the
 * consumer is scheduled.
 *
 * A plan that finds it, in the syntax described at
 * https://testflows.com/docs/machine/steering-programs.md. CONSUMER and
 * PRODUCER stand for the thread ids the program prints:
 *
 *   when tid=CONSUMER:on {task-sched hold[1],keep[4]  task-int preempt}
 *   when tid=PRODUCER:on {task-sched drain}
 *
 * until console~FAILURE
 */

#define _GNU_SOURCE

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define DEFAULT_CYCLES 2000
#define DEFAULT_WINDOW 50
#define DEFAULT_GAP 200000

static volatile int ready;
static volatile int seen;
static volatile int done;
static volatile int consumer_ready;
static volatile unsigned long dummy;

static void *producer(void *arg)
{
	int cycles = *(int *)arg;
	int i;
	int j;

	printf("producer tid %d\n", (int)gettid());
	fflush(stdout);
	while (!consumer_ready)
		;
	for (i = 0; i < cycles; i++) {
		ready = 1;
		for (j = 0; j < DEFAULT_WINDOW; j++)
			dummy += (unsigned long)j;
		ready = 0;
		for (j = 0; j < DEFAULT_GAP; j++)
			dummy += (unsigned long)j;
	}
	done = 1;
	return NULL;
}

static void *consumer(void *arg)
{
	(void)arg;
	printf("consumer tid %d\n", (int)gettid());
	fflush(stdout);
	consumer_ready = 1;
	while (!done) {
		if (ready)
			seen = 1;
	}
	if (ready)
		seen = 1;
	return NULL;
}

int main(int argc, char **argv)
{
	pthread_t prod;
	pthread_t cons;
	int cycles = DEFAULT_CYCLES;

	if (argc > 1)
		cycles = atoi(argv[1]);
	if (cycles <= 0) {
		fprintf(stderr, "Usage: %s [cycles]\n", argv[0]);
		return 1;
	}

	printf("=== Narrow window test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d windows of %d busy loops, gap %d\n",
	       cycles, DEFAULT_WINDOW, DEFAULT_GAP);
	printf("FAILURE if the consumer misses every window\n");
	fflush(stdout);

	if (pthread_create(&cons, NULL, consumer, NULL) != 0) {
		perror("pthread_create");
		return 1;
	}
	if (pthread_create(&prod, NULL, producer, &cycles) != 0) {
		perror("pthread_create");
		return 1;
	}

	printf("Ready\n");
	fflush(stdout);

	if (pthread_join(prod, NULL) != 0 || pthread_join(cons, NULL) != 0) {
		perror("pthread_join");
		return 1;
	}

	if (!seen) {
		printf("FAILURE: consumer missed every ready window (dummy %lu)\n",
		       dummy);
		fflush(stdout);
		return 1;
	}

	printf("SUCCESS: consumer observed a ready window (dummy %lu)\n", dummy);
	fflush(stdout);
	return 0;
}
