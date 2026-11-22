# Debugging: the machine's debugger and GDB inside the machine

The machine is stopped between commands, so nothing moves while you look,
and any point of any run can be debugged. Build the program with `-g` for
source lines and variables. The examples use a run named `app` and tid 412;
add `-s <session>` to each command.

## Looking

| Action | Command |
|---|---|
| General registers; `--all` adds x87, SSE, AVX | `machine regs app 412` |
| Memory, as GDB's x | `machine mem app 412 '$sp'`, `machine mem app 412 '$pc' 4i` |
| The stack | `machine backtrace app 412` |
| A variable by its type, in a frame | `machine print app 412 'shape->at.x'`, `machine print app 412 index --frame 1` |
| Locals, arguments | `machine locals app 412`, `machine locals app 412 --args` |
| Symbols, libraries, mappings | `machine symbols app 412 main`, `machine libs app 412`, `machine mem app 412 --mapped` |

A location is a number, `$reg`, `NAME`, `PATH:NAME`, `file.c:56` or `+N`.

## Stopping

| Action | Command |
|---|---|
| A breakpoint, for every task of the program | `machine break app add 412 main`, `machine break app add 412 app.c:73` |
| Conditional, temporary, hardware | `machine break app add 412 main --temporary --condition '$rdi != 0'`, `--hardware` |
| A breakpoint that prints and goes on, as dprintf | `machine break app add 412 puts --print 'puts {$rdi:s}'` |
| A watchpoint: stop after a write | `machine watch app add 412 counter` |
| Catchpoints: exec, exit, fork, syscall, signal, fault, io | `machine catch app add signal SIGSEGV --in tid=57`, `machine catch app add syscall openat` |
| List, disable, delete | `machine points app`, `machine break app disable 2`, `machine break app delete 2` |

## Moving

| Action | Command |
|---|---|
| Run to the next hit, as continue | `machine run app --iters 0 --within 60s` |
| Run until a task reaches a place | `machine run app --until 'tid=412:at=init.c:106' --within 60s` |
| Step: stepi, nexti, step, next, finish | `machine step app 412`, `--over`, `--line`, `--line --over`, `--out` |
| Back to the newest hit, as reverse-continue | `machine run app --back` |
| Step back, as reverse-stepi and reverse-step | `machine step app 412 --back 3`, `machine step app 412 --back --line` |

## Changing

Writes are recorded, so a replay makes them at the same point.

| Action | Command |
|---|---|
| Write a register | `machine regs app 412 rax=0 rip=main+4` |
| Call a function in the task, or return from the current one now | `machine call app 412 compute 3`, `machine return app 412 0` |
| Write memory, or flip bits | `machine mem app 412 counter=42`, `machine mem app 412 'counter^=0x10'` |

## GDB inside the machine

GDB runs in the program's container with `machine exec`, attached to the
process. The image needs GDB. Use `--user` to run it as the process's user:
a container has no `CAP_SYS_PTRACE`. Checkpoint first, so every GDB session
can start from the same state.

A batch of commands chosen in advance; `exec run` runs the machine until GDB
exits and prints what it printed:

```bash
machine checkpoint app ready
machine exec app run --container app --user app \
    -c 'gdb -batch -ex "break handle_request" -ex continue -ex "bt 3" -p $(pidof app)'
```

To decide each command from what GDB printed, keep it attached with its
input on a named pipe. Each `exec start` prints the id of the process it
started. Use the id the third one, GDB's, printed as `<gdb-id>` below:

```bash
machine exec app run   --container app --user app -- mkfifo /tmp/gdb.in
machine exec app start --container app --user app -c 'sleep infinity > /tmp/gdb.in'
machine exec app start --container app --user app -c 'gdb -q -p $(pidof app) < /tmp/gdb.in'
machine exec app start --container app --user app -c 'printf "%s\n" "break handle_request" c "echo step-2\n" > /tmp/gdb.in'
machine run app --until '/output/exec/<gdb-id>/stdout~step-2' --within 60s
machine exec app proc <gdb-id> stdout
```

The docs page walks through a full session on ClickHouse.

## Docs

- Debugging, both ways: https://testflows.com/docs/machine/debugging.md
- Every command: https://testflows.com/docs/machine/commands.md
