# Sessions and disks

Runs execute in a session, and a run boots a disk. Create the session, build
a disk of the program, then create runs from the disk.

## The session

```bash
machine sessions create work --cpus 2
```

- `--cpus` sets the vCPUs, `--mem` the memory and `--storage` the disk space.
  A race needs at least 2 vCPUs, so its threads can run at once. The Free
  plan's session is 2 vCPUs, 4G of memory and 32G of disk at most.
- A session costs credits while it runs, charged an hour ahead; the unused
  part comes back when it stops. Ask your person before you create one.
- Name it on every later command with `-s work`. `machine sessions use` and
  an exported `TESTFLOWS_MACHINE_SESSION` hold only for the shell they ran
  in, and each of your commands may run in a new one. `-s` takes a session
  connected on this computer, which `sessions create` does unless given
  `--no-use`. Otherwise it is refused with `Session work is not connected
  here`: run `machine sessions use work` once.
- Each run's machine takes its memory plus 128MB of the session, and 256MB of
  every session is not for machines. A 256MB machine needs 384MB, so a 4G
  session holds ten. A machine holds its memory until you stop it,
  whether or not its program finished: `machine -s work stop <run>`.
- A session with no command for its idle time, 10 minutes on Free, stops,
  and so do its runs. The runs are kept: `machine start <run>` starts one
  again. `machine sessions keep work 30m` keeps it while you think.
- When done: `machine sessions delete work -y`.

## The disk

`machine disks build` takes exactly one source. The machine runs it as a
container under Docker Compose.

| The program | Build |
|---|---|
| A static executable | `machine disks build --binary ./app app -- <args>` |
| A script or a program that needs a runtime | `machine disks build --from python:3.12-slim --add app.py app -- python app.py` |
| A Docker image of your own | `machine disks build --image myapp:latest app` |
| A Compose project | `machine disks build --compose ./deploy stack` |

- `--binary` takes a static x86_64 executable and nothing else: no loader,
  no libraries. The machine runs it as `/` and its file name:
  `--binary ./app` is `/app`, the path `--in` and `focus` take. Compile it with `-static`. Add `-g` to name source lines in
  breakpoints.
- `--from` pulls a public image and needs no Docker on this computer.
  `--image` and `--compose` need Docker, and the images for `linux/amd64`,
  even on an ARM computer.
- The arguments after `--` are the program's.
- Name a disk in lowercase.

## The run

```bash
machine -s work create bug --disk app --cpus 2
```

The run boots and waits for your commands. `machine -s work run bug` drives
it; `--until` and `--within` bound how far. `machine -s work output bug`
prints the last 25 lines it printed, and `-n :` every line.

## Account and storage

| Action | Command |
|---|---|
| The plan, its caps, the credits left, units in use | `machine account show` |
| The credits: plan credits and usage credits | `machine account credits` |
| What sessions were charged | `machine account activity --since 7d` |
| Hardware classes, their regions and hourly prices | `machine sessions classes` |
| A session on another class: `--class` | `machine sessions create work --class standard-ie` |
| Storage the account uses; free what deleted runs hold | `machine storage show`, `machine storage prune` |

Read these before you ask your person to spend: tell them what a session
will cost and what they have left.

## Commands and docs

- [`machine sessions create`](https://testflows.com/docs/machine/commands.md#machine-sessions-create),
  [`machine sessions delete`](https://testflows.com/docs/machine/commands.md#machine-sessions-delete),
  [`machine sessions keep`](https://testflows.com/docs/machine/commands.md#machine-sessions-keep),
  [`machine disks build`](https://testflows.com/docs/machine/commands.md#machine-disks-build),
  [`machine create`](https://testflows.com/docs/machine/commands.md#machine-create),
  [`machine run`](https://testflows.com/docs/machine/commands.md#machine-run),
  [`machine output`](https://testflows.com/docs/machine/commands.md#machine-output),
  [`machine stop`](https://testflows.com/docs/machine/commands.md#machine-stop)
- Sessions: sizes, memory, idle stops: https://testflows.com/docs/machine/sessions.md
- Disks: every source, runtimes, Compose projects: https://testflows.com/docs/machine/disks.md
- Running a machine: https://testflows.com/docs/machine/running-a-machine.md
- Account, plans, credits, storage: https://testflows.com/docs/machine/account-and-billing.md
