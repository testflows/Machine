# JuiceFS data corruption

A test that reproduces
[juicedata/juicefs#7555](https://github.com/juicedata/juicefs/issues/7555)
inside a machine. JuiceFS 1.3.1 can return wrong bytes when it reads back a
file written in appends. zstd decompresses a slice
into a shared read buffer and can overwrite the slice next to it. The objects
in the store stay correct, and only the read is wrong. The fix,
[juicedata/juicefs#7556](https://github.com/juicedata/juicefs/pull/7556),
shipped in JuiceFS 1.3.4 and 1.4.2.

| Service | What it runs |
|---|---|
| `redis` | Redis, which holds the JuiceFS metadata |
| `rustfs` | [RustFS](https://rustfs.com), the S3 object store |
| `jfs` | a JuiceFS client built from a release tag, and the test, `client/run.sh` |

The test formats a volume with zstd. Each round writes 64 MiB of compressible
data through one open file, in appends of random sizes between 13 KiB and
1 MiB, with a `sync` after each one. It then reads the file back cold, through
a fresh read-only mount with an empty cache, and compares its hash with the
original. It prints one line a round and a verdict.

## Run it

The machine has 2 vCPUs and 4G of memory, and the test runs in a session of
4 vCPUs and 8G, which leaves the session's other two vCPUs for recording the
run. A session that size needs the
[Starter plan or higher](https://testflows.com/machine/pricing/).

From `examples/`, create the session, build the client image for
`linux/amd64`, then the disk, then run the test to its verdict:

```bash
machine sessions create jfs --cpus 4 --mem 8G
cd compose/juicefs
docker build --platform linux/amd64 -t jfs-client:local client
machine disks build --compose . jfs131
machine create jfs131 --disk jfs131 --cpus 2 --mem 4096
machine run jfs131 --until 'console~(done: [0-9]+ of|mount of /. failed)' --within 20m
machine output jfs131 -n : | grep -E "round|done"
machine stop jfs131
```

The client image builds JuiceFS 1.3.1. In our run, the test took 101 seconds
of machine time and 155 seconds of real time, and two rounds out of five came
back corrupt:

```text
jfs | jfs | round 1: CORRUPT sha c6ae65853736b624 != 599049e5570df6de, first byte 45043596, 45809 bytes differ (55 slice rows)
jfs | jfs | round 2: clean (53 slice rows)
jfs | jfs | round 3: clean (46 slice rows)
jfs | jfs | round 4: CORRUPT sha 852114f9a5c137dd != 599049e5570df6de, first byte 48008454, 54678 bytes differ (54 slice rows)
jfs | jfs | round 5: clean (55 slice rows)
jfs | jfs | done: 2 of 5 rounds corrupt
```

Each corrupt round names the hash it read, the first byte that differs from
the original, and how many bytes differ.

The bug depends on which slice finishes decompressing first, so another
recording can corrupt other rounds and other bytes.

## Replay it

A run replays exactly: the same rounds, the same corrupted bytes and the
same verdict. Fork the run at its first checkpoint and replay the recorded
entries on the branch:

```bash
machine fork jfs131 --at 1 --name jfs131-again
machine replay jfs131-again
diff <(machine output jfs131 -n :) <(machine output jfs131-again -n :) && echo identical
machine hash jfs131-again mem,disk
machine stop jfs131-again
machine start jfs131
machine hash jfs131 mem,disk
machine stop jfs131
```

```text
✔ Forked jfs131/jfs131-again
  From checkpoint 1 of jfs131, nothing replayed
✔ Replayed 1916254 entries to entry 1916255. Converged.
identical
```

The replay took 52 seconds of real time. `machine start` brings the stopped
run back at its tip, so both hashes are taken at the same entry. The replay
reached the same memory and disk as the run it replays:

```text
Entry 1916255
mem          0x02e67ecc52c3f553
disk         0x4c34c06ebd172119
```

## Check the fix

Build the client image with another release, then build a disk and run it as
above under another name:

```bash
docker build --platform linux/amd64 --build-arg JUICEFS_VERSION=v1.4.2 -t jfs-client:local client
machine disks build --compose . jfs142
machine create jfs142 --disk jfs142 --cpus 2 --mem 4096
machine run jfs142 --until 'console~(done: [0-9]+ of|mount of /. failed)' --within 20m
machine output jfs142 -n : | grep -E "juicefs version|round|done"
machine stop jfs142
```

On 1.4.2, every round came back clean, in 99 seconds of machine time and
160 seconds of real time:

```text
jfs | jfs | juicefs version 1.4.2+unknown, volume formatted
jfs | jfs | round 1: clean (48 slice rows)
jfs | jfs | round 2: clean (64 slice rows)
jfs | jfs | round 3: clean (61 slice rows)
jfs | jfs | round 4: clean (71 slice rows)
jfs | jfs | round 5: clean (78 slice rows)
jfs | jfs | done: 0 of 5 rounds corrupt
```

## Run it on the host

The project also runs on the host, from the same directory:

```bash
docker compose up --abort-on-container-exit
```

The host needs FUSE. On the host, the bug shows up less often than in the
machine: one round out of eleven came back corrupt in our runs.

## Notes

- The client is built with `CGO_ENABLED=1`, as the released binaries are.
  JuiceFS uses DataDog/zstd, which is cgo, and a build without cgo uses
  another zstd that does not have the bug.
- The data must be compressible but full of literals. zstd stores random
  data almost as is, and the broken code never runs. `client/data.py` makes
  it the way the test that came with the fix does.
- The appends must be of irregular sizes. Appends of 256 KiB end every slice
  on a zstd block boundary, and 1.3.1 comes back clean every time.

*Real time is for reference only, to demonstrate what we saw in our
experiments.*
