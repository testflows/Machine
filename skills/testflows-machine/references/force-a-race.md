# Force a race: name the window, hold a thread in it, prove the fix

A race needs one thread at a place while another does something. On real
hardware that lines up rarely, so the failure is rare. In the machine you put
the threads there: a breakpoint stops the first thread on the line, holding
keeps it there, and the second runs to where it acts. One try shows the bug.

The example is `examples/starvation/missed-wakeup.c` in
https://github.com/testflows/Machine. Every command below was run on it.

## 1. Name the window

From the code, say which thread must be where while another does what, and
name the source line where the first one stops. In the example, the waiter
checks `done` under the lock, drops the lock, and takes it again to wait:

```c
pthread_mutex_lock(&mutex);
if (!done) {
        pthread_mutex_unlock(&mutex);       /* line 75                    */
        pthread_mutex_lock(&mutex);         /* line 76: the window is here */
        ...
        rc = pthread_cond_timedwait(&cond, &mutex, &ts);
```

The window: the waiter has seen `done == 0` and dropped the lock, while the
signaler sets `done` and signals. The signal wakes nobody, and the waiter
waits out its whole timeout with `done` already set.

## 2. Build it to reach the window once

Build with `-g -static`. The breakpoint names the file the program was
compiled from, so compile the fix from a file of the same name. Give the
program the least work that reaches the window: this one takes a number of
trials, and one is enough. Each thread the program creates is focused and
steered, so 2000 trials would take minutes of real time.

```bash
gcc -O0 -g -static -pthread -o missed-wakeup starvation/missed-wakeup.c
machine -s work disks build --binary ./missed-wakeup mw -- 1
```

## 3. Boot the program

```bash
machine -s work create mw --disk mw --cpus 2
machine -s work focus mw add comm=missed-wakeup
machine -s work run mw --until comm=missed-wakeup:exec --within 60s    # the program started
machine -s work run mw --until comm=missed-wakeup:sched --within 60s   # focus resolves on a dispatch
machine -s work tasks mw --focused                                     # main's tid
```

`machine focus` may print `Machine has not linked; nothing can be steered`
right after `focus add`. It is a status line, not a refusal: the focus is
added, and running until the program starts is the way on.

## 4. Force the window

```bash
machine -s work break mw add <main-tid> missed-wakeup.c:76   # covers every thread of the program
machine -s work run mw --iters 0 --within 60s                # the waiter stops on the line
machine -s work tasks mw --focused                           # the waiter's tid and the signaler's
machine -s work tasks mw hold <waiter-tid>                   # it stays in the window
machine -s work break mw delete 1
machine -s work run mw --until 'tid=<signaler-tid>:at=missed-wakeup.c:109' --within 60s   # it has signalled
machine -s work tasks mw release <waiter-tid>
machine -s work run mw --until 'console~(FAILURE: missed|SUCCESS:)' --within 60s
machine -s work output mw -n : | grep -E 'FAILURE:|SUCCESS:'
```

```
FAILURE: missed wakeup (trial 0)
```

`--until 'tid=N:at=LOCATION'` runs until that task reaches a function or a
`file:line`. Run the second thread to the point just after it acts: line 109
is the signaler's unlock, after its signal.

## 5. Check the signature

The failure must be the one the bug causes. Match its exact line: the
program's header also says `FAILURE if timedwait expires...`, and
`console~FAILURE` stops on the header before anything has run. A hold also
delays a thread in machine time, so a check with a time limit can fire from
the hold alone. See `pitfalls.md`.

## 6. Prove the fix

Fix the code, build it, and run the same steps on it. Lines that moved move
in the commands: the fix below adds two lines, so the signaler's unlock is
line 111. The same interleaving must now pass:

```c
        rc = 0;
        while (!done && rc != ETIMEDOUT)
                rc = pthread_cond_timedwait(&cond, &mutex, &ts);
```

```
SUCCESS: every wait saw the signal
```

Show your person both outputs, and the commands that repeat them.

## Commands and docs

- [`machine focus add`](https://testflows.com/docs/machine/commands.md#machine-focus-add),
  [`machine tasks`](https://testflows.com/docs/machine/commands.md#machine-tasks),
  [`machine tasks hold`](https://testflows.com/docs/machine/commands.md#machine-tasks-hold),
  [`machine tasks release`](https://testflows.com/docs/machine/commands.md#machine-tasks-release),
  [`machine break add`](https://testflows.com/docs/machine/commands.md#machine-break-add),
  [`machine run`](https://testflows.com/docs/machine/commands.md#machine-run)
- Focus and holding threads: https://testflows.com/docs/machine/steering-programs.md
- Breakpoints, running to a place: https://testflows.com/docs/machine/debugging.md
- The example: https://github.com/testflows/Machine/blob/main/examples/starvation/missed-wakeup.c
