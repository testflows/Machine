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
 * Lease expiration vs in-flight work: a virtual-time race.
 *
 * The worker has a fixed amount of spinning to do. Main waits on a lease
 * that is long enough for that work when virtual time runs at 1x. The
 * test fails if the lease expires before the worker publishes.
 *
 * The other starvation examples delay a thread. This one expires the
 * lease without starving anyone: vtime-add jumps the clock while the
 * worker is still running, so the armed timer fires before the work
 * commits.
 *
 * A plan that finds it, in the syntax described at
 * https://testflows.com/docs/machine/steering-programs.md:
 *
 *   when /examples/lease:on {mode fast fast=10k:100k  vtime-add 500ms[1],0[9]}
 *
 * or asymmetric rates:
 *
 *   when vcpu=1 {vtime-rate 10/1}
 *   when vcpu=0 {vtime-rate 1/10}
 *
 * until console~FAILURE
 *
 * Unperturbed: SUCCESS. The worker finishes inside the lease.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_WORK 20000000UL
#define DEFAULT_LEASE_MS 200
#define DEFAULT_TRIALS 50

static volatile int stop;
static volatile int published;
static volatile int worker_ready;
static volatile unsigned long dummy;
static unsigned long work_iters;

static void *worker(void *arg)
{
	unsigned long i;

	(void)arg;
	printf("worker tid %d\n", (int)gettid());
	fflush(stdout);
	worker_ready = 1;
	while (!stop) {
		if (!published) {
			for (i = 0; i < work_iters; i++)
				dummy += i;
			published = 1;
		}
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
	int lease_ms = DEFAULT_LEASE_MS;
	int trial;

	work_iters = DEFAULT_WORK;
	if (argc > 1)
		trials = atoi(argv[1]);
	if (argc > 2)
		lease_ms = atoi(argv[2]);
	if (argc > 3)
		work_iters = strtoul(argv[3], NULL, 10);
	if (trials <= 0 || lease_ms <= 0 || work_iters == 0) {
		fprintf(stderr, "Usage: %s [trials] [lease_ms] [work_iters]\n",
			argv[0]);
		return 1;
	}

	printf("=== Lease expiration test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d trials, %d ms lease, %lu work iters\n",
	       trials, lease_ms, work_iters);
	printf("FAILURE if the lease expires before the worker publishes\n");
	fflush(stdout);

	if (pthread_create(&thread, NULL, worker, NULL) != 0) {
		perror("pthread_create");
		return 1;
	}
	while (!worker_ready)
		;

	printf("Ready\n");
	fflush(stdout);

	for (trial = 0; trial < trials; trial++) {
		published = 0;
		if (sleep_ms(lease_ms) != 0) {
			perror("nanosleep");
			stop = 1;
			pthread_join(thread, NULL);
			return 1;
		}
		if (!published) {
			printf("FAILURE: lease expired before worker finished (trial %d, dummy %lu)\n",
			       trial, dummy);
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

	printf("SUCCESS: worker finished inside every lease (dummy %lu)\n", dummy);
	fflush(stdout);
	return 0;
}
