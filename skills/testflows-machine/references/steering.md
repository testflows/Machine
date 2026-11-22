# Steering programs: tasks, holds, plans, interrupts, machine time

The examples use a run named `app`; add `-s <session>` to each command.

## Tasks and focus

| Action | Command |
|---|---|
| List the tasks: tid, tgid, comm, EXE, state | `machine tasks app` |
| Focus a program, to steer its tasks | `machine focus app add comm=app`, or by path `machine focus app add /usr/bin/app` |
| The focused tasks alone | `machine tasks app --focused` |
| Check a program runs, reading only | `machine tasks app exist /usr/bin/app` |
| Send a signal | `machine tasks app signal 412 SIGTERM` |

A program must be focused before a plan or a `when` names it, and the
machine must have started it: `machine run app --until comm=app:exec`. Focus
resolves on the next dispatch, so `tasks --focused` lists the tasks after
one more run, such as `--until comm=app:sched`.

## Holding threads

| Action | Command |
|---|---|
| Hold a task: it does not run while others go on | `machine tasks app hold 412` |
| Release it | `machine tasks app release 412` |

A held task loses machine time. A check with a time limit can fail from the
hold alone. `force-a-race.md` puts holding to use.

## Plans

A plan draws the schedule while a program runs: when tasks are stepped,
held or preempted. The seed decides the draws, so a seed that fails is a
reproduction. `machine plan --help` lists every axis.

```
# plan.txt
when comm=app:on   mode step[1],fast[9] step=1:3 task-sched hold[1],keep[9] task-int preempt[3],none[7]
```

```bash
machine plan app plan.txt --seed 3 --until 'console~FAIL' --within 60s
machine run app --mode step,fast --task-sched keep,hold --seed 3 --within 60s
machine axis app set mode step,fast fast=1:100k      # defaults every later run draws from
```

`fuzz-with-plans.md` puts plans to use.

## Interrupts

| Action | Command |
|---|---|
| Preempt the task on a vCPU, or send an NMI | `machine irq app preempt --vcpu 1`, `machine irq app --nmi --vcpu 0` |
| Hold interrupts a drawn time before they arrive | `machine run app --irq-delay timer=0:2ms,blk=1ms` |
| Interrupts owed and held | `machine irqs app` |

## Machine time

| Action | Command |
|---|---|
| The starting date of a run | `machine create app --disk app --vtime-base 1767225600`, a Unix time |
| A vCPU's clock rate: vCPU 1's work costs 4x machine time | `machine vtime app rate 10,1=40` |
| What a wait costs on the clock | `machine vtime app cost io=20us pause=50` |

The machine's date can also be changed as a fault: `inject-faults.md`.

## Docs

- Steering programs: tasks, focus, holds, plans, interrupts, machine time:
  https://testflows.com/docs/machine/steering-programs.md
- Schedules: https://testflows.com/docs/machine/running-a-machine.md
- Every command: https://testflows.com/docs/machine/commands.md
