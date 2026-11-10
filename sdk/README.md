# TestFlows™ Machine SDK

SDKs for [TestFlows™ Machine](https://testflows.com/machine), the deterministic
execution environment.

| Directory | Package | Install |
|---|---|---|
| [`python/`](python/) | `testflows.machine` | `pip install testflows.machine` |

Licensed under the [Apache License 2.0](LICENSE).

## Python

Build the wheel and the source archive into `python/dist/`, then publish them:

```bash
cd python
pip install build twine
./package
twine upload dist/*
```

`./install` installs the wheel `./package` built into the running Python.
The version is in `python/testflows/machine/v1/_version.py`.
