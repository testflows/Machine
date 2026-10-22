# postgresql

A PostgreSQL 17.11 server in a machine, with a client session that sends a
query every second. The machine's own debugger stops the session's backend
from outside the guest, and GDB in the image attaches to it from inside. The
run records every stop, so a checkpoint, a rewind or a replay returns to it.

| File | What it is |
|---|---|
| [Dockerfile](Dockerfile) | The image, in two targets: the server as it ships, and with its debug information |
| [start.sh](start.sh) | The entrypoint. It starts the server, prints its version, starts one client session that sends a query every second, then prints `postgresql: ready` once that session's backend runs |
| [gdb.sh](gdb.sh) | Attaches GDB to the session's backend |

## 📦 1. The Image

PostgreSQL 17.11 on the official image, Debian 13, from PGDG's packages.

```bash
cd debug/postgresql
docker build --target server -t postgresql-server:17.11 .
docker build --target debug -t postgresql-debug:17.11 .
```

| Image | What a debugger shows |
|---|---|
| `postgresql-server` | The server binary's exported functions by name. Debian strips its static ones, as `exec_simple_query` |
| `postgresql-debug` | Every function, its arguments, locals, types and source lines, from PGDG's `postgresql-17-dbgsym` of the same build |

## 💾 2. The Disks

```bash
machine disks build --image postgresql-server:17.11 postgresql-server
machine disks build --image postgresql-debug:17.11 postgresql-debug
```

## 🚀 3. Up to the Server

```bash
machine create pg --disk postgresql-debug --mem 2048 --cpus 2
machine run pg --until 'console~postgresql: ready'
machine checkpoint pg ready
machine tasks pg
```

```text
    TID     TGID  COMM             CPU  NEXT    KIND    FOCUS  HOLD  YIELD  PIN  SLICE  EXE
    214      214  postgres                      user                                    /usr/lib/postgresql/17/bin/postgres
    ...
    289      289  psql                          user                                    /usr/lib/postgresql/17/bin/psql
    292      292  postgres                      user                                    /usr/lib/postgresql/17/bin/postgres
```

The client session is `psql`, and its backend is the `postgres` task after
it: tid 292 here. The commands below name it.

## 🐞 4. The Machine's Debugger

Run the backend to the function that runs a simple query, and keep a
checkpoint there:

```bash
machine run pg --until 'tid=292:at=exec_simple_query'
machine checkpoint pg query
machine backtrace pg 292
```

```text
✔ Reached exec_simple_query, postgres.c:1019, in tid 292
#  ADDRESS         FUNCTION                                                                                                        SOURCE
0  0x556ccfc7d110  exec_simple_query (query_string=0x556cd01e6838 "SELECT count(*) FROM generate_series(1, 100000);")            postgres.c:1019
1  0x556ccfc7f060  PostgresMain+6128 (dbname=<Not read: dbname, optimized out.>, username=<Not read: username, optimized out.>)  postgres.c:4787
2  0x556ccfc79843  BackendMain+67 (startup_data=<Not read: startup_data, optimized out.>, ...)                                  backend_startup.c:105
...
6  0x556ccf8f7d05  main+533 (argc=1, argv=0x556cd018d580)                                                                       main.c:199
```

The server is built with `-O2`, as it ships. A value the compiler keeps
nowhere at an instruction is `optimized out`, as GDB says it.

Run into the parser, finish it, and read what it parsed:

```bash
machine run pg --until 'tid=292:at=pg_parse_query'
machine step pg 292 --out
machine step pg 292 --line --over
machine step pg 292 --line --over
machine print pg 292 '*parsetree_list'
machine print pg 292 '*(RawStmt *)parsetree_list->elements[0].ptr_value'
machine print pg 292 '((RawStmt *)parsetree_list->elements[0].ptr_value)->stmt->type'
```

```text
✔ Reached pg_parse_query, postgres.c:617, in tid 292
✔ Returned to exec_simple_query+159, postgres.c:1074, in tid 292
Value returned in rax = 93925836486432, 0x556cd01e7720
✔ Stepped 1 line to exec_simple_query+162, postgres.c:1071, in tid 292
✔ Stepped 1 line to exec_simple_query+165, postgres.c:1074, in tid 292
*parsetree_list = {type = T_List, length = 1, max_length = 5, elements = 0x556cd01e7738, initial_elements = {}}
*(RawStmt *)parsetree_list->elements[0].ptr_value = {type = T_RawStmt, stmt = 0x556cd01e75f0, stmt_location = 0, stmt_len = 47}
((RawStmt *)parsetree_list->elements[0].ptr_value)->stmt->type = T_SelectStmt
```

The parser returns the list in `rax`. The two steps move it to where the
debug information says `parsetree_list` is, and the line steps of an
optimized function go back and forth, as GDB's do.

Watch a global. From the checkpoint, the run stops after the backend writes
`debug_query_string`, the query it runs:

```bash
machine rewind pg to ^ready
machine run pg --until 'tid=292:write=debug_query_string'
machine run pg --until 'tid=292:write=debug_query_string'
machine print pg 292 debug_query_string
```

```text
✔ Written debug_query_string at exec_simple_query+1076, postgres.c:1388, in tid 292
Value = 0, 0x0
✔ Written debug_query_string at exec_simple_query+67, postgres.c:1034, in tid 292
Value = 93925836482616, 0x556cd01e6838
debug_query_string = 0x556cd01e6838 "SELECT count(*) FROM generate_series(1, 100000);"
```

The first write ends one query and the second begins the next.

### Without debug information

On `postgresql-server` the same session stops at an exported function. A
static one is not found, and its frames are `??`:

```bash
machine create pgs --disk postgresql-server --mem 2048 --cpus 2
machine run pgs --until 'console~postgresql: ready'
machine run pgs --until 'tid=288:at=exec_simple_query'
machine run pgs --until 'tid=288:at=pg_parse_query'
machine backtrace pgs 288
machine mem pgs 288 '$rdi' s
```

```text
✗ No symbol: exec_simple_query
  Find one by a pattern machine symbols pgs 288 --regex exec_simple_query
✔ Reached pg_parse_query, in tid 288
 #  ADDRESS         FUNCTION                     SOURCE  FILE
 0  0x563658989bc0  pg_parse_query                       /usr/lib/postgresql/17/bin/postgres
 1  0x56365898a1af  ??                                   /usr/lib/postgresql/17/bin/postgres
 2  0x56365898c060  PostgresMain+6128                    /usr/lib/postgresql/17/bin/postgres
 3  0x563658986843  BackendMain+67                       /usr/lib/postgresql/17/bin/postgres
 4  0x5636588ef6de  postmaster_child_launch+158          /usr/lib/postgresql/17/bin/postgres
 5  0x5636588f3021  ??                                   /usr/lib/postgresql/17/bin/postgres
 6  0x5636588f50c0  PostmasterMain+3168                  /usr/lib/postgresql/17/bin/postgres
 7  0x563658604d05  main+533                             /usr/lib/postgresql/17/bin/postgres
   0x563658ef3838:  "SELECT count(*) FROM generate_series(1, 100000);"
```

Frame 1 is `exec_simple_query` and frame 5 `ServerLoop`, which the debug
package names.

The query is the function's first argument, in `rdi`, and `mem` reads it as a
string.

## 🔧 5. GDB, a Command at a Time

`machine exec run` runs GDB in the server's container. `gdb.sh` attaches to
the backend, runs its commands and detaches:

```bash
machine rewind pg to ^ready
machine exec pg run --container app --user postgres -- /examples/debug/gdb.sh \
    -ex 'break exec_simple_query' -ex continue -ex 'bt 3' -ex 'print query_string'
```

```text
Breakpoint 1, exec_simple_query (query_string=0x556cd01e6838 "SELECT count(*) FROM generate_series(1, 100000);") at ./build/../src/backend/tcop/postgres.c:1019
#0  exec_simple_query (query_string=0x556cd01e6838 "SELECT count(*) FROM generate_series(1, 100000);") at ./build/../src/backend/tcop/postgres.c:1019
#1  0x0000556ccfc7f060 in PostgresMain (dbname=<optimized out>, username=<optimized out>) at ./build/../src/backend/tcop/postgres.c:4787
#2  0x0000556ccfc79843 in BackendMain (startup_data=<optimized out>, startup_data_len=<optimized out>) at ./build/../src/backend/tcop/backend_startup.c:105
$1 = 0x556cd01e6838 "SELECT count(*) FROM generate_series(1, 100000);"
[Inferior 1 (process 85) detached]
```

GDB and the machine's debugger read the same debug information, and say
the same frames, arguments and lines.

GDB runs as `postgres`, the server's user. The container has no
`CAP_SYS_PTRACE`, so only a process of the same user can attach. The process
id GDB says is the backend's in the container.

## 🔗 6. GDB That Stays Attached

For one GDB session that several commands feed, see
[A session that stays attached](https://testflows.com/docs/machine/#a-session-that-stays-attached).
Its steps work here with `gdb.sh` and the `postgres` user.

## 🔁 7. Again, From the Same State

The checkpoints hold the machine as it was. Rewind to one, and the next
session starts from the same instruction, with the same queries:

```bash
machine rewind pg to ^query
```

## 📌 Good to Know

- The debug package must be of the server's exact build. A debugger takes
  it only when its build id is the server's.
- The machine's debugger needs nothing in the image. GDB, `gdb.sh` and
  `procps` are there for the GDB sections alone.
- A run holds 3 hardware breakpoints and watchpoints, which `run --until
  write=` and GDB's `watch` share.
