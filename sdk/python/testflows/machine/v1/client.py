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
"""The client: where the SDK starts, and the one object a program holds.

The SDK mirrors the CLI. A top-level command is a method of `Client`, a
command group is an attribute, and a command that takes a run is a method of
`Run`: `machine create` is `client.create()`, `machine sessions use` is
`client.sessions.use()`, and `machine start app` is `run.start()`.
"""

from __future__ import annotations

import builtins
import contextlib
import dataclasses
from datetime import datetime, timezone
import time
import os
from pathlib import Path
from typing import Any, Callable, Generator, Literal, Optional, Sequence, overload

from testflows.machine._core import api
from testflows.machine._core import branches as _branches
from testflows.machine._core import cloud_run_source as _cloud_run_source
from testflows.machine._core import ipc as _ipc
from testflows.machine._core import lifecycle as _lifecycle
from testflows.machine._core import machines as _machines
from testflows.machine._core import parsers as _parsers
from testflows.machine._core import ping as _ping
from testflows.machine._core import storage_ops as _storage_ops
from testflows.machine._core import run_source as _run_source
from testflows.machine._core import sessions as _sessions
from testflows.machine._core import sorting as _sorting
from testflows.machine._core import wait as _wait
from testflows.machine._core import wire as _wire

from . import _args
from . import _service
from . import _warn
from . import _when
from . import results
from .errors import ApiError, InvalidArgument, ServiceUnavailable, _translated
from .disks import Disk, Disks
from .ping import Ping, _public
from .runs import Run
from .sessions import Session, SessionClosing, Sessions

#: Set to 1, a client given no session works with the service on this
#: machine.
_LOCAL_ENV = "TESTFLOWS_MACHINE_LOCAL"

#: The values `create` accepts for its enumerated options.
_MEM_TYPES = ("auto", "hugepages")
_MEM_MODES = ("auto", "map", "lazy")
_CONSOLES = ("serial", "virtio")
_FRAMEBUFFERS = ("vnc", "gtk", "sdl", "none")

#: A notice's deadline as the client keys it, and as `Session.closing_at`
#: holds it when the service cannot be read: ISO 8601 in UTC.
_NOTICE_TIME = "%Y-%m-%dT%H:%M:%S"


def _deadline(value: str) -> datetime:
    """A deadline as the service or the record writes it, in UTC. One with no
    offset is UTC already."""
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _deadline_key(at: datetime) -> str:
    """How the client remembers a deadline it told."""
    return at.astimezone(timezone.utc).strftime(_NOTICE_TIME)


#: `runs --parents`: where a run reads its data parents from.
_PARENTS = ("local", "remote")

#: What `create` accepts, the bounds the CLI holds the same flags to. A vCPU
#: count and a memory size the machine cannot serve are refused here rather
#: than at the spawn.
_MAX_CPUS = _wire.MAX_VCPUS
_MAX_MEM_MB = 1048576


class Client:
    """The one object a program holds, as the `machine` command is.

    A top-level CLI command is a method here, and a command group is an
    attribute: `machine create` is `create`, `machine runs` is `runs`,
    `machine sessions use` is `sessions.use`. A command that takes a run is
    a method of the `Run` these return.

    A run lives in a session: a machine the account rents, with its own
    CPUs, memory and storage. The client has a current session, as the CLI
    does: `session=` sets it, as `-s` does, and `sessions.use` or
    `sessions.connect` change it for this client only, never for the CLI.
    Commands that need a machine go to the current session at the time of
    the call. With no session, reading the account's runs still works:
    `runs`, `branches`, `marks`, and a run's `describe`, `lineage` and
    `checkpoints` read the account's catalog.

    Nothing is contacted until a call needs it. Every call signs in with
    TESTFLOWS_MACHINE_API_KEY when it is set, otherwise the login stored by
    `machine login` or `login.verify`.

    Example:
        >>> import testflows.machine.v1 as machine
        >>> client = machine.Client()
        >>> with client.sessions.create("ci-1", cpus=4) as session:
        ...     client.sessions.use(session)
        ...     run = client.create("app", disk="app")
        ...     run.run(until="tasks")
        ...     run.focus.add("/app")
        ...     run.run(until="/app:exit")
        ...     run.checkpoint("done")

    A session can be scheduled to stop for maintenance of its host. The
    client reads the notice at the start of a call into the session, at most
    once every ten minutes from the service, and tells the program once for
    each notice: through `on_session_closing` when it is given, or with a
    `SessionClosing` warning. The session's `closing_at` and
    `closing_reason` say when and why. Use `Session.migrate` before then to
    keep its machines running. A call that is already running when the
    deadline passes is not told.

    Example:
        >>> def closing(session):
        ...     session.migrate()
        >>> client = machine.Client(on_session_closing=closing)

    Args:
        session (str, optional): The current session, by name, id or unique
            id prefix, as `-s` names it.
        api_url (str, optional): The service's URL, as `--api-url` gives it;
            defaults to TESTFLOWS_MACHINE_API_URL, else the public service.
        on_session_closing (callable, optional): Called with the `Session`
            once for each maintenance notice, before the call that found it
            goes on. A call it makes goes on in the session it leaves
            current, so a callback that migrates moves the call with it. An
            exception it raises is raised from that call, and the notice is
            not told again.
    """

    def __init__(
        self,
        *,
        session: Optional[str] = None,
        api_url: Optional[str] = None,
        on_session_closing: Optional[Callable[[Session], None]] = None,
    ) -> None:
        local = os.environ.get(_LOCAL_ENV) == "1"
        if local and session is not None:
            raise InvalidArgument(
                f"{_LOCAL_ENV} is set, so a client cannot take a session."
                f" Unset it to work with session {session!r}."
            )
        self._local = local
        self._session_ref = session
        self._session: Optional[Session] = None
        #: The session each moved one went to, `sessions migrate`, so every
        #: Session object of a moved session names where it went.
        self._moved_ids: dict[str, str] = {}
        self._api_url = api_url
        self._api: Optional[tuple[api.client.Client, dict[str, Any]]] = None
        self._services: dict[str, _service._Service] = {}
        self._on_session_closing = on_session_closing
        #: The deadlines each session's notice was told for, so a notice is
        #: told once, and again only when it changes. Both the record's and
        #: the session's as read again, which can differ while the record lags.
        self._closing_told: dict[str, set[str]] = {}

    @classmethod
    def _signed_in(
        cls, client: api.client.Client, creds: dict[str, Any], api_url: Optional[str]
    ) -> "Client":
        """A client with no session, over a login already made."""
        made = cls(api_url=api_url)
        made._api = (client, creds)
        return made

    # -- the session -----------------------------------------------------------

    @property
    def session(self) -> Optional[Session]:
        """Session: The current session, read on first use; None when the
        client has none.

        Raises:
            NotFound: No session matches the name the client was given.
            InvalidArgument: The name matches more than one session.
        """
        if self._session_ref is None:
            return None
        if self._session is None:
            self._session = self.sessions.show(self._session_ref)
        return self._session

    @property
    def sessions(self) -> Sessions:
        """Sessions: The account's sessions, as `machine sessions`: create,
        list, use and delete them.

        Raises:
            NotSignedIn: There are no credentials.
        """
        return Sessions(self)

    @property
    def disks(self) -> Disks:
        """Disks: The account's disks, as `machine disks`: push, list and
        delete them.

        Raises:
            NotSignedIn: There are no credentials.
        """
        return Disks(*self._account())

    @property
    def storage(self) -> "Storage":
        """Storage: What the account stores, as `machine storage`."""
        return Storage(self)

    @property
    def preload(self) -> "Preload":
        """Preload: Copies onto the current session's disk, as `machine
        preload`."""
        return Preload(self)

    @property
    def offload(self) -> "Offload":
        """Offload: Copies dropped from the current session's disk, as
        `machine offload`."""
        return Offload(self)

    @property
    def marks(self) -> "Marks":
        """Marks: The marks of every tree, as `machine marks`."""
        return Marks(self)

    @property
    def delete(self) -> "Delete":
        """Delete: Deleting every run of the session, or those booting a
        disk, as `machine delete -a`; `cleanup` resumes interrupted ones."""
        return Delete(self)

    @property
    def runs(self) -> "Runs":
        """Runs: The account's runs, as `machine runs`: call it to list them,
        and index it to get one."""
        return Runs(self)

    # -- runs ------------------------------------------------------------------

    def create(
        self,
        name: Optional[str] = None,
        *,
        disk: "Disk | str",
        like: "Run | str | None" = None,
        labels: Optional[dict[str, str]] = None,
        keep_output: "Sequence[str] | str | None" = None,
        drop_output: "Sequence[str] | str | None" = None,
        cpus: Optional[int] = None,
        mem: Optional[int] = None,
        mem_type: Optional[str] = None,
        console: Optional[str] = None,
        mem_mode: Optional[str] = None,
        fs_sync: Optional[bool] = None,
        fs_dirsync: Optional[bool] = None,
        fb: Optional[str] = None,
        rng: Optional[bool] = None,
        rng_seed: Optional[int] = None,
        daemon: Optional[bool] = None,
        single_step: Optional[bool] = None,
        limit: Optional[int] = None,
        backstop_limit: Optional[int] = None,
        pin_cpu: Optional[int] = None,
        vtime_base: Optional[int] = None,
        vtime_rate: Optional[str] = None,
        vtime_io_cost: Optional[int] = None,
        vtime_rdtsc_cost: Optional[int] = None,
        vtime_pause_cost: Optional[int] = None,
        auto_checkpoints: Optional[int] = None,
        side_dump: Optional[bool] = None,
        side_trace: Optional[bool] = None,
        mem_hash: Optional[bool] = None,
        disk_hash: Optional[bool] = None,
        repeatable: Optional[bool] = None,
        limit_margin: Optional[int] = None,
        timer_slack: Optional[str] = None,
        instr_margin: Optional[int] = None,
        dispatch_bound: Optional[str] = None,
        fast_backstop: Optional[int] = None,
        irq_wait: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> Run:
        """Create a run on a disk and bring its machine up, as
        `machine create` does, in the current session.

        The machine boots nothing on its own: it waits for commands, and
        `Run.run` drives it. Every option left None takes its value from
        `like`, a run whose options are copied, else the default: 1 vCPU,
        256 MB, an RNG device, fast execution with no limit. Used in a
        `with` block, the run is deleted when the block exits, unless the
        block calls `Run.keep`.

        Example:
            >>> run = client.create("app", disk="app", cpus=2,
            ...                     rng_seed=7, labels={"suite": "nightly"})
            >>> twin = client.create(disk="app", like=run, cpus=4)
            >>> with client.create(disk="app") as scratch:
            ...     scratch.run(until="tasks")

        Args:
            name (str, optional): The run's name; one is generated when
                omitted.
            disk (str): The disk to boot, by name; `Client.disks` lists
                them and `Disks.push` adds one.
            like (Run or str, optional): A run whose options to copy.
            labels (dict[str, str], optional): Labels to attach, for
                `runs(labels=...)`; an empty value removes one `like`
                carried.
            keep_output (str or list[str], optional): Sources whose output
                the run stores though it is dropped: an output file or a
                directory of them by its path, such as
                `/output/drop/app/debug.log`, or a pattern such as
                `/output/drop/*/*.log`. The run keeps them for its whole
                life, and a branch of it has them unless it names its own.
            drop_output (str or list[str], optional): Sources whose output
                the run does not store: a path, a device such as `ttyS1`,
                or a pattern. `Run.fork` describes the patterns and which
                of a kept and a dropped name decides.
            cpus (int, optional): vCPUs.
            mem (int, optional): Guest memory in MB.
            mem_type (str, optional): Guest memory backing: `auto` or
                `hugepages`.
            mem_mode (str, optional): How a cold restore maps memory:
                `auto`, `map` or `lazy`.
            console (str, optional): The device the run's console is:
                `serial` for the first serial port, `ttyS0`, or `virtio`
                for the virtio console, `hvc0`. Defaults to `serial`.
            fs_sync (bool, optional): Mount the root filesystem `sync`, so
                writes reach the disk at once.
            fs_dirsync (bool, optional): Mount it `dirsync`.
            fb (str, optional): Open a framebuffer to watch the guest:
                `vnc`, `gtk`, `sdl` or `none`.
            rng (bool, optional): Give the guest an RNG device, whose seed
                a branch can change; on by default, False leaves it out.
            rng_seed (int, optional): The RNG's seed; the same seed gives
                the same random bytes.
            daemon (bool, optional): Wait for commands; False runs the
                machine to completion. The run keeps it, and every later
                start and fork repeats it. Defaults to True.
            single_step (bool, optional): Run in step mode, for runs that
                do not wait for commands. Kept the same way. Defaults to
                False.
            limit (int, optional): Conditional branches a fast dispatch may
                run, steps in step mode; 0 for no limit.
            backstop_limit (int, optional): Step modes only: instructions a
                dispatch may run without stepping.
            pin_cpu (int, optional): Pin this machine to a host CPU; the
                default lets it pick the least-loaded. A CPU belongs to one
                machine, so the run does not keep it.
            vtime_base (int, optional): The guest's clock at boot, in Unix
                seconds; defaults to 2026-03-01.
            vtime_rate (str, optional): Virtual nanoseconds per instruction,
                `n` or `n/d`.
            vtime_io_cost (int, optional): Virtual nanoseconds each I/O exit
                costs.
            vtime_rdtsc_cost (int, optional): Virtual nanoseconds each RDTSC
                exit costs.
            vtime_pause_cost (int, optional): Virtual nanoseconds each PAUSE
                costs.
            auto_checkpoints (int, optional): Checkpoint every this many
                entries; 0 for never.
            side_dump (bool, optional): Record the machine's state with every
                entry, which `Run.dump(entry=...)` reads back; costs disk.
            side_trace (bool, optional): Record an execution trace with
                every entry.
            mem_hash (bool, optional): Hash the machine's memory at every
                entry, so that a replay compares memory too. Off by default.
            disk_hash (bool, optional): Hash the machine's disk at every
                entry, so that a replay compares the disk too. Off by default.
            repeatable (bool, optional): Record so that the same run,
                recorded again from the same start with the same commands,
                records the same entries. Fast execution stops exactly at
                its limit and at timer deadlines, which records more
                slowly. The run keeps it, and every start and fork of it
                records the same way. None: the session's default. A
                run's describe shows the value it was given.
            limit_margin (int, optional): How many branches short of each
                stop the branch counter interrupts. Fewer is faster, too
                few misses stops. Defaults to the skid of the host's
                branch counter.
            timer_slack (str, optional): How late a timer may be in code
                that takes no exit, a duration such as `20ms`, or `0` for
                no later than its deadline. More is faster. Defaults to
                `20ms`.
            instr_margin (int, optional): How many instructions short of a
                stop placed by an instruction count the instruction counter
                interrupts. A replay that passes such a stop needs more.
                Defaults to the host's: 50,000 on AMD, 3,630 on Intel.
            dispatch_bound (str, optional): The host time a fast dispatch
                may run before it stops, a duration such as `500ms`. A stop
                it places does not repeat in another recording. Defaults to
                `1s`.
            fast_backstop (int, optional): How many instructions a fast
                dispatch may run with no other stop, in code that retires no
                branch. Defaults to 100,000,000.
            irq_wait (int, optional): How many branches a dispatch runs
                while an interrupt waits for the guest to take it. Defaults
                to 200.
            wait (bool, optional): Return once the machine accepts commands.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: The new run.

        Raises:
            InvalidArgument: An option or label is not valid, or the client
                has no session.
            AlreadyExists: A run has this name.
            NotFound: The disk or the `like` run does not exist.
            Timeout: The machine was not ready within `timeout`.
            ApiError: The machine failed to start; the message says why.
        """
        # The same bounds the CLI holds these to, so a number the machine would
        # refuse is refused here, with the range in the message.
        _args.bounded("cpus", cpus, 1, _MAX_CPUS)
        _args.bounded("mem", mem, 1, _MAX_MEM_MB)
        _args.bounded("limit", limit, 0)
        _args.bounded("backstop_limit", backstop_limit, 0)
        _args.bounded("auto_checkpoints", auto_checkpoints, 0)
        _args.bounded("pin_cpu", pin_cpu, _args.MIN_PIN_CPU, _args.MAX_PIN_CPU)
        explicit: dict[str, Any] = {
            key: value
            for key, value in (
                ("cpus", cpus),
                ("memory", mem),
                ("mem_type", mem_type),
                ("mem_mode", mem_mode),
                ("console", console),
                ("fs_sync", fs_sync),
                ("fs_dirsync", fs_dirsync),
                ("fb", fb),
                ("rng", rng),
                ("rng_seed", rng_seed),
                ("daemon", daemon),
                ("single_step", single_step),
                ("limit", limit),
                ("backstop_limit", backstop_limit),
                ("pin_cpu", pin_cpu),
                ("vtime_base", vtime_base),
                ("vtime_io_cost", vtime_io_cost),
                ("vtime_rdtsc_cost", vtime_rdtsc_cost),
                ("vtime_pause_cost", vtime_pause_cost),
                ("auto_checkpoint_interval", auto_checkpoints),
                ("side_dump", side_dump),
                ("side_trace", side_trace),
                ("mem_hash", mem_hash),
                ("disk_hash", disk_hash),
                ("repeatable", repeatable),
                ("limit_margin", _args.limit_margin(limit_margin)),
                ("timer_slack", _args.timer_slack(timer_slack)),
                ("instr_margin", _args.stop_count("instr_margin", instr_margin)),
                ("dispatch_bound", _args.dispatch_bound(dispatch_bound)),
                ("fast_backstop", _args.stop_count("fast_backstop", fast_backstop)),
                ("irq_wait", _args.irq_wait(irq_wait)),
            )
            if value is not None
        }
        _args.check_choice("mem_type", mem_type, _MEM_TYPES)
        _args.check_choice("mem_mode", mem_mode, _MEM_MODES)
        _args.check_choice("console", console, _CONSOLES)
        _args.check_choice("fb", fb, _FRAMEBUFFERS)
        with _translated():
            for label in labels or {}:
                _machines.validate_label_name(label)
            if vtime_rate is not None:
                num, den = _parsers.vtime_rate_type(vtime_rate)
                explicit.update(vtime_rate_num=num, vtime_rate_den=den)
        service = self._service()
        resolved = service.disk(_args.disk_ref(disk))
        with service.client() as client, _translated():
            kwargs = dict(_machines.RUN_DEFAULTS)
            if like is not None:
                like_id = like.id if isinstance(like, Run) else like
                kwargs.update(
                    _machines.describe_to_run_kwargs(client.describe(like_id))
                )
            kwargs.update(explicit)
            # `like` first, then the disk's own labels, then this call's: a
            # disk labelled `version=1.2` is not relabelled 1.0 by copying
            # the options of a run that booted an older one.
            merged = {
                **(kwargs.pop("labels", None) or {}),
                **resolved.labels,
                **(labels or {}),
            }
            merged = {key: value for key, value in merged.items() if value}
            if merged:
                kwargs["labels"] = merged
            # What `like` copied stands for each list this call does not
            # give.
            kwargs["output"] = _args.stored(keep_output, drop_output).over(
                kwargs.get("output") or _ipc.STORED_BY_DIRECTORY
            )
            made = _machines.run_machine(
                client, name=name or "", disk=resolved.id, **kwargs
            )
            if wait:
                _lifecycle.wait_ready(client, made, timeout_s=timeout)
            run_name = _machines.display_name(client, made.run_id)
        return Run(self, made.run_id, run_name)

    @overload
    def ops(
        self,
        *,
        op_id: Optional[int] = ...,
        states: Optional[builtins.list[str]] = ...,
        types: Optional[builtins.list[str]] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[results.Op]: ...

    @overload
    def ops(
        self,
        *,
        op_id: Optional[int] = ...,
        states: Optional[builtins.list[str]] = ...,
        types: Optional[builtins.list[str]] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[results.Op], None, None]: ...

    def ops(
        self,
        *,
        op_id: Optional[int] = None,
        states: Optional[builtins.list[str]] = None,
        types: Optional[builtins.list[str]] = None,
        since: "_when.When | None" = None,
        until: "_when.When | None" = None,
        limit: Optional[int] = None,
        offset: int = 0,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[results.Op] | Generator[builtins.list[results.Op], None, None]":
        """List the current session's operations on runs, newest first, as
        `machine ops` does.

        An operation is a checkpoint, branch, switch or rewind the session
        carried out. The list is every operation in every state, which is
        where to look when one of those calls returned an error; `states`
        keeps some. Every matching operation is returned unless `limit` is
        given. `Run.ops` lists one run's.

        With `watch`, it yields the list at once, then again each time it
        changes, such as a checkpoint finishing. Leave the loop, or pass
        `timeout`, to stop watching.

        Example:
            >>> client.ops(states=["failed"], since="1h")
            [{'op_id': '17897...', 'op_type': 'checkpoint', 'state': 'failed', ...}]
            >>> for running in client.ops(states=["running"], watch=True, timeout=300):
            ...     if not running:
            ...         break

        Args:
            op_id (int, optional): Return this operation alone, whatever its
                state or type.
            states (list[str], optional): Keep these states: `running`,
                `unfinished`, `done`, `failed`; `^state` leaves one out.
                Defaults to every state.
            types (list[str], optional): Keep these types: `checkpoint`,
                `branch`, `switch`, `rewind`, `start`, `delete`; `^type`
                leaves one out. Defaults to every type.
            since (datetime or int or str, optional): Keep those started at
                or after this time: a datetime, Unix seconds, a date or an
                age such as `1h`.
            until (datetime or int or str, optional): Keep those started at
                or before this time.
            limit (int, optional): Return at most this many.
            offset (int, optional): Skip this many first. Defaults to 0.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Op]: One record per operation: its id,
            type, state, run, entry, start time and error; with `watch`, an
            iterator of such lists.

        Raises:
            InvalidArgument: A state, type or time is not valid, or the
                client has no session.
        """
        return self._ops(
            None,
            op_id=op_id,
            states=states,
            types=types,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
            watch=watch,
            timeout=timeout,
        )

    @overload
    def branches(
        self,
        *,
        running: bool = ...,
        depth: Optional[int] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[Run]: ...

    @overload
    def branches(
        self,
        *,
        running: bool = ...,
        depth: Optional[int] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[Run], None, None]: ...

    def branches(
        self,
        *,
        running: bool = False,
        depth: Optional[int] = None,
        since: "_when.When | None" = None,
        until: "_when.When | None" = None,
        sort: str = "created",
        reverse: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[Run] | Generator[builtins.list[Run], None, None]":
        """List the runs of every tree, newest first, as `machine branches`
        does.

        Every matching run is returned unless `limit` is given. Answers with
        no session, from the account's catalog. `Run.branches` lists one
        run's tree.

        With `watch`, it yields the list at once, then again each time it
        changes: a branch forked or deleted, or one starting or stopping.
        Leave the loop, or pass `timeout`, to stop watching.

        Example:
            >>> [b.name for b in client.branches(depth=0)]
            ['app', 'db']
            >>> for running in client.branches(running=True, watch=True, timeout=600):
            ...     if not running:
            ...         break

        Args:
            running (bool, optional): Keep those with a machine. Defaults to
                False.
            depth (int, optional): Keep those this many forks from their
                root; 0 is the roots.
            since (datetime or int or str, optional): Keep those created at
                or after this time: a datetime, Unix seconds, a date or an
                age such as `1h`.
            until (datetime or int or str, optional): Keep those created at
                or before this time.
            sort (str, optional): `created`, newest first, or `name`, A to
                Z. Defaults to `created`.
            reverse (bool, optional): Turn the order round. Defaults to
                False.
            limit (int, optional): Return at most this many.
            offset (int, optional): Skip this many first. Defaults to 0.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Run]: The runs; with `watch`, an iterator of such lists.

        Raises:
            InvalidArgument: `sort` or a time is not valid.
        """
        return self._branches(
            None,
            running=running,
            depth=depth,
            since=since,
            until=until,
            sort=sort,
            reverse=reverse,
            limit=limit,
            offset=offset,
            watch=watch,
            timeout=timeout,
        )

    def cleanup(self, *, force: bool = False) -> results.Cleanup:
        """Remove what machines that ended uncleanly left behind in the
        current session, as `machine cleanup` does.

        A machine that crashed or was killed from outside leaves stale
        entries the service clears here. Runs themselves are not deleted.
        With `force`, machines that no longer answer are killed and their
        entries removed too.

        Args:
            force (bool, optional): Also kill orphaned machines and remove
                entries whose machine is already gone. Defaults to False.

        Returns:
            Cleanup: What was cleaned, and the runs it touched.

        Raises:
            InvalidArgument: The client has no session.
        """
        with self._connection() as client, _translated():
            done = _machines.clean(client, force=force)
        return results.cleanup(dataclasses.asdict(done))

    def ping(self, *, count: int = 3) -> Ping:
        """Measure whether the current session's service answers, and how
        fast, as `machine ping` does.

        For a client with no session it measures the account's service
        instead. A service that cannot be reached is a result, not an
        exception: `Ping.ok` is False and `Ping.error` says why.

        Example:
            >>> ping = client.ping()
            >>> ping.ok, round(ping.rtt_ms, 1)
            (True, 1.8)

        Args:
            count (int, optional): Round trips to time, at least 1.
                Defaults to 3.

        Returns:
            Ping: Whether it answered, and its round trips.

        Raises:
            NotRunning: The current session is not running.
        """
        count = max(1, count)
        if self._local:
            return _public(_ping.probe_socket(None, count))
        session = self.session
        client, creds = self._account()
        if session is None:
            base_url = _ping.api_base_url(creds, self._api_url)
            return _public(_ping.probe_api(creds, base_url, count))
        try:
            with _translated():
                self._service()
        except ServiceUnavailable as unreachable:
            return _public(_ping.Probe(False, error=str(unreachable)))
        return _public(
            _ping.probe_session(client, creds, session.id, count, self._api_url)
        )

    def stop(
        self,
        *,
        all: bool,
        dry_run: bool = False,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> builtins.list[Run]:
        """Stop every running machine in the current session, as `machine
        stop -a` does.

        Each machine checkpoints its tip first, so its run starts again
        where it stopped. `Run.stop` stops one.

        Example:
            >>> [r.name for r in client.stop(all=True, dry_run=True)]
            ['app', 'app/try-1']
            >>> client.stop(all=True)

        Args:
            all (bool): Stop every running machine; required, as `-a` is.
            dry_run (bool, optional): List what would be stopped, stopping
                nothing. Defaults to False.
            wait (bool, optional): Return once every machine is gone.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            list[Run]: The runs stopped, or with `dry_run` those that would
            be.

        Raises:
            InvalidArgument: `all` is False, or the client has no session.
            ApiError: Some machines could not be stopped; the message names
                the first.
        """
        _require_all(all, "Stop one run with run.stop().")
        with self._connection() as client, _translated():
            names = _names(client)
            done, errors = _lifecycle.stop_all(
                client, dry_run=dry_run, until_stopped=wait, timeout_s=timeout
            )
        if isinstance(done, _machines.StopDryRunResult):
            return [Run(self, run_id, name) for name, run_id in done.would_stop]
        _raise_first(errors, f"Stopped {done.stopped}")
        return [Run(self, run_id, names.get(run_id, "")) for run_id in done.run_ids]

    def wait(
        self,
        *runs: "Run | str",
        for_: str,
        all: bool = False,
        interval: float = _wait.DEFAULT_INTERVAL_S,
        timeout: Optional[float] = None,
    ) -> None:
        """Wait until runs satisfy a predicate, as `machine wait` does.

        It watches without driving: other programs or threads run the
        machines. With several runs, it returns once every one satisfies
        it. `Run.wait` waits for one.

        Example:
            >>> client.wait(a, b, for_="/app:exit", timeout=600)
            >>> client.wait(for_="stopped", all=True)

        Args:
            *runs (Run or str): The runs, as `Run` objects or names.
            for_ (str): The predicate, as for `Run.wait`. The trailing
                underscore avoids the Python keyword.
            all (bool, optional): Wait for every running run in the session
                instead of naming them. Defaults to False.
            interval (float, optional): Seconds between checks. Defaults
                to 1.
            timeout (float, optional): Seconds to wait; None waits without a
                limit, 0 checks once.

        Raises:
            InvalidArgument: `for_` is not a predicate, runs are named with
                `all`, or none are named without it.
            Timeout: It did not hold for every run within `timeout`.
            NotRunning: A run stopped, so the predicate can no longer hold.
        """
        if all and runs:
            raise InvalidArgument("Name runs or pass all=True, not both.")
        if not all and not runs:
            raise InvalidArgument("Run name required.")
        _args.positive("interval", interval)
        predicate = _args.predicate(for_)
        with self._connection() as client, _translated():
            if all:
                live = _machines.list_machines(client, enrich=False)
                ids = [m.run_id for m in live]
            else:
                ids = [
                    r.id if isinstance(r, Run) else client.resolve_run_ref(r)
                    for r in runs
                ]
            _wait.poll_all(
                client, ids, predicate, timeout_s=timeout, interval_s=interval
            )

    def __repr__(self) -> str:
        if self._session_ref is not None:
            return f"Client(session={self._session_ref!r})"
        return "Client()"

    # -- internals -------------------------------------------------------------

    def _use(self, session: Session) -> None:
        """Make `session` current for this client."""
        self._session_ref = session.id
        self._session = session

    def _moved(self, old_id: str, session: Session) -> None:
        """`old_id` moved to `session`: every Session object of `old_id` names it
        now, and the client uses it in its place when it was current."""
        self._moved_ids[old_id] = session.id
        self._services.pop(old_id, None)
        if self._session is not None and self._session.id == session.id:
            self._use(session)

    def _forget(self, session_id: str) -> None:
        """Drop `session_id` as current, when it is: it was deleted."""
        self._services.pop(session_id, None)
        if self._session is not None and self._session.id == session_id:
            self._session_ref = None
            self._session = None

    def _account(self) -> tuple[api.client.Client, dict[str, Any]]:
        """The account's service and the login to it, signed in once."""
        if self._api is None:
            with _translated():
                self._api = api.auth.signed_in(self._api_url)
        return self._api

    def _service(self) -> _service._Service:
        """The current session's service."""
        if self._local:
            return self._services.setdefault("", _service.local())
        session = self.session
        if session is None:
            raise InvalidArgument(
                "No session. Use one with client.sessions.use(name),"
                " or create one with client.sessions.create()."
            )
        if session.id not in self._services:
            client, creds = self._account()
            session_id = session.id
            self._services[session_id] = _service.of_session(
                client,
                creds,
                session_id,
                session.name,
                follow=lambda: self._follow_from(session_id),
            )
        return self._services[session.id]

    @contextlib.contextmanager
    def _connection(self) -> Generator[_ipc.Client, None, None]:
        """A connection to the current session's service.

        A call that fails because the session moved, `sessions migrate`, makes
        the client follow it: the next call goes to where the session went. The
        failed call is raised as it failed, since it may have done its work.

        A session's maintenance notice is told first, so a callback that
        migrates sends the call to the new session."""
        if not self._local:
            self._tell_closing()
        try:
            with self._service().client() as client:
                yield client
        except Exception as err:
            if not self._local and _sessions.moved_away(err):
                self._follow()
            raise

    def _tell_closing(self) -> None:
        """Tell the program of the current session's maintenance notice, once
        for each deadline: `on_session_closing`, or a `SessionClosing`
        warning. Marked told before the callback runs, so the calls it makes
        do not tell it again."""
        session = self.session
        if session is None:
            return
        notice = _sessions.notice(session.id, self._account)
        if notice is None:
            if self._closing_told.pop(session.id, None):
                session._closing(None, None)
            return
        by = _deadline_key(notice.by)
        told = self._closing_told.setdefault(session.id, set())
        if by in told:
            return
        told.add(by)
        if not self._closing_still(session, by, notice.reason):
            return
        deadline = _deadline(str(session.closing_at))
        told.add(_deadline_key(deadline))
        if self._on_session_closing is not None:
            self._on_session_closing(session)
            return
        reason = session.closing_reason or notice.reason
        _warn.warn(
            f"Session {session.name} stops at {deadline:%Y-%m-%d %H:%M} UTC"
            f" because of {reason}. Move it before then with Session.migrate.",
            SessionClosing,
        )

    def _closing_still(self, session: Session, by: str, reason: str) -> bool:
        """Read `session` again before a notice is told: the notice is read
        from a record up to ten minutes old, and a session that moved or ended
        since keeps its notice, which nothing can act on. When the service
        does not answer, the notice is told as recorded: a notice never fails
        the call it rides on."""
        try:
            session.refresh()
        except Exception:
            session._closing(by, reason)
            return True
        live = session.state in _sessions.NOTICE_STATES
        moved = bool(session._row.get("successor_id"))
        return live and not moved and session.closing_at is not None

    def _follow(self) -> None:
        """Use the session the current one moved to, when it moved."""
        if self._session is not None:
            self._follow_from(self._session.id)

    def _follow_from(self, old_id: str) -> Optional[Path]:
        """Follow session `old_id` to where it moved, and give its socket; None
        when it did not move. Bound to each session's service, so a session that
        is not the current one follows its own move, not the current one's."""
        client, creds = self._account()
        try:
            info = _sessions.follow(client, creds, old_id)
        except Exception:  # the error that led here is the one to report
            return None
        if info is None:
            return None
        moved = Session(self, info)
        self._moved(old_id, moved)
        return api.store.session_socket(moved.id)

    def _now_id(self, session_id: str) -> str:
        """The id a session has now: the session it moved to, after every move this
        client followed."""
        while session_id in self._moved_ids:
            session_id = self._moved_ids[session_id]
        return session_id

    @contextlib.contextmanager
    def _catalog(self) -> Generator[_run_source.RunSource, None, None]:
        """Where runs are read from: the current session, else the account's
        catalog."""
        if self._local or self._session_ref is not None:
            with self._connection() as client:
                yield client
            return
        with self._account_catalog() as source:
            yield source

    @contextlib.contextmanager
    def _account_catalog(
        self,
    ) -> Generator[_cloud_run_source.CloudRunSource, None, None]:
        """The account's catalog of runs, whichever session is current."""
        api_client, creds = self._account()
        source = _cloud_run_source.CloudRunSource(api_client, creds)
        try:
            with _translated():
                yield source
        finally:
            source.close()

    @contextlib.contextmanager
    def _copies(self) -> Generator[_ipc.Client, None, None]:
        """A connection to the service of the session whose disk holds the
        copies."""
        if self._local:
            raise InvalidArgument(
                "The service on this machine keeps every run on its disk,"
                " so it has no copies to preload or offload."
            )
        with self._connection() as client:
            yield client

    def _disk_id(self, name: str) -> str:
        """The id of the account's disk `name`."""
        client, creds = self._account()
        with _translated():
            row = api.auth.authed(creds, lambda t: client.disk_get(t, name))
        return str(row["disk_id"])

    def _name(self, source: _run_source.RunSource, run_id: str) -> str:
        """What a run is called, read from where runs are read."""
        if isinstance(source, _ipc.Client):
            return _machines.display_name(source, run_id)
        return _machines.catalog_name(source, run_id)

    def _ops(
        self,
        run_id: Optional[str],
        *,
        op_id: Optional[int],
        states: Optional[builtins.list[str]],
        types: Optional[builtins.list[str]],
        since: "_when.When | None",
        until: "_when.When | None",
        limit: Optional[int],
        offset: int,
        watch: bool,
        timeout: Optional[float],
    ) -> "builtins.list[results.Op] | Generator[builtins.list[results.Op], None, None]":
        filters = _args.op_filters(op_id, states, types, _machines.OP_TYPE_CHOICES)

        def read() -> builtins.list[results.Op]:
            with self._connection() as client, _translated():
                rows = _machines.list_ops(
                    client,
                    run_id=run_id,
                    op_id=op_id,
                    states=filters.states,
                    op_types=filters.types,
                    since=_when.unix_seconds(since),
                    until=_when.unix_seconds(until),
                    limit=limit,
                    offset=offset,
                )
            return [results.op(dataclasses.asdict(row)) for row in rows]

        return _args.watched(read, _args.same, watch, timeout)

    def _branches(
        self,
        run_id: Optional[str],
        *,
        running: bool,
        depth: Optional[int],
        since: "_when.When | None",
        until: "_when.When | None",
        sort: str,
        reverse: bool,
        limit: Optional[int],
        offset: int,
        watch: bool,
        timeout: Optional[float],
    ) -> "builtins.list[Run] | Generator[builtins.list[Run], None, None]":
        _args.check_choice("sort", sort, _sorting.BRANCH_SORT_COLS)

        def read() -> builtins.list[tuple[str, str, str]]:
            """Each branch's id, name and execution: a branch starting or
            stopping is a change."""
            with self._catalog() as source, _translated():
                listed = source.branch_list(scope_run_id=run_id)
                liveness = _machines.catalog_liveness(source, run_id)
            page, _ = _branches.page_branches(
                listed,
                running=running,
                depth=depth,
                liveness=liveness,
                since=_when.unix_seconds(since),
                until=_when.unix_seconds(until),
                sort=sort,
                reverse=reverse,
                limit=limit,
                offset=offset,
            )
            return [
                (
                    b.run_id,
                    b.name,
                    liveness[b.run_id].execution if b.run_id in liveness else "",
                )
                for b in page
            ]

        def present(rows: builtins.list[tuple[str, str, str]]) -> builtins.list[Run]:
            return [Run(self, run_id, name) for run_id, name, _ in rows]

        return _args.watched(read, present, watch, timeout)

    def _marks(self, run_id: str) -> builtins.list[results.Mark]:
        with self._catalog() as source, _translated():
            rows = source.list_marks(run_id)
            wanted = {marked for _, marked in rows}
            names = {
                row.run_id: row.name
                for row in source.runs_query()
                if row.run_id in wanted
            }
        return [
            results.Mark(name=word, run_id=marked, run_name=names.get(marked, ""))
            for word, marked in rows
        ]


class Runs:
    """The account's runs, as `Client.runs`; not created directly.

    Call it to list runs, as `machine runs` does, and index it by name, id,
    unique id prefix or `%mark` to get one.

    Example:
        >>> run = client.runs["app"]
        >>> [r.name for r in client.runs(states=["running"])]
        ['app', 'app/try-1']
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    @overload
    def __call__(
        self,
        root: "Run | str | None" = ...,
        *,
        states: Optional[builtins.list[str]] = ...,
        running: bool = ...,
        roots: bool = ...,
        labels: Optional[dict[str, str]] = ...,
        flags: Optional[dict[str, int]] = ...,
        disk: "Disk | str | None" = ...,
        parents: Optional[str] = ...,
        synced: Optional[bool] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        size: bool = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[Run]: ...

    @overload
    def __call__(
        self,
        root: "Run | str | None" = ...,
        *,
        states: Optional[builtins.list[str]] = ...,
        running: bool = ...,
        roots: bool = ...,
        labels: Optional[dict[str, str]] = ...,
        flags: Optional[dict[str, int]] = ...,
        disk: "Disk | str | None" = ...,
        parents: Optional[str] = ...,
        synced: Optional[bool] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        size: bool = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[Run], None, None]: ...

    def __call__(
        self,
        root: "Run | str | None" = None,
        *,
        states: Optional[builtins.list[str]] = None,
        running: bool = False,
        roots: bool = False,
        labels: Optional[dict[str, str]] = None,
        flags: Optional[dict[str, int]] = None,
        disk: "Disk | str | None" = None,
        parents: Optional[str] = None,
        synced: Optional[bool] = None,
        since: "_when.When | None" = None,
        until: "_when.When | None" = None,
        sort: str = "created",
        reverse: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        size: bool = False,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[Run] | Generator[builtins.list[Run], None, None]":
        """List runs, newest first, as `machine runs` does.

        Every run and branch is returned, or those matching every filter
        given. The list is read from the catalog, so stopped runs are listed
        too, and with no session it answers from the account's catalog.
        Each run's `listing` holds its row. Every matching run is returned
        unless `limit` is given.

        With `watch`, it yields the list at once, then again each time it
        changes: a run created or deleted, or one changing state. Leave the
        loop, or pass `timeout`, to stop watching.

        Example:
            >>> client.runs(labels={"suite": "nightly"}, states=["^running"])
            >>> client.runs("app", sort="name")
            >>> [(r.listing.running, r.listing.runs) for r in client.runs(roots=True)]
            [(1, 3), (0, 1)]

        Args:
            root (Run or str, optional): List one tree, by its root or any
                run in it.
            states (list[str], optional): Keep these states: `running`,
                `paused`, `halted`, `recovering`, `read-only`, `stopped`,
                `killed`, `terminated`, `died`, `failed`; `^state` leaves one
                out.
            running (bool, optional): Keep running runs, as
                `states=["running"]`. Defaults to False.
            roots (bool, optional): One run per tree, its root, with the
                tree's counts in `listing.running` and `listing.runs`.
                Defaults to False.
            labels (dict[str, str], optional): Keep runs with every one of
                these labels.
            flags (dict[str, int], optional): Keep runs whose flags match: a
                flag name, such as `rng` or `daemon`, to 1 for set or 0 for
                unset.
            disk (str, optional): Keep runs that boot this disk, by name
                or id.
            parents (str, optional): Keep runs that read their data parents
                `local` or `remote`.
            synced (bool, optional): True keeps runs published to shared
                storage, False those with nothing published.
            since (datetime or int or str, optional): Keep those created at
                or after this time: a datetime, Unix seconds, a date or an
                age such as `7d`.
            until (datetime or int or str, optional): Keep those created at
                or before this time.
            sort (str, optional): `created`, newest first, or `name`,
                `cpus`, `memory`, `rng-seed` or `limit`, lowest first.
                Defaults to `created`.
            reverse (bool, optional): Turn the order round. Defaults to
                False.
            limit (int, optional): Return at most this many.
            offset (int, optional): Skip this many first. Defaults to 0.
            size (bool, optional): Measure each run's size on the session,
                into `listing.disk_bytes` and `listing.ram_bytes`; needs a
                session. Defaults to False.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[Run]: The runs; with `watch`, an iterator of such lists.

        Raises:
            InvalidArgument: A filter, sort key or time is not valid.
        """
        terms = list(states or [])
        if running:
            terms.append("running")
        for term in terms:
            _args.check_choice(
                "state", term.removeprefix("^"), _machines.CATALOG_STATE_CHOICES
            )
        for flag in flags or {}:
            _args.check_choice("flag", flag, _machines.CATALOG_FLAG_NAMES)
        _args.check_choice("parents", parents, _PARENTS)
        _args.check_choice("sort", sort, _sorting.CATALOG_SORT_COLS)
        columns: dict[str, object] = {}
        if parents is not None:
            columns["parents"] = parents
        if synced is not None:
            columns["durable_through"] = synced
        query = _machines.CatalogQuery(
            states=_machines.resolve_states(terms or None),
            disk=None if disk is None else _args.disk_ref(disk),
            filters=columns or None,
            flag_filters=dict(flags) if flags else None,
            label_filters=(
                {_machines.to_wire_label_name(k): v for k, v in labels.items()}
                if labels
                else None
            ),
            since=_when.unix_seconds(since),
            until=_when.unix_seconds(until),
            sort=_sorting.CATALOG_SORT_FIELDS[sort],
            reverse=_sorting.descending(sort, reverse),
            limit=limit,
            offset=offset,
            usage=size,
        )
        catalog = self._client._connection if size else self._client._catalog

        def read() -> Sequence[_machines.RunRootRow | _machines.RunTreeRow]:
            with catalog() as source, _translated():
                if roots and root is None:
                    return _machines.catalog_roots(source, query)
                scope = None
                if root is not None:
                    ref = root.id if isinstance(root, Run) else root
                    scope = _machines.tree_root(source, source.resolve_run_ref(ref))
                return _machines.catalog_runs(source, query, root=scope)

        def present(
            rows: Sequence[_machines.RunRootRow | _machines.RunTreeRow],
        ) -> builtins.list[Run]:
            return [
                Run(self._client, row.run_id, row.name, results.listing(row))
                for row in rows
            ]

        return _args.watched(read, present, watch, timeout)

    def get(self, ref: str) -> Run:
        """Get a run by name, as every command that takes a run resolves
        one; `runs[ref]` does the same.

        Args:
            ref (str): The run's name, a branch's as `root/branch`, its id, a
                prefix of the id only it has, or `%mark`.

        Returns:
            Run: The run.

        Raises:
            NotFound: No run matches.
            InvalidArgument: The prefix matches more than one run.
        """
        with self._client._catalog() as source, _translated():
            run_id = source.resolve_run_ref(ref)
            name = self._client._name(source, run_id)
        return Run(self._client, run_id, name)

    def __getitem__(self, ref: str) -> Run:
        return self.get(ref)


class Marks:
    """The marks of every tree, as `Client.marks`; not created directly.

    `Run.marks` lists one tree's, and adds and removes a run's.

    Example:
        >>> [(m.name, m.run_name) for m in client.marks.list()]
        [('found-race', 'app/try-3')]
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def list(self) -> builtins.list[results.Mark]:
        """List the marks of every tree, as `machine marks list` does.

        Answers with no session, from the account's catalog.

        Returns:
            list[Mark]: Each mark and the run that carries it.
        """
        return self._client._marks("")


class Storage:
    """What the account stores, as `Client.storage`; not created directly.

    Runs and disks live in the account's shared storage; deleting them
    frees their space once a prune reclaims it.

    Example:
        >>> client.storage.show()["used_bytes"]
        52428800
        >>> client.storage.prune()
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def show(self, *, refresh: bool = False) -> results.StorageInfo:
        """Read what the account stores and its allowance, as `machine
        storage show` does.

        The sizes come from the last measurement, which the document dates.
        `refresh` starts a new measurement; `ops` shows it running.

        Args:
            refresh (bool, optional): Measure again instead of reading the
                last measurement. Defaults to False.

        Returns:
            StorageInfo: The sizes, the allowance,
            and under `prune` the current or last prune.

        Raises:
            NotSignedIn: There are no credentials.
        """
        client, creds = self._client._account()
        with _translated():
            used = api.auth.authed(creds, lambda t: client.storage(t, refresh=refresh))
            running = api.auth.authed(creds, client.prune_status)
        return results.storage({**used, "prune": running})

    @overload
    def ops(
        self,
        *,
        op_id: Optional[int] = ...,
        states: Optional[builtins.list[str]] = ...,
        types: Optional[builtins.list[str]] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[results.StorageOp]: ...

    @overload
    def ops(
        self,
        *,
        op_id: Optional[int] = ...,
        states: Optional[builtins.list[str]] = ...,
        types: Optional[builtins.list[str]] = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[results.StorageOp], None, None]: ...

    def ops(
        self,
        *,
        op_id: Optional[int] = None,
        states: Optional[builtins.list[str]] = None,
        types: Optional[builtins.list[str]] = None,
        since: "_when.When | None" = None,
        until: "_when.When | None" = None,
        limit: Optional[int] = None,
        offset: int = 0,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[results.StorageOp] | Generator[builtins.list[results.StorageOp], None, None]":
        """List the account's storage operations, newest first, as `machine
        storage ops` does.

        A storage operation is a measurement, a prune or a check. The list is
        every operation in every state; `states` keeps some. Every matching
        operation is returned unless `limit` is given.
        With `watch`, it yields the list at once, then again each time it
        changes, such as a prune finishing.

        Example:
            >>> client.storage.ops(states=["failed"], since="1d")
            [{'op_id': '42', 'op_type': 'prune', 'state': 'failed', ...}]

        Args:
            op_id (int, optional): Return this operation alone, whatever its
                state or type.
            states (list[str], optional): Keep these states: `pending`,
                `running`, `done`, `failed`; `^state` leaves one out.
                Defaults to every state.
            types (list[str], optional): Keep these types: `measure`,
                `prune`, `verify`, `check`; `^type` leaves one out. Defaults
                to every type.
            since (datetime or int or str, optional): Keep those started at
                or after this time: a datetime, Unix seconds, a date or an
                age such as `7d`.
            until (datetime or int or str, optional): Keep those started at
                or before this time.
            limit (int, optional): Return at most this many.
            offset (int, optional): Skip this many first. Defaults to 0.
            watch (bool, optional): Yield the list each time it changes.
                Defaults to False.
            timeout (float, optional): With `watch`, end the generator after
                this many seconds, without raising; None watches until the
                caller leaves the loop.

        Returns:
            list[StorageOp]: One record per operation: its id,
            type, state, start time and error; with `watch`, an iterator of
            such lists.

        Raises:
            InvalidArgument: A state, type or time is not valid.
            NotSignedIn: There are no credentials.
        """
        filters = _args.op_filters(
            op_id,
            states,
            types,
            _machines.STORAGE_OP_TYPE_CHOICES,
            _machines.STORAGE_OP_STATE_CHOICES,
        )

        def read() -> builtins.list[results.StorageOp]:
            with self._client._account_catalog() as source:
                rows = _machines.list_storage_ops(
                    source,
                    op_id=op_id,
                    states=filters.states,
                    op_types=filters.types,
                    since=_when.unix_seconds(since),
                    until=_when.unix_seconds(until),
                    limit=limit,
                    offset=offset,
                )
            return [results.storage_op(dataclasses.asdict(row)) for row in rows]

        return _args.watched(read, _args.same, watch, timeout)

    def prune(self, *, status: bool = False) -> Optional[results.Prune]:
        """Start reclaiming the space deleted runs and disks still hold, as
        `machine storage prune` does.

        The prune runs on the service after this returns; `status` and `ops`
        show it. It needs the account's storage to itself, so it is refused
        while any session is running: `sessions.delete(all=True)` first.

        Args:
            status (bool, optional): Return the current or last prune
                instead of starting one. Defaults to False.

        Returns:
            Prune: The prune; with `status`,
            None when there has been none.

        Raises:
            Conflict: A session is running.
            NotSignedIn: There are no credentials.
        """
        client, creds = self._client._account()
        with _translated():
            if status:
                row: Optional[dict[str, Any]] = api.auth.authed(
                    creds, client.prune_status
                )
                return None if row is None else results.prune(row)
            started: dict[str, Any] = api.auth.authed(creds, client.prune_start)
            return results.prune(started)

    def check(
        self,
        *,
        wait: bool = True,
        timeout: Optional[float] = None,
        status: bool = False,
    ) -> Optional[results.StorageCheck]:
        """Read every object of the account's storage back and check it
        against its name, as `machine storage check` does.

        Nothing is written and nothing is locked, so it runs beside sessions
        and beside a prune. The check runs on the service: it goes on when a
        wait here ends, and `ops` lists it. One runs at a time, and asking
        while one runs follows that one.

        Example:
            >>> found = client.storage.check()
            >>> (found.state, found.failed, found.unreadable)
            ('done', 0, [])

        Args:
            wait (bool, optional): Return once the check has ended. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.
            status (bool, optional): Return the current or last check
                instead of starting one. Defaults to False.

        Returns:
            StorageCheck: The check, with `state` `failed` and `unreadable`
            filled in when an object could not be read; with `status`, None
            when there has been none.

        Raises:
            Conflict: The account has no storage yet.
            Timeout: The check had not ended within `timeout`. It goes on.
            NotSignedIn: There are no credentials.
        """
        client, creds = self._client._account()
        with _translated():
            if status:
                last: Optional[dict[str, Any]] = api.auth.authed(
                    creds, client.storage_check
                )
                return None if last is None else results.storage_check(last)
            row: dict[str, Any] = api.auth.authed(creds, client.storage_check_start)
            if wait:
                row = _storage_ops.wait_check(
                    lambda: api.auth.authed(creds, client.storage_check),
                    row,
                    timeout_s=timeout,
                )
            return results.storage_check(row)


class Delete:
    """Deleting runs, as `Client.delete`; not created directly.

    Call it to delete every run in the current session, or every run that
    boots a disk, as `machine delete -a` and `machine delete --disk` do.
    `cleanup` resumes deletes that were interrupted. `Run.delete` deletes
    one run and its branches.

    Example:
        >>> client.delete(disk="app", dry_run=True)
        []
        >>> client.delete.cleanup().recovered
        0
    """

    def __init__(self, client: "Client") -> None:
        self._client = client

    def __call__(
        self,
        *,
        all: bool = False,
        disk: "Disk | str | None" = None,
        stop: bool = False,
        force: bool = False,
        dry_run: bool = False,
        timeout: Optional[float] = None,
    ) -> builtins.list["Run"]:
        """Delete every run in the current session, or every run that boots
        a disk, as `machine delete -a` and `machine delete --disk` do.

        `timeout` bounds the whole call. Run out part way, the run it
        stopped at goes on being deleted and the rest were not started.

        `Run.delete` deletes one run and its branches; `cleanup` resumes
        deletes that were interrupted.

        Example:
            >>> [r.name for r in client.delete(disk="app", dry_run=True)]
            ['app', 'app/try-1']
            >>> client.delete(all=True, stop=True)

        Args:
            all (bool, optional): Delete every run in the session. Defaults
                to False.
            disk (str, optional): Delete every run that boots this disk,
                by name or id, whichever session it is in.
            stop (bool, optional): Kill running machines first. Defaults to
                False.
            force (bool, optional): As `stop`, and delete a run whose machine
                is already gone or was replaced. Defaults to False.
            dry_run (bool, optional): List what would be deleted or
                recovered, acting on nothing. Defaults to False.
            timeout (float, optional): Seconds to wait for every run to be
                gone; None waits without a limit.

        Returns:
            list[Run]: The runs deleted, or with `dry_run` those that would
            be.

        Raises:
            InvalidArgument: Neither `all` nor `disk` is given, both are,
                or the client has no session.
            NotFound: No run boots `disk`.
            ApiError: Some runs could not be deleted; the message names the
                first.
        """
        if all == (disk is not None):
            raise InvalidArgument(
                "Pass all=True or disk=, not both."
                if all
                else "Delete one run with run.delete(), or pass all=True or disk=."
            )
        stop = stop or force
        deadline = None if timeout is None else time.monotonic() + timeout
        rows = []
        if disk is not None:
            # Which runs boot a disk is the account's question: it holds the
            # disks, and a session's service has none.
            if self._client._local:
                raise InvalidArgument(
                    "disk= needs a session: the local service has no account disks."
                )
            with self._client._account_catalog() as source, _translated():
                rows = _lifecycle.booting(source, _args.disk_ref(disk))
        with self._client._connection() as client, _translated():
            names = _names(client)
            if disk is not None:
                done, errors = _lifecycle.delete_by_disk(
                    client,
                    rows,
                    force=stop,
                    force_override=force,
                    dry_run=dry_run,
                    deadline=deadline,
                )
            else:
                done, errors = _lifecycle.delete_all(
                    client,
                    force=stop,
                    force_override=force,
                    dry_run=dry_run,
                    deadline=deadline,
                )
        if isinstance(done, _machines.DeleteDryRunResult):
            return [
                Run(self._client, run_id, name) for name, run_id in done.would_delete
            ]
        _raise_first(errors, f"Deleted {done.deleted}")
        return [
            Run(self._client, run_id, names.get(run_id, "")) for run_id in done.run_ids
        ]

    def cleanup(
        self, *, full_scan: bool = False, dry_run: bool = False
    ) -> results.Recovery:
        """Resume deletes that were interrupted, as `machine delete --cleanup -a`
        does.

        A delete that stopped part way leaves a marker; this finishes the
        work each one names.

        Args:
            full_scan (bool, optional): Scan every branch instead of the
                recorded roots, for a marker the index lost. Defaults to
                False.
            dry_run (bool, optional): List what would be recovered, doing
                nothing. Defaults to False.

        Returns:
            Recovery: What it finished, what it left, and what went wrong.

        Raises:
            InvalidArgument: The client has no session.
        """
        with self._client._connection() as client, _translated():
            return results.recovery(
                dataclasses.asdict(
                    _machines.delete_cleanup(
                        client, full_scan=full_scan, dry_run=dry_run
                    )
                )
            )


class Preload:
    """Copies onto the current session's disk, as `Client.preload`; not
    created directly.

    A run is kept in shared storage and copied to a session's disk when it
    starts there; preloading copies it ahead of time, so starting it does
    not wait for the download. Call it with `all=True` to copy everything
    the session's runs need, as `machine preload -a` does. `Run.preload`
    copies one run.

    Example:
        >>> client.preload(all=True)
        >>> client.preload.disk("app")
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def __call__(self, *, all: bool, timeout: Optional[float] = None) -> None:
        """Copy every run folder and boot disk the session's runs need, as
        `machine preload -a` does.

        Args:
            all (bool): Copy everything; required, as `-a` is.
            timeout (float, optional): Seconds to wait for the copies; None
                waits without a limit.

        Raises:
            InvalidArgument: `all` is False, or the client has no session.
            Timeout: The copies did not finish within `timeout`.
        """
        _require_all(all, "Preload one run with run.preload().")
        with self._client._copies() as client, _translated():
            _machines.preload_all_both(client, timeout_s=timeout)

    def run(self, *, all: bool, timeout: Optional[float] = None) -> None:
        """Copy every data parent the session's runs need, as `machine
        preload run -a` does. `Run.preload` copies one run.

        Args:
            all (bool): Copy every one; required, as `-a` is.
            timeout (float, optional): Seconds to wait for the copies; None
                waits without a limit.

        Raises:
            InvalidArgument: `all` is False, or the client has no session.
            Timeout: The copies did not finish within `timeout`.
        """
        _require_all(all, "Preload one run with run.preload().")
        with self._client._copies() as client, _translated():
            _machines.preload_all(client, timeout_s=timeout)

    def disk(
        self,
        disk: "Disk | str | None" = None,
        *,
        all: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Copy a disk onto the session, as `machine preload disk` does, so
        creating a run from it does not wait for the download.

        Args:
            disk (str, optional): The disk's name or id.
            all (bool, optional): Copy every boot disk the session's runs
                need instead. Defaults to False.
            timeout (float, optional): Seconds to wait for the copy; None
                waits without a limit.

        Raises:
            InvalidArgument: Neither or both of `disk` and `all` are given,
                or the client has no session.
            NotFound: No disk has this name.
            Timeout: The copy did not finish within `timeout`.
        """
        with self._client._copies() as client, _translated():
            if _one_or_all(disk, all) is None:
                _machines.preload_all(client, timeout_s=timeout, disks=True)
            else:
                digest = self._client._disk_id(str(disk))
                _machines.preload_disk(client, digest, timeout_s=timeout)


class Offload:
    """Copies dropped from the current session, as `Client.offload`;
    not created directly.

    Offloading frees the session's storage; the runs and disks stay in shared
    storage and are copied back when a run needs them. Call it with no
    arguments to list what can be dropped, as `machine offload` does, or
    with `all=True` to drop all of it. `Run.offload` drops one run.

    Example:
        >>> client.offload()["runs"]
        [{'run_id': 'whAK...', 'name': 'app', 'disk_bytes': 52428800, ...}]
        >>> client.offload(all=True)
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def __call__(
        self,
        *,
        all: bool,
        runs: bool = False,
        disks: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Drop every copy not in use, as `machine offload -a` does.

        `list` reads what can be dropped; this drops it. `Run.offload` drops
        one run, and `disk` one disk.

        Example:
            >>> client.offload(all=True)
            >>> [c.name for c in client.offload.list().runs]
            []

        Args:
            all (bool): Drop every one; required, as `-a` is.
            runs (bool, optional): Only the session's run folders. Defaults
                to False.
            disks (bool, optional): Only the session's disk files.
                Defaults to False.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Raises:
            InvalidArgument: `all` is False, or the client has no session.
        """
        _require_all(all, "Read what can be dropped with client.offload.list().")
        both = runs == disks
        with self._client._copies() as client, _translated():
            if both:
                _machines.offload_all_both(client, timeout_s=timeout)
            else:
                _machines.offload_all(client, timeout_s=timeout, disks=disks)

    def list(self, *, runs: bool = False, disks: bool = False) -> results.Copies:
        """List the session's copies that can be dropped, as `machine
        offload list` does.

        Only copies not in use are listed; the rest are counted.

        Args:
            runs (bool, optional): Only the session's run folders. Defaults
                to False.
            disks (bool, optional): Only the session's disk files.
                Defaults to False.

        Returns:
            Copies: The run folders and the disk files it holds.

        Raises:
            InvalidArgument: The client has no session.
        """
        both = runs == disks
        with self._client._copies() as client, _translated():
            return results.copies(
                _machines.cache_list(
                    client, runs=runs or both, disks=disks or both
                ).to_json()
            )

    def run(self, *, all: bool, timeout: Optional[float] = None) -> None:
        """Drop every run folder not in use, as `machine offload run -a`
        does. `Run.offload` drops one run.

        Args:
            all (bool): Drop every one; required, as `-a` is.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Raises:
            InvalidArgument: `all` is False, or the client has no session.
        """
        _require_all(all, "Offload one run with run.offload().")
        with self._client._copies() as client, _translated():
            _machines.offload_all(client, timeout_s=timeout)

    def disk(
        self,
        disk: "Disk | str | None" = None,
        *,
        all: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        """Drop the session's copy of a disk, as `machine offload disk`
        does. The disk stays in the account.

        Args:
            disk (str, optional): The disk's name or id.
            all (bool, optional): Drop every disk not in use instead.
                Defaults to False.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Raises:
            InvalidArgument: Neither or both of `disk` and `all` are given,
                or the client has no session.
            NotFound: No disk has this name.
            Conflict: A running machine uses the copy.
        """
        with self._client._copies() as client, _translated():
            if _one_or_all(disk, all) is None:
                _machines.offload_all(client, timeout_s=timeout, disks=True)
            else:
                digest = self._client._disk_id(str(disk))
                _machines.offload_disk(client, digest, timeout_s=timeout)


def _require_all(all: bool, hint: str) -> None:
    """InvalidArgument unless `all`, the only form a many-run command has."""
    if not all:
        raise InvalidArgument(f"Pass all=True. {hint}")


def _one_or_all(name: "Disk | str | None", all: bool) -> Optional[str]:
    """The disk named, or None for `all`; InvalidArgument unless exactly one
    is given. A `Disk` names itself, as a run and a session do."""
    if (name is None) == (not all):
        raise InvalidArgument("Name a disk, or pass all=True.")
    return None if name is None else _args.disk_ref(name)


def _names(client: _ipc.Client) -> dict[str, str]:
    """Every run's name by id, read before a many-run command removes them."""
    return {b.run_id: b.name for b in client.branch_list()}


def _raise_first(errors: builtins.list[str], done: str) -> None:
    """ApiError naming the first of `errors`, when there are any."""
    if errors:
        raise ApiError(f"{done}, {len(errors)} failed; first {errors[0]}")
