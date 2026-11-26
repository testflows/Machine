# Hello random

A machine that boots Linux, prints a greeting and the date, and then prints
256 bytes read from `/dev/random` in hex. On a computer, every run prints
other bytes. In a machine, a replay prints the same bytes and reaches the same
machine state, bit for bit.

The disk is built from the public `ubuntu:24.04` image, so no Docker is
needed. The machine runs one command in it:

```bash
echo "Hello from TestFlows Machine!"; date; head -c 256 /dev/random | od -A x -t x1 -v
```

## Run it

Create a session, build the disk, then run the machine until it powers off:

```bash
machine sessions create hello-random
machine disks build --from ubuntu:24.04 --entrypoint /bin/sh hello-random -- -c 'echo "Hello from TestFlows Machine!"; date; head -c 256 /dev/random | od -A x -t x1 -v'
machine create hello-random --disk hello-random
machine run hello-random --until halted --within 5m
machine output hello-random --source /output/keep/app/console -n :
```

The last command shows what the program printed. `machine output
hello-random -n :` shows the whole console, from the kernel's first message to
the reboot.

## Replay it

Fork the run at its first checkpoint, which is the start of the machine, and
replay the branch to the end. Then compare the output of the two runs:

```bash
machine fork hello-random --from 1 --name hello-random-replay
diff <(machine output hello-random --source all -n :) <(machine output hello-random-replay --source all -n :) && echo identical
```

`machine hash` prints the hashes of memory, the disk, every device and every
part of each vCPU. Both runs print the same hashes:

```bash
machine hash hello-random
machine hash hello-random-replay
```

A stopped machine is started again with `machine start` before `machine hash`
can read it.

## Change the inputs

The random bytes come from the seed of the machine's random number generator
device and from the timing of the machine. Fork at the first checkpoint with
another seed, and the bytes change:

```bash
machine fork hello-random --at 1 --name hello-random-seed7 --rng-seed 7
machine run hello-random-seed7 --until halted --within 5m
```

Fork again and double the rate of the machine's clock. The date moves, and the
bytes change too, because Linux mixes timing into its random pool:

```bash
machine fork hello-random --at 1 --name hello-random-rate20
machine vtime hello-random-rate20 rate 20
machine run hello-random-rate20 --until halted --within 5m
```

Each branch replays exactly, like the run it was forked from.

## The log

[hello-random.log](hello-random.log) is the complete output of one run, from
`machine output hello-random --source all -n : --wide`: every line of every
source, with the entries that printed it.
