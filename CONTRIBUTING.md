# Contributing to TestFlows™ Machine

Thank you for helping improve TestFlows™ Machine. You can contribute in
several ways:

* **Report a bug or request a feature.**
  [Open an issue](https://github.com/testflows/Machine/issues/new/choose).
* **Ask a question or share an idea** in
  [Discussions](https://github.com/testflows/Machine/discussions).
* **Contribute an example** to [`examples/`](examples/).
* **Improve the Python SDK** in [`sdk/python/`](sdk/python/).

The client, the SDK Core and the service are not open source, so they are not
in this repository. Report a problem with them in an issue.

## Pull requests

1. For anything larger than a small fix, open an issue or a discussion first,
   so we can agree on the approach before you spend time on it.
2. Fork the repository and create a branch for your change.
3. Keep a pull request to one change, and describe what it does and how you
   tested it.
4. Never include an API key, a token, a password or other secrets.

**Pull requests do not run CI.** No workflow runs on a pull request, from a
fork or otherwise. A maintainer reviews the change and tests it before merging.

## Examples

An example is a small program or Compose project that shows something
TestFlows™ Machine can find, record or replay. Each one:

* builds with the `Makefile` in [`examples/`](examples/), or with its own
  `Dockerfile` or Compose file;
* says in its README or its header comment what it does and what to look for;
* starts with the Apache License 2.0 header the other examples carry.

## Python SDK

The SDK supports Python 3.11 and later. Format Python code with
[black](https://github.com/psf/black), and keep the public API typed. See the
[SDK's README](sdk/python/README.md) for building and installing it from
source.

## License

The examples and the Python SDK are licensed under the
[Apache License 2.0](LICENSE). By submitting a contribution, you agree that it
is licensed under the same license, as section 5 of the license states, and
you confirm that you have the right to submit it.

## Code of conduct

Everyone taking part is expected to follow our
[Code of Conduct](CODE_OF_CONDUCT.md).
