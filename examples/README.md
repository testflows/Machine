# TestFlows™ Machine Examples

Programs and Compose projects to run on [TestFlows™ Machine](https://testflows.com/machine/).

## ✨ Getting Started

```bash
git clone https://github.com/testflows/Machine-Examples.git
cd Machine-Examples
```

## 🧪 Programs

The programs are small and written to be run many times. Most hold a
concurrency bug with a known shape, and what they print depends on how their
threads interleaved, which is the thing a machine decides, records and can
replay. The two in `stress/` hold no bug at all: they drive the kernel hard
enough that a replay either matches or does not.

| Example | What it does | The bug |
|---|---|---|
| [hello/hello-world.c](hello/hello-world.c) | Prints a greeting and the kernel it runs on | None. The smallest disk that boots and runs a program |
| [races/data-race.c](races/data-race.c) | N threads increment one shared counter with no lock | Lost update. The read-add-write is three steps and another thread lands between two of them, so the total comes out short |
| [starvation/timeout-starve.c](starvation/timeout-starve.c) | Main arms a timeout and waits; the worker must publish before it fires | The worker is starved for the whole wait and the timeout expires with nothing published. It is the ONLY runnable thread while main sleeps, so no pick-next policy finds this |
| [starvation/pipe-wakeup.c](starvation/pipe-wakeup.c) | A reader blocks on a pipe, main writes one byte then waits | The reader is starved after becoming runnable and misses the timeout. Both threads are runnable after the write, so a scheduler can pick the reader |
| [starvation/futex-wakeup.c](starvation/futex-wakeup.c) | A waiter blocks on a locked mutex, main unlocks then waits | The waiter is starved after the unlock. Same shape as pipe-wakeup with a futex instead of a pipe |
| [starvation/narrow-window.c](starvation/narrow-window.c) | A producer raises a flag for a few iterations, then lowers it for a long gap | The consumer is starved through every window and never sees the flag up. Needs frequent short delays, not long ones |
| [starvation/missed-wakeup.c](starvation/missed-wakeup.c) | A waiter checks a predicate, then waits on it, with no loop around the check | Missed wakeup. Held between the check and the wait, the signaler signals into nobody and the waiter sleeps through it. The timeout expires with the predicate already true |
| [starvation/lease.c](starvation/lease.c) | Main waits on a lease long enough for the worker's fixed work | The lease expires before the worker publishes. Nobody is starved: virtual time jumps while the work is still running, so the timer fires early |
| [stress/memstress.c](stress/memstress.c) | Allocates close to the VM's memory limit across threads, with madvise, fork/COW and fault cycles | Not a bug of its own. It drives the kernel's memory management hard, which is the work a replay has to reproduce exactly |
| [stress/memswapstress.c](stress/memswapstress.c) | Allocates 150% of RAM against a swap file, forcing swap-out, swap-in and swap cache | Not a bug of its own. Same purpose one layer down, through swap |

## 🚀 Run One From the Image

Every program is in one image, `testflows/machine-examples`, at
`/examples/<name>`. `disks build` reads the image from the local Docker, so pull
it first. A machine is x86_64, so ask for `linux/amd64` even on an ARM computer:

```bash
docker pull --platform linux/amd64 testflows/machine-examples
```

Each example then takes five commands:

1. `machine disks build` builds a disk that runs the program. `--entrypoint`
   names it, and arguments after `--` are its arguments.
2. `machine create` creates a run from the disk and waits for commands.
3. `machine run --until halted` drives the run until the program exits and the
   machine powers off.
4. `machine console` shows what the machine printed; the program's own lines
   start with `app-1`.
5. `machine stop` ends the machine, which holds its memory until it is
   stopped.

Each program's arguments and defaults are at the top of its source.

### 👋 hello-world

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/hello-world hello-world
machine create hello-world --disk hello-world
machine run hello-world --until halted
machine console hello-world | grep app-1
machine stop hello-world
```

### 🏁 data-race

Eight threads, each incrementing the counter 100000 times:

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/data-race data-race -- 8
machine create data-race --disk data-race --cpus 2
machine run data-race --until halted
machine console data-race | grep app-1
machine stop data-race
```

### ⏱️ timeout-starve

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/timeout-starve timeout-starve -- 200 20
machine create timeout-starve --disk timeout-starve
machine run timeout-starve --until halted
machine console timeout-starve | grep app-1
machine stop timeout-starve
```

### 🚰 pipe-wakeup

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/pipe-wakeup pipe-wakeup -- 200 20
machine create pipe-wakeup --disk pipe-wakeup
machine run pipe-wakeup --until halted
machine console pipe-wakeup | grep app-1
machine stop pipe-wakeup
```

### 🔒 futex-wakeup

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/futex-wakeup futex-wakeup -- 200 20
machine create futex-wakeup --disk futex-wakeup
machine run futex-wakeup --until halted
machine console futex-wakeup | grep app-1
machine stop futex-wakeup
```

### 🪟 narrow-window

Two vCPUs, so the consumer spins while the producer opens windows:

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/narrow-window narrow-window -- 2000
machine create narrow-window --disk narrow-window --cpus 2
machine run narrow-window --until halted
machine console narrow-window | grep app-1
machine stop narrow-window
```

### 📭 missed-wakeup

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/missed-wakeup missed-wakeup -- 2000
machine create missed-wakeup --disk missed-wakeup
machine run missed-wakeup --until halted
machine console missed-wakeup | grep app-1
machine stop missed-wakeup
```

### 📜 lease

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/lease lease -- 50 200
machine create lease --disk lease
machine run lease --until halted
machine console lease | grep app-1
machine stop lease
```

### 🧠 memstress

256MB across four threads, ten iterations, in a 512MB machine:

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/memstress memstress -- 256 4 10
machine create memstress --disk memstress --mem 512
machine run memstress --until halted
machine console memstress | grep app-1
machine stop memstress
```

### 💽 memswapstress

768MB, more than the 512MB machine holds, so the kernel swaps:

```bash
machine disks build --image testflows/machine-examples --entrypoint /examples/memswapstress memswapstress -- 768 4 5
machine create memswapstress --disk memswapstress --mem 512
machine run memswapstress --until halted
machine console memswapstress | grep app-1
machine stop memswapstress
```

## 🔨 Build Them Yourself

`make` compiles every program static and x86_64, which is what a disk built
from a binary requires:

```bash
make            # every program, into bin/
make check      # and prove each one is static x86_64
make list       # what would be built
```

```bash
machine disks build --binary bin/data-race race -- 8
machine create --disk race
```

Or build the image itself, the same one `testflows/machine-examples` is:

```bash
docker build -t machine-examples .
machine disks build --image machine-examples --entrypoint /examples/data-race race -- 8
```

## 🐳 Compose Environments

`compose/` holds whole environments rather than programs: a docker compose
project that `machine disks build --compose` turns into a disk. The machine
runs the project with `--abort-on-container-exit`, so it ends when a service
exits.

| Environment | What it does | What it shows |
|---|---|---|
| [compose/two-services](compose/two-services/docker-compose.yml) | A busybox server and a client that fetches from it by service name | Services reach each other by name, over the network Docker gives the project, the same as on any host |
| [compose/clickhouse](compose/clickhouse/docker-compose.yml) | A ClickHouse server and a client that queries it | A real database server in a machine. `config.xml` replaces the image's: IPv4 only, because the machine's kernel has no IPv6, and every background pool capped, because the defaults hang startup in a small VM without an error |
| [compose/postgres](compose/postgres/docker-compose.yml) | A PostgreSQL server and a client that runs `queries.sql` against it | Queries that run and end the machine. The client waits for a healthcheck over TCP, because the image first starts a temporary server on its socket alone, and `ON_ERROR_STOP` makes a failed query the machine's exit code |
| [compose/clickhouse-regression](compose/clickhouse-regression/README.md) | Altinity's ClickHouse regression suite, which brings up its own Compose project inside the machine | A test suite that drives Docker Compose itself. The runner binds the machine's Docker socket, the sources sit at one path both sides of it agree on, and `scale: 0` carries the images the suite starts but no service here names |

```
machine disks build --compose compose/clickhouse ch
machine create --disk ch
```

Each project is also runnable on the host, which is how to fix one without
waiting for a boot:

```
cd compose/clickhouse && docker compose up --abort-on-container-exit
```

## 📚 Learn More

- 🌐 **Website:** [testflows.com/machine](https://testflows.com/machine/)
- 📖 **Documentation:** [testflows.com/docs/machine](https://testflows.com/docs/machine/)
- ⬇️ **Download:** [testflows.com/machine/download](https://testflows.com/machine/download/)

## 📜 License

Apache 2.0; see [LICENSE](LICENSE).

---

Learn to write test programs, not just tests 👽
