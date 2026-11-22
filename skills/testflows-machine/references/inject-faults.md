# Inject faults: make the exact call fail

Contents: 1. Name the call. 2. Fail it. Every kind of fault: system calls,
the kernel, the disk, container resources, containers, the clock, the
network. 3. Check and prove.

A failure path runs when a call fails: a `connect` refused, a `write` that
hits a full disk, an allocation that returns nothing. On real hardware those
calls almost never fail, so the code behind them is rarely run. In the
machine you make the call the code points at fail, once, and see what the
program does. A replay fails the same calls with the same errors.

## 1. Name the call

From the code, say which call's failure leads to the bug, and which of its
calls: "the second `write` to the journal returns `ENOSPC`, and the error is
dropped". Name the program by the path the machine runs it from:
`machine -s work tasks <run>` shows it under EXE.

## 2. Fail it

`--in` says whose calls fail, and is required: a program's path, `comm=NAME`,
`tgid=N`, `tid=N` or `all`. `--after N` lets N matching calls through first,
and `--times 1` fails one call and no more.

```bash
# The second write of /app fails with ENOSPC, once
machine -s work inject bug add syscall write ENOSPC --in /app --after 1 --times 1
# Every connect of /app is refused
machine -s work inject bug add syscall connect ECONNREFUSED --in /app
# A read of /app waits 0 to 20ms first, then runs
machine -s work inject bug add syscall read --delay 0:20ms --in /app
# malloc in /app returns 0 without running, one call in a hundred
machine -s work inject bug add function malloc --return 0 --prob 0.01 --in /app
# A block request fails with EIO
machine -s work inject bug add disk --times 1 --in all
# A write to the data file stores nothing, and reports success
machine -s work inject bug add disk-lost --file /data/wal --times 1 --in all
```

Then run the program and read what it did:

```bash
machine -s work run bug --until 'console~(ERROR|OK)' --within 60s
machine -s work output bug -n :            # every line, so an early signature is not missed
machine -s work inject bug          # each fault, with the calls it matched and fired on
```

A fault on a program's path can be added before the program starts: it
matches from the program's exec on. A program that loads shared libraries
opens and reads them first, so count those calls in `--after`, or add a
fault on `openat` or `read` once the program is up.

## Every kind of fault

The examples use a run named `app`; add `-s <session>` to each command.

| Fault | Command |
|---|---|
| A system call returns an error, from the man page's errors at random | `machine inject app add syscall open,openat valid --pick 2 --in comm=cat` |
| A kernel allocation fails | `machine inject app add slab --after 50 --times 1 --in comm=cat` |
| A copy to or from a program's memory fails | `machine inject app add usercopy --times 1 --in /usr/bin/app` |
| A disk request fails, or never completes | `machine inject app add disk --times 1 --in all`, `machine inject app add disk-timeout --times 1 --in all` |
| A write is torn, lost, or its bytes changed, and reports success | `machine inject app add disk-torn --file '/data/**' --op write --times 1 --in all` |
| Any fault active 2s in every 7s of machine time | `machine inject app add disk --on 2s --off 5s --in all` |
| Stop where a fault fires, or at a file's I/O | `machine catch app add fault openat`, `machine catch app add io --file '/db/*/data.bin' --op write` |
| List, delete, clear | `machine inject app`, `machine inject app delete 2`, `machine inject app clear` |

**Resources of a container**

| Fault | Command |
|---|---|
| Memory limit: past it, its OOM killer | `machine containers app memory db 64m --for 30s` |
| Processes: past it, fork fails with EAGAIN | `machine containers app pids db 20` |
| Open files, ephemeral ports | `machine containers app files api 64`, `machine containers app ports api 40000-40009` |
| CPU | `machine containers app throttle db --cpus 0.1` |

**Containers**

| Fault | Command |
|---|---|
| Kill it, and restart it after 5s | `machine containers app kill db --restart 5s` |
| Crash it: its volumes lose what was not synced | `machine containers app crash db --restart 0` |
| Pause it | `machine containers app pause db --for 5s` |

**The clock**

| Fault | Command |
|---|---|
| Jump the machine's date, or set it | `machine clock app jump +59m50s`, `machine clock app set 2038-01-19T03:13:30Z` |
| Jump, then undo after 10s of machine time | `machine clock app jump -30m --for 10s` |
| One container's date, drift or slew | `machine clock app jump +2s --container db`, `machine clock app drift +200ppm --container db` |
| Steps as NTP makes them, from a seed | `machine clock app jitter -200ms:200ms --every 10m --seed 42` |
| A leap second at the next midnight | `machine clock app leap insert` |
| Back to machine time | `machine clock app reset` |

**The network**

| Fault | Command |
|---|---|
| Delay, lose, duplicate, corrupt, reorder a service's packets | `machine net app delay db 100ms --jitter 20ms`, `machine net app loss db 10%` |
| Partition two services | `machine net app partition db api` |
| Each link and its faults | `machine net app` |
| Hold a container's packets, then release them | `machine net app hold db`, `machine net app release db` |
| Reset its TCP connections | `machine net app reset db` |
| Take the faults off its links, or every link | `machine net app heal db`, `machine net app heal` |

To capture a link's packets, run the machine's own tcpdump on the link
`machine containers app` lists under `LINK`, and write under
`/.machine/output/keep`, which the run keeps as output:
`machine exec app start -- tcpdump --immediate-mode -U -i <link> -w /.machine/output/keep/db.pcap`

## 3. Check and prove

The failure must be the one the bug causes, such as the dropped error or the
torn record, not just any failure. Then add the same fault to a run of the
fix: it must now handle the failure.

## Commands and docs

- [`machine inject add`](https://testflows.com/docs/machine/commands.md#machine-inject-add),
  [`machine inject add syscall`](https://testflows.com/docs/machine/commands.md#machine-inject-add-syscall),
  [`machine inject add function`](https://testflows.com/docs/machine/commands.md#machine-inject-add-function),
  [`machine inject add disk`](https://testflows.com/docs/machine/commands.md#machine-inject-add-disk),
  [`machine tasks`](https://testflows.com/docs/machine/commands.md#machine-tasks)
- Fault injection: system calls, functions, memory, resources:
  https://testflows.com/docs/machine/fault-injection.md
- Container faults: https://testflows.com/docs/machine/container-faults.md
- Clock faults: https://testflows.com/docs/machine/clock-faults.md
- Network faults: https://testflows.com/docs/machine/network-faults.md
