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
 * Timeout starvation.
 *
 * The shape of the Firefox ImageBridge bug in Robert O'Callahan's "rr
 * Chaos Mode", 2016.
 *
 * Main arms a timeout and waits. The worker is the only runnable thread
 * during the wait and must publish before the timeout fires. The test
 * fails if the worker is starved for the whole wait: the timeout expires
 * with published == 0.
 *
 * A scheduler that only picks among currently-runnable threads cannot
 * find this. While main sleeps the worker is the only runnable thread, so
 * any pick-next policy runs it and the test passes. Finding it needs
 * refusing to run the worker even when it is the only runnable thread,
 * which is what `task-sched hold` does.
 *
 * A plan that finds it, in the syntax described at
 * https://testflows.com/docs/machine/steering-programs.md. The program
 * prints its thread ids before Ready; 56 and 57 stand for them here:
 *
 *   when tid=57:on {task-sched hold  task-int preempt}
 *   when tid=56:on {task-sched drain}
 *
 * until console~FAILURE
 *
 * Unperturbed: SUCCESS. The worker runs during every wait.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_TRIALS 200
#define DEFAULT_TIMEOUT_MS 20

static volatile int stop;
static volatile int go;
static volatile int published;
static volatile int worker_ready;

static void *bridge(void *arg)
{
	(void)arg;
	printf("bridge tid %d\n", (int)gettid());
	fflush(stdout);
	worker_ready = 1;
	while (!stop) {
		if (go)
			published = 1;
	}
	return NULL;
}

static int sleep_ms(int ms)
{
	struct timespec ts;

	ts.tv_sec = ms / 1000;
	ts.tv_nsec = (long)(ms % 1000) * 1000000L;
	while (nanosleep(&ts, &ts) != 0) {
		if (errno != EINTR)
			return -1;
	}
	return 0;
}

int main(int argc, char **argv)
{
	pthread_t thread;
	int trials = DEFAULT_TRIALS;
	int timeout_ms = DEFAULT_TIMEOUT_MS;
	int trial;

	if (argc > 1)
		trials = atoi(argv[1]);
	if (argc > 2)
		timeout_ms = atoi(argv[2]);
	if (trials <= 0 || timeout_ms <= 0) {
		fprintf(stderr, "Usage: %s [trials] [timeout_ms]\n", argv[0]);
		return 1;
	}

	printf("=== Timeout starvation test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d trials, %d ms timeout\n", trials, timeout_ms);
	printf("FAILURE if the worker is starved for a whole timeout\n");
	fflush(stdout);

	if (pthread_create(&thread, NULL, bridge, NULL) != 0) {
		perror("pthread_create");
		return 1;
	}
	while (!worker_ready)
		;

	printf("Ready\n");
	fflush(stdout);

	for (trial = 0; trial < trials; trial++) {
		published = 0;
		go = 1;
		if (sleep_ms(timeout_ms) != 0) {
			perror("nanosleep");
			stop = 1;
			pthread_join(thread, NULL);
			return 1;
		}
		go = 0;
		if (!published) {
			printf("FAILURE: worker starved for the whole %d ms timeout (trial %d)\n",
			       timeout_ms, trial);
			fflush(stdout);
			stop = 1;
			pthread_join(thread, NULL);
			return 1;
		}
	}

	stop = 1;
	if (pthread_join(thread, NULL) != 0) {
		perror("pthread_join");
		return 1;
	}

	printf("SUCCESS: worker ran during every timeout\n");
	fflush(stdout);
	return 0;
}
