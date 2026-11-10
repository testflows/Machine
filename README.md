<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/testflows/Machine/main/.github/assets/testflows-logo-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/testflows/Machine/main/.github/assets/testflows-logo-light.png">
  <img alt="TestFlows" src="https://raw.githubusercontent.com/testflows/Machine/main/.github/assets/testflows-logo-light.png" width="460">
</picture>

<h3>TestFlows™ Machine is a deterministic hypervisor and execution environment.</h3>

Record a run of your software, replay it exactly, and branch it from any point
to explore what else could have happened. It runs in the cloud as a self-serve
service, with a free plan and paid plans.

[Website](https://testflows.com/machine) ·
[Documentation](https://testflows.com/docs/machine/) ·
[Pricing](https://testflows.com/machine/pricing/) ·
[Examples](examples/) ·
[Python SDK](sdk/python/) ·
[Discussions](https://github.com/testflows/Machine/discussions)

</div>

> [!NOTE]
> TestFlows™ Machine is in public beta. We're improving it quickly with
> feedback from users like you. Backward compatibility isn't guaranteed yet,
> and you may need to update the client and the SDK Core often.

## Features

* **[Deterministic runs](https://testflows.com/docs/machine/#running-a-machine)**
  * [Random numbers](https://testflows.com/docs/machine/#random-numbers)
  * [Multiple vCPUs](https://testflows.com/docs/machine/#multiple-vcpus)
  * [Recording that repeats](https://testflows.com/docs/machine/#recording-that-repeats)
  * [Time compression](https://testflows.com/docs/machine/#time-compression)
* **[Deterministic replay](https://testflows.com/docs/machine/#replay)**
  * [Comparing entries](https://testflows.com/docs/machine/#comparing-entries)
  * [Comparing two machines](https://testflows.com/docs/machine/#comparing-two-machines)
* **[Checkpoints and branches](https://testflows.com/docs/machine/#checkpoints-and-branches)**
  * [Checkpoints](https://testflows.com/docs/machine/#checkpoints)
  * [Starting a branch](https://testflows.com/docs/machine/#starting-a-branch)
  * [Switching the machine](https://testflows.com/docs/machine/#switching-the-machine)
  * [The tree](https://testflows.com/docs/machine/#the-tree)
  * [Marks](https://testflows.com/docs/machine/#marks)
* **[Disks](https://testflows.com/docs/machine/#disks)**
  * [Docker and Compose](https://testflows.com/docs/machine/#docker-and-compose)
  * [Programs that need a runtime](https://testflows.com/docs/machine/#programs-that-need-a-runtime)
  * [Compose projects](https://testflows.com/docs/machine/#compose-projects)
* **[Steering programs](https://testflows.com/docs/machine/#steering-programs)**
  * [Focus](https://testflows.com/docs/machine/#focus)
  * [Holding threads](https://testflows.com/docs/machine/#holding-threads)
  * [Plans](https://testflows.com/docs/machine/#plans)
  * [Interrupts](https://testflows.com/docs/machine/#interrupts)
  * [Machine time](https://testflows.com/docs/machine/#machine-time)
* **[Fault injection](https://testflows.com/docs/machine/#fault-injection)**
  * [System call faults](https://testflows.com/docs/machine/#system-call-faults)
  * [Function faults](https://testflows.com/docs/machine/#function-faults)
  * [Memory faults](https://testflows.com/docs/machine/#memory-faults)
  * [Resource exhaustion](https://testflows.com/docs/machine/#resource-exhaustion)
  * [Kernel faults](https://testflows.com/docs/machine/#kernel-faults)
  * [Disk faults](https://testflows.com/docs/machine/#disk-faults)
  * [Container faults](https://testflows.com/docs/machine/#container-faults)
  * [Clock faults](https://testflows.com/docs/machine/#clock-faults)
  * [Network faults](https://testflows.com/docs/machine/#network-faults)
* **[Debugging](https://testflows.com/docs/machine/#debugging)**
  * [The machine's debugger](https://testflows.com/docs/machine/#the-machine%E2%80%99s-debugger)
  * [GDB inside the machine](https://testflows.com/docs/machine/#gdb-inside-the-machine)
* **[Inspecting a run](https://testflows.com/docs/machine/#inspecting-a-run)**
  * [Commands in the guest](https://testflows.com/docs/machine/#commands-in-the-guest)
  * [Copying files](https://testflows.com/docs/machine/#copying-files)
  * [Core dumps](https://testflows.com/docs/machine/#core-dumps)
  * [Profiling](https://testflows.com/docs/machine/#profiling)
  * [Interactive mode](https://testflows.com/docs/machine/#interactive-mode)
* **[Reading the disk](https://testflows.com/docs/machine/#reading-the-disk)**
* **[Scripting](https://testflows.com/docs/machine/#scripting)**
* **[Python SDK](https://testflows.com/docs/machine/#python-sdk)**

## Install the client

On Linux (x86_64 and arm64), on a Mac with Apple Silicon, and on Windows
inside WSL:

```bash
curl https://testflows.com/machine/install -fsS | bash
```

The installer signs you in, or creates your account, with a code sent to your
email. It checks the client's checksum and signature, puts `machine-env` in
`~/.local/bin`, and puts `machine` beside it unless another program already
has that name. macOS has its own `/usr/bin/machine`, so on a Mac start
`machine-env` first, which gives you the client as `machine`. Run the same
command again to update. See the
[download page](https://testflows.com/machine/download/) for details and for
checking the download yourself.

## Sign up

The installer creates your account if you don't have one. You can also sign up
on the [sign-up page](https://testflows.com/machine/portal/signup/) or with
`machine account signup`. Then sign in from the terminal:

```bash
machine login
```

Every account starts on the free plan. For scripts and CI, create an API key
with `machine account api-keys create ci` and put it in the
`TESTFLOWS_MACHINE_API_KEY` environment variable instead of signing in.

## Your first run

Save `print("hello, world")` as `hello.py`. Then create a session, build a
disk that runs it, run it until the machine halts, and read the last lines it
printed:

```bash
machine sessions create first --cpus 2
machine disks build --from python:3.12-slim --add hello.py hello -- python hello.py
machine create hello-run --disk hello
machine run hello-run --until halted
machine output hello-run -n -5 --plain
```

[Getting started](https://testflows.com/docs/machine/#getting-started)
continues with checkpoints, branches and replay.

## Examples

* [`examples/`](examples/) has small programs that each hold a known kind of
  bug, such as data races, starvation and missed wakeups, and Compose
  projects. Build disks from them and explore them with branches and replay.
* [`sdk/python/examples/`](sdk/python/examples/) has scripts that drive runs
  from Python.

## Python SDK

Drive runs, branches and replays from Python:

```bash
pip3 install testflows.machine
```

The SDK runs on the SDK Core, which the client's installer installs. See the
[Python SDK](sdk/python/) for how to use it.

## Documentation

* **[What is it?](https://testflows.com/docs/machine/#what-is-it)**: [Concepts](https://testflows.com/docs/machine/#concepts)
* **[Getting started](https://testflows.com/docs/machine/#getting-started)**: [The client](https://testflows.com/docs/machine/#the-client), [Signing up](https://testflows.com/docs/machine/#signing-up), [Your first run](https://testflows.com/docs/machine/#your-first-run), [Getting help](https://testflows.com/docs/machine/#getting-help)
* **[Disks](https://testflows.com/docs/machine/#disks)**: [Docker and Compose](https://testflows.com/docs/machine/#docker-and-compose), [Programs that need a runtime](https://testflows.com/docs/machine/#programs-that-need-a-runtime), [Compose projects](https://testflows.com/docs/machine/#compose-projects), [Building disks on an ARM machine](https://testflows.com/docs/machine/#building-disks-on-an-arm-machine), [Images at boot](https://testflows.com/docs/machine/#images-at-boot), [Disk size](https://testflows.com/docs/machine/#disk-size)
* **[Sessions](https://testflows.com/docs/machine/#sessions)**: [Idle sessions stop](https://testflows.com/docs/machine/#idle-sessions-stop)
* **[Running a machine](https://testflows.com/docs/machine/#running-a-machine)**: [Creating a run](https://testflows.com/docs/machine/#creating-a-run), [Random numbers](https://testflows.com/docs/machine/#random-numbers), [Multiple vCPUs](https://testflows.com/docs/machine/#multiple-vcpus), [Driving it](https://testflows.com/docs/machine/#driving-it), [Schedules](https://testflows.com/docs/machine/#schedules), [Recording that repeats](https://testflows.com/docs/machine/#recording-that-repeats), [Time compression](https://testflows.com/docs/machine/#time-compression), [Waiting](https://testflows.com/docs/machine/#waiting), [Inspecting a run](https://testflows.com/docs/machine/#inspecting-a-run), [Sending input](https://testflows.com/docs/machine/#sending-input), [Commands in the guest](https://testflows.com/docs/machine/#commands-in-the-guest), [Copying files](https://testflows.com/docs/machine/#copying-files), [Core dumps](https://testflows.com/docs/machine/#core-dumps), [Profiling](https://testflows.com/docs/machine/#profiling), [Interactive mode](https://testflows.com/docs/machine/#interactive-mode), [Stopping and starting](https://testflows.com/docs/machine/#stopping-and-starting)
* **[Checkpoints and branches](https://testflows.com/docs/machine/#checkpoints-and-branches)**: [Checkpoints](https://testflows.com/docs/machine/#checkpoints), [Starting a branch](https://testflows.com/docs/machine/#starting-a-branch), [Switching the machine](https://testflows.com/docs/machine/#switching-the-machine), [The tree](https://testflows.com/docs/machine/#the-tree), [Marks](https://testflows.com/docs/machine/#marks)
* **[Replay](https://testflows.com/docs/machine/#replay)**: [Replay margins](https://testflows.com/docs/machine/#replay-margins), [Comparing entries](https://testflows.com/docs/machine/#comparing-entries), [At the control prompt](https://testflows.com/docs/machine/#at-the-control-prompt), [From Python](https://testflows.com/docs/machine/#from-python), [Comparing two machines](https://testflows.com/docs/machine/#comparing-two-machines)
* **[Steering programs](https://testflows.com/docs/machine/#steering-programs)**: [Looking at tasks](https://testflows.com/docs/machine/#looking-at-tasks), [Focus](https://testflows.com/docs/machine/#focus), [Holding threads](https://testflows.com/docs/machine/#holding-threads), [Plans](https://testflows.com/docs/machine/#plans), [Interrupts](https://testflows.com/docs/machine/#interrupts), [Machine time](https://testflows.com/docs/machine/#machine-time)
* **[Fault injection](https://testflows.com/docs/machine/#fault-injection)**: [System call faults](https://testflows.com/docs/machine/#system-call-faults), [Function faults](https://testflows.com/docs/machine/#function-faults), [Memory faults](https://testflows.com/docs/machine/#memory-faults), [Resource exhaustion](https://testflows.com/docs/machine/#resource-exhaustion), [Kernel faults](https://testflows.com/docs/machine/#kernel-faults), [Disk faults](https://testflows.com/docs/machine/#disk-faults), [Faults that cycle](https://testflows.com/docs/machine/#faults-that-cycle), [Stopping at a fault](https://testflows.com/docs/machine/#stopping-at-a-fault), [Changing faults](https://testflows.com/docs/machine/#changing-faults), [What a fault misses](https://testflows.com/docs/machine/#what-a-fault-misses), [Container faults](https://testflows.com/docs/machine/#container-faults), [Clock faults](https://testflows.com/docs/machine/#clock-faults), [Network faults](https://testflows.com/docs/machine/#network-faults)
* **[Debugging](https://testflows.com/docs/machine/#debugging)**: [The machine's debugger](https://testflows.com/docs/machine/#the-machine%E2%80%99s-debugger), [How many stops a run holds](https://testflows.com/docs/machine/#how-many-stops-a-run-holds), [GDB inside the machine](https://testflows.com/docs/machine/#gdb-inside-the-machine)
* **[Reading the disk](https://testflows.com/docs/machine/#reading-the-disk)**: [Unsynced writes](https://testflows.com/docs/machine/#unsynced-writes)
* **[Account and billing](https://testflows.com/docs/machine/#account-and-billing)**
* **[Scripting](https://testflows.com/docs/machine/#scripting)**
* **[Commands](https://testflows.com/docs/machine/#commands)**
* **[Python SDK](https://testflows.com/docs/machine/#python-sdk)**: [Installing](https://testflows.com/docs/machine/#installing), [Hello Machine](https://testflows.com/docs/machine/#hello-machine), [The same names](https://testflows.com/docs/machine/#the-same-names), [Signing in](https://testflows.com/docs/machine/#signing-in), [Sessions](https://testflows.com/docs/machine/#sessions-1), [Disks](https://testflows.com/docs/machine/#disks-1), [Runs](https://testflows.com/docs/machine/#runs), [Faults](https://testflows.com/docs/machine/#faults), [Container faults](https://testflows.com/docs/machine/#container-faults-1), [Clock faults](https://testflows.com/docs/machine/#clock-faults-1), [Network faults](https://testflows.com/docs/machine/#network-faults-1), [Stopping at an event](https://testflows.com/docs/machine/#stopping-at-an-event), [Debugging a task](https://testflows.com/docs/machine/#debugging-a-task), [Points in a run's history](https://testflows.com/docs/machine/#points-in-a-run%E2%80%99s-history), [Watching and following](https://testflows.com/docs/machine/#watching-and-following), [Choosing entries](https://testflows.com/docs/machine/#choosing-entries), [Cleaning up](https://testflows.com/docs/machine/#cleaning-up), [Errors](https://testflows.com/docs/machine/#errors), [Examples](https://testflows.com/docs/machine/#examples), [License](https://testflows.com/docs/machine/#license)

## Community

This repository is the home of the TestFlows™ Machine community: bug
reports, feature requests, discussions, the examples and the Python SDK. The
client, the SDK Core and the service are not open source.

* **Report a bug.** Search the
  [existing issues](https://github.com/testflows/Machine/issues) first, then
  [open an issue](https://github.com/testflows/Machine/issues/new/choose) with
  the commands you ran, what you expected, what happened, and the output of
  `machine --version`.
* **Request a feature.**
  [Open a feature request](https://github.com/testflows/Machine/issues/new/choose)
  that describes what you're trying to do.
* **Ask a question or share an idea** in
  [Discussions](https://github.com/testflows/Machine/discussions). See
  [SUPPORT.md](SUPPORT.md) for other ways to get help.
* **Contribute an example or an improvement to the Python SDK.** See
  [CONTRIBUTING.md](CONTRIBUTING.md).
* **Report a security vulnerability** privately, never in public. See
  [SECURITY.md](SECURITY.md).

> [!WARNING]
> Issues and discussions are public. Never include an API key, a token, a
> password or other secrets, and remove them from any output you paste.

Everyone taking part in this community is expected to follow our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Legal

The Python SDK in [`sdk/`](sdk/) and the examples in [`examples/`](examples/)
are licensed under the Apache License 2.0; see the LICENSE file in each. No
other file in this repository is licensed under it.

The TestFlows™ Machine client, the SDK Core and the service are provided under
the [Terms of Service](https://testflows.com/machine/legal/terms-of-service/)
and the [Client EULA](https://testflows.com/machine/legal/client-eula/).

TestFlows™ and the TestFlows logo are trademarks of Katteli Inc.
