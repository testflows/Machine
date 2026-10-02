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
 * Mutex wakeup starvation.
 *
 * A waiter blocks on a locked mutex. Main unlocks, then waits a timeout.
 * The waiter must set the flag before the timeout. The test fails if the
 * waiter is starved after the unlock.
 *
 * Same shape as pipe-wakeup.c, with a futex instead of a pipe: both
 * threads are runnable after the unlock.
 *
 * Modeled on futex_wakeup in rr's chaos tests,
 * https://github.com/rr-debugger/rr/tree/master/src/chaos-test.
 *
 * A plan that finds it: the one in pipe-wakeup.c, holding the waiter
 * instead of the reader.
 *
 * Unperturbed: SUCCESS. The waiter runs during the timeout.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_TRIALS 200
#define DEFAULT_TIMEOUT_MS 20

static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static volatile int flag;
static volatile int stop;
static volatile int worker_ready;

static void *waiter(void *arg)
{
	(void)arg;
	printf("waiter tid %d\n", (int)gettid());
	fflush(stdout);
	worker_ready = 1;
	while (!stop) {
		pthread_mutex_lock(&mutex);
		flag = 1;
		pthread_mutex_unlock(&mutex);
		while (flag && !stop)
			;
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

	printf("=== Futex wakeup test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d trials, %d ms timeout\n", trials, timeout_ms);
	printf("FAILURE if the waiter is starved after unlock\n");
	fflush(stdout);

	pthread_mutex_lock(&mutex);
	if (pthread_create(&thread, NULL, waiter, NULL) != 0) {
		perror("pthread_create");
		return 1;
	}
	while (!worker_ready)
		;

	printf("Ready\n");
	fflush(stdout);

	for (trial = 0; trial < trials; trial++) {
		flag = 0;
		pthread_mutex_unlock(&mutex);
		if (sleep_ms(timeout_ms) != 0) {
			perror("nanosleep");
			stop = 1;
			pthread_join(thread, NULL);
			return 1;
		}
		if (!flag) {
			printf("FAILURE: waiter starved after unlock (trial %d)\n",
			       trial);
			fflush(stdout);
			stop = 1;
			pthread_join(thread, NULL);
			return 1;
		}
		pthread_mutex_lock(&mutex);
	}

	stop = 1;
	pthread_mutex_unlock(&mutex);
	if (pthread_join(thread, NULL) != 0) {
		perror("pthread_join");
		return 1;
	}

	printf("SUCCESS: waiter ran after every unlock\n");
	fflush(stdout);
	return 0;
}
