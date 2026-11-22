---
name: testflows-machine
description: Everything about TestFlows Machine, a deterministic simulation testing environment, and how to use it to reproduce rare bugs on purpose and prove fixes. Use when the person asks what TestFlows Machine is, what it can do for their project, what it costs or how to start; mentions deterministic simulation testing; or has a flaky or rare failure, a race, a hang, a timeout or a fault to reproduce, or a fix to verify. Covers what it is and its plans, installing and signing in the machine client with the person's help, sessions and disks, forcing the exact interleaving or fault the code points at, fuzzing when it points at none, and proving the fix.
metadata:
  version: "20261122"
---

# TestFlows Machine

TestFlows Machine is a deterministic simulation testing environment, run as a
cloud service. It runs software built for Linux x86_64 in a machine that
controls everything the software could see vary: time, interrupts, the
thread schedule, random numbers and input. You drive it from this computer
with the `machine` client or the Python SDK; nothing runs locally.

## What you are dealing with

- **Session.** A set of cloud resources, vCPUs, memory and disk space, that
  you create and delete. Runs execute in a session. It costs credits while
  it runs, and stops by itself after an idle time, which ends the charge.
- **Disk.** What a machine boots: your program packed as a container image,
  from a static binary, a public image with files added, your own Docker
  image or a Compose project. Inside the machine, Linux boots and runs it
  under Docker Compose.
- **Run.** One machine booted from a disk, and its recording. The machine
  executes only when you tell it to, with `machine run`, until a condition
  you give: the program exits, a line is printed, a thread reaches a
  function, or an amount of machine time passes. Between commands it is
  stopped, and you can inspect and change it.
- **Recording and replay.** A run records every source of variation as it
  executes. Replaying the recording repeats the execution exactly, to the
  instruction, as often as you like. So a failure seen once is a failure
  you have for good.
- **Checkpoint and branch.** A checkpoint saves the whole machine at a
  point. A branch is a new run from a point of another; `machine fork`
  makes one. Each branch then goes its own way: another seed, another
  fault, another schedule, while the original stays as it was. A branch is
  named `root/name`: the root run's name, a slash, the name you gave it.
- **Machine time.** The machine's clock, separate from the wall clock. Idle
  waits are skipped, so a 2-second timeout costs almost nothing, and a
  thread you hold loses machine time while others go on.
- **Tasks.** The threads of the programs in the machine, by tid. Focus a
  program with `machine focus <run> add comm=<name>` to steer its
  tasks, then stop them at breakpoints, hold and release them, and run until one
  reaches a line.
- **Faults.** Rules that make calls of a program fail or wait: system
  calls, functions, disk requests, the network, the clock, containers. The
  recording keeps them, so a replay fails the same calls.
- **Seeds and plans.** A plan file says what to draw while a program runs:
  schedules, steps, holds, delays. The plan's `--seed` decides its draws,
  so the same seed gives the same run, and a fuzzer's failing seed is a
  reproduction. A fault and the machine's random number device each have a
  seed of their own.

## The method: name the bug, force it, prove the fix

A person reports a rare failure: a flaky test, a hang, a lost update, a crash
that never happens locally. Do not run the program again and again hoping
the failure shows. Read the code and name the cause: which thread must be
where while another acts, or which call must fail. Make the machine produce
exactly that, and one try shows the bug. Run the same steps on the fix, and
one try proves it.

**A race.** Stop one thread in the window with a breakpoint, hold it there,
run the other thread through the part that races, then release:

```bash
machine -s work create bug --disk app --cpus 2
machine -s work focus bug add comm=app
machine -s work run bug --until comm=app:exec --within 60s    # the program started
machine -s work run bug --until comm=app:sched --within 60s   # focus resolves on a dispatch
machine -s work tasks bug --focused                      # its tids
machine -s work break bug add <tid> app.c:73             # the window's line
machine -s work run bug --iters 0 --within 60s          # runs until a thread stops there
machine -s work tasks bug hold <tid-at-the-line>
machine -s work break bug delete 1
machine -s work run bug --until 'tid=<other-tid>:at=<function>' --within 60s   # the other acts
machine -s work tasks bug release <tid-at-the-line>
machine -s work run bug --until 'console~(FAIL|OK)' --within 60s
machine -s work output bug -n :                    # every line, to search
```

**A failure path.** Fail the exact call the code points at, here the second
`write` of the program, once. `--in` takes the program's path as
`machine tasks` shows it under EXE:

```bash
machine -s work inject bug add syscall write ENOSPC --in /app --after 1 --times 1
```

**No named cause.** Fuzz: checkpoint once the program starts, fork a branch
for each seed, and run each with a drawn schedule:

```bash
machine -s work checkpoint bug started
machine -s work fork bug --at ^started --name s1
machine -s work plan bug/s1 plan.txt --seed 1 --until 'console~(FAIL|OK)' --within 120s
```

**Check the signature.** The failure must be the one the bug causes, not any
failure: a held thread loses machine time, so a check with a time limit can
fail from the hold alone. Then run the fix under the same steps or seeds.

## What you can do

Each line is a capability, an example, and the reference file with the rest.
Every reference file ends with links to the docs pages it covers.

- **Run and inspect**: run until a condition, read output, type input, run
  commands in the machine, copy files, core dumps, profiling, read the disk
  at a checkpoint. `machine exec app run -- ls /`. `running-and-inspecting.md`
- **Time compression**: idle waits are skipped, so a 10-minute sleep passes
  in seconds; on by default. `running-and-inspecting.md`
- **Retroactive logging**: a run drops a program's debug logs, and a branch
  that replays a stretch from a checkpoint before it writes them again.
  `machine fork app --from ^before --keep-output /output/drop/app/debug.log`.
  `running-and-inspecting.md`
- **Time travel**: checkpoints, branches from any point, go, switch, rewind,
  replay, diff. `machine rewind app to ^booted`. `time-travel.md`
- **Steering**: focus a program, hold and release threads, plans drawn from
  a seed, interrupts, machine time. `machine tasks app hold 412`.
  `steering.md`
- **Fault injection**: system calls, functions, kernel allocations, disk
  requests and writes, container resources, containers, the clock, the
  network. `machine net app partition db api`. `inject-faults.md`
- **Debugging**: registers, memory, the stack, variables, breakpoints,
  watchpoints, catchpoints, stepping forward and back, writes; GDB inside the
  machine. `machine run app --back`. `debugging.md`
- **Sessions, disks, account**: session sizes and classes, disks from any
  source, plan, credits, activity, storage. `machine account show`.
  `sessions-and-disks.md`
- **Scripting**: JSON, exit codes, timeouts, recordings that repeat.
  `running-and-inspecting.md`
- **Python SDK**: all of the above from a program; learn it from its
  documented source. `python-sdk.md`

## Core commands

| Command | Does |
|---|---|
| `machine sessions create work --cpus 2` | A session; it costs while it runs |
| `machine disks build --binary ./app app` | A disk of a static program; `--from`, `--image`, `--compose` for others |
| `machine create <run> --disk <disk>` | A run, booted and waiting for commands |
| `machine run <run> --until <cond> --within <time>` | Run until a condition, bounded by machine time; `--iters 0` runs to the next breakpoint; always give `--within` |
| `machine output <run> -n :` | Every line the program printed; the last 25 without `-n` |
| `machine checkpoint <run> <name>`, `machine fork <run> --at ^<name>` | Save a point, branch from it |
| `machine stop <run>`, `machine sessions delete work -y` | Clean up |

## Where to find it

Read a file or a page when you need it, not before.

| You need | Find it in |
|---|---|
| To answer what Machine is and what it can do for a project | https://testflows.com/machine.md |
| To answer what it costs: plans, credits, billing | https://testflows.com/docs/machine/account-and-billing.md, and for your person https://testflows.com/machine/pricing/ |
| To install the client and sign it in with your person | `references/setup.md` |
| A session, and a disk of the program | `references/sessions-and-disks.md` |
| To force a race: a thread held at a line while another acts | `references/force-a-race.md` |
| To run, read output, send input, exec, copy files, profile, read the disk | `references/running-and-inspecting.md` |
| Checkpoints, branches, go, switch, rewind, replay, diff | `references/time-travel.md` |
| Focus, holds, plans, interrupts, machine time | `references/steering.md` |
| To make an exact call fail, and every other fault: disk, containers, clock, network | `references/inject-faults.md` |
| The debugger, and GDB inside the machine | `references/debugging.md` |
| The Python SDK: its docs, its source by class, its examples | `references/python-sdk.md` |
| To search from seeds with a plan file | `references/fuzz-with-plans.md` |
| To search from seeds with a Python program | `references/fuzz-with-python.md` |
| A refusal, a failure that may not be the bug, cleanup | `references/pitfalls.md` |
| The concepts: runs, replay, checkpoints, branches | https://testflows.com/docs/machine/overview.md |
| Every docs section | https://testflows.com/docs/machine.md |
| A command's options | https://testflows.com/docs/machine/commands.md, or `machine <command> --help` |
| Programs that hold known bugs, to try | https://github.com/testflows/Machine/tree/main/examples |

Every docs page has this Markdown version for agents. Each reference file
ends with the commands and docs sections it relies on.

## Keep this skill up to date

This skill's version is the `version:` at the top of this file. Once per
conversation, before the first command, read the version it publishes:

```bash
curl -fsSL https://raw.githubusercontent.com/testflows/Machine/main/skills/testflows-machine/SKILL.md | grep -m1 'version:'
```

If it is newer than this one, tell your person and advise updating: the
newer skill may know commands and fixes this one does not. Update only when
they agree, by running the install command again. Put the folder this skill
is in, `~/.claude/skills` or `~/.agents/skills`, in both places:

```bash
curl -fsSL https://codeload.github.com/testflows/Machine/tar.gz/main | tar -xz -C ~/.claude/skills --strip-components=2 Machine-main/skills/testflows-machine
```

The update takes effect in the agent's next session. If the check fails,
go on with this version.

## Rules

- You sign the client in with your person's help: they read a code from an
  email or a page and tell it to you. They accept the Terms of Service
  themselves, on the page the sign-up email opens. Never accept them for
  your person.
- Ask before you use credits or pay: a session costs credits while it
  runs. The API key the set-up saves is part of signing in; ask
  before creating or deleting any other key.
- Never print an API key or commit one. The client saves its key and never
  shows it.
- Name the session on every command with `-s <session>`. `machine sessions
  use` holds only for the shell it ran in, and each of your commands may run
  in a new one. `-s` takes a session connected on this computer: `machine
  sessions create` connects the session it creates, unless given
  `--no-use`, and `machine sessions use <session>` connects one.
- Bound every command that runs the machine: `--within` on a run, or the
  global `--timeout <seconds>`. Without one, a condition never met runs
  until it is cancelled.
- Put `-o json` before the command. An error is a JSON document on standard
  error with exit code `2`, and each of its `hints` is a command that gets
  you out.
- Delete the runs and the session you created when you are done, but keep
  a run that reproduces a bug: it is the evidence, and it replays.

## Workflow for a bug

```
- [ ] The client works: machine account whoami passes (setup.md)
- [ ] A session and a disk of the program (sessions-and-disks.md)
- [ ] Named the cause from the code: the race window, or the failing call
- [ ] Forced it (force-a-race.md or inject-faults.md), or searched (fuzz-*.md)
- [ ] The failure is the bug's own signature, not any failure (pitfalls.md)
- [ ] The fix passes the same scenario: the same steps, or the same seeds
- [ ] Showed your person both outputs and the commands that repeat them
- [ ] Stopped the runs, kept the one that reproduces, deleted the session
```
