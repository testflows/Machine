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
"""Warnings attributed to the program's own call."""

from __future__ import annotations

import contextlib
import os
import sys
import types
import warnings
from typing import Optional

#: Frames a warning is never attributed to: the SDK's own, and contextlib's,
#: which a `with` on the SDK's connections passes through.
_SKIPPED = (os.path.dirname(__file__) + os.sep, contextlib.__file__)


def warn(message: str, category: type[Warning]) -> None:
    """Warn at the first frame outside the SDK: the program's call, however
    many of the SDK's own calls lie between."""
    level = 1
    frame: Optional[types.FrameType] = sys._getframe(1)
    while frame is not None and frame.f_code.co_filename.startswith(_SKIPPED):
        frame = frame.f_back
        level += 1
    warnings.warn(message, category, stacklevel=level + 1)
