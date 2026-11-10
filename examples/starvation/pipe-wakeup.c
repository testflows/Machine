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
 * Pipe wakeup starvation.
 *
 * A reader blocks on a pipe. Main writes one byte, then waits a timeout.
 * The reader must set the flag before the timeout. The test fails if the
 * reader is starved after becoming runnable.
 *
 * Both threads are runnable after the write, so a scheduler that picks
 * among runnable threads can find this one by never picking the reader.
 *
 * Modeled on pipe_wakeup in rr's chaos tests,
 * https://github.com/rr-debugger/rr/tree/master/src/chaos-test.
 *
 * A plan that finds it, in the syntax described at
 * https://testflows.com/docs/machine/steering-programs.md. READER and
 * MAIN stand for the thread ids the program prints:
 *
 *   when tid=READER:on {task-sched hold  task-int preempt}
 *   when tid=MAIN:on   {task-sched drain}
 *
 * until console~FAILURE
 *
 * Unperturbed: SUCCESS. The reader runs during the timeout.
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

static int pipe_fds[2];
static volatile int flag;
static volatile int stop;
static volatile int worker_ready;

static void *reader(void *arg)
{
	char ch;

	(void)arg;
	printf("reader tid %d\n", (int)gettid());
	fflush(stdout);
	worker_ready = 1;
	while (!stop) {
		if (read(pipe_fds[0], &ch, 1) != 1)
			break;
		flag = 1;
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

	printf("=== Pipe wakeup test ===\n");
	printf("Main tid %d\n", (int)gettid());
	printf("%d trials, %d ms timeout\n", trials, timeout_ms);
	printf("FAILURE if the reader is starved after the write\n");
	fflush(stdout);

	if (pipe(pipe_fds) != 0) {
		perror("pipe");
		return 1;
	}
	if (pthread_create(&thread, NULL, reader, NULL) != 0) {
		perror("pthread_create");
		return 1;
	}
	while (!worker_ready)
		;

	printf("Ready\n");
	fflush(stdout);

	for (trial = 0; trial < trials; trial++) {
		flag = 0;
		if (write(pipe_fds[1], "x", 1) != 1) {
			perror("write");
			stop = 1;
			close(pipe_fds[1]);
			pthread_join(thread, NULL);
			return 1;
		}
		if (sleep_ms(timeout_ms) != 0) {
			perror("nanosleep");
			stop = 1;
			close(pipe_fds[1]);
			pthread_join(thread, NULL);
			return 1;
		}
		if (!flag) {
			printf("FAILURE: reader starved after write (trial %d)\n",
			       trial);
			fflush(stdout);
			stop = 1;
			close(pipe_fds[1]);
			pthread_join(thread, NULL);
			close(pipe_fds[0]);
			return 1;
		}
	}

	stop = 1;
	close(pipe_fds[1]);
	if (pthread_join(thread, NULL) != 0) {
		perror("pthread_join");
		return 1;
	}
	close(pipe_fds[0]);

	printf("SUCCESS: reader ran after every write\n");
	fflush(stdout);
	return 0;
}
