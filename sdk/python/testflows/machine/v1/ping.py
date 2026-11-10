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
"""Reachability and latency of the service and the sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from testflows.machine._core import ping as _ping


@dataclass(frozen=True)
class Ping:
    """A service's reachability and latency, as `Client.ping` measured it.

    Attributes:
        ok (bool): Whether the service was reached.
        samples (tuple[float, ...]): Warm round trips in milliseconds, after
            the connection was made.
        connect_ms (float, optional): Milliseconds to make the connection;
            None when it was not made.
        error (str): Why the service was not reached, in a few words; empty
            when `ok` is True.
    """

    ok: bool
    samples: tuple[float, ...]
    connect_ms: Optional[float]
    error: str

    @property
    def rtt_ms(self) -> Optional[float]:
        """float: The average round trip in milliseconds; None when the
        service was not reached."""
        return sum(self.samples) / len(self.samples) if self.samples else None

    def as_json(self) -> dict[str, Any]:
        """The fields as a JSON-serializable dict.

        Returns:
            dict[str, Any]: One key per attribute.
        """
        return {
            "ok": self.ok,
            "samples": list(self.samples),
            "connect_ms": self.connect_ms,
            "error": self.error,
        }


def _public(probe: _ping.Probe) -> Ping:
    """The public form of a probe."""
    return Ping(
        ok=probe.ok,
        samples=tuple(probe.samples),
        connect_ms=probe.connect_ms,
        error=probe.error,
    )
