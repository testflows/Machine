# Running and inspecting a machine

The examples use a run named `app`. Add `-s <session>` to each command.

## Driving the run

| Action | Command |
|---|---|
| Run until something happens, bounded by machine time | `machine run app --until 'console~ready' --within 30s` |
| Run until a program starts, exits, or a task reaches a place | `machine run app --until comm=app:exec`, `--until /usr/bin/app:exit` once the program is focused, `--until 'tid=412:at=main'` |
| Run a number of iterations | `machine run app --iters 100` |
| Stop a drive at the end of its dispatch | `machine cancel app --soft` |
| Stop the machine, start it again at its tip | `machine stop app`, `machine start app` |

A run stops when the `--until` predicate holds or after `--iters`. Without
either it runs one iteration. `--iters 0` has no bound: give `--within` too.
`machine wait <run> --for <predicate>` waits for a run another command
drives.

## Recording that repeats

Replay always repeats a run exactly. To make a new recording of the same
start take the same course too, create the run with `--repeatable`:
`machine create app --disk app --repeatable --cpus 2`. It records slower.

## Scripting

Every command takes `-o json` before it, prints one JSON document, and exits
`2` on a refusal. In a script, `-q` drops progress, and `--timeout` bounds a
command that waits:

```bash
machine -q --timeout 300 run app --until tasks || echo "boot did not finish"
```

## Time compression

Idle time is skipped: once every vCPU is idle, the clock jumps to the next
timer. A 2-second timeout costs almost nothing, and ten minutes of sleep pass
in about three seconds. Replay skips the same time. To watch a program wait
at the clock's own rate:

```bash
machine run app --no-idle-skip --within 10s
```

## Random numbers and vCPUs

| Action | Command |
|---|---|
| Seed the machine's random number device | `machine create app --disk app --rng-seed 7`, or later `machine rng app seed 7` |
| Several vCPUs, so threads run at once | `machine create app --disk app --cpus 2` |

## Output and retroactive logging

| Action | Command |
|---|---|
| The last 25 lines the program printed | `machine output app` |
| Every line, to search | `machine output app -n : \| grep ERROR`, or `machine output app search 'connection refused'` |
| One source: a file under `/output`, a container's console | `machine output app --source /output/keep/db/console -n :` |
| The sources that printed, kept and dropped | `machine output app sources` |

**Retroactive logging.** A program writes detailed logs under
`$MACHINE_DROP_OUTPUT_DIR`, and the run stores none of them, so the run stays
small and fast. Because the run replays exactly, the logs are not lost: fork
a branch from a point before the stretch you care about, keeping the file,
and it is written again as the branch replays.

```bash
machine checkpoint app before                  # before the stretch runs
machine run app --until '/output/drop/app/debug.log~connection refused' --within 60s
machine fork app --from ^before --name look --keep-output /output/drop/app/debug.log
machine output app/look --source /output/drop/app/debug.log -n :
```

## Input, commands and files

| Action | Command |
|---|---|
| Type into the console | `machine send app ls`; Ctrl-C is `machine send -n -e app '\x03'` |
| Run a command and wait for it | `machine exec app run -- ls -la /` |
| Run it in a container, as a user | `machine exec app run --container db --user root -- id` |
| Start one and return | `machine exec app start -- sleep 5` |
| Copy a file in, or out | `machine put app ./app.conf /etc/app.conf`, `machine get app /var/log/app.log ./app.log` |
| A core dump of a process; the process dies of it | `machine core app 412 ./app.core` |
| Profile a second of everything, or one container | `machine profile app`, `machine profile app --for 5s --container db --flame` |
| Profile a stretch already recorded | `machine profile app --from ^slow --to ^done` |
| Drive it interactively, with slash commands | `machine control app` |

## Reading the disk

Files as a checkpoint saved them, without starting the machine. Data the
program has not synced is not there: sync, checkpoint, then read.

```bash
machine fs ls app /var/log --at ^done
machine fs cat app /var/log/app.log --at ^done
machine exec app run -- sync && machine checkpoint app && machine fs ls app / --at now
```

## Docs

- Running a machine, output, input, commands, profiling:
  https://testflows.com/docs/machine/running-a-machine.md
- Reading the disk: https://testflows.com/docs/machine/reading-the-disk.md
- Scripting: JSON, exit codes, timeouts: https://testflows.com/docs/machine/scripting.md
- Every command: https://testflows.com/docs/machine/commands.md
