# clickhouse-gdb

A ClickHouse server with GDB beside it, in a machine. GDB attaches to the
server in the machine as it does on any Linux host: breakpoints, `stepi`,
`next`, `finish`, `hbreak` and `watch` all work. The run records the session,
so a replay repeats it, and a checkpoint before it lets you run it again from
the same state.

| File | What it is |
|---|---|
| [Dockerfile](Dockerfile) | The image, in two targets: with and without ClickHouse's debug information |
| [start.sh](start.sh) | The entrypoint. It starts the server, prints `clickhouse-gdb: ready` once the server answers, then sends a query every second |
| [gdb.sh](gdb.sh) | Attaches GDB to the server |
| [session.gdb](session.gdb) | A session to show: a breakpoint, `stepi`, `next`, `finish`, `hbreak`, a hardware `watch` and `detach` |

## 📦 1. The Image

```bash
cd gdb/clickhouse
docker build --target gdb -t clickhouse-gdb:24.8.14.39 .
docker build --target gdb-debug -t clickhouse-gdb-debug:24.8.14.39 .
```

| Image | Size | What GDB shows |
|---|---|---|
| `clickhouse-gdb` | 0.9GB | Function names, from the server binary's own symbol table |
| `clickhouse-gdb-debug` | 3.3GB | Also arguments, locals, types and source lines, from `clickhouse-common-static-dbg` |

The same breakpoint in each:

```text
clickhouse-gdb
#0  0x00000000113b9c40 in DB::executeQuery(std::__1::basic_string<char, ...
#1  0x000000001253ec64 in DB::TCPHandler::runImpl() ()

clickhouse-gdb-debug
#0  DB::executeQuery (query=..., context=..., flags=..., stage=DB::QueryProcessingStage::Complete) at ./build_docker/./src/Interpreters/executeQuery.cpp:1393
#1  0x000000001253ec64 in DB::TCPHandler::runImpl (this=0x705897567000) at ./build_docker/./src/Server/TCPHandler.cpp:521
```

The image is Ubuntu 26.04 with the server copied in. The server's own image is
Ubuntu 20.04, and its GDB 9.2 does not read the DWARF 5 that the debug
information is in. The server binary is static, so it runs on any base.

## 💾 2. The Disk

```bash
machine disks build --image clickhouse-gdb:24.8.14.39 clickhouse-gdb
```

For the debug information, build `clickhouse-gdb-debug:24.8.14.39` into a disk
of that name, and use it in place of `clickhouse-gdb` below.

## 🚀 3. Up to the Server

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

## 🐞 4. GDB

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

## 🔁 5. Again, From the Same State

The checkpoint holds the machine as it was before GDB attached. Rewind to it
and the next session starts from the same instruction, with the same queries:

```
/rewind to ^ready
```

## 📌 Good to Know

- A run holds 3 hardware breakpoints and watchpoints. GDB's `hbreak` and
  `watch` share them with `machine break` and `machine watch`.
- GDB on the host compares line by line with GDB in the machine. Run the
  image with `docker run --cap-add SYS_PTRACE` and the same `gdb.sh` as root.
- For a GDB that stays attached between commands, see
  [Input for a program you started](https://testflows.com/docs/machine/#input-for-a-program-you-started).
