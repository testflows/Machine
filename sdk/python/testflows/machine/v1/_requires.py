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
"""The TestFlows™ Machine core this SDK runs on.

The core is the `testflows.machine.core` package, which pip installs with
the SDK. This module refuses to import without a core of an interface version
the SDK supports, which catches a core removed or replaced by hand.
"""

from __future__ import annotations

#: The core interface versions this SDK works with.
CORE_API_VERSIONS = range(1, 2)

_INSTALL = "Install the core with pip install --upgrade testflows.machine.core"
#: Modules whose absence means the core is not installed. Any other import
#: failure is a core that is present and cannot load, and keeps its own error.
_CORE_MODULES = ("testflows", "testflows.machine", "testflows.machine._core")

try:
    import testflows.machine._core as _core
except ModuleNotFoundError as error:
    if error.name not in _CORE_MODULES:
        raise
    raise ImportError(
        f"The TestFlows™ Machine core is not installed in this Python. {_INSTALL}"
    ) from None

_found = getattr(_core, "API_VERSION", None)
if _found not in CORE_API_VERSIONS:
    raise ImportError(
        f"The installed TestFlows™ Machine core has interface version {_found};"
        f" this SDK needs {CORE_API_VERSIONS.start} to"
        f" {CORE_API_VERSIONS.stop - 1}. {_INSTALL}"
    )
