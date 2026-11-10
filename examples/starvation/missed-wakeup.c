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
 * Missed wakeup: check, then wait, without a loop.
 *
 * The waiter takes the lock, sees done == 0, and calls timedwait. If it
 * is held between that check and the wait, the signaler sets done and
 * signals into nobody, then the waiter sleeps through the signal. The
 * timedwait expires with done already 1: a missed wakeup, not a hang.
 *
 * The window is between the load of done and the wait. A plan that finds
 * it parks the waiter there by stepping it, and runs the signaler
 * through; the syntax is described at
 * https://testflows.com/docs/machine/steering-programs.md:
 *
 *   when /examples/missed-wakeup:off {task-sched drain[1],keep[4]}
 *   when /examples/missed-wakeup:on  {mode step[1],fast[9] step=1,2,3
 *                   task-sched hold[1],keep[9]  task-int preempt[3],none[7]}
 *
 * until console~FAILURE
 */

#define _GNU_SOURCE

#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_TRIALS 2000
#define WAIT_MS 50

static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cond;
static volatile int done;
static volatile int waiter_ready;
static volatile int stop;
static volatile int printed;

static void *waiter(void *arg)
{
	struct timespec ts;
	int rc;

	(void)arg;
	if (!printed) {
		printf("waiter tid %d\n", (int)gettid());
		fflush(stdout);
	}
	pthread_mutex_lock(&mutex);
	waiter_ready = 1;
	if (!done) {
		/*
		 * Drop the lock, then wait without rechecking. The window
		 * is this unlock: a hold here lets the signaler finish
		 * before the wait is armed.
		 */
		pthread_mutex_unlock(&mutex);
		pthread_mutex_lock(&mutex);
		if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
			pthread_mutex_unlock(&mutex);
			return (void *)(intptr_t)-1;
		}
		ts.tv_nsec += (long)WAIT_MS * 1000000L;
		if (ts.tv_nsec >= 1000000000L) {
			ts.tv_sec += 1;
			ts.tv_nsec -= 1000000000L;
		}
		rc = pthread_cond_timedwait(&cond, &mutex, &ts);
		if (rc == ETIMEDOUT && done) {
			pthread_mutex_unlock(&mutex);
			return (void *)(intptr_t)1;
		}
	}
	pthread_mutex_unlock(&mutex);
	return (void *)(intptr_t)0;
}

static void *signaler(void *arg)
{
	(void)arg;
	if (!printed) {
		printf("signaler tid %d\n", (int)gettid());
		fflush(stdout);
		printed = 1;
	}
	while (!waiter_ready && !stop)
		;
	pthread_mutex_lock(&mutex);
	done = 1;
	pthread_cond_signal(&cond);
	pthread_mutex_unlock(&mutex);
	return NULL;
}

int main(int argc, char **argv)
{
	pthread_t w;
	pthread_t s;
	int trials = DEFAULT_TRIALS;
	int trial;
	void *wstatus;

	pthread_condattr_t attr;

	if (argc > 1)
		trials = atoi(argv[1]);
	if (trials <= 0) {
		fprintf(stderr, "Usage: %s [trials]\n", argv[0]);
		return 1;
	}

	pthread_condattr_init(&attr);
	pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
	pthread_cond_init(&cond, &attr);
	pthread_condattr_destroy(&attr);

	printf("=== Missed wakeup test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d trials, %d ms wait\n", trials, WAIT_MS);
	printf("FAILURE if timedwait expires with done already set\n");
	fflush(stdout);
	printf("Ready\n");
	fflush(stdout);

	for (trial = 0; trial < trials; trial++) {
		done = 0;
		waiter_ready = 0;
		stop = 0;
		if (pthread_create(&w, NULL, waiter, NULL) != 0) {
			perror("pthread_create");
			return 1;
		}
		if (pthread_create(&s, NULL, signaler, NULL) != 0) {
			perror("pthread_create");
			return 1;
		}
		if (pthread_join(s, NULL) != 0 || pthread_join(w, &wstatus) != 0) {
			perror("pthread_join");
			return 1;
		}
		if ((intptr_t)wstatus == 1) {
			printf("FAILURE: missed wakeup (trial %d)\n", trial);
			fflush(stdout);
			return 1;
		}
		if ((intptr_t)wstatus == -1) {
			perror("clock_gettime");
			return 1;
		}
	}

	printf("SUCCESS: every wait saw the signal\n");
	fflush(stdout);
	return 0;
}
