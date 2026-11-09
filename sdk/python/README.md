# TestFlows™ Machine Python SDK

Python SDK for [TestFlows™ Machine](https://testflows.com/machine), the deterministic
execution environment.

```python
import testflows.machine.v1 as machine

client = machine.Client()
with client.sessions.create("ci-1", cpus=4) as session:
    client.sessions.use(session)
    run = client.create("app", disk="app")
    run.run(until="tasks")
    # Focus a program before waiting on it.
    run.focus.add("/app")
    run.run(until="/app:exit")
    run.checkpoint("done")
    branch = run.fork(at="^done", name="retry")
# leaving the block deletes the session
```

## Installing

```sh
pip install testflows.machine
```

Python 3.11 or later, on Linux x86_64. pip also installs
`testflows.machine.core`, the TestFlows™ Machine core the SDK runs on.

## The client

A program holds one `machine.Client`, and the SDK mirrors the `machine`
command:

| CLI | SDK |
|---|---|
| a top-level command, `machine create --disk app` | a client method, `client.create(disk="app")` |
| a command group, `machine sessions use ci-1` | a client attribute, `client.sessions.use("ci-1")` |
| a command that takes a run, `machine fork app --at ^boot` | a `Run` method, `run.fork(at="^boot")` |
| a group that takes a run, `machine tasks app hold 56` | a `Run` attribute, `run.tasks.hold(56)` |
| a positional operand | a positional argument |
| a flag, `--no-wait`, `-w`, `-f` | a keyword argument, `wait=False`, `watch=True`, `follow=True` |

Runs execute in a session, a machine the account rents. The client has a
current session, as the CLI does: `machine.Client(session="ci-1")` sets it,
as `-s` does, and `client.sessions.use(...)` changes it for this client
only, never for the CLI. Commands that need a machine go to the current
session. Without one, the account's runs are still readable: `client.runs()`,
`client.branches()`, `client.marks.list()`, and a run's `describe()`,
`lineage()` and `checkpoints()`. Nothing is contacted until a call needs it.

## Signing in

Every call signs in with, in order:

1. `TESTFLOWS_MACHINE_API_KEY`, for automation and CI;
2. the login stored by `machine login`, shared with the CLI.

A program can sign in itself; the code arrives by email:

```python
machine.login.start("me@example.com")
client = machine.login.verify("me@example.com", code="123456")
```

`machine.login.logout()` signs out the stored login.

## Runs

| Call | Does |
|---|---|
| `client.create(name, disk=..., cpus=..., ...)` | Create a run from a disk and bring it up; takes every `machine create` option |
| `client.runs(...)`, `client.runs["app"]` | Runs, filtered, sorted and paged as `machine runs` does; one by name |
| `run.run(iters=..., until=..., mode=..., ...)`, `run.plan(file)` | Drive it |
| `run.checkpoint(name)`, `run.checkpoints(...)` | Checkpoint it, list its checkpoints |
| `run.fork(...)`, `run.go(point)`, `run.switch(branch)`, `run.detach(...)` | Branch |
| `run.replay()`, `run.play(...)`, `run.diff(...)` | Replay and compare |
| `run.describe()`, `run.state()`, `run.now()`, `run.dump(...)`, `run.entries(...)` | Read it |
| `run.output(...)`, `run.debug.log(...)`, `run.debug.err(...)`, `run.control.log(...)` | Read its output and history |
| `run.output.search(pattern, from_=..., to=...)`, `run.output.find(pattern)` | Find text in its output, between two points; a match's `place` is where the match started to print, for `go` or `fork` |
| `run.fs.ls(path, at=...)`, `.cat(...)`, `.cp(...)` | Read its disk at a checkpoint, which `at` names; data not yet synced to the disk is not visible |
| `run.send(text, device=...)` | Send input to a device of the run, or to a file in it by its path; a `Sent` has `queued`, and `process` for a file |
| `run.exec(command)`, `run.exec(command, wait=False)`, `run.process(id)`, `run.processes()` | Execute a command in the run; a `Process` has `pid`, `exit_code`, `stdout`, `stderr`, `events` and `exits` |
| `run.exec(command, container="db", user="root")` | Execute it in a container, named by its Compose service or by its own name, as a user of it |
| `run.core(pid, dest, container=...)` | Get a core dump of a process into a file; the process dies of it |
| `run.profile("2s", container=..., pid=...)` | Sample what runs for a time, without adding a process to the run; a `Profile` has `samples`, `functions`, `stacks`, and `folded` text for flame graph tools |
| `run.profile(from_="^request", to="^reply")` | Profile a stretch the run already recorded; the run is not changed |
| `run.profile("2s", in_guest=True)` | Sample with a process in the run, for the functions of the kernel by name |
| `run.containers()` | List the containers and the Compose service of each: `name`, `service`, `state`, `image` |
| `run.put(source, path, container=..., mode=..., owner=...)` | Put a file of any size into the run or into a container of it; `source` is bytes or the path of a file |
| `run.get(path, dest, container=...)` | Get a file out, as it is now; a `Got` has `size`, `mode`, `uid`, `gid`, and `data` when no `dest` is given |
| `run.tasks()`, `run.focus.add(...)`, `run.irq(...)`, `run.vtime.rate(...)` | Steer the guest |
| `run.task(tid)` | A guest task to debug, by its tid: `registers()`, `read(address, size)`, `address("main+4")`, `symbol(address)`, `symbols(regex="^wait")`, `backtrace()`, `libraries()` |
| `task.set(rax=0, rip="main+4")`, `task.write(address, data)` | Write its registers and its tgid's memory; a replay makes the writes again |
| `task.breakpoint("main")`, `task.watch("counter", on="access")` | Stop its tgid at a location, or at a write or access of its bytes; `run.run(...).hits` names the one the run ended at |
| `task.step()`, `task.step(line=True, over=True)`, `task.finish()` | Step an instruction or a line, over calls, or out of the function; `run.run(until="tid=412:at=main")` runs to a location, `tgid=7:at=0x401136` to one in any thread of a process, and `tid=412:write=counter` or `tgid=7:access=counter` until the bytes are written or read |
| `run.breakpoints.catch("fork", in_="tgid=1")`, `run.breakpoints.catch("exit", in_="comm=cat")` | Stop at an event of a task: exec, exit, fork, vfork, syscall, signal, load, unload, throw, catch, rethrow; `in_` takes `tid=N`, `tgid=N` or `comm=NAME`, and `hits[0].event` says which |
| `run.breakpoints.hits()`, `run.back()`, `run.back(entries=3)` | The hits the run log keeps; onto a branch at the newest one, or entries back |
| `run.breakpoints.list()`, `run.breakpoints.delete(number)` | The run's breakpoints, watchpoints and catchpoints, and the hardware stops GDB in the guest holds; a branch has its parent's |
| `run.breakpoints.enable(2, False)`, `.condition(2, "counter > 3")`, `.ignore(2, 5)` | Disable or enable a stop, set where its hits count, or pass its next hits |
| `task.print("shape->at.x", frame=1)`, `task.print("counter = 3")`, `task.locals(args=True)` | An expression in a frame, a write of one with `TARGET = VALUE`, the locals or the arguments of a frame |
| `task.call("strlen", "buf")`, `task.return_(0)` | Call a function in the task, or return from its innermost one |
| `task.back()`, `task.back(line=True, over=True)` | Go back over the task's code onto a branch, as reverse-stepi and reverse-next |
| `run.tasks.signal(412, "SIGTERM")` | Send a task a signal; a replay sends it at the same instruction |
| `run.syscalls()`, `run.syscalls.counts(task)`, `run.ints`, `run.irq.vectors()` | The guest kernel's tables, and a task's counts |
| `run.start()`, `run.stop()`, `run.pause()`, `run.kill()`, `run.delete()` | Lifecycle |
| `client.stop(all=True)`, `client.delete(all=True)`, `client.delete(disk=...)` | Every run at once |
| `client.delete.cleanup()`, `client.cleanup()` | Finish interrupted deletes; clear stale state |

## Watching and following

A listing takes `watch=True` and then yields it each time it changes, as
`machine ... -w` repaints it. A log takes `follow=True` and then yields new
lines as they come, as `-f` does.

```python
for runs in client.runs(states=["running"], watch=True, timeout=60):
    print([r.name for r in runs])
for line in run.output(follow=True, timeout=60):
    print(line)
```

## What calls answer with

Every call answers with a record whose fields are documented on it:
`run.describe()` a `RunInfo`, `run.tasks()` a list of `TaskInfo`, `run.vcpu()` a
`Vcpu`, `run.syscalls.counts(task)` a `Counts`. `as_json()` turns any record
into a dict. `run.dump()` is the one document: what it carries depends on the
sections asked for.

Which entries a reader wants is one grammar everywhere, whether it filters
the output, reads the log or drives a playback:

| Spelling | Means |
|---|---|
| `200` or `(100, 200)` | that entry, or the range, both ends included |
| `"100:200"`, `"100:"`, `":200"`, `":"` | the same range, from 100 on, up to 200, every entry |
| `"-20"` | the last 20 entries |
| `slice(100, 200)`, `slice(-20, None)` | a Python slice, half-open as Python's are: 100 through **199**, and the last 20 |

The pair and the string are the machine's own inclusive range, the one its
messages and the `machine` command print; the slice is Python's, so
`(100, 200)` covers one more entry than `slice(100, 200)`. Each follows its
own convention, and a bad step is refused rather than silently changed.

`lines=` selects lines of output in the same grammar: an int or `"20"` for line
20, `-20` or `"-20"` for the last 20, `"20:"` from the 20th on, `"10:20"` for a
window, and `0` for none, which starts a follow at the end.

## Points in a run's history

A call that takes a point, such as `fork(at=...)`, `go`, `rewind`,
`replay(to=...)` or `profile(from_=..., to=...)`, takes it as text, `"^booted"`,
`"#300"`, `"1.194s"` or `"-10ms"`, or as one of three records:

| Record | Is | Taken by |
|---|---|---|
| `Point` | A time: `vtime`, the machine's clock in nanoseconds | Every run. Each resolves it to its own entry |
| `Place` | One entry: `run_id` that recorded it, `entry_id`, `point` and `vtime_offset` | The run that recorded it and its branches forked after it |
| `Checkpoint` | A saved state: its `name`, and the `place` it was saved at | As its place |

`start(at=...)` and the `fs` readers take a checkpoint only, by its name or as
a `Checkpoint`: they open a saved state, and a time or an entry is not one.

A point moves by a duration: an int of nanoseconds, a str such as `"10ms"`, or
a `timedelta`. Subtract one point from another for the nanoseconds between them.
`run.now()` is a `Place`, and `run.place(point)` finds the entry a point names.

```python
booted = run.checkpoint("booted")
branch = run.fork(at=booted.place.point + "10ms")
here = branch.now()
run.place(here.point)
```

The last line takes the branch's time on the parent. `run.place(here)` is
refused there once the two have recorded past the fork, since the parent's
entry of that id is another state.

## Sessions

A session is a cloud machine with its own CPUs, memory and storage.

| Call | Does |
|---|---|
| `client.sessions.create(name, cpus=..., wait=True)` | Create a session; waits until it is running |
| `client.sessions.use("ci-1")` | Make it the client's current session |
| `client.sessions()`, `client.sessions.show("ci-1")` | Every session; one by name, id, or unique id prefix |
| `client.sessions.rename("ci-1", "ci-2")` | Rename |
| `client.sessions.keep("ci-1", 30)`, `session.keep(30)` | Keep it running while idle for 30 minutes, up to the plan's limit. An idle session serves no command, and stops after the plan's idle time |
| `client.sessions.delete("ci-1")`, `client.sessions.delete(all=True)` | Delete |
| `session.wait("running", timeout=...)`, `session.refresh()` | Wait for a state; read it again |

## Cleaning up

A session uses credits from the moment it is created until it is deleted, or
stops after its plan's idle time, and a run keeps its log and checkpoints until
it is deleted. Both work as context
managers: created in a `with` block, they are deleted when the block exits,
however it exits. A run takes its branches with it.

```python
with client.sessions.create(cpus=4) as session:
    client.sessions.use(session)
    with client.create(disk="app") as run:
        run.run(until="tasks")
```

Without `with`, nothing is deleted until `delete()` is called. To keep a run
that found something, leave it out of a `with` block.

## Errors

Every exception derives from `machine.Error`:

| Exception | Raised when |
|---|---|
| `NotSignedIn` | there are no credentials |
| `Unauthorized` | credentials or a sign-in code were rejected |
| `Forbidden` | the account may not do this |
| `NotFound` | no resource matches the name or id |
| `AlreadyExists` | a resource with that name exists |
| `InvalidArgument` | an argument is not valid, or a reference is ambiguous |
| `NotRunning` | the resource exists and is not running |
| `Conflict` | the request conflicts with the resource's state |
| `Diverged` | a replay did not reproduce what the run recorded |
| `Timeout` | a wait ran out of time |
| `ServiceUnavailable` | the service could not be reached |
| `ApiError` | the service refused the request |

## Examples

`examples/` holds plain scripts, each run as `python <script> --disk <name>`.
Each creates its own session and deletes it at the end.

| Script | Shows |
|---|---|
| `hello.py` | Create a run, boot it, read its console |
| `disks.py` | Push, rename and delete a disk a run boots |
| `checkpoint_and_fork.py` | Checkpoint 0 at boot, a named one after, and branches from it |
| `stop_and_resume.py` | What `stop`, `kill`, `start` and `sync` keep |
| `replay_verify.py` | Replay a recording, and name what diverged |
| `resume_elsewhere.py` | Stop a run in one session, start it in another |
| `explore_seeds.py` | One branch per seed; failing branches marked and kept |
| `force_interleaving.py` | Hold one thread while another runs past it |
| `plan_file.py` | Drive by `when` and `else` clauses |
| `watch_and_follow.py` | Watch a listing and follow a console |
| `syscall_profile.py` | Count a program's system calls and interrupts |
| `read_fs.py` | Read and copy files from a checkpoint's disk |
| `machine_time.py` | The machine's clock (vtime): rate and pushing it forward |
| `inject_interrupt.py` | Inject an interrupt, and replay it |
| `ci_job.py` | A CI job: API key, a session per job, an exit code |
| `error_handling.py` | Each error condition and its recovery |
| `preload_and_offload.py` | Copies on a session's disk, and account storage |

## License

The SDK is licensed under the Apache License 2.0. The TestFlows™ Machine core,
`testflows.machine.core`, is not part of it and has its own license.
