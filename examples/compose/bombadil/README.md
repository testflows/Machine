# Bombadil in a machine

[Bombadil](https://github.com/antithesishq/bombadil) is a
property-based tester for web and terminal UIs. It drives a program with
random input and checks properties written in TypeScript. This example runs
its terminal examples in a machine, so a run that finds a violation can be
replayed exactly, and each violation is a point in the run to fork at.

Bombadil has its own `--reproduce`, which plays the recorded actions again
and is best-effort: on a computer, the program does not always answer the same
input the same way. In a machine, the run is recorded, and a replay is the
same run: the same input at the same moments, the same screens, the same
violations.

| Example | Program | What its specification checks |
|---|---|---|
| `cat` | `cat` | `hello world` shows on more than five lines within 5 seconds; `cat` never exits with an error code or shows a replacement character |
| `nano` | `nano` | The bottom lines keep nano's main key bindings, `Help`, `Exit` and `Read`, outside its prompts |
| `btop` | `btop` | `btop` never exits with an error code or shows a replacement character |
| `tetris` | `bastet` | The screen is never blank |
| `tb` | `tigerbeetle repl` against a one-replica cluster | The REPL never exits with an error code or shows a replacement character |

The image takes Bombadil 0.8.0, its five specifications and TigerBeetle
0.17.9 from their releases, and checks the two binaries by their SHA-256.
Bombadil's other examples need the internet, `claude` and `wikipedia`, or do
not say which program they test, `text_editor`, so this example leaves them
out.

## Running it

Build the image and a disk from the project, then create a run and drive it
until the machine powers off. The machine needs 2 vCPUs and 2.5G of memory,
so the session needs 2 vCPUs and 4G:

```bash
cd examples/compose/bombadil
docker compose build
machine sessions create bombadil --cpus 2 --mem 4G
machine disks build --compose . bombadil
machine create bombadil --disk bombadil --cpus 2 --mem 2560
machine run bombadil --until halted --within 15m
```

A machine has 256M of memory unless `--mem` says otherwise, which is too
little for Docker, a Compose network and TigerBeetle: it runs, but it swaps.

Each example runs for 20 seconds of machine time. To change that, or to run
some of the examples, edit `command` in `docker-compose.yml`: `["5s", "nano",
"tb"]` runs two of them for 5 seconds each.

`tb` starts TigerBeetle, which does its I/O with `io_uring`, so the service
sets `seccomp:unconfined`: Docker's default seccomp profile refuses the
`io_uring` calls.

## Starting from a formatted cluster

TigerBeetle formats a 1 GiB write-ahead log before it listens, which costs
about 86 seconds of machine time. The cluster starts before any example, and
the console says when it listens, so a checkpoint there holds a formatted
cluster. Every run forked from it starts there. This replaces the last two
commands above: create the run, stop it where the cluster listens, then run
it and its forks to the end:

```bash
machine create bombadil --disk bombadil --cpus 2 --mem 2560
machine run bombadil --until 'console~tigerbeetle: listening' --within 15m
machine checkpoint bombadil ready
machine run bombadil --until halted --within 15m
machine fork bombadil --at ^ready --name walk-7 --rng-seed 7
machine run walk-7 --until halted --within 15m
```

A fork with another `--rng-seed` is another walk: the machine's random
numbers differ from that point on, so Bombadil types other keys and resizes
the terminal at other moments. In our run, the fork took 7 seconds of real
time, and the cluster was listening in it from the start.

## What it found

The console says how each example ended:

```bash
machine output bombadil | grep bombadil:
```

Each example leaves its screen log, every screen Bombadil saw and each action
it took, and its trace as output files of the run:

```bash
machine output bombadil sources
machine output bombadil --source /output/keep/app/bombadil/nano.screen.log -n -20
```

`nano` reports violations of `hasStandardBindings`, nano's key bindings missing
from its two bottom lines. They are not bugs in nano. Each is the first screen
Bombadil samples after it resizes the terminal, before nano has repainted, and
the bindings are back on the next screen. The property checks that screen with
`next`, and Bombadil samples it once nano has been quiet for 5 ms
(`--quiescence-timeout-ms`), which a repaint can take longer than.

## nano with eventually

[nano-eventually.patch](nano-eventually.patch) gives the property
`eventually(...).within(1, "seconds")` in place of `next(...)`, so nano has time
to repaint. The image applies it to Bombadil's `nano.ts` as `nano-eventually.ts`,
and `nano-eventually` runs it against the same nano. It is not one of the five
that run by default; name it in `command`:

```yaml
    command: ["20s", "nano", "nano-eventually"]
```

How each did, 60 seconds of each on a host CPU limited with `docker run --cpus`,
where a repaint takes longer than on a whole CPU:

| CPU | `nano` | `nano-eventually` |
|---|---|---|
| a whole CPU | 0 violations, three runs | 0 |
| 0.2 | 2 | 0 |
| 0.1 | 5 | 0 |
| 0.05 | 2 | 0 |

In a machine, the clock advances 10 ns for each branch a program retires
(`machine vtime <run> rate`), so a repaint takes longer on it than on a fast
host, as on a slower CPU. 20 seconds of each at each rate:

| ns per branch | screens a second | `nano` | `nano-eventually` |
|---|---|---|---|
| 1 | 118 | 0 violations | 0 |
| 2 | 117 | 0 | 0 |
| 5 | 93 | 0 | 0 |
| 10, the default | 67 | 3 | 0 |
| 20 | 46 | 4 | 0 |

At 10 the example samples 67 screens a second, as on 0.1 of a host CPU. Five
more runs with other seeds, three at 10 and two at 20, gave `nano` 14 more
violations and `nano-eventually` none.

Against `nano -x`, which never shows the bindings, both properties are violated,
so the patched one still finds what it is for.

## Going to a violation

[bombadil_events.py](bombadil_events.py) reads an example's screen log with
the Python SDK. The machine knows which entry printed each line, so each
action and each violation gets Bombadil's time and its place in the run:

```bash
pip install testflows.machine
python bombadil_events.py bombadil --example nano --events nano.tsv --cast nano.cast
```

One of our runs answered with two violations. Another walk of the same
project finds other ones, at other times and entries:

```
nano: 1125 screens, 1125 actions, 2 violations
  1. hasStandardBindings at 4.779s, entry 145457
     machine fork bombadil --at '#145457' --name nano-violation-1
  2. hasStandardBindings at 14.951s, entry 294215
     machine fork bombadil --at '#294215' --name nano-violation-2
```

- `--events` writes every action and violation with its time and entry.
- `--cast` writes the screens as an [asciinema](https://asciinema.org)
  recording, with a marker at each violation. `asciinema play nano.cast`
  plays it, and asciinema-player lists the markers and jumps to each.
- `--show N` prints the screen at violation N.

The fork command it prints starts a branch where Bombadil printed the screen
that violated the property, with nano running as it was there, to debug, to
inspect, or to try other input from there.

## Replaying it

Fork the run at its first checkpoint and replay it to its end:

```bash
machine fork bombadil --from 1 --name bombadil-replay
diff <(machine output bombadil -n : --source all) \
     <(machine output bombadil-replay -n : --source all) && echo same
machine hash bombadil
machine hash bombadil-replay
```

The two runs print the same bytes on every source, the screen logs and the
traces included, so `diff` prints nothing but `same`. `machine hash` gives
the same hashes for both at the last entry: memory, the disk, the devices
and every vCPU.
