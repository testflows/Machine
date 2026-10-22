# Debugging a Database in a Machine

Two database servers, each with a disk to debug it on: the server as it
ships, and the same server with its debug information.

| Example | Server | Debug information from |
|---|---|---|
| [clickhouse](clickhouse/README.md) | ClickHouse 26.8.31.2, C++ built by clang | `clickhouse-common-static-dbg`, 1.7GB of DWARF 5 |
| [postgresql](postgresql/README.md) | PostgreSQL 17.11, C built by gcc | `postgresql-17-dbgsym`, PGDG's debug package |

Each example builds an image in two targets, a disk of each, and runs the
server to a checkpoint where it answers queries. A client in the image sends
a query every second, so the query path runs without one being sent.

## 🐞 Three Ways to Debug

| Way | What it needs | What it is for |
|---|---|---|
| The machine's debugger | Nothing in the image | Stops, steps, prints and watches from outside the guest. The run records every stop: a checkpoint, a rewind or a replay returns to it |
| GDB, a command at a time | GDB in the image | `machine exec run` attaches GDB, runs its commands and detaches |
| GDB that stays attached | GDB in the image | One GDB session the commands of several `machine exec` feed |

The machine's debugger reads the program files on the machine's disk, and
GDB reads them in the container. Both read the same debug information.

## 📦 With and Without Debug Information

| On the disk | What a debugger shows |
|---|---|
| The program's symbol table | Function names in a backtrace, a stop at a function by its name, registers and memory |
| Its debug information too | Source lines, arguments, locals, types, `print` of an expression, a stop at `FILE:LINE`, a watch of a variable by its name |

A server as it ships keeps its symbol table, except a static function's
name, which a stripped program drops. The debug package of the server's
exact build gives the rest. A debugger takes it only when its build id is
the server's.

See [Debugging](https://testflows.com/docs/machine/#debugging) for every
command of the machine's debugger.
