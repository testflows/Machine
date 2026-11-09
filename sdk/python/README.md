# TestFlows™ Machine Python SDK

## Introduction

[TestFlows™ Machine] is a deterministic execution environment: record a run,
replay it exactly, and branch it from any point. The Python SDK does from
Python what the `machine` command does, so a test, a CI job or an exploration
program drives runs, branches and replays directly.

## Documentation

You can find the Python SDK's documentation at
https://testflows.com/docs/machine/#python-sdk.

## Supported environment

* Linux x86_64
* [Python 3] >= 3.11
* [TestFlows™ Machine client], which installs the SDK Core

## Installation

The SDK depends on the TestFlows™ Machine SDK Core, a proprietary compiled
module licensed under the [Client EULA]. The SDK Core is not on PyPI and is
not part of this repository. It is installed with the [TestFlows™ Machine
client], so install the client first:

```bash
$ curl https://testflows.com/machine/install -fsS | bash
```

Then install the SDK using [pip3]

```bash
$ pip3 install testflows.machine
```

or from sources

```bash
$ git clone https://github.com/testflows/Machine-SDK.git
$ cd Machine-SDK/python
$ ./package && ./install
```

## Signing in

The SDK signs in with the API key in `TESTFLOWS_MACHINE_API_KEY`, for
automation and CI, or with the login the `machine login` command stored.

## Hello Machine

Save a program as `hello.py`:

```python
print("hello, world")
```

Then build a disk that runs it, boot a run from the disk in a new session, and
print what the machine printed:

```python
import testflows.machine.v1 as machine

client = machine.Client()
if "hello" not in {disk.name for disk in client.disks()}:
    client.disks.build(
        "hello", from_="python:3.12-slim", add=["hello.py"], command=["python", "hello.py"]
    )

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk="hello") as run:
        run.run(until="console~hello, world", timeout=300)
        for line in run.output(lines=-5):
            print(line)
```

A run moves only when you drive it, and `until` names where to stop: here,
once the console shows `hello, world`. Leaving a `with` block deletes what it
created, so the session never outlives the program. You build a disk once and
create as many runs from it as you like.

More scripts are in [examples](https://github.com/testflows/Machine-SDK/tree/main/python/examples).

## Want to know more?

Find more information about [TestFlows™ Machine] at https://testflows.com/machine.

## License

The SDK is licensed under the Apache License 2.0. The SDK Core it depends on is
proprietary and is not part of the SDK. It is licensed under the [Client EULA],
which is part of the TestFlows™ Machine [Terms of Service].

[TestFlows™ Machine]: https://testflows.com/machine
[TestFlows™ Machine client]: https://testflows.com/machine/download/
[Client EULA]: https://testflows.com/machine/legal/client-eula/
[Terms of Service]: https://testflows.com/machine/legal/terms-of-service/
[pip3]: https://github.com/pypa/pip
[Python 3]: https://www.python.org/
