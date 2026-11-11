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
"""Sessions: cloud machines that host runs, as `machine sessions`."""

from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any, Generator, Literal, Optional, overload

from testflows.machine._core import api
from testflows.machine._core import migration as _migration
from testflows.machine._core import sessions as _sessions

from . import _args
from .errors import ApiError, InvalidArgument, NotFound, _translated

if TYPE_CHECKING:
    from .client import Client

#: The state of a session that was deleted.
_DELETED = "terminated"

#: MB in a GB: `create` takes storage in GB, the service in MB.
_MB_PER_GB = 1024

#: The fields a session exposes, in the order `as_json` lists them.
FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "state",
    "cpus",
    "mem_mb",
    "storage_mb",
    "instance_type",
    "error",
    "created_at",
    "started_at",
    "stopped_at",
)


class Session:
    """One session: a machine the account rents, with its own CPUs,
    memory and storage, where runs execute.

    Not created directly: `Client.sessions` creates, lists and shows
    sessions. `client.sessions.use(session)` makes one the client's current
    session, where its commands go. The attributes are what the last read
    returned; `refresh` reads them again.

    A session uses credits from the moment it is created until it stops. Used
    in a `with` block, it is deleted when the block exits, however it exits.
    Without `with`, it lives until `delete`, or until it has been idle for
    its plan's idle time: no command for that long, `keep` aside.

    Example:
        >>> with client.sessions.create("ci-1", cpus=4) as session:
        ...     client.sessions.use(session)
        ...     run = client.create(disk="app")
    """

    def __init__(self, client: "Client", row: dict[str, Any]) -> None:
        self._client = client
        self._row = row

    @property
    def id(self) -> str:
        """str: The session's id. A session moved with `migrate` has a new id,
        which this follows, whichever client or object moved it."""
        return self._client._now_id(str(self._row["session_id"]))

    @property
    def name(self) -> str:
        """str: The session's name, unique in the account; `rename` changes
        it."""
        return str(self._row.get("name") or self.id)

    @property
    def state(self) -> str:
        """str: `provisioning` while its machine is prepared, `running` once
        runs can execute there, then `stopping`, and `terminated` once it is
        gone; `failed` when it could not start, with `error` saying why."""
        return str(self._row.get("state") or "")

    @property
    def cpus(self) -> Optional[int]:
        """int: vCPUs reserved for the session and shared by its runs."""
        return _int_or_none(self._row.get("cpus"))

    @property
    def mem_mb(self) -> Optional[int]:
        """int: Memory reserved for the session in MB, shared by its runs."""
        return _int_or_none(self._row.get("mem_mb"))

    @property
    def storage_mb(self) -> Optional[int]:
        """int: Storage reserved for the session in MB, for the runs and disks
        copied there."""
        return _int_or_none(self._row.get("storage_mb"))

    @property
    def storage_gb(self) -> Optional[int]:
        """int: The disk in GB, the unit `Sessions.create` takes; rounded
        down when the service gave an uneven number of MB."""
        megabytes = self.storage_mb
        return None if megabytes is None else megabytes // _MB_PER_GB

    @property
    def instance_type(self) -> Optional[str]:
        """str: The class of machine the session runs on."""
        value = self._row.get("instance_type")
        return None if value is None else str(value)

    @property
    def error(self) -> Optional[str]:
        """str: Why the session failed; None unless `state` is `failed`."""
        value = self._row.get("error")
        return None if value is None else str(value)

    @property
    def created_at(self) -> Optional[str]:
        """str: When the session was created, ISO 8601."""
        return _str_or_none(self._row.get("created_at"))

    @property
    def started_at(self) -> Optional[str]:
        """str: When the session first reached `running`, ISO 8601; None
        before then."""
        return _str_or_none(self._row.get("started_at"))

    @property
    def stopped_at(self) -> Optional[str]:
        """str: When the session stopped, ISO 8601; None while it runs."""
        return _str_or_none(self._row.get("stopped_at"))

    @property
    def closing_at(self) -> Optional[str]:
        """str: When the session moves to another host for maintenance, ISO
        8601; None when no move is scheduled. Use `migrate` to move it sooner.
        Read again with `refresh`."""
        return _str_or_none(self._row.get("closing_at"))

    @property
    def closing_reason(self) -> Optional[str]:
        """str: Why the session moves to another host, such as a kernel
        update; None when no move is scheduled."""
        return _str_or_none(self._row.get("closing_reason"))

    def refresh(self) -> "Session":
        """Read the session again, so its attributes are current.

        Returns:
            Session: This session.

        Raises:
            NotFound: The session no longer exists.
        """
        client, creds = self._client._account()
        with _translated():
            self._row = _sessions.get(client, creds, self.id)
        return self

    def wait(
        self, state: str = "running", *, timeout: Optional[float] = None
    ) -> "Session":
        """Wait until the session is in `state`, as `machine sessions create`
        waits for `running`.

        A new session takes minutes to reach `running` while its machine is
        prepared. Waiting for `running` ends at once when the session can no
        longer get there, such as one that failed.

        Example:
            >>> session = client.sessions.create(cpus=4, wait=False)
            >>> session.wait("running", timeout=900)

        Args:
            state (str, optional): The state to wait for. Defaults to
                `running`.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Session: This session, read in `state`.

        Raises:
            NotRunning: Waiting for `running` and the session failed or
                stopped; `error` says why.
            Timeout: The session was not in `state` within `timeout`.
        """
        client, creds = self._client._account()
        with _translated():
            self._row = _sessions.wait_until(
                client, creds, self.id, state, timeout_s=timeout
            )
        return self

    def rename(self, new_name: str) -> "Session":
        """Rename the session, as `machine sessions rename` does.

        Its machine, its runs and its id are unchanged.

        Args:
            new_name (str): The new name: up to 64 letters, digits, dots,
                dashes and underscores, starting with a letter or digit.

        Returns:
            Session: This session, renamed.

        Raises:
            InvalidArgument: `new_name` is not a valid session name.
            AlreadyExists: Another session has this name.
        """
        _require_valid_name(new_name)
        client, creds = self._client._account()
        with _translated():
            api.auth.authed(
                creds, lambda t: client.session_rename(t, self.id, new_name)
            )
        self._row = {**self._row, "name": new_name}
        return self

    def keep(self, minutes: int) -> "Session":
        """Keep the session running while it is idle, as `machine sessions keep` does.

        A session is idle while it serves no command. An idle session stops
        after the plan's idle time. Keep holds it for `minutes`, up to the
        plan's limit. A later keep replaces the hold.

        Args:
            minutes (int): How long to hold the session, in minutes.

        Returns:
            Session: This session, kept.

        Raises:
            InvalidArgument: `minutes` is less than 1.
            Forbidden: `minutes` is more than the plan allows.
            NotFound: The session is not running.
        """
        if minutes < 1:
            raise InvalidArgument(f"Keep for at least 1 minute, not {minutes}.")
        client, creds = self._client._account()
        with _translated():
            self._row = _sessions.keep(client, creds, self.id, minutes)
        return self

    def migrate(self) -> "Session":
        """Move the session to a new session with its runs, as `machine
        sessions migrate` does.

        A session of the same class and size is created. Each running machine
        is stopped, which checkpoints it, and started in the new session at
        that checkpoint, in the same state. The new session takes the name,
        and the old one is deleted. A command running on a machine ends, as
        `stop` ends it.

        This object becomes the new session. When it was the client's current
        session, the client uses the new one.

        Example:
            >>> session = client.sessions.show("ci-1")
            >>> session.migrate().name
            'ci-1'

        Returns:
            Session: This session, moved.

        Raises:
            ApiError: The move failed. Every machine was put back in the
                session and the new one was deleted; or a machine started in
                another state, which is a divergence, and both sessions are
                kept.
        """
        client, creds = self._client._account()
        old_id = self.id
        with _translated():
            planned = _migration.plan(client, creds, old_id)
            moved = _migration.migrate(client, creds, planned)
        self._row = moved.session
        self._client._moved(old_id, self)
        return self

    def delete(self, *, wait: bool = True, timeout: Optional[float] = None) -> None:
        """Delete the session, as `machine sessions delete` does.

        Its machines stop, their runs are published to shared storage, and
        the session stops using credits. A session already gone is not an error,
        so cleanup code can call this unconditionally. When it is the
        client's current session, the client has none after.

        Example:
            >>> try:
            ...     run_tests(client)
            ... finally:
            ...     session.delete()

        Args:
            wait (bool, optional): Return once the session is gone. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Raises:
            Timeout: The session was not gone within `timeout`.
        """
        client, creds = self._client._account()
        with _translated():
            _sessions.delete(client, creds, self.id)
            if wait:
                _sessions.wait_until_gone(client, creds, self.id, timeout_s=timeout)
            api.store.clear_session(self.id)
        self._client._forget(self.id)

    def as_json(self) -> dict[str, Any]:
        """The session's attributes as a JSON-serializable dict.

        Returns:
            dict[str, Any]: One key per attribute, `id` through `stopped_at`.
        """
        return {field: getattr(self, field) for field in FIELDS}

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc: object) -> None:
        self.delete()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Session) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)

    def __repr__(self) -> str:
        return f"Session(name={self.name!r}, id={self.id!r}, state={self.state!r})"


class Sessions:
    """The account's sessions, as `Client.sessions`; not created directly.

    Call it to list them, as `machine sessions` does. A session named by
    `session` is a `Session`, or its name, id or unique id prefix; where it
    may be left out, the account's only session is meant.

    Example:
        >>> [s.name for s in client.sessions()]
        ['ci-1', 'ci-2']
        >>> client.sessions.use("ci-1")
    """

    def __init__(self, client: "Client") -> None:
        self._client = client

    @overload
    def __call__(
        self, *, watch: Literal[False] = ..., timeout: Optional[float] = ...
    ) -> builtins.list[Session]: ...

    @overload
    def __call__(
        self, *, watch: Literal[True], timeout: Optional[float] = ...
    ) -> Generator[builtins.list[Session], None, None]: ...

    def __call__(
        self, *, watch: bool = False, timeout: Optional[float] = None
    ) -> "builtins.list[Session] | Generator[builtins.list[Session], None, None]":
        """List the account's sessions, as `machine sessions` does; the
        same as `list`.

        Args:
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Session]: The sessions; with `watch`, an iterator of such
            lists.
        """
        return _args.watched(self._rows, self._sessions, watch, timeout)

    @overload
    def list(
        self, *, watch: Literal[False] = ..., timeout: Optional[float] = ...
    ) -> builtins.list[Session]: ...

    @overload
    def list(
        self, *, watch: Literal[True], timeout: Optional[float] = ...
    ) -> Generator[builtins.list[Session], None, None]: ...

    def list(
        self, *, watch: bool = False, timeout: Optional[float] = None
    ) -> "builtins.list[Session] | Generator[builtins.list[Session], None, None]":
        """List the account's sessions, as `machine sessions list` does.

        Deleted sessions are left out. With `watch`, it yields the list at
        once, then again each time it changes: a session created, deleted,
        or changing state. Leave the loop, or pass `timeout`, to stop.

        Example:
            >>> for sessions in client.sessions.list(watch=True, timeout=900):
            ...     if all(s.state == "running" for s in sessions):
            ...         break

        Args:
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Session]: The sessions; with `watch`, an iterator of such
            lists.

        Raises:
            NotSignedIn: There are no credentials.
        """
        return _args.watched(self._rows, self._sessions, watch, timeout)

    def create(
        self,
        name: Optional[str] = None,
        *,
        cpus: int = 1,
        mem: Optional[int] = None,
        storage: Optional[int] = None,
        class_: Optional[str] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Session:
        """Create a session, as `machine sessions create` does.

        The session uses credits from the moment it is created until it
        stops. Create it in a `with` block to have it deleted when the
        block exits, however it exits; without `with`, it lives until
        `Session.delete`, or until it has been idle for its plan's idle time. Its memory and storage default to what `cpus` buys
        on the account's hardware class. `use` makes it the client's current
        session.

        Example:
            >>> with client.sessions.create("ci-1", cpus=4) as session:
            ...     client.sessions.use(session)
            ...     run = client.create(disk="app")

        Args:
            name (str, optional): The session's name: up to 64 letters,
                digits, dots, dashes and underscores, starting with a letter
                or digit. One is generated when omitted.
            cpus (int, optional): vCPUs to reserve. Defaults to 1.
            mem (int, optional): Memory to reserve in MB.
            storage (int, optional): Disk to reserve, in GB; the session
                reports it as `storage_mb`, and `storage_gb` in these terms.
            class_ (str, optional): The hardware class to run on; defaults
                to the plan's. The trailing underscore avoids the Python
                keyword.
            wait (bool, optional): Return once the session is running.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Session: The new session.

        Raises:
            InvalidArgument: `name` is not a valid session name.
            AlreadyExists: A session has this name.
            Forbidden: The account may not create sessions, such as one that
                is not active or over its plan's limits, or the class is not
                sold to the account's plan.
            NoCapacity: No host of the class has room for the session now.
                Retry later, ask for less, or pick another class.
            ClassUnavailable: No machine of the class is in service. Pick
                another class.
            NotRunning: The session failed to start; `error` says why.
            Timeout: It was not running within `timeout`.
        """
        if name is not None:
            _require_valid_name(name)
        client, creds = self._client._account()
        with _translated():
            row = _sessions.create(
                client,
                creds,
                cpus,
                name,
                class_,
                mem,
                None if storage is None else storage * _MB_PER_GB,
            )
        session = Session(self._client, row)
        if wait:
            session.wait("running", timeout=timeout)
        return session

    def use(
        self,
        session: "Session | str | None" = None,
        *,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Session:
        """Make a session the client's current session, as `machine sessions
        use` does.

        A session this machine already holds a tunnel to is RECHECKED, not
        trusted: the tunnel outlives a daemon that has gone silent, so `use`
        tests it and reconnects through a fresh tunnel when it does not
        answer. Otherwise it connects, waiting while the session is still
        provisioning. Only this client changes: the CLI's current session is
        left as it is.

        Example:
            >>> session = client.sessions.use("ci-1")
            >>> client.session == session
            True

        Args:
            session (Session or str, optional): The session; defaults to the
                only one.
            wait (bool, optional): Wait while the session is provisioning;
                False refuses a session that is not running yet. Defaults to
                True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Session: The session, now current.

        Raises:
            NotFound: No session matches.
            InvalidArgument: The prefix matches more than one session, or
                none is named and there are several.
            NotRunning: The session is not running and `wait` is False, or it
                failed.
            ServiceUnavailable: The session does not respond.
        """
        known = self.show(session)
        with _translated():
            if api.store.session_connected(known.id) and _sessions.recheck(known.id):
                self._client._use(known)
                return known
        return self.connect(known, wait=wait, timeout=timeout)

    def migrate(self, session: "Session | str | None" = None) -> Session:
        """Move a session to a new session with its runs, as `machine sessions
        migrate` does. See `Session.migrate`.

        Args:
            session (Session or str, optional): The session; defaults to the
                only one.

        Returns:
            Session: The session, moved.

        Raises:
            NotFound: No session matches.
            ApiError: The move failed. See `Session.migrate`.
        """
        target = session if isinstance(session, Session) else self.show(session)
        return target.migrate()

    def show(self, session: "Session | str | None" = None) -> Session:
        """Read a session, as `machine sessions show` does.

        Args:
            session (Session or str, optional): The session; defaults to the
                only one.

        Returns:
            Session: The session, read now.

        Raises:
            NotFound: No session matches; a deleted session is not found.
            InvalidArgument: The prefix matches more than one session, or
                none is named and there are several.
        """
        client, creds = self._client._account()
        ref = _ref(session)
        with _translated():
            session_id = _sessions.resolve_ref_or_sole(self._rows(), ref)
            row = _sessions.get(client, creds, session_id)
        if row.get("state") == _DELETED:
            raise NotFound(f"No such session: {ref!r}.")
        return Session(self._client, row)

    def delete(
        self,
        session: "Session | str | None" = None,
        *,
        all: bool = False,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> builtins.list[Session]:
        """Delete a session, or every session, as `machine sessions delete`
        does.

        Its machines stop, their runs are published to shared storage, and
        the session stops costing. Already gone is not an error. A prune is
        refused while any session is live, so `all` is what
        `storage.prune` needs.

        Example:
            >>> client.sessions.delete("ci-1")
            >>> client.sessions.delete(all=True)

        Args:
            session (Session or str, optional): The session; defaults to the
                only one.
            all (bool, optional): Delete every session. Defaults to False.
            wait (bool, optional): Return once the sessions are gone; with
                `all` they are asked one after another and waited for after
                that, so N sessions cost one wait. False returns once each
                request is accepted, as `machine sessions delete -a` does.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            list[Session]: The sessions deleted.

        Raises:
            InvalidArgument: A session is named with `all`.
            NotFound: No session matches.
            ApiError: With `all`, some sessions could not be deleted; the
                message names the first.
        """
        if all:
            if session is not None:
                raise InvalidArgument("Name a session or pass all=True, not both.")
            return self._delete_all(wait=wait, timeout=timeout)
        doomed = session if isinstance(session, Session) else self.show(session)
        doomed.delete(wait=wait, timeout=timeout)
        return [doomed]

    def rename(self, session: "Session | str", new_name: str) -> Session:
        """Rename a session, as `machine sessions rename` does.

        Its machine and its runs are unchanged.

        Args:
            session (Session or str): The session.
            new_name (str): The new name: up to 64 letters, digits, dots,
                dashes and underscores, starting with a letter or digit.

        Returns:
            Session: The session, renamed.

        Raises:
            InvalidArgument: `new_name` is not a valid session name.
            NotFound: No session matches.
            AlreadyExists: Another session has this name.
        """
        target = session if isinstance(session, Session) else self.show(session)
        return target.rename(new_name)

    def keep(self, session: "Session | str", minutes: int) -> Session:
        """Keep a session running while it is idle, as `machine sessions keep` does.

        Args:
            session (Session or str): The session.
            minutes (int): How long to hold it, in minutes, up to the plan's
                limit.

        Returns:
            Session: The session, kept.

        Raises:
            InvalidArgument: `minutes` is less than 1.
            Forbidden: `minutes` is more than the plan allows.
            NotFound: No session matches, or it is not running.
        """
        target = session if isinstance(session, Session) else self.show(session)
        return target.keep(minutes)

    def connect(
        self,
        session: "Session | str | None" = None,
        *,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Session:
        """Connect to a session and make it the client's current session, as
        `machine sessions connect` does.

        Opens this machine's tunnel to the session, waiting while it is
        still provisioning. `use` is the everyday verb.

        Args:
            session (Session or str, optional): The session; defaults to the
                only one.
            wait (bool, optional): Wait while the session is provisioning;
                False refuses a session that is not running yet. Defaults to
                True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Session: The session, now current.

        Raises:
            NotFound: No session matches.
            NotRunning: The session is not running and `wait` is False, or it
                failed.
            ServiceUnavailable: The session does not respond.
        """
        client, creds = self._client._account()
        with _translated():
            row = _sessions.connect(
                client, creds, _ref(session), wait=wait, timeout_s=timeout
            )
        made = Session(self._client, row)
        self._client._use(made)
        return made

    def disconnect(self, session: "Session | str | None" = None) -> bool:
        """Close this machine's tunnel to a session, as `machine sessions
        disconnect` does.

        The session keeps running. The tunnel is shared on this machine, so
        the CLI and every other program lose it too; the next command that
        needs it opens it again.

        Args:
            session (Session or str, optional): The session; defaults to the
                client's current one.

        Returns:
            bool: True when a tunnel was closed.

        Raises:
            InvalidArgument: None is named and the client has no session.
            NotFound: No session matches.
        """
        target = self._client.session if session is None else None
        if session is not None:
            target = session if isinstance(session, Session) else self.show(session)
        if target is None:
            raise InvalidArgument("No session. Name the session to disconnect.")
        with _translated():
            closed = _sessions.tunnel_stop(target.id)
        self._client._forget(target.id)
        return closed

    def _list(self) -> builtins.list[Session]:
        return self._sessions(self._rows())

    def _sessions(self, rows: builtins.list[dict[str, Any]]) -> builtins.list[Session]:
        return [Session(self._client, row) for row in rows]

    def _rows(self) -> builtins.list[dict[str, Any]]:
        """The account's sessions, less the deleted ones: the service's list can
        trail a deletion that has already completed."""
        client, creds = self._client._account()
        with _translated():
            rows = api.auth.authed(creds, client.session_list)
        return [row for row in rows if row.get("state") != _DELETED]

    def _delete_all(
        self, *, wait: bool, timeout: Optional[float]
    ) -> builtins.list[Session]:
        """Delete every session, each in turn, reporting the first failure
        after trying them all.

        Asked one after another rather than waited for one after another: the
        wait, when there is one, comes after every session has been told to
        go, so N sessions cost one wait and not N.
        """
        done: builtins.list[Session] = []
        errors: builtins.list[str] = []
        for session in self._list():
            try:
                session.delete(wait=False)
                done.append(session)
            except Exception as exc:
                errors.append(f"{session.name}: {exc}")
        if errors:
            raise ApiError(
                f"Deleted {len(done)}, {len(errors)} failed; first {errors[0]}"
            )
        if wait:
            for session in done:
                with _translated():
                    client, creds = self._client._account()
                    _sessions.wait_until_gone(
                        client, creds, session.id, timeout_s=timeout
                    )
        return done


def _ref(session: "Session | str | None") -> Optional[str]:
    """What names `session` to the service: its id, or the text given."""
    return session.id if isinstance(session, Session) else session


def _require_valid_name(name: str) -> None:
    if not _sessions.valid_name(name):
        raise InvalidArgument(f"Invalid session name {name!r}: {_sessions.NAME_RULE}.")


def _int_or_none(value: Any) -> Optional[int]:
    return None if value is None else int(value)


def _str_or_none(value: Any) -> Optional[str]:
    return None if value is None else str(value)
