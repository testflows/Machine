<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/testflows-logo-dark.png">
  <source media="(prefers-color-scheme: light)" srcset=".github/assets/testflows-logo-light.png">
  <img alt="TestFlows" src=".github/assets/testflows-logo-light.png" width="420">
</picture>

<h3>TestFlows™ Machine is a deterministic hypervisor and execution environment.</h3>

Record a run of your software, replay it exactly, and branch it from any point
to explore what else could have happened. It runs in the cloud as a self-serve
service, with a free plan and paid plans.

[Website](https://testflows.com/machine) ·
[Documentation](https://testflows.com/docs/machine/) ·
[Pricing](https://testflows.com/machine/pricing/) ·
[Python SDK](https://github.com/testflows/Machine-SDK) ·
[Discussions](https://github.com/testflows/Machine/discussions)

</div>

> [!NOTE]
> TestFlows™ Machine is in public beta. We're improving it quickly with
> feedback from users like you. Backward compatibility isn't guaranteed yet,
> and you may need to update the client and the SDK Core often.

## Install the client

On Linux (x86_64 and arm64), on a Mac with Apple Silicon, and on Windows
inside WSL:

```bash
curl https://testflows.com/machine/install -fsS | bash
```

The installer signs you in, or creates your account, with a code sent to your
email. It checks the client's checksum and signature, and puts `machine` and
`machine-env` in `~/.local/bin`. Run the same command again to update. On a
Mac, start `machine-env` first: macOS has its own `/usr/bin/machine`. See the
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

## Python SDK

Drive runs, branches and replays from Python:

```bash
pip3 install testflows.machine
```

The SDK runs on the SDK Core, which the client's installer installs. See the
[Python SDK](https://github.com/testflows/Machine-SDK) for examples.

## Community

This repository is where the TestFlows™ Machine community reports bugs,
requests features and discusses the product. It does not contain source code.

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
* **Report a security vulnerability** privately, never in public. See
  [SECURITY.md](SECURITY.md).

> [!WARNING]
> Issues and discussions are public. Never include an API key, a token, a
> password or other secrets, and remove them from any output you paste.

Everyone taking part in this community is expected to follow our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Legal

TestFlows™ Machine is provided under the
[Terms of Service](https://testflows.com/machine/legal/terms-of-service/)
and the [Client EULA](https://testflows.com/machine/legal/client-eula/). The
Python SDK is licensed under the Apache License 2.0.

TestFlows™ and the TestFlows logo are trademarks of Katteli Inc.
