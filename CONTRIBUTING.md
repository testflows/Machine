# Contributing to TestFlows™ Machine

TestFlows™ Machine is a product you can help shape in many ways: with bug
reports, ideas, examples, and improvements to the Python SDK. We appreciate
every effort that makes it better.

Thank you!

## Legal Info

Every contribution needs the [TestFlows™ Individual CLA](cla/CLA-20261110.md)
(Contributor License Agreement). When you open a pull request, its description asks you to
check a box that says you have read and agree to it. Please review it before
you check the box. We can merge a pull request only after its author has
checked it, and your agreement covers your later contributions too.

Optionally, to make contributions more legally binding, your employer as a
legal entity may want to sign the
[TestFlows™ Corporate CLA](cla/CORPORATE-CLA-20261110.md) with Katteli Inc. If
you're interested in doing so, contact us at
[legal@testflows.com](mailto:legal@testflows.com).

## Ways to contribute

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
5. Sign your commits. The `main` branch accepts only commits GitHub can
   verify. See GitHub's guide to
   [signing commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits)
   with an SSH or GPG key.

**Pull requests do not run CI.** No workflow runs on a pull request, from a
fork or otherwise. A maintainer reviews the change and tests it before merging.

## Technical Information

### Examples

An example is a small program or Compose project that shows something
TestFlows™ Machine can find, record or replay. Each one:

* builds with the `Makefile` in [`examples/`](examples/), or with its own
  `Dockerfile` or Compose file;
* says in its README or its header comment what it does and what to look for;
* starts with the Apache License 2.0 header the other examples carry.

### Python SDK

The SDK supports Python 3.11 and later. Format Python code with
[black](https://github.com/psf/black), and keep the public API typed. See the
[SDK's README](sdk/python/README.md) for building and installing it from
source, and the [documentation](https://testflows.com/docs/machine/#python-sdk)
for how it is used.

Maintainers publish a release from `sdk/python`. `./package` builds the wheel
and the source archive into `dist/`, and refuses either if it carries anything
but the SDK's own files. The version is in
`testflows/machine/v1/_version.py`.

```bash
cd sdk/python
pip3 install build twine
./package
twine upload dist/*
```

Each version published to PyPI is also a GitHub release, with the same two
files, on a signed tag `python-sdk/v<version>`:

```bash
git tag -s python-sdk/v0.1.2 -m "Python SDK 0.1.2"
git push origin python-sdk/v0.1.2
gh release create python-sdk/v0.1.2 dist/* --title "Python SDK 0.1.2" --notes-file NOTES.md
```

## See Also

* [Code of Conduct](CODE_OF_CONDUCT.md)
* [Security policy](SECURITY.md)
* [Getting help](SUPPORT.md)
