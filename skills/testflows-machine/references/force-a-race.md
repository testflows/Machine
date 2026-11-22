# Force a race: name the window, hold a thread in it, prove the fix

A race needs one thread at a place while another does something. On real
hardware that lines up rarely, so the failure is rare. In the machine you put
the threads there: a breakpoint stops the first thread on the line, holding
keeps it there, and the second runs to where it acts. One try shows the bug.

## 1. Name the window

From the code, say which thread must be where while another does what, and
name the source line where the first one stops. Example, a job queue whose
worker checks the queue without the lock:

```c
if (queue_empty()) {               /* checked without the lock      */
    pthread_mutex_lock(&lock);     /* line 73: the window is here  */
    pthread_cond_timedwait(&ready, &lock, &deadline);
```

The window: the worker has seen an empty queue and not yet taken the lock,
while the producer pushes a job and signals. The signal wakes nobody, and the
worker then waits out its whole timeout.

## 2. Boot the program

Build with `-g -static`, and a disk of it, as `sessions-and-disks.md` says.

```bash
machine -s work create bug --disk app --cpus 2
machine -s work focus bug add comm=app
machine -s work run bug --until comm=app:exec --within 60s    # the program started
machine -s work run bug --until comm=app:sched --within 60s   # focus resolves on a dispatch
machine -s work tasks bug --focused               # its tids
```

`machine focus` may print `Machine has not linked; nothing can be steered`
right after `focus add`. It is a status line, not a refusal: the focus is
added, and running until the program starts is the way on.

## 3. Force the window

```bash
machine -s work break bug add <tid> app.c:73       # any tid of the program: it covers them all
machine -s work run bug --iters 0 --within 60s     # runs until a thread reaches the line
machine -s work tasks bug hold <worker-tid>        # it stays in the window
machine -s work break bug delete 1
machine -s work run bug --until 'tid=<producer-tid>:at=wait_drained' --within 60s   # the producer acts
machine -s work tasks bug release <worker-tid>
machine -s work run bug --until 'console~(STALL|OK:)' --within 60s
machine -s work output bug -n :
```

`--until 'tid=N:at=LOCATION'` runs until that task reaches a function or a
`file:line`. Run the second thread to the point just after it acts, here
after it pushed and signalled.

## 4. Check the signature

The failure must be the one the bug causes: here
`STALL: a job waited 2000 ms`, the worker's whole idle timeout. A hold delays
a thread in machine time, so a check with a time limit can fire because of
the hold alone. See `pitfalls.md`.

## 5. Prove the fix

Fix the code, build it, and run the same steps on it, with the breakpoint on
the corresponding line. The same interleaving must now pass: the program
prints its success line, not the failure.

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
