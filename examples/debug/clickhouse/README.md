# clickhouse

A ClickHouse 26.8.31.2 server in a machine, with a client that sends a query
every second. The machine's own debugger stops the server's threads from
outside the guest, and GDB in the image attaches to the server from inside.
The run records every stop, so a checkpoint, a rewind or a replay returns to
it.

| File | What it is |
|---|---|
| [Dockerfile](Dockerfile) | The image, in two targets: the server as it ships, and with its debug information |
| [start.sh](start.sh) | The entrypoint. It starts the server, prints its version, the commit it was built from and a link to that source, then `clickhouse: ready` once the server answers, then sends a query every second |
| [gdb.sh](gdb.sh) | Attaches GDB to the server |
| [session.gdb](session.gdb) | A GDB session to show: a breakpoint, `stepi`, `next`, `finish`, `hbreak`, a hardware `watch` and `detach` |

## 📦 1. The Image

ClickHouse 26.8.31.2 on the server's own image, Ubuntu 22.04.

```bash
cd debug/clickhouse
docker build --target server -t clickhouse-server:26.8.31.2 .
docker build --target debug -t clickhouse-debug:26.8.31.2 .
```

| Image | Size | What a debugger shows |
|---|---|---|
| `clickhouse-server` | 1.3GB | Every function by name, from the server binary's own symbol table |
| `clickhouse-debug` | 6.5GB | Also arguments, locals, types and source lines, from `clickhouse-common-static-dbg` of the same build: 1.7GB of DWARF 5 |

## 📖 2. The Source

The server is built from ClickHouse commit
[bb452427e21b](https://github.com/ClickHouse/ClickHouse/tree/bb452427e21b3693aef652d597bb7fe4437b088a),
tagged [v26.8.31.2-lts](https://github.com/ClickHouse/ClickHouse/tree/v26.8.31.2-lts).
A debugger's source lines are that commit's: `executeQuery.cpp:3636` below is
[executeQuery.cpp#L3636](https://github.com/ClickHouse/ClickHouse/blob/bb452427e21b3693aef652d597bb7fe4437b088a/src/Interpreters/executeQuery.cpp#L3636).
The server says so itself, in `system.build_options`, and `start.sh` prints
it on the console as the server comes up:

```text
clickhouse: 26.8.31.2, commit bb452427e21b3693aef652d597bb7fe4437b088a
clickhouse: source https://github.com/ClickHouse/ClickHouse/tree/bb452427e21b3693aef652d597bb7fe4437b088a
clickhouse: ready
```

## 💾 3. The Disks

```bash
machine disks build --image clickhouse-server:26.8.31.2 clickhouse-server
machine disks build --image clickhouse-debug:26.8.31.2 clickhouse-debug
```

## 🚀 4. Up to the Server

```bash
machine create ch --disk clickhouse-debug --mem 8192 --cpus 2
machine run ch --until 'console~clickhouse: ready'
machine checkpoint ch ready
machine tasks ch
```

```text
    TID     TGID  COMM             CPU  NEXT    KIND    FOCUS  HOLD  YIELD  PIN  SLICE  EXE
    212      212  clickhouse-serv               user                                    /usr/bin/clickhouse
    ...
```

The server is tgid 212 here. A query runs in one of its threads, which a stop
names.

## 🐞 5. The Machine's Debugger

Run the server until a query reaches a line of `executeQuery`, and keep a
checkpoint there:

```bash
machine run ch --until 'tgid=212:at=executeQuery.cpp:3636'
machine checkpoint ch query
machine backtrace ch 295
```

```text
✔ Reached DB::executeQuery+50, executeQuery.cpp:3636, in tid 295
#  ADDRESS         FUNCTION
0  0x1d386372      DB::executeQuery+50 (query={__data_ = 0x7f71fbd5f3c0 "SELECT sum(number) FROM numbers(1000000)", __size_ = 40}, context={__ptr_ = 0x7f71fba96a00, __cntrl_ = 0x7f71fd433800}, flags={internal = false, ...}, stage=Complete)
1  0x1ed12ec3      DB::TCPHandler::runImpl+25475 (this=0x7f72ec29de00)
2  0x1ed37d64      DB::TCPHandler::run+36 (this=<Not read: this, it was in $rdi, which a call does not keep.>)
3  0x24d40607      Poco::Net::TCPServerConnection::start+7 (this=<Not read: this, it was in $rdi, which a call does not keep.>)
4  0x24d40af5      Poco::Net::TCPServerDispatcher::run+309 (this=0x7f71c8833600)
5  0x24d03f5c      Poco::PooledThread::run+412 (this=0x7f72eccce680)
...
```

The server is built with `-O2`, as it ships. `query` is in two registers, put
together as one value, and a value the compiler keeps nowhere at an
instruction is `optimized out`, as GDB says it. Functions are named short, as
GDB names them; `machine --signatures backtrace` names them by their whole
signature.

```bash
machine locals ch 295 --args
machine print ch 295 query.__data_
```

```text
query = {__data_ = 0x7f71fbd5f3c0 "SELECT sum(number) FROM numbers(1000000)", __size_ = 40}
context = {__ptr_ = 0x7f71fba96a00, __cntrl_ = 0x7f71fd433800}
flags = {internal = false, user_initiated = <optimized out>, distributed_backup_restore = <optimized out>, parse_query_from_initial_buffer = <optimized out>, background = <optimized out>}
stage = Complete
query.__data_ = 0x7f71fbd5f3c0 "SELECT sum(number) FROM numbers(1000000)"
```

Watch a local. `ast` holds the parsed query; the run stops after each write
of its pointer, the second where the parser stores the tree:

```bash
machine run ch --until 'tid=295:write=ast.px'
machine run ch --until 'tid=295:write=ast.px'
machine step ch 295 --out
machine print ch 295 '*ast.px'
```

```text
✔ Written ast.px at DB::executeQuery+148, vector.h:582, in tid 295
Value = 0, 0x0
✔ Written ast.px at DB::executeQueryImpl+2545, intrusive_ptr.hpp:76, in tid 295
Value = 140126035091776, 0x7f71a28ba140
✔ Returned to DB::executeQuery+559, executeQuery.cpp:3649, in tid 295
Value returned in rax = 4, 0x4
*ast.px = {<DB::TypePromotion<DB::IAST>> = {}, _vptr$IAST = 0x36d2750, children = {...}, ...}
```

The vtable names the tree's class, and a cast reads it as that class:

```bash
machine symbols ch 295 0x36d2750
machine print ch 295 '*(DB::ASTSelectWithUnionQuery *)ast.px'
```

```text
ADDRESS    SIZE  KIND      NAME                                       SOURCE  FILE
0x36d2750   168  variable  vtable for DB::ASTSelectWithUnionQuery+16          /usr/bin/clickhouse
*(DB::ASTSelectWithUnionQuery *)ast.px = {<DB::ASTQueryWithOutput> = {<DB::IAST> = {...}, ...}, ...}
```

### Without debug information

On `clickhouse-server` the same stop names every frame, from the symbol
table, with no arguments or lines. The arguments are in the registers the
ABI passes them in: `query` in `rsi` and `rdx`, a pointer and a length:

```bash
machine create cs --disk clickhouse-server --mem 3072 --cpus 2
machine run cs --until 'console~clickhouse: ready'
machine run cs --until 'tgid=211:at=DB::executeQuery'
machine backtrace cs 295
machine locals cs 295 --args
machine regs cs 295 rsi rdx
machine mem cs 295 '$rsi' s
```

```text
✔ Reached DB::executeQuery, in tid 295
#  ADDRESS         FUNCTION                                 SOURCE  FILE
0  0x1d386340      DB::executeQuery                                 /usr/bin/clickhouse
1  0x1ed12ec3      DB::TCPHandler::runImpl+25475                    /usr/bin/clickhouse
2  0x1ed37d64      DB::TCPHandler::run+36                           /usr/bin/clickhouse
3  0x24d40607      Poco::Net::TCPServerConnection::start+7          /usr/bin/clickhouse
4  0x24d40af5      Poco::Net::TCPServerDispatcher::run+309          /usr/bin/clickhouse
∅ No debug information for this frame
REGISTER  VALUE           NATURAL
rsi       0x7f0c4f05bb80  139690842110848
rdx       0x28            40
   0x7f0c4f05bb80:  "SELECT sum(number) FROM numbers(1000000)"
```

A global is at its symbol's address, with no type. A cast gives it one, and
a watch takes it. `CurrentMetrics::values` is the server's array of gauges,
and its first is the count of queries running:

```bash
machine rewind cs to ^ready
machine run cs --until 'tgid=211:write=*(long *)&CurrentMetrics::values'
```

```text
✔ Written *(long *)&CurrentMetrics::values at DB::ProcessList::insert+8266, in tid 295
Value = 1, 0x1
```

## 🔧 6. GDB, a Command at a Time

`machine exec run` runs GDB in the server's container. `gdb.sh` attaches to
the server, runs its commands and detaches:

```bash
machine rewind cs to ^ready
machine exec cs run --container app --user clickhouse -- /examples/debug/gdb.sh \
    -ex 'break DB::executeQuery' -ex continue -ex 'bt 3'
```

```text
Breakpoint 1 at 0x1d386340 (3 locations)
#0  0x000000001d386340 in DB::executeQuery(std::__1::basic_string_view<char, std::__1::char_traits<char> >, std::__1::shared_ptr<DB::Context>, DB::QueryFlags, DB::QueryProcessingStage::Enum) ()
#1  0x000000001ed12ec3 in DB::TCPHandler::runImpl() ()
#2  0x000000001ed37d64 in DB::TCPHandler::run() ()
[Inferior 1 (process 7) detached]
```

`-x` gives `gdb.sh` a file of commands, such as the session in the image:

```bash
machine exec cs run --container app --user clickhouse -- /examples/debug/gdb.sh -x /examples/debug/session.gdb
```

GDB runs as `clickhouse`, the server's user. The container has no
`CAP_SYS_PTRACE`, so only a process of the same user can attach. On
`clickhouse-debug` GDB reads the server's 1.7GB of debug information first:
it holds about 3.8GB, so give the machine `--mem 12288`, and the first
breakpoint takes about 13 minutes. The machine's debugger reads a unit of it
at a time and needs no memory of the machine.

## 🔗 7. GDB That Stays Attached

For one GDB session that several commands feed, see
[A session that stays attached](https://testflows.com/docs/machine/#a-session-that-stays-attached).

## 🔁 8. Again, From the Same State

The checkpoints hold the machine as it was. Rewind to one, and the next
session starts from the same instruction, with the same queries:

```bash
machine rewind ch to ^query
```

## 📌 Good to Know

- The debug package must be of the server's exact build. A debugger takes
  `/usr/lib/debug/usr/bin/clickhouse.debug` only when its build id is the
  server's.
- The machine's debugger needs nothing in the image. GDB, `gdb.sh` and
  `procps` are there for the GDB sections alone.
- A run holds 3 hardware breakpoints and watchpoints. `run --until write=`,
  `machine watch` and GDB's `hbreak` and `watch` share them. One more makes
  GDB say `Couldn't write debug register: No space left on device`.
