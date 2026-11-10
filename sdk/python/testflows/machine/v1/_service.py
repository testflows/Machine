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
"""The service a session's runs live in, and how to reach it."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from testflows.machine._core import api
from testflows.machine._core import ipc
from testflows.machine._core import machines
from testflows.machine._core import sessions as _sessions
from testflows.machine._core import tunnel

from .errors import NotFound, ServiceUnavailable, _translated


class _Service:
    """One session's service: where its socket is and how disks are named.

    `socket` is None for the local service's default socket. `disks` lists
    the account's disks, which a cloud session names by; None for the
    local service, which takes a path. `renew` keeps a cloud session's
    access current before each connection; None for the local service.
    """

    def __init__(
        self,
        socket: Optional[Path],
        disks: Optional[Callable[[], list[dict[str, Any]]]],
        label: str,
        renew: Optional[Callable[[], None]] = None,
    ) -> None:
        self.socket = socket
        self.disks = disks
        self.label = label
        self.renew = renew

    @contextlib.contextmanager
    def client(self) -> Generator[ipc.Client, None, None]:
        """A connection to the service, closed on exit, with engine errors
        translated to the SDK's."""
        with _translated():
            if self.socket is None and not ipc.Client.is_available():
                raise ServiceUnavailable(f"The {self.label} is not running.")
            if self.renew is not None:
                self.renew()
            client = ipc.Client(socket_path=self.socket)
            try:
                client.connect()
            except OSError as exc:
                raise ServiceUnavailable(
                    f"Could not reach the {self.label}: {exc}"
                ) from None
            try:
                yield client
            finally:
                client.close()

    def disk(self, disk: str) -> "machines.DiskRef":
        """What the service takes for `disk`, and what the catalog says about
        it: an absolute path for the local service, or the content hash of the
        named disk plus its labels for a cloud session."""
        with _translated():
            return machines.resolve_disk(
                disk, local=self.disks is None, list_disks=self.disks
            )


def local() -> _Service:
    """The local service, on its default socket."""
    return _Service(None, None, "local service")


def of_session(
    client: api.client.Client, creds: dict[str, Any], session_id: str, name: str
) -> _Service:
    """A cloud session's service, through this machine's tunnel to it.

    Opens the tunnel when it is not up; reuses it when it is.
    """
    with _translated():
        _sessions.connect(client, creds, session_id, wait=False)
        socket = api.store.session_socket(session_id)
    sdir = api.store.session_dir(session_id)

    def renew() -> None:
        if not tunnel.renew_stale_cert(sdir, session_id, client, creds):
            raise NotFound(f"Session {name} no longer exists.")

    return _Service(
        socket,
        lambda: api.auth.authed(creds, client.disk_list),
        f"service of session {name}",
        renew,
    )
