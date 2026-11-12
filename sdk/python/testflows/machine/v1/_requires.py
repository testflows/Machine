# Copyright 2026 Katteli Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The TestFlows Machine SDK Core this SDK runs on.

The SDK Core is one proprietary file, `_testflows_machine_core.abi3.so`,
licensed under the TestFlows Machine Client EULA and installed by the
client's installer into `~/.testflows/machine/sdk/`. It is not on PyPI. This module loads it under the
name `testflows.machine._core`, from the file `TESTFLOWS_MACHINE_CORE` names,
else an importable `testflows.machine._core`, else the installed file. It
refuses to import without a core of an interface version the SDK supports.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

import testflows.machine

#: The core interface versions this SDK works with.
CORE_API_VERSIONS = range(1, 2)
#: The least revision of that interface this SDK needs. A core from before
#: revisions has none and counts as 1.
CORE_API_REVISION = 2

#: The name the core is loaded under, and the file it ships as.
CORE_MODULE = "testflows.machine._core"
CORE_FILE = "_testflows_machine_core.abi3.so"
#: Where the client's installer puts the core.
CORE_DIR = os.path.join("~", ".testflows", "machine", "sdk")
#: A core file to load instead of the installed one.
CORE_ENV = "TESTFLOWS_MACHINE_CORE"

_INSTALLER = "curl https://testflows.com/machine/install -fsS | bash"
#: Modules whose absence means the core is not importable. Any other import
#: failure is a core that is present and cannot load, and keeps its own error.
_CORE_MODULES = ("testflows", "testflows.machine", CORE_MODULE)


def _load(path: str) -> ModuleType:
    """The core file at `path`, loaded as `testflows.machine._core`."""
    spec = importlib.util.spec_from_file_location(CORE_MODULE, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Not a TestFlows Machine SDK Core: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[CORE_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[CORE_MODULE]
        raise
    setattr(testflows.machine, "_core", module)
    return module


def _importable() -> ModuleType | None:
    """An importable `testflows.machine._core`, or None."""
    try:
        import testflows.machine._core as core
    except ModuleNotFoundError as error:
        if error.name not in _CORE_MODULES:
            raise
        return None
    return core


def _core() -> ModuleType:
    """The core, from the first place that has one."""
    named = os.environ.get(CORE_ENV)
    if named:
        if not os.path.isfile(named):
            raise ImportError(f"{CORE_ENV} names no file: {named}")
        return _load(named)
    core = _importable()
    if core is not None:
        return core
    installed = os.path.join(os.path.expanduser(CORE_DIR), CORE_FILE)
    if os.path.isfile(installed):
        return _load(installed)
    raise ImportError(
        "The TestFlows Machine SDK Core is not installed."
        f" Install it with the TestFlows Machine client {_INSTALLER}"
    )


_loaded = _core()
_found = getattr(_loaded, "API_VERSION", None)
if _found not in CORE_API_VERSIONS:
    raise ImportError(
        f"The TestFlows Machine SDK Core has interface version {_found};"
        f" this SDK needs {CORE_API_VERSIONS.start} to"
        f" {CORE_API_VERSIONS.stop - 1}."
        f" Update it with the TestFlows Machine client {_INSTALLER}"
    )
_revision = getattr(_loaded, "API_REVISION", 1)
if _revision < CORE_API_REVISION:
    raise ImportError(
        "The TestFlows Machine SDK Core is older than this SDK."
        f" Update it with the TestFlows Machine client {_INSTALLER}"
    )
