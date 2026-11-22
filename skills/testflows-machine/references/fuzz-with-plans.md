# Fuzz with plans: search for the window from seeds

Use this when reading the code does not point at one interleaving. A plan
draws the scheduling while the program runs: when a thread is held, stepped
or preempted. Every run repeats exactly from its seed, so a seed that fails
is a reproduction you can hand over.

Searching costs many runs where forcing a named window costs one, and a
narrow window can take many seeds. Name the window when you can:
`force-a-race.md`.

## The plan

A plan file is a list of clauses: when a state of a program holds, what to
draw for each dispatch. `machine plan --help` lists every axis.

```
# plan.txt: while missed-wakeup runs its own code, sometimes step it, hold it or preempt it
when comm=missed-wakeup:on   mode step[1],fast[9] step=1:3 task-sched hold[1],keep[9] task-int preempt[3],none[7]
```

`[N]` makes a choice N times as likely. A `when` that names a program needs
it focused first.

## The search

Fork a branch from a checkpoint for each seed, so every seed starts from the
same state, and stop each branch when it has its answer. The disk is `mw`,
`examples/starvation/missed-wakeup.c` built as `force-a-race.md` builds it:

```bash
machine -s work create fz --disk mw --cpus 2
machine -s work focus fz add comm=missed-wakeup
machine -s work run fz --until comm=missed-wakeup:exec --within 60s
machine -s work checkpoint fz started
for seed in 1 2 3 4 5 6 7 8; do
  machine -s work fork fz --at ^started --name s$seed
  machine -s work plan fz/s$seed plan.txt --seed $seed --until 'console~(FAILURE: missed|SUCCESS:)' --within 120s
  machine -s work output fz/s$seed -n : | grep -E 'FAILURE:|SUCCESS:'
  machine -s work stop fz/s$seed
done
```

A branch's full name is the root's name, a slash and its own: `fz/s3`. `--within` bounds a run by
the machine's clock, so a seed that never ends the program still ends.

## Check each seed

Match the bug's own signature, never any failure. A plan's holds and steps
delay threads in machine time, so a check with a time limit fires without
the bug. Run the fix over the same seeds: a seed that still fails it is not
the bug. See `pitfalls.md`.

## Commands and docs

- [`machine plan`](https://testflows.com/docs/machine/commands.md#machine-plan),
  [`machine checkpoint`](https://testflows.com/docs/machine/commands.md#machine-checkpoint),
  [`machine fork`](https://testflows.com/docs/machine/commands.md#machine-fork),
  [`machine focus add`](https://testflows.com/docs/machine/commands.md#machine-focus-add),
  [`machine stop`](https://testflows.com/docs/machine/commands.md#machine-stop)
- Plans, focus and holding threads: https://testflows.com/docs/machine/steering-programs.md
- Schedules and seeds: https://testflows.com/docs/machine/running-a-machine.md
- Checkpoints and branches: https://testflows.com/docs/machine/checkpoints-and-branches.md
