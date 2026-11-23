# The Python SDK: learn it from the docs and the source

Contents: install and sign in, the shape of a program, where each part is in
the source, examples to start from.

The SDK, `import testflows.machine.v1 as machine`, does everything the client
does, from a program: sessions, disks, runs, branches, replay, faults,
steering and debugging. Its source is public and documented in its
docstrings, so the source is the reference: read the class you need before
you call it.

## Install and sign in

```bash
pip3 install testflows.machine
```

The SDK runs on the SDK Core, which the client's installer installs into
`~/.testflows/machine/sdk/`. Install the client first, as `setup.md` says.
The SDK signs in as the client does: `TESTFLOWS_MACHINE_API_KEY`, the login,
or the saved key.

## The shape of a program

```python
import testflows.machine.v1 as machine

client = machine.Client()
with client.sessions.create(cpus=2) as session:      # deleted at the end of the block
    client.sessions.use(session)
    run = client.create("app", disk="app", cpus=2)
    run.run(until="console~ready", within="30s")
    run.checkpoint("ready")
    branch = run.fork(at="^ready", name="try-1")      # named "app/try-1"
    branch.inject.add("syscall", "write", "ENOSPC", in_="/usr/bin/app", after=1, times=1)
    branch.run(until="console~(ERROR|OK)", within="60s")
    print("\n".join(branch.output()))                 # every line
```

- A command's options are the method's keywords, mostly by the same name:
  `machine run --task-sched` is `run.run(task_sched=...)`. Some differ:
  `--in` is `in_=`, `--from` is `from_=`, `--return` is `ret=`, and a
  `--no-` flag is the keyword set to `False`, as `idle_skip=False`. Read the
  method's signature before you call it.
- Each call returns a record from `results.py`, with `as_json()`.
- Each failure raises an exception from `errors.py`, one class per
  condition: `NotRunning`, `Diverged`, `Timeout`, `NoCapacity`.
- A `with` block on a run deletes it and its branches at the end, unless
  `run.keep()` was called on that handle. Keep a root that holds a branch
  you want, out of a `with` block.

## Where each part is in the source

Read these at
https://github.com/testflows/Machine/tree/main/sdk/python/testflows/machine/v1,
or raw, for reading whole: replace the file name in
https://raw.githubusercontent.com/testflows/Machine/main/sdk/python/testflows/machine/v1/runs.py

| You need | File | Classes |
|---|---|---|
| The client, runs by name, deleting a tree | `client.py` | `Client`, `Runs`, `Marks` |
| Sessions: create, use, keep, delete | `sessions.py` | `Session`, `Sessions` |
| Disks: build from a binary, an image, a Compose project | `disks.py` | `Disk`, `Disks` |
| A run: run, plan, checkpoint, fork, go, rewind, replay, diff, output, stop | `runs.py` | `Run`, `Output` |
| Tasks, focus, holds, breakpoints, the debugger | `runs.py` | `Tasks`, `Task` for the debugger, `Focus`, `Breakpoints` |
| Faults, containers, the network, interrupts, the disk | `runs.py` | `Faults` as `Run.inject`, `Containers`, `Net`, `Irq`, `Fs` |
| What calls return | `results.py` | `RunResult`, `ReplayResult`, `Diff`, `Checkpoint`, `RunInfo` |
| What calls raise | `errors.py` | `Error` and its subclasses |
| Signing in from a program | `login.py` | |

`runs.py` is long. Search it for `def <name>` rather than reading it whole:
each method's docstring gives its arguments, what it returns and what it
raises, with an example.

## Examples to start from

Each is a complete program, in
https://github.com/testflows/Machine/tree/main/sdk/python/examples:

| Example | Shows |
|---|---|
| `hello.py` | Create a run, boot it, read its console |
| `checkpoint_and_fork.py` | Checkpoint a booted run and fork branches from it |
| `force_interleaving.py` | Hold one thread while another runs |
| `missed_wakeup.py` | Stop a thread at a breakpoint, hold it, and force a missed wakeup |
| `explore_seeds.py` | One branch per seed, failures kept |
| `plan_file.py` | Drive a run by a plan |
| `fault_injection.py` | Fail system calls and disk writes |
| `container_crash.py` | Crash one container while the others run |
| `inject_interrupt.py` | Inject an interrupt, and replay it at the same point |
| `debug_task.py` | Stop at a system call and read the task as a debugger does |
| `replay_verify.py` | Replay a recording and check it reproduces |
| `machine_time.py` | The machine's clock |
| `read_fs.py` | Read files as a checkpoint saved them |
| `syscall_profile.py` | Profile system calls and interrupts |
| `stop_and_resume.py`, `resume_elsewhere.py` | Stop and start runs, in another session too |
| `watch_and_follow.py` | Follow a console while another thread drives |
| `ci_job.py` | A CI job: one session, runs labeled by commit, an exit code |
| `error_handling.py` | Each error and its recovery |
| `disks.py`, `preload_and_offload.py` | Disks, and the session's storage |

`fuzz-with-python.md` builds a fuzzer on these.

## Docs

- The Python SDK: https://testflows.com/docs/machine/python-sdk.md
- The SDK's README: https://github.com/testflows/Machine/tree/main/sdk/python
- Every command, whose options the methods take:
  https://testflows.com/docs/machine/commands.md
