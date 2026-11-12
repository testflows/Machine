# TestFlows Machine SDK

SDKs for [TestFlows Machine](https://testflows.com/machine), the deterministic
execution environment. An SDK does from a program what the `machine` command
does: it drives runs, branches, replays and faults.

| Directory | Package | Install |
|---|---|---|
| [`python/`](python/) | `testflows.machine` | `pip3 install testflows.machine` |

An SDK runs on the SDK Core, which the
[client's installer](https://testflows.com/machine/download/) installs. Install
the client first, then the SDK. See the [Python SDK](python/) for its
requirements and a first program, and the
[documentation](https://testflows.com/docs/machine/#python-sdk) for how to use
it.

Licensed under the [Apache License 2.0](LICENSE).
