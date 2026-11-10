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
"""The time bounds a listing takes: a datetime, Unix seconds, or text naming a
date (`2026-06-01`, any ISO form) or an age (`7d`, `24h`, `30m`). A naive
datetime is read as UTC."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

from testflows.machine._core import disks as _disks

from .errors import InvalidArgument

When = Union[datetime, int, str]


def iso(when: Optional[When]) -> Optional[str]:
    """`when` as an ISO 8601 UTC instant, or None."""
    if when is None:
        return None
    return _instant(when).isoformat()


def unix_seconds(when: Optional[When]) -> Optional[int]:
    """`when` in Unix seconds, or None."""
    if when is None or isinstance(when, int):
        return when
    return int(_instant(when).timestamp())


def _instant(when: When) -> datetime:
    """`when` as an aware UTC datetime."""
    if isinstance(when, int):
        return datetime.fromtimestamp(when, timezone.utc)
    if isinstance(when, str):
        try:
            return datetime.fromisoformat(_disks.parse_since(when, "time"))
        except RuntimeError as exc:
            raise InvalidArgument(str(exc)) from None
    if when.tzinfo is None:
        return when.replace(tzinfo=timezone.utc)
    return when
