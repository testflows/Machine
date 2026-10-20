# clickhouse-gdb

A ClickHouse server with GDB beside it, in a machine. GDB attaches to the
server in the machine as it does on any Linux host: breakpoints, `stepi`,
`next`, `finish`, `hbreak` and `watch` all work. The run records the session,
so a replay repeats it, and a checkpoint before it lets you run it again from
the same state.

| File | What it is |
|---|---|
| [Dockerfile](Dockerfile) | The image, in two targets: with and without ClickHouse's debug information |
| [start.sh](start.sh) | The entrypoint. It starts the server, prints its version, the commit it was built from and a link to that source, then `clickhouse-gdb: ready` once the server answers, then sends a query every second |
| [gdb.sh](gdb.sh) | Attaches GDB to the server |
| [session.gdb](session.gdb) | A session to show: a breakpoint, `stepi`, `next`, `finish`, `hbreak`, a hardware `watch` and `detach` |

## 📦 1. The Image

ClickHouse 26.8.31.2, on the server's own image: Ubuntu 22.04, whose GDB 12
reads the DWARF 5 the debug information is in.

```bash
cd gdb/clickhouse
docker build --target gdb -t clickhouse-gdb:26.8.31.2 .
docker build --target gdb-debug -t clickhouse-gdb-debug:26.8.31.2 .
```

| Image | Size | What GDB shows |
|---|---|---|
| `clickhouse-gdb` | 1.3GB | Function names, from the server binary's own symbol table |
| `clickhouse-gdb-debug` | 6.5GB | Also arguments, locals, types and source lines, from `clickhouse-common-static-dbg` |

The same breakpoint in each:

```text
clickhouse-gdb
#0  0x000000001d386340 in DB::executeQuery(std::__1::basic_string_view<char, ...
#1  0x000000001ed12ec3 in DB::TCPHandler::runImpl() ()

clickhouse-gdb-debug
#0  DB::executeQuery (query=..., context=..., flags=..., stage=DB::QueryProcessingStage::Complete) at ./src/Interpreters/executeQuery.cpp:3636
#1  0x000000001ed12ec3 in DB::TCPHandler::runImpl (this=0x74d66cf61000) at ./src/Server/TCPHandler.cpp:945
query = {..., __data_ = 0x74d66d0e4470 "SELECT sum(number) FROM numbers(1000000)", __size_ = 40}
```

## 📖 2. The Source

The server is built from ClickHouse commit
[bb452427e21b](https://github.com/ClickHouse/ClickHouse/tree/bb452427e21b3693aef652d597bb7fe4437b088a),
tagged [v26.8.31.2-lts](https://github.com/ClickHouse/ClickHouse/tree/v26.8.31.2-lts).
GDB's source lines are that commit's: the breakpoint above is
[executeQuery.cpp:3636](https://github.com/ClickHouse/ClickHouse/blob/bb452427e21b3693aef652d597bb7fe4437b088a/src/Interpreters/executeQuery.cpp#L3636).
The server says so itself, in `system.build_options`, and `start.sh` prints it
on the console as the server comes up:

```text
clickhouse-gdb: 26.8.31.2, commit bb452427e21b3693aef652d597bb7fe4437b088a
clickhouse-gdb: source https://github.com/ClickHouse/ClickHouse/tree/bb452427e21b3693aef652d597bb7fe4437b088a
clickhouse-gdb: ready
```

For GDB to show a file's lines, put the file in the container under `/src`, as
the debug information names it, and tell GDB to look there with `directory /`:

```bash
curl -fsSLO https://raw.githubusercontent.com/ClickHouse/ClickHouse/bb452427e21b3693aef652d597bb7fe4437b088a/src/Interpreters/executeQuery.cpp
machine put ch executeQuery.cpp /src/Interpreters/executeQuery.cpp --container app
```

```
/exec run --container app --user clickhouse -- /examples/gdb/gdb.sh -ex 'directory /' -ex 'break DB::executeQuery' -ex continue -ex list
```

## 💾 3. The Disk

```bash
machine disks build --image clickhouse-gdb:26.8.31.2 clickhouse-gdb
```

For the debug information, build `clickhouse-gdb-debug:26.8.31.2` into a disk
of that name, and use it in place of `clickhouse-gdb` below, with
`--mem 12288`. GDB holds about 3.8GB once it has read the server's 1.7GB of
debug information, and a 4GB machine kills it. Reading it takes about 19
minutes of machine time, 13 minutes on the clock, before the first breakpoint;
the server's symbol table alone takes seconds.

## 🚀 4. Up to the Server

```bash
machine create ch --disk clickhouse-gdb --mem 4096 --cpus 2
machine control ch
```

At the control prompt, run the machine until the server answers, and keep a
checkpoint there:

```
/run until 'console~clickhouse-gdb: ready'
/checkpoint ready
```

## 🐞 5. GDB

```
/exec run --container app --user clickhouse -- /examples/gdb/gdb.sh -ex 'break DB::executeQuery' -ex continue -ex 'bt 3'
```

GDB attaches, waits for the next query to reach `DB::executeQuery`, prints the
stack and detaches. `gdb.sh` runs the commands it is given, then detaches.
`-x` gives it a file of commands, such as the session in the image:

```
/exec run --container app --user clickhouse -- /examples/gdb/gdb.sh -x /examples/gdb/session.gdb
```

GDB runs as `clickhouse`, the server's user. The container has no
`CAP_SYS_PTRACE`, so only a process of the same user can attach.

## 🔁 6. Again, From the Same State

The checkpoint holds the machine as it was before GDB attached. Rewind to it
and the next session starts from the same instruction, with the same queries:

```
/rewind to ^ready
```

## 📌 Good to Know

- A run holds 3 hardware breakpoints and watchpoints. GDB's `hbreak` and
  `watch` share them with `machine break` and `machine watch`. One more
  makes GDB say `Couldn't write debug register: No space left on device`
  at the `continue` that inserts it. GDB's `break` takes none of them.
- GDB on the host compares line by line with GDB in the machine. Run the
  image with `docker run --cap-add SYS_PTRACE` and the same `gdb.sh` as root.
- The machine has a debugger of its own, which needs nothing on the disk:
  see [Debugging](https://testflows.com/docs/machine/#debugging).
- For a GDB that stays attached between commands, see
  [Input for a program you started](https://testflows.com/docs/machine/#input-for-a-program-you-started).
