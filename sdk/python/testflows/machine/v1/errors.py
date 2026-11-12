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
"""Exceptions raised by the SDK.

Each type is a condition, not a resource: `NotFound` is raised for a session,
a run or a checkpoint, and the message names what was not found. Every
exception derives from `Error`.
"""

from __future__ import annotations

import argparse
import contextlib
from typing import Generator, Optional

from testflows.machine._core import api
from testflows.machine._core import disks
from testflows.machine._core import ipc
from testflows.machine._core import refused
from testflows.machine._core import replay
from testflows.machine._core import sessions as _sessions
from testflows.machine._core import wait


class Error(Exception):
    """Base class of every exception the SDK raises.

    Catch it to handle any SDK failure; the message says what went wrong in
    one sentence, naming the run, session or disk involved.
    """


class NotSignedIn(Error):
    """There are no credentials: no stored login and no
    TESTFLOWS_MACHINE_API_KEY.

    Sign in with `login.start` and `login.verify`, or `machine login`, or
    set TESTFLOWS_MACHINE_API_KEY.
    """


class Unauthorized(Error):
    """The credentials or the sign-in code were rejected.

    The login expired or was revoked, the API key is not valid, or the code
    was wrong. Sign in again.
    """


class Forbidden(Error):
    """The account may not do this, such as an account that is not active,
    or a request beyond its plan's limits."""


class NoCapacity(Error):
    """No host of the session's class has room for it now: not enough CPUs,
    RAM or disk, which the message names.

    Nothing about the request is wrong. The same create can succeed once
    other sessions end; a smaller session or another class may fit now. A
    session that was created and then lost its room raises this too, and
    keeps its name until it is deleted.
    """


class ClassUnavailable(Error):
    """No machine of the session's class is in service.

    Nothing about the request is wrong, and the class stays valid. A create
    on it succeeds once a machine of it is in service; a smaller session
    does not. Another class may be available now.
    """


class NotFound(Error):
    """No resource matches the name or id: a run, branch, checkpoint,
    session, disk, file or container. The message names what was looked
    for."""


class AlreadyExists(Error):
    """A resource with that name already exists. Choose another name, or
    use the existing resource."""


class InvalidArgument(Error, ValueError):
    """An argument is not valid, or a reference matches more than one
    resource. The message names the argument and what it accepts.

    It is a `ValueError` too, so code that catches that also catches this.
    """


class NotRunning(Error):
    """The resource exists and is not running: a run with no machine, or a
    session that is not `running`. Start it, or wait for it, first."""


class Conflict(Error):
    """The request conflicts with the resource's current state, such as a
    machine already running, a command in progress, a guest that has halted,
    or branches in the way of a delete. The message says which; change the
    state, then retry."""


class Diverged(Error):
    """A machine's state did not match what was recorded. `Run.play` stopped
    where the machine no longer matched the recording, or `Session.migrate`
    started a machine in another state than it stopped in. `Run.replay`
    reports a divergence in its result instead."""


class Timeout(Error, TimeoutError):
    """A wait ran out of time before the expected state was reached.

    The work goes on in the service: a run keeps booting, a session keeps
    starting. Wait again, or undo it. It is a `TimeoutError` too.
    """


class ServiceUnavailable(Error):
    """The service could not be reached, or cannot answer right now.

    The account's service did not answer, a session's tunnel is down, or
    the network failed. Retrying later is safe.
    """


class ApiError(Error):
    """The service failed to carry out the request for a reason of its own.
    The message is the service's reason."""


#: Engine exception type -> the public exception it becomes. Checked in order,
#: so a subclass is listed before its base.
_MAPPING: tuple[tuple[type[BaseException], type[Error]], ...] = (
    (api.auth.NotSignedIn, NotSignedIn),
    (api.client.Unauthorized, Unauthorized),
    (api.client.InvalidLoginCode, Unauthorized),
    (api.client.AccountInactive, Forbidden),
    (api.client.AccountClosing, Forbidden),
    (api.client.NoCapacity, NoCapacity),
    (api.client.ClassUnavailable, ClassUnavailable),
    (api.client.ServiceUnavailable, ServiceUnavailable),
    (api.client.SessionNameTaken, AlreadyExists),
    (api.transport.TransportError, ServiceUnavailable),
    (_sessions.SessionNotFound, NotFound),
    (_sessions.AmbiguousSession, InvalidArgument),
    (_sessions.SessionNoCapacity, NoCapacity),
    (_sessions.SessionNotRunning, NotRunning),
    (_sessions.NameTaken, AlreadyExists),
    (_sessions.Unreachable, ServiceUnavailable),
    (disks.NotFound, NotFound),
    (api.client.NotFound, NotFound),
    (api.client.Conflict, Conflict),
    (api.client.Forbidden, Forbidden),
    (api.client.Invalid, InvalidArgument),
    (api.client.ApiError, ApiError),
    (replay.Diverged, Diverged),
    (ipc.RunNotFound, NotFound),
    (ipc.AmbiguousRun, InvalidArgument),
    (refused.NotFound, NotFound),
    (refused.AlreadyExists, AlreadyExists),
    (refused.Invalid, InvalidArgument),
    (refused.NotRunning, NotRunning),
    (refused.Diverged, Diverged),
    (refused.TimedOut, Timeout),
    (refused.Refused, Conflict),
    (ipc.ProtocolError, ServiceUnavailable),
    (TimeoutError, Timeout),
    (ConnectionError, ServiceUnavailable),
    (argparse.ArgumentTypeError, InvalidArgument),
    (ValueError, InvalidArgument),
)

_C = ipc.IpcErrorCode

#: The service's refusal code -> the public exception it becomes. A code not
#: listed is the service's own failure, ApiError.
_CODES: dict[int, type[Error]] = {
    int(_C.NOT_RUNNING): NotRunning,
    int(_C.TERMINATED): NotRunning,
    int(_C.ALREADY_EXISTS): AlreadyExists,
    int(_C.INVALID): InvalidArgument,
    int(_C.OUT_OF_RANGE): InvalidArgument,
    int(_C.NOT_ANCESTOR): InvalidArgument,
    int(_C.NO_SUCH_PATH): NotFound,
    int(_C.NO_SUCH_RUN): NotFound,
    int(_C.NO_SUCH_TASK): NotFound,
    int(_C.NO_SUCH_STOP): NotFound,
    int(_C.NOT_A_FILE): InvalidArgument,
    int(_C.READ_ONLY): Conflict,
    int(_C.BUSY): Conflict,
    int(_C.ALREADY_PENDING): Conflict,
    int(_C.HASHING): Conflict,
    # The run was created without an RNG device; no later command adds one.
    int(_C.NO_RNG): InvalidArgument,
    int(_C.NOT_PENDING): Conflict,
    int(_C.STARTING): Conflict,
    int(_C.HAS_CHILDREN): Conflict,
    int(_C.NOT_SYNCED): Conflict,
    int(_C.REWIND_UNFINISHED): Conflict,
    int(_C.NEEDS_RECOVERY): Conflict,
    int(_C.IS_RUNNING): Conflict,
    int(_C.HALTED): Conflict,
    int(_C.NEVER_RAN): Conflict,
    int(_C.ALREADY_RAN): Conflict,
    int(_C.PUBLISH_SETTLING): Conflict,
    int(_C.PARENT_NOT_PUBLISHED): Conflict,
    int(_C.RUNNING_ELSEWHERE): Conflict,
    int(_C.NOT_READY): Conflict,
    int(_C.DESYNC): Diverged,
    int(_C.TIMEOUT): Timeout,
    int(_C.NOT_RESPONDING): ServiceUnavailable,
    int(_C.STORE_UNAVAILABLE): ServiceUnavailable,
}


def _public(exc: BaseException) -> Optional[type[Error]]:
    """The public exception type for an engine exception, or None."""
    for engine_type, public_type in _MAPPING:
        if isinstance(exc, engine_type):
            return public_type
    if isinstance(exc, (ipc.MachineError, wait.WaitAborted)):
        return _CODES.get(exc.code, ApiError)
    # A failure the engine reports with no type of its own: the service could
    # not carry out the request. Exactly RuntimeError, so a subclass that is a
    # bug, such as RecursionError, still surfaces as itself.
    if type(exc) is RuntimeError:
        return ApiError
    return None


@contextlib.contextmanager
def _translated() -> Generator[None, None, None]:
    """Raise the public exception for an engine exception, message unchanged."""
    try:
        yield
    except Error:
        raise
    except Exception as exc:
        public = _public(exc)
        if public is None:
            raise
        raise public(str(exc)) from None
