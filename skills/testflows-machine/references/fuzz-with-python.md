# Fuzz with Python: a search with its own checks

A program can draw more than a plan file says, check each run with its own
code, and keep the run that fails. Use it for many seeds, or when the check
is more than a line of output.

## Install

```bash
pip3 install testflows.machine
```

The SDK runs on the SDK Core, which the client's installer installs. It signs
in as the client does: `TESTFLOWS_MACHINE_API_KEY`, the login, or the saved
key.

## The fuzzer

```python
import testflows.machine.v1 as machine

SIGNATURE = "STALL: a job waited 2000 ms"   # the bug's own failure, never any failure

client = machine.Client()
with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    # Not in a with block: deleting the root deletes its branches, the
    # one that reproduces included.
    run = client.create("fz", disk="app", cpus=2)
    run.focus.add("comm=app")
    run.run(until="comm=app:exec")
    run.checkpoint("started")
    for seed in range(20):
        branch = run.fork(at="^started", name=f"s{seed}")
        branch.run(until="console~(STALL|OK:)", within="120s",
                   mode="step,fast", step="1:3",
                   task_sched="keep,hold", seed=seed)
        if any(SIGNATURE in line for line in branch.output()):   # every line
            print("seed", seed, "reproduces it:", branch.describe().name)
            break
        branch.stop()
    run.stop()
```

- The session's `with` block deletes the session at its end. The runs stay,
  with their recordings. Show your person the branch that reproduced, and
  delete the branches that did not: `client.runs["fz/s3"].delete()`. Delete
  the whole tree only when your person agrees: it holds the reproduction.
- `run.run` takes every option of `machine run` as a keyword: `mode`, `step`,
  `task_sched`, `task_int`, `seed`, `until`, `within`.
- `run.plan("plan.txt", seed=3)` runs a plan file from Python.
- `run.tasks.hold(tid)`, `run.tasks.release(tid)` and
  `run.task(tid).breakpoint("app.c:73")` force a named window from Python, as
  `force-a-race.md` does from the client. `run.run(iters=0)` runs to the next
  breakpoint hit.
- `run.inject.add("syscall", "write", "ENOSPC", in_="/app", after=1, times=1)`
  fails one exact call, as `inject-faults.md` does.

Run the fix over the same seeds before calling it proved.

## Commands and docs

- Python SDK: sessions, runs, scheduling, faults, debugging:
  https://testflows.com/docs/machine/python-sdk.md
- The SDK's examples: https://github.com/testflows/Machine/tree/main/sdk/python/examples
- [`machine run`](https://testflows.com/docs/machine/commands.md#machine-run),
  whose options `run.run` takes
