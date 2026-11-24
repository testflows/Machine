# Jepsen and etcd

A [Jepsen](https://jepsen.io) test of a three-node [etcd](https://etcd.io)
cluster, run inside a machine. The control node checks a compare-and-set
register for linearizability while Jepsen partitions the nodes into random
halves, 5 seconds out of every 10.

| Service | What it runs |
|---|---|
| `n1`, `n2`, `n3` | etcd and sshd. Jepsen starts and stops etcd over SSH, and partitions the nodes with iptables |
| `control` | the test, `control/src/jepsen/etcd_machine.clj`, built into one jar |

A machine reaches no network, so everything the test needs is in the images:
etcd on the nodes, and the test with all its dependencies on the control node.

## Run it

The machine has 2 vCPUs and 4G of memory, so the test runs in a session of
2 vCPUs and 6G. A session that size needs the
[Starter plan or higher](https://testflows.com/machine/pricing/).

From `examples/`, create the session, build the two images for
`linux/amd64`, then the disk, then run the test to Jepsen's verdict:

```bash
machine sessions create jepsen --cpus 2 --mem 6G
cd compose/jepsen-etcd
docker build --platform linux/amd64 -t jepsen-node:local node
docker build --platform linux/amd64 -t jepsen-control:local control
machine disks build --compose . jepsen
machine create jepsen --disk jepsen --cpus 2 --mem 4096
machine run jepsen --until 'console~(Everything looks good|Analysis invalid|Jepsen broke)' --within 10m
machine output jepsen -n : | grep -E ':linear \{|:valid\? (true|false)\}|Everything looks good|Analysis invalid'
machine stop jepsen
```

The test took 186 seconds of machine time and 188 seconds of real time, and
ended with Jepsen's verdict:

```text
control |  :linear {:valid? true,
control |  :valid? true}
control | Everything looks good! ヽ(‘ー`)ノ
```

The whole log of that run is [logs/pass.log](logs/pass.log): what each
service printed, every operation Jepsen ran and the analysis.

## See Jepsen catch a bug

[stale-reads.patch](stale-reads.patch) runs the test with
`--reads serializable`. A serializable read is answered by the node from its
own copy, without the leader, so a node behind the majority answers with a
value the majority has already replaced. Apply the patch, then build and run
as above under another name:

```bash
git apply stale-reads.patch
machine disks build --compose . jepsen-stale
machine create jepsen-stale --disk jepsen-stale --cpus 2 --mem 4096
machine run jepsen-stale --until 'console~(Everything looks good|Analysis invalid|Jepsen broke)' --within 10m
machine output jepsen-stale -n : | grep -E ':linear \{|:valid\? (true|false)\}|Everything looks good|Analysis invalid'
machine stop jepsen-stale
git apply -R stale-reads.patch
```

In this run, a write of 0 completed and the next read returned 1, the value
it replaced:

```text
control | INFO [2026-03-01 00:02:11,247] jepsen worker 2 - jepsen.print 2	:ok	:write	0
control | INFO [2026-03-01 00:02:11,257] jepsen worker 1 - jepsen.print 1	:invoke	:read	nil
control | INFO [2026-03-01 00:02:11,276] jepsen worker 1 - jepsen.print 1	:ok	:read	1
```

Jepsen found no order of the operations that a single register could have
produced:

```text
control |  :linear {:valid? false,
control |  :valid? false}
control | Analysis invalid! (ﾉಥ益ಥ）ﾉ ┻━┻
```

The whole log of that run is [logs/fail.log](logs/fail.log).

## Replay it

A run replays exactly: the same operations, the same values at the same
times, and the same verdict. Fork the run at its first checkpoint and replay
the recorded entries on the branch:

```bash
machine fork jepsen-stale --at 1 --name jepsen-stale-replay
machine replay jepsen-stale-replay
diff <(machine output jepsen-stale -n :) <(machine output jepsen-stale-replay -n :) && echo identical
machine stop jepsen-stale-replay
```

```text
✔ Forked jepsen-stale/jepsen-stale-replay
  From checkpoint 1 of jepsen-stale, nothing replayed
✔ Replayed 460379 entries to entry 460380. Converged.
identical
```

The replay reproduces the failing history line for line, so the stale read
is there to debug every time. The passing run replays the same way.

Replay is also more than five times faster than the run it replays. Recording the
same test again from the same checkpoint took as long as the first time, so
the difference is the cost of recording, not a warm cache:

| Run | Machine time | Recorded in | Replayed in |
|---|---|---|---|
| `jepsen` | 186 s | 188 s | 33 s |
| `jepsen-stale` | 177 s | 180 s | 32 s |

## Run it on the host

The project also runs on the host, from the same directory, which is the
quicker way to change the test:

```bash
docker compose up --abort-on-container-exit
```

## Notes

- The nodes use `iptables-legacy`. The machine's kernel has the legacy
  tables, not nftables, which Debian's `iptables` uses by default.
- The control node makes an SSH key at its start and shares it with the
  nodes through the `ssh-keys` volume, so no key is stored in the images.
- Jepsen records the test's git state with `git`, downloads the nodes' logs
  with `scp` and stops etcd with `killall`, so the images carry all three.
- The logs keep what the services printed. `machine output -n :` also shows
  the machine's boot.
