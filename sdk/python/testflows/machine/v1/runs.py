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
"""Runs: machines recorded entry by entry, and the branches forked from them.

Every CLI command that takes a run is a method of `Run`, with the run
dropped: `machine fork app --at ^boot` is `run.fork(at="^boot")`, and
`machine tasks app hold 56` is `run.tasks.hold(56)`.
"""

from __future__ import annotations

import builtins
import dataclasses
import io
import os
import shlex
import time
import warnings
from pathlib import Path
from collections.abc import Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Final,
    Generator,
    Literal,
    Optional,
    TextIO,
    Union,
    overload,
)

from testflows.machine._core import axis as _axis
from testflows.machine._core import clock as _clock
from testflows.machine._core import fs as _fs
from testflows.machine._core import branches as _branches
from testflows.machine._core import breakpoints as _breakpoints
from testflows.machine._core import branching as _branching
from testflows.machine._core import console_source as _console_source
from testflows.machine._core import control as _control
from testflows.machine._core import control_log as _control_log
from testflows.machine._core import cursor as _cursor
from testflows.machine._core import debugger as _debugger
from testflows.machine._core import drive as _drive
from testflows.machine._core import faults as _faults
from testflows.machine._core import focus as _focus
from testflows.machine._core import hashes as _hashes
from testflows.machine._core import hist as _hist
from testflows.machine._core import ipc as _ipc
from testflows.machine._core import irqs as _irqs
from testflows.machine._core import ksym as _ksym
from testflows.machine._core import lifecycle as _lifecycle
from testflows.machine._core import line_select as _line_select
from testflows.machine._core import reverse as _reverse
from testflows.machine._core import run_options as _run_options
from testflows.machine._core import signals as _signals
from testflows.machine._core import stepping as _stepping
from testflows.machine._core import variables as _variables
from testflows.machine._core import log_lines as _log_lines
from testflows.machine._core import machines as _machines
from testflows.machine._core import parsers as _parsers
from testflows.machine._core import containers as _containers
from testflows.machine._core import net as _net
from testflows.machine._core import process as _process
from testflows.machine._core import profile as _profile
from testflows.machine._core import replay as _replay
from testflows.machine._core import run_logs as _run_logs
from testflows.machine._core import run_source as _run_source
from testflows.machine._core import send as _send
from testflows.machine._core import sidedump as _sidedump
from testflows.machine._core import sorting as _sorting
from testflows.machine._core import tasks as _tasks
from testflows.machine._core import vectors as _vectors
from testflows.machine._core import vtime as _vtime
from testflows.machine._core import rng as _rng
from testflows.machine._core import wait as _wait

from . import _args
from . import _when
from . import results
from .errors import (
    ApiError,
    Conflict,
    Diverged,
    InvalidArgument,
    NotFound,
    Timeout,
    _translated,
)

if TYPE_CHECKING:
    from .client import Client

#: `start --parents`: where a run reads its data parents from.
_PARENTS = ("local", "remote")


def _run_options_of(
    daemon: Optional[bool],
    single_step: Optional[bool],
    limit: Optional[int],
    backstop_limit: Optional[int],
    repeatable: Optional[bool] = None,
    limit_margin: Optional[int] = None,
    timer_slack: Optional[str] = None,
    stops: _run_options.StopControls = _run_options.STOPS_KEEP,
) -> _run_options.RunOptions:
    """The run options a start, fork or detach named, checked against the bounds
    `create` holds them to; None for each one not given, which keeps the run's."""
    return _run_options.RunOptions(
        daemon=daemon,
        single_step=single_step,
        repeatable=repeatable,
        limit_margin=_args.limit_margin(limit_margin),
        timer_slack=_args.timer_slack(timer_slack),
        limit=_args.bounded("limit", limit, 0),
        backstop_limit=_args.bounded("backstop_limit", backstop_limit, 0),
        stops=stops,
    )


def _stops_of(
    instr_margin: Optional[int],
    dispatch_bound: Optional[str],
    fast_backstop: Optional[int],
    irq_wait: Optional[int],
) -> _run_options.StopControls:
    """The stop controls a fork, detach or go named, checked as `create`
    checks them; None for each one not given, which keeps the run's."""
    return _run_options.StopControls(
        instr_margin=_args.stop_count("instr_margin", instr_margin),
        dispatch_bound=_args.dispatch_bound(dispatch_bound),
        fast_backstop=_args.stop_count("fast_backstop", fast_backstop),
        irq_wait=_args.irq_wait(irq_wait),
    )


def _point_text(
    client: Optional[_ipc.Client],
    run_id: str,
    name: str,
    value: "str | results.Point | results.Place | results.Checkpoint | None",
) -> Optional[str]:
    """A point argument as the text the engine resolves on `run_id`: a
    point as its time, a place or checkpoint as its entry once `run_id`'s
    line is found to hold it, and a str as it is."""
    if isinstance(value, results.Point):
        # `@` makes it a time even where a checkpoint has a name like one.
        return f"@{value}"
    if isinstance(value, (results.Place, results.Checkpoint)):
        assert client is not None
        return _branching.place_point(client, run_id, results.place_of(value))
    return _args.point(name, value)


def _pin_cpu_of(pin_cpu: Optional[int]) -> Optional[int]:
    """The host CPU a start, fork or detach named, checked; None lets the
    machine pick the least-loaded."""
    return _args.bounded("pin_cpu", pin_cpu, _args.MIN_PIN_CPU, _args.MAX_PIN_CPU)


def _rng_seed_of(seed: Optional[int]) -> Optional[int]:
    """The seed a fork, detach or go named, checked; None names none."""
    if seed is None:
        return None
    with _translated():
        return _parsers.rng_seed_type(str(seed))


def _tree_names(client: _ipc.Client, run_id: str) -> dict[str, str]:
    """The name of each run in the tree below `run_id`, by id."""
    return {b.run_id: b.name for b in client.branch_list(scope_run_id=run_id)}


class Run:
    """One run: a machine executing a disk, and the log it records.

    Every stop of a vCPU records an entry in the run's log, so a run's
    history is exact: it can be checkpointed, forked into branches, replayed
    and compared. A branch is a run too, forked from a point in its parent's
    history, and every method here works on it.

    Every CLI command that takes a run is a method here, with the run
    dropped: `machine start app` is `run.start()`, `machine fork app --at
    ^boot` is `run.fork(at="^boot")`, and a command group such as `tasks` is
    an attribute, `run.tasks.hold(56)`.

    Not created directly: `Client.create`, `Client.runs` and the branching
    methods return runs. A run is a handle to its client: each method is one
    command, sent to the client's current session at the time of the call,
    so `client.sessions.use(other)` makes the next `run.start()` start it
    there. A client with no session reads the account's catalog, so
    `describe`, `lineage`, `branches`, `checkpoints` and `marks` answer
    without one. Two handles to one run compare equal.

    Many methods take a point in the run's history. A point is `^name` for a
    checkpoint, `#id` for an entry, a virtual time, `-10ms` back from here,
    `now`, `parent` or `parent N` for where this branch or an ancestor was
    forked, `root`, or `%mark` for a marked run.

    Used in a `with` block, a run is deleted when the block exits, however
    it exits, with its branches, and its machine killed first. Without
    `with`, nothing deletes it but `delete`. A run the block turns out to
    want, such as one that has just failed, calls `keep` and the block
    leaves it alone.

    Example:
        >>> with client.create("app", disk="app") as run:
        ...     run.run(until="tasks")
        ...     run.focus.add("/app")
        ...     run.checkpoint("booted")
        ...     branch = run.fork(at="^booted", name="try-1")
        ...     branch.run(until="/app:exit", mode="step,fast", seed=7)
        >>> with client.create(disk="app") as run:
        ...     result = run.fork(at="^booted").replay()
        ...     run.keep(not result.converged)

    Attributes:
        listing (Listing, optional): The run as `Client.runs` listed it; None
            for a run reached any other way.
    """

    def __init__(
        self,
        client: "Client",
        run_id: str,
        name: str,
        listing: Optional[results.Listing] = None,
    ) -> None:
        self._client = client
        self._id = run_id
        self._name = name
        self.listing = listing
        self._keep = False
        self._delete_op_id: Optional[int] = None

    @property
    def id(self) -> str:
        """str: The run's id, which never changes."""
        return self._id

    @property
    def name(self) -> str:
        """str: The run's name; a branch's is qualified by its tree,
        `root/branch`."""
        return self._name

    # -- reading the run -------------------------------------------------------

    def describe(self) -> results.RunInfo:
        """Read how the run was created and where it came from, as `machine
        describe` does.

        Answers with no session, from the account's catalog.

        Example:
            >>> info = run.describe()
            >>> info.state, info.cpus, info.mem
            ('running', 2, 512)

        Returns:
            RunInfo: Its state, options, parent and labels.

        Raises:
            NotFound: The run no longer exists.
        """
        with self._client._catalog() as source:
            return results.run_info(_machines.get_machine_details(source, self._id))

    def state(self) -> results.State:
        """Read what the run's machine is doing, as `machine state` does.

        Example:
            >>> run.state().activity
            'idle'

        A run with no machine answers too: `state` is then the word the run
        list shows, such as `stopped`, and `entry_id` is where its record
        ends.

        Returns:
            State: The machine's state, where it is, and the plan its last
            drive drew from.
        """
        with self._client._connection() as client, _translated():
            info = _control.state(client, self._id)
        return results.state(info)

    def now(self) -> Optional[results.Place]:
        """Read where the run is: the newest entry in its log, as `machine
        now` does.

        Returns:
            Place: The newest entry and its time; None for a run that has
            recorded nothing yet.

        Raises:
            NotFound: The run no longer exists.
        """
        with self._client._connection() as client, _translated():
            if _cursor.of_run(client, self._id) is None:
                return None
            return results.place(_branching.place_of(client, self._id, None))

    def hash(self) -> results.Hashes:
        """Read the machine's state hashes as it stands, as `machine hash`
        does.

        Each is computed now, whether or not the run hashes memory and the
        disk at each entry. Use it to compare two machines stopped at the
        same point, such as a branch and the run it replayed. These are not
        the hashes `dump --show hash` prints: those are what the newest
        entry recorded, before its exit was handled, with memory and the
        disk 0 unless the run hashes them. These are after that entry, with
        its exit handled. The read changes nothing, and the run records
        nothing.

        Memory is read from all of RAM, so the call takes as long as that
        takes, with no time limit. The machine refuses commands that would
        change it until the call returns. A KeyboardInterrupt cancels it.

        Example:
            >>> a = run.go("#300", name="a").hash()
            >>> b = other.go("#300", name="b").hash()
            >>> a.mem == b.mem
            True

        Returns:
            Hashes: Memory's, the disk's, each device's and each vCPU's.

        Raises:
            NotRunning: The run has no machine.
            NotFound: The run no longer exists.
        """
        with self._client._connection() as client, _translated():
            return results.hashes(_hashes.read(client, self._id))

    def place(
        self, point: "str | results.Point | results.Place | results.Checkpoint"
    ) -> results.Place:
        """Find the entry of this run a point names.

        A point resolves to the first entry at the last time at or before
        it. A str is any point a method takes, such as `^booted`, `#300` or
        `-10ms`. A place or a checkpoint is checked against this run's
        history and returned as it is.

        Example:
            >>> at = run.place(Point("1.194s"))
            >>> at.entry_id, str(at.point)
            (5120, '1.193998210s')

        Args:
            point (str or Point or Place or Checkpoint): The point.

        Returns:
            Place: The entry, the run that recorded it, and its time.

        Raises:
            NotFound: No checkpoint matches `point`.
            InvalidArgument: The point is past where the run got to, or a
                place is not in this run's history.
        """
        with self._client._connection() as client, _translated():
            text = _point_text(client, self._id, "point", point)
            return results.place(_branching.place_of(client, self._id, text))

    # -- lifecycle -------------------------------------------------------------

    def start(
        self,
        *,
        force: bool = False,
        read_only: bool = False,
        recover: bool = True,
        at: "str | results.Checkpoint | None" = None,
        parents: Optional[str] = None,
        daemon: Optional[bool] = None,
        single_step: Optional[bool] = None,
        limit: Optional[int] = None,
        backstop_limit: Optional[int] = None,
        pin_cpu: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Start a machine for the run, as `machine start` does.

        A stopped run resumes at its tip, where it stopped. A branch made
        with `fork(start=False)` gets its first machine. A tip that was
        never checkpointed, such as after a crash, is reached by replaying
        from the last checkpoint, unless `recover` is False. With `at`, the
        machine comes up at that checkpoint and replays back to the tip.

        Example:
            >>> run.stop()
            >>> run.start()
            >>> run.run(iters=10)

        Args:
            force (bool, optional): Stop a machine that is already running
                first. Defaults to False.
            read_only (bool, optional): Come up at a checkpoint, `at` or the
                last one, to read it without recording. Defaults to False.
            recover (bool, optional): Replay an unsaved tip from the last
                checkpoint; False refuses such a run instead. Defaults to
                True.
            at (str or Checkpoint, optional): The checkpoint to come up at,
                by name or entry id, also spelled `^name` or `#id` as in a
                point; the run then replays to its tip.
            parents (str, optional): Read the run's data parents from the
                session, `local`, or from shared storage, `remote`.
            daemon (bool, optional): Wait for commands, or with False run to
                completion, for this machine only. Defaults to the run's.
            single_step (bool, optional): Run in step mode, for this machine
                only, when it does not wait for commands. Defaults to the
                run's.
            limit (int, optional): Conditional branches a fast dispatch may
                run, steps in step mode, for this machine only; 0 for no
                limit. Defaults to the run's.
            backstop_limit (int, optional): Step modes only: instructions a
                dispatch may run without stepping, for this machine only.
                Defaults to the run's.
            pin_cpu (int, optional): Pin this machine to a host CPU; the
                default lets it pick the least-loaded.
            wait (bool, optional): Return once the machine accepts commands.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            Conflict: A machine is already running and `force` is False, or
                the tip is unsaved and `recover` is False.
            NotFound: The run no longer exists, or no checkpoint matches
                `at`.
            Timeout: The machine was not ready within `timeout`.
            ApiError: The machine failed to start; the message says why.
        """
        _args.check_choice("parents", parents, _PARENTS)
        options = _run_options_of(daemon, single_step, limit, backstop_limit)
        pin = _pin_cpu_of(pin_cpu)
        with self._client._connection() as client, _translated():
            _lifecycle.start(
                client,
                _lifecycle.StartRequest(
                    run_id=self._id,
                    name=self._name,
                    force=force,
                    read_only=read_only,
                    at=_point_text(client, self._id, "at", at),
                    recover=recover,
                    parents=parents,
                    options=options,
                    pin_cpu=pin,
                    wait=wait,
                    timeout_s=timeout,
                ),
            )
        return self

    def stop(self, *, wait: bool = True, timeout: Optional[float] = None) -> "Run":
        """Stop the machine, as `machine stop` does, keeping everything it
        recorded.

        The tip is checkpointed first, so `start` resumes exactly where it
        stopped. A run already stopped, or already gone, is not an error, so
        cleanup code can call this unconditionally. `Client.stop` stops every
        run.

        Args:
            wait (bool, optional): Return once the machine is gone. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            Timeout: The machine was not gone within `timeout`.
        """
        with self._client._connection() as client, _translated():
            _lifecycle.stop(
                client, self._id, name=self._name, until_stopped=wait, timeout_s=timeout
            )
        return self

    def pause(self, *, wait: bool = True, timeout: Optional[float] = None) -> "Run":
        """Pause the machine where it is, as `machine pause` does.

        A paused machine keeps its state in memory and executes nothing;
        `run` is refused until `resume`.

        Args:
            wait (bool, optional): Return once it is paused. Defaults to
                True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            NotRunning: The run has no machine.
            Timeout: It was not paused within `timeout`.
        """
        with self._client._connection() as client, _translated():
            _lifecycle.pause(
                client, self._id, name=self._name, until_paused=wait, timeout_s=timeout
            )
        return self

    def resume(self, *, wait: bool = True, timeout: Optional[float] = None) -> "Run":
        """Resume a paused machine, as `machine resume` does.

        Args:
            wait (bool, optional): Return once it accepts commands. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            NotRunning: The run has no machine.
            Timeout: It was not ready within `timeout`.
        """
        with self._client._connection() as client, _translated():
            _lifecycle.resume(
                client, self._id, name=self._name, until_ready=wait, timeout_s=timeout
            )
        return self

    def kill(self, *, wait: bool = True, timeout: Optional[float] = None) -> "Run":
        """Kill the machine at once, as `machine kill` does.

        Use it for a machine that does not answer `stop`. The machine is asked
        to stop and save first, and is forced only if it does not go; a forced
        machine loses the entries since its last checkpoint. `state()` then
        reads `stopped` for one that saved and `killed` for one that was
        forced. A machine already gone is not an error.
        Returns once the service has reaped the machine, so the run can be
        started or deleted straight away.

        Args:
            wait (bool, optional): Return once the machine is gone. Defaults
                to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            Timeout: The machine was not gone within `timeout`.
        """
        with self._client._connection() as client, _translated():
            _lifecycle.kill(
                client, self._id, name=self._name, until_gone=wait, timeout_s=timeout
            )
        return self

    def delete(
        self,
        *,
        recursive: bool = False,
        stop: bool = False,
        force: bool = False,
        dry_run: bool = False,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> builtins.list["Run"]:
        """Delete the run, its log and its checkpoints, as `machine delete`
        does.

        The delete is accepted or refused at once and then carried out, which
        for a run with many checkpoints takes a while. `wait=False` returns
        once it is accepted, for one run only: a tree is deleted run by run.
        A machine killed by `stop` is waited for either way. The delete is
        then an operation to follow: `delete_op_id` is its id, for
        `Client.ops(op_id=...)`.

        `timeout` bounds the whole call, one run or a tree. Run out part way
        through a tree, the run it stopped at goes on being deleted and the
        rest were not started.

        A run with branches is refused unless `recursive`, which deletes the
        whole tree below it first. A running machine is refused unless
        `stop`, which kills it. `Client.delete` deletes every run, or every
        run that boots a disk.

        Example:
            >>> [r.name for r in root.delete(recursive=True, dry_run=True)]
            ['app/try-1', 'app/try-0', 'app']
            >>> root.delete(recursive=True, stop=True)

        Args:
            recursive (bool, optional): Delete its branches too. Defaults to
                False.
            stop (bool, optional): Kill running machines first. Defaults to
                False.
            force (bool, optional): As `stop`, and delete a run whose machine
                is already gone or was replaced. Defaults to False.
            dry_run (bool, optional): List what would be deleted, deleting
                nothing. Defaults to False.
            wait (bool, optional): Return once the run is gone. Defaults to
                True.
            timeout (float, optional): Seconds to wait for the run, or all
                of its tree, to be gone; None waits without a limit.

        Returns:
            list[Run]: The runs deleted, or with `dry_run` those that would
            be, branches before their parents.

        Raises:
            Conflict: The run has branches and `recursive` is False, or a
                machine is running and `stop` is False.
            NotFound: The run no longer exists.
            ApiError: Some runs of the tree could not be deleted; the
                message names the first.
            ValueError: `wait` is False with `recursive` or `dry_run`.
            Timeout: The run or its tree was not gone within `timeout`. A
                delete under way goes on.
        """
        if not wait and (recursive or dry_run):
            raise ValueError("wait=False deletes one run")
        stop = stop or force
        with self._client._connection() as client, _translated():
            if dry_run:
                listed = _machines.delete(
                    client, self._id, recursive=recursive, dry_run=True
                )
                assert isinstance(listed, _machines.DeleteDryRunResult)
                names = _tree_names(client, self._id)
                return [
                    Run(self._client, run_id, names.get(run_id, name))
                    for name, run_id in listed.would_delete
                ]
            if recursive:
                # Named before the delete: afterwards no listing has them.
                names = _tree_names(client, self._id)
                done = _machines.recursive_delete(
                    client,
                    self._id,
                    force=stop,
                    force_override=force,
                    deadline=None if timeout is None else time.monotonic() + timeout,
                )
                gone = [
                    Run(self._client, run_id, names.get(run_id, ""))
                    for run_id in done.deleted
                ]
                if done.failed:
                    run_id, reason = done.failed[0]
                    raise ApiError(
                        f"Deleted {len(done.deleted)}, {len(done.failed)} failed;"
                        f" first {run_id}: {reason}"
                    )
                return gone
            if stop:
                _machines.kill_for_delete(client, self._id, force_override=force)
            op = _machines.delete_started(client, self._id)
            if op and wait:
                client.wait_delete(self._id, op, timeout_s=timeout)
            elif op:
                self._delete_op_id = op
        return [self]

    @property
    def delete_op_id(self) -> Optional[int]:
        """Optional[int]: The id of the delete `delete(wait=False)` started;
        None when none was.

        Example:
            >>> run.delete(wait=False)
            >>> client.ops(op_id=run.delete_op_id)[0].state
            'done'
        """
        return self._delete_op_id

    def preload(self, *, timeout: Optional[float] = None) -> "Run":
        """Copy the run onto the session ahead of its start, as
        `machine preload run` does.

        A run is kept in shared storage and copied to a session when
        it starts there; preloading copies it, and the disk it boots, ahead
        of time. Already there is not an error.

        Example:
            >>> run.preload()
            >>> run.start()

        Args:
            timeout (float, optional): Seconds to wait for the copy; None
                waits without a limit.

        Returns:
            Run: This run.

        Raises:
            InvalidArgument: The client has no session.
            Timeout: The copy did not finish within `timeout`.
        """
        with self._client._copies() as client, _translated():
            _machines.preload(client, self._id, timeout_s=timeout)
        return self

    def offload(self, *, timeout: Optional[float] = None) -> "Run":
        """Drop the session's copy of the run, as `machine offload run` does,
        to free the session's disk.

        The run stays in shared storage and is copied back when it starts
        again. Only a run that is not running can be offloaded, and only
        once it is published to shared storage.

        Args:
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: This run.

        Raises:
            InvalidArgument: The client has no session.
            Conflict: The run is running, or not yet in shared storage.
        """
        with self._client._copies() as client, _translated():
            _machines.offload(client, self._id, timeout_s=timeout)
        return self

    # -- branching -------------------------------------------------------------

    def fork(
        self,
        *,
        at: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        name: Optional[str] = None,
        rebase: bool = False,
        depth: Optional[int] = None,
        lean: bool = False,
        start: bool = True,
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
        rng_seed: Optional[int] = None,
        daemon: Optional[bool] = None,
        single_step: Optional[bool] = None,
        limit: Optional[int] = None,
        backstop_limit: Optional[int] = None,
        keep_output: "Sequence[str] | str | None" = None,
        drop_output: "Sequence[str] | str | None" = None,
        pin_cpu: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Fork a branch from a point in this run's history, as `machine
        fork` does.

        The branch starts with this run's state at `at` and runs alongside
        it: this run keeps its machine. Fork many branches from one
        checkpoint to explore different schedules from the same state. A
        point between checkpoints is reached by replaying to it.

        Example:
            >>> run.focus.add("/app")
            >>> run.checkpoint("booted")
            >>> tries = [run.fork(at="^booted", name=f"try-{i}") for i in range(4)]
            >>> for i, branch in enumerate(tries):
            ...     branch.run(until="/app:exit", mode="step,fast", seed=i)

        Args:
            at (str or Point or Place or Checkpoint, optional): Where the
                branch starts: `^name` a checkpoint, `#id` an entry, a
                virtual time, `-10ms` back, `now`, `parent N`, `root` or
                `%mark`; None forks here and now.
            from_ (str or Point or Place or Checkpoint, optional): An
                earlier point to replay the branch from: `^name`, `#id` or a
                virtual time. The branch is forked
                at the checkpoint at or before it and replays to `at`, so it
                stores the output of that stretch by its own `keep_output`
                and `drop_output`. As `machine fork --from`. None forks at
                the checkpoint nearest `at`.
            name (str, optional): The branch's name, qualified as
                `root/name`; defaults to the entry it lands on.
            rebase (bool, optional): Collapse the lineage into the branch
                instead of sharing it. Defaults to False.
            depth (int, optional): With `rebase`, how many root-side
                ancestors to keep; defaults to 1, the root.
            lean (bool, optional): With `rebase`, drop the absorbed history
                so those runs become deletable. Defaults to False.
            start (bool, optional): Start a machine for it; False creates it
                without one, for `start` later. Defaults to True.
            side_dump (bool, optional): Record a per-entry debug dump. What a
                run records is fixed when it is created, so a branch is where
                it changes; None records whatever this run records.
            side_trace (bool, optional): Record a per-entry execution trace,
                on the same terms as `side_dump`.
            mem_hash (bool, optional): Hash the machine's memory at every
                entry, on the same terms as `side_dump`.
            disk_hash (bool, optional): Hash the machine's disk at every
                entry, on the same terms as `side_dump`.
            repeatable (bool, optional): Stop fast dispatches exactly at
                their limit, so recording the branch again repeats it. None
                does what this run does.
            limit_margin (int, optional): How many branches short of each
                stop the branch counter interrupts. None is this run's.
            timer_slack (str, optional): How late a timer may be in the
                branch's code that takes no exit, `0` or a duration such as
                `20ms`. None is this run's.
            instr_margin (int, optional): How many instructions short of a
                stop placed by an instruction count the instruction counter
                interrupts, 0 for the host's. None is this run's.
            dispatch_bound (str, optional): The host time a fast dispatch
                may run before it stops, `0` for the default or a duration
                such as `500ms`. None is this run's.
            fast_backstop (int, optional): How many instructions a fast
                dispatch may run with no other stop, 0 for the default.
                None is this run's.
            irq_wait (int, optional): How many branches a dispatch runs
                while an interrupt waits for the guest to take it, 0 for the
                default. None is this run's.
            rng_seed (int, optional): Seed the branch's RNG device from this
                number once it is up, so its guest draws other random
                numbers. Needs `start` and `wait`, and an RNG device, which
                a run has unless created with `rng=False`. None keeps this
                run's generator.
            daemon (bool, optional): Wait for commands, or with False run to
                completion. The branch keeps it, as a run keeps what it was
                created with; defaults to this run's.
            single_step (bool, optional): Run in step mode, when it does not
                wait for commands, kept the same way. Defaults to this run's.
            limit (int, optional): Conditional branches a fast dispatch may
                run, steps in step mode; 0 for no limit. The branch keeps it, as a run keeps what it was
                created with; defaults to this run's.
            backstop_limit (int, optional): Step modes only: instructions a
                dispatch may run without stepping, kept the same way.
                Defaults to this run's.
            keep_output (str or list[str], optional): Sources whose output
                the branch stores though it is dropped: an output file or a
                directory of them by its path, such as
                `/output/drop/app/debug.log`. What a program writes
                under `MACHINE_DROP_OUTPUT_DIR` is not stored otherwise,
                so a branch that keeps it, replayed, is how it is read. A
                name may be a pattern such as `/output/drop/*/*.log`, in
                the syntax of the `glob` module: `*`, `?` and `[a-z]` do
                not match a slash, and `**` matches any number of
                directories. Given, they replace the names this run keeps,
                and an empty list keeps none; the default, None, is what
                this run keeps.
            drop_output (str or list[str], optional): Sources whose output
                the branch does not store: a path, a device such as
                `ttyS1`, or a pattern. Given, they replace the names this
                run drops, and an empty list drops none; the default,
                None, is what this run drops. Where a kept and a dropped
                name both cover a source, a name spelled out decides over
                a pattern, and of two names spelled out the longer one;
                otherwise the source is stored.
            pin_cpu (int, optional): Pin the branch's machine to a host CPU;
                the default lets it pick the least-loaded.
            wait (bool, optional): Return once its machine accepts commands.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: The new branch.

        Raises:
            AlreadyExists: A branch has this name, or the point already has
                an unnamed branch.
            NotFound: No checkpoint or entry matches `at`.
            NotRunning: Forking here and now, and the run has no machine.
            Timeout: The branch was not ready within `timeout`.
            ApiError: The branch failed to start; the message says why. The
                branch stays, and `start` gives it its first life later.
        """
        return self._branch(
            _branching.fork,
            _branching.BranchRequest(
                run_id=self._id,
                run_name=self._name,
                at=self._point("at", at),
                replay_from=self._point("from_", from_),
                branch_name=name,
                rebase=rebase,
                depth=depth,
                lean=lean,
                no_start=not start,
                side_dump=side_dump,
                side_trace=side_trace,
                mem_hash=mem_hash,
                disk_hash=disk_hash,
                options=_run_options_of(
                    daemon,
                    single_step,
                    limit,
                    backstop_limit,
                    repeatable,
                    limit_margin,
                    timer_slack,
                    _stops_of(instr_margin, dispatch_bound, fast_backstop, irq_wait),
                ),
                output=_args.stored(keep_output, drop_output),
                pin_cpu=_pin_cpu_of(pin_cpu),
                rng_seed=_rng_seed_of(rng_seed),
                wait=wait,
                timeout_s=timeout,
            ),
        )

    def detach(
        self,
        *,
        at: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        name: Optional[str] = None,
        start: bool = True,
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
        rng_seed: Optional[int] = None,
        daemon: Optional[bool] = None,
        single_step: Optional[bool] = None,
        limit: Optional[int] = None,
        backstop_limit: Optional[int] = None,
        keep_output: "Sequence[str] | str | None" = None,
        drop_output: "Sequence[str] | str | None" = None,
        pin_cpu: Optional[int] = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Copy a point of this run into a new root run, as `machine detach`
        does.

        The new run shares no history with this one: its log starts at the
        point and it has no parent, so this tree can be deleted without it.
        Use it to keep a state as a starting point for other work.

        Example:
            >>> base = run.detach(at="^booted", name="app-booted")

        Args:
            at (str or Point or Place or Checkpoint, optional): Where the
                new run starts, as for `fork`; None detaches here and now.
            from_ (str or Point or Place or Checkpoint, optional): An
                earlier point to replay the new run from, as for `fork`. The
                new run is copied at the checkpoint
                at or before it and replays to `at`.
            name (str, optional): The new root's name; defaults to one
                naming this run and the entry.
            start (bool, optional): Start a machine for it. Defaults to True.
            side_dump (bool, optional): Record a per-entry debug dump. What a
                run records is fixed when it is created, so the new root is
                where it changes; None records whatever this run records.
            side_trace (bool, optional): Record a per-entry execution trace,
                on the same terms as `side_dump`.
            mem_hash (bool, optional): Hash the machine's memory at every
                entry, on the same terms as `side_dump`.
            disk_hash (bool, optional): Hash the machine's disk at every
                entry, on the same terms as `side_dump`.
            repeatable (bool, optional): Stop fast dispatches exactly at
                their limit, so recording the branch again repeats it. None
                does what this run does.
            limit_margin (int, optional): How many branches short of each
                stop the branch counter interrupts. None is this run's.
            timer_slack (str, optional): How late a timer may be in the
                branch's code that takes no exit, `0` or a duration such as
                `20ms`. None is this run's.
            instr_margin (int, optional): As for `fork`.
            dispatch_bound (str, optional): As for `fork`.
            fast_backstop (int, optional): As for `fork`.
            irq_wait (int, optional): As for `fork`.
            rng_seed (int, optional): Seed the new root's RNG device, as for
                `fork`.
            daemon (bool, optional): Wait for commands, or with False run to
                completion. The new root keeps it, as a run keeps what it was
                created with; defaults to this run's.
            single_step (bool, optional): Run in step mode, when it does not
                wait for commands, kept the same way. Defaults to this run's.
            limit (int, optional): Conditional branches a fast dispatch may
                run, steps in step mode; 0 for no limit. The new root keeps it, as a run keeps what it was
                created with; defaults to this run's.
            backstop_limit (int, optional): Step modes only: instructions a
                dispatch may run without stepping, kept the same way.
                Defaults to this run's.
            keep_output (str or list[str], optional): Sources whose output
                the new root stores though it is dropped, as for `fork`.
            drop_output (str or list[str], optional): Sources whose output
                the new root does not store, as for `fork`.
            pin_cpu (int, optional): Pin the new root's machine to a host CPU;
                the default lets it pick the least-loaded.
            wait (bool, optional): Return once its machine accepts commands.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: The new root.

        Raises:
            AlreadyExists: A run has this name.
            NotFound: No checkpoint matches `at`.
            Timeout: It was not ready within `timeout`.
        """
        return self._branch(
            _branching.detach,
            _branching.BranchRequest(
                run_id=self._id,
                run_name=self._name,
                at=self._point("at", at),
                replay_from=self._point("from_", from_),
                branch_name=name,
                no_start=not start,
                side_dump=side_dump,
                side_trace=side_trace,
                mem_hash=mem_hash,
                disk_hash=disk_hash,
                options=_run_options_of(
                    daemon,
                    single_step,
                    limit,
                    backstop_limit,
                    repeatable,
                    limit_margin,
                    timer_slack,
                    _stops_of(instr_margin, dispatch_bound, fast_backstop, irq_wait),
                ),
                output=_args.stored(keep_output, drop_output),
                pin_cpu=_pin_cpu_of(pin_cpu),
                rng_seed=_rng_seed_of(rng_seed),
                wait=wait,
                timeout_s=timeout,
            ),
        )

    def go(
        self,
        point: "str | results.Point | results.Place | results.Checkpoint",
        *,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        name: Optional[str] = None,
        rebase: bool = False,
        depth: Optional[int] = None,
        lean: bool = False,
        repeatable: Optional[bool] = None,
        limit_margin: Optional[int] = None,
        timer_slack: Optional[str] = None,
        instr_margin: Optional[int] = None,
        dispatch_bound: Optional[str] = None,
        fast_backstop: Optional[int] = None,
        irq_wait: Optional[int] = None,
        rng_seed: Optional[int] = None,
        keep_output: "Sequence[str] | str | None" = None,
        drop_output: "Sequence[str] | str | None" = None,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Fork a branch at a point and switch this machine onto it, as
        `machine go` does.

        Unlike `fork`, no second machine starts: this run stops and the
        branch gets the machine. Use it to step back to an earlier point and
        try again without a machine per attempt.

        Example:
            >>> run.focus.add("/app")
            >>> run.checkpoint("booted")
            >>> retry = run.go("^booted", name="retry")
            >>> retry.run(until="/app:exit", seed=2)

        Args:
            point (str or Point or Place or Checkpoint): Where to fork, as
                `at` is for `fork`.
            from_ (str or Point or Place or Checkpoint, optional): An
                earlier point to replay the branch from, as for `fork`.
            name (str, optional): The branch's name; defaults to the entry
                it lands on.
            rebase (bool, optional): Collapse the lineage into the branch
                instead of sharing it. Defaults to False.
            depth (int, optional): With `rebase`, how many root-side
                ancestors to keep; defaults to 1, the root.
            lean (bool, optional): With `rebase`, drop the absorbed history
                so those runs become deletable. Defaults to False.
            repeatable (bool, optional): Stop fast dispatches exactly at
                their limit, as for `fork`. None does what this run does.
            limit_margin (int, optional): As for `fork`.
            timer_slack (str, optional): As for `fork`.
            instr_margin (int, optional): As for `fork`.
            dispatch_bound (str, optional): As for `fork`.
            fast_backstop (int, optional): As for `fork`.
            irq_wait (int, optional): As for `fork`.
            rng_seed (int, optional): Seed the branch's RNG device once the
                machine is on it, as for `fork`.
            keep_output (str or list[str], optional): Sources whose output
                the branch stores though it is dropped, as for `fork`.
            drop_output (str or list[str], optional): Sources whose output
                the branch does not store, as for `fork`.
            wait (bool, optional): Return once the branch's machine accepts
                commands. Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: The branch, which now has the machine.

        Raises:
            AlreadyExists: A branch has this name.
            NotFound: No checkpoint or entry matches `point`.
            Timeout: The branch was not ready within `timeout`.
        """
        return self._branch(
            _branching.go,
            _branching.BranchRequest(
                run_id=self._id,
                run_name=self._name,
                at=self._point("point", point),
                replay_from=self._point("from_", from_),
                branch_name=name,
                rebase=rebase,
                depth=depth,
                lean=lean,
                options=_run_options.RunOptions(
                    repeatable=repeatable,
                    limit_margin=_args.limit_margin(limit_margin),
                    timer_slack=_args.timer_slack(timer_slack),
                    stops=_stops_of(
                        instr_margin, dispatch_bound, fast_backstop, irq_wait
                    ),
                ),
                output=_args.stored(keep_output, drop_output),
                rng_seed=_rng_seed_of(rng_seed),
                wait=wait,
                timeout_s=timeout,
            ),
        )

    def back(
        self,
        *,
        entries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Go back, onto a branch, as `machine run --back` and `machine step
        --back` do: to the newest breakpoint, watchpoint or catchpoint hit
        before now, or `entries` executions back. The run stays as it was.

        Example:
            >>> earlier = run.back()
            >>> earlier.breakpoints.hits()[-1].entry_id == earlier.now().entry_id
            True

        Args:
            entries (int, optional): How many executions back to go, of
                any vCPU, in place of the newest hit. Each step of a
                stepped stretch is one.
            timeout (float, optional): Seconds to wait for the branch.

        Returns:
            Run: The branch the machine is on.

        Raises:
            NotFound: No hit before now.
            InvalidArgument: More entries back than the run has.
        """
        request = _branching.BranchRequest(
            run_id=self._id, run_name=self._name, timeout_s=timeout
        )
        with self._client._connection() as client, _translated():
            went = (
                _reverse.to_hit(client, request)
                if entries is None
                else _reverse.over_entries(client, request, entries)
            )
        return Run(self._client, went.switched.run_id, went.switched.name)

    def switch(
        self,
        branch: "Run | str",
        *,
        wait: bool = True,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Switch this machine onto another branch of the tree, as `machine
        switch` does.

        This run stops, checkpointing its tip, and the machine comes up as
        `branch` at that branch's tip. Use it to move between branches of
        one tree with a single machine.

        Example:
            >>> kid = run.fork(at="^booted", name="kid", start=False)
            >>> on = run.switch(kid)
            >>> on == kid
            True

        Args:
            branch (Run or str): The branch: a `Run`, or its name, `%mark`,
                `parent`, `parent N` or `root`.
            wait (bool, optional): Return once the machine accepts commands.
                Defaults to True.
            timeout (float, optional): Seconds to wait; None waits without a
                limit.

        Returns:
            Run: The run the machine is now on.

        Raises:
            NotFound: No branch matches `branch`.
            Conflict: `branch` already has a machine.
            Timeout: The machine was not ready within `timeout`.
        """
        return self._branch(
            _branching.switch,
            _branching.BranchRequest(
                run_id=self._id,
                run_name=self._name,
                target=branch.id if isinstance(branch, Run) else branch,
                wait=wait,
                timeout_s=timeout,
            ),
        )

    def rewind(
        self, to: "str | results.Point | results.Place | results.Checkpoint"
    ) -> results.Rewound:
        """Put the machine back at any entry from #1 to its tip and discard
        what came after it, as `machine rewind` does.

        The run keeps its id, its name and its history up to the point; only
        its tail goes. Use it to take a replay again, from wherever it should
        start, instead of starting a new run. The point is landed on exactly,
        checkpoint or not, and becomes a checkpoint. The tip changes nothing,
        unless a replay diverged there, in which case it is restored. A point
        before the run was forked moves the run back along the line it came
        from; replay then follows that line again.

        Example:
            >>> run.rewind(to="^booted")
            Rewound(entry_id=1843, entries_dropped=5219)

        Args:
            to (str or Point or Place or Checkpoint): The point: `^name`,
                `#entry` or a virtual time.

        Returns:
            Rewound: The entry the run is back at, and what it discarded.

        Raises:
            NotFound: No checkpoint matches `to`.
            InvalidArgument: The point is past the tip, a place or
                checkpoint is not in this run's history, or a branch is
                forked past it.
            NotRunning: The run has no machine.
        """
        with self._client._connection() as client, _translated():
            point = _point_text(client, self._id, "to", to)
            assert point is not None
            done = _branching.rewind(client, self._id, point)
        return results.Rewound(
            entry_id=done.entry_id, entries_dropped=done.entries_dropped
        )

    def send(
        self,
        data: "str | bytes",
        *,
        device: str = "console",
        newline: bool = True,
    ) -> results.Sent:
        """Send input to a device of the run, as `machine send` does.

        A program in the run reads the input from the device, as it reads
        what a person types. The input is queued and this returns: the
        device takes it the next time the run executes, and the run
        records it there, so a replay reproduces it.

        Example:
            >>> run.send("ls").queued
            3
            >>> run.run(until="console~\\\\$ $")
            >>> run.send("\\x03", newline=False).queued
            1
            >>> wrote = run.send("c", device="/dev/gdb_pipe")
            >>> run.run(until="vtime=+1s")
            >>> run.process(wrote.process).exit_code
            0

        Args:
            data (str or bytes): What to send. A str is text, and a newline
                follows it unless `newline` is False. bytes are sent as
                they are.
            device (str, optional): The device: `console`, `ttyS0` to
                `ttyS3`, `hvc0` or `keyboard`. The keyboard takes text and
                types it on a US keyboard, a key for each character. A
                name that begins with `/` is the path of a file in the
                machine, such as a pipe a program reads its input from:
                the bytes go at its end. Defaults to `console`.
            newline (bool, optional): Send a newline after a str, which is
                Enter. Defaults to True.

        Returns:
            Sent: How many bytes were queued and for what. For a path it
            holds the id of the process that writes the file, which
            `process` reads.

        Raises:
            InvalidArgument: `device` names no device of the run, or the
                keyboard has no key for a character of `data`.
            NotRunning: The run has no machine.
            Conflict: The device has no room for the input until the run
                executes.
        """
        if isinstance(data, str):
            sent = _send.payload(data, newline=newline)
        elif isinstance(data, (bytes, bytearray)):
            sent = bytes(data)
        else:
            raise InvalidArgument(f"data must be a str or bytes, got {data!r}.")
        with self._client._connection() as client, _translated():
            return results.sent(_process.deliver(client, self._id, sent, device))

    def exec(
        self,
        command: "str | Sequence[str]",
        *,
        container: Optional[str] = None,
        user: Optional[str] = None,
        net: Optional[str] = None,
        wait: bool = True,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.Process:
        """Execute a command in the run, or in a container of it, as
        `machine exec` does.

        A list is a program of the machine's disk and its arguments, and no
        shell reads it. A str is a line for the shell of the disk, with its
        pipes and redirections, as `machine exec <run> run -c` takes one.
        The run records what the process prints and how it ends, so a
        replay reproduces them.

        Example:
            >>> done = run.exec(["ls", "/"])
            >>> done.exit_code, done.stdout
            (0, ['bin', 'etc'])
            >>> run.exec("mkfifo /dev/in")
            >>> reader = run.exec("cat < /dev/in", wait=False)
            >>> run.send("hello", device="/dev/in").queued
            6
            >>> run.run(until=f"/output/exec/{reader.id}/stdout~hello")
            >>> run.process(reader.id).stdout
            ['hello']
            >>> run.exec(["psql", "-c", "select 1"], container="db",
            ...          user="postgres").exit_code
            0

        Args:
            command (str or Sequence[str]): The program and its arguments
                as a list, or a line for the shell as a str.
            container (str, optional): The container to execute it in, by
                its id, its name or a service with one running container;
                None for the machine itself. The command then has the
                container's environment and working directory. An id is
                preferred: a name is known shortly after the container
                starts. A container the machine does not know is refused
                before the machine runs.
            user (str, optional): The user the command runs as in the
                container, a name or numbers such as `1000:1000`; None for
                the container's own. Needs `container`.
            net (str, optional): A container whose network a program of
                the machine runs on, as `--net` does: tcpdump, ip or ping
                of the machine, on that container's interfaces. Not with
                `container`.
            wait (bool, optional): Run the machine until the command ends,
                as `machine exec <run> run` does. False sends the command
                and returns, as `start` does: the process is `pending`
                until the run executes. Defaults to True.
            within (str, optional): The machine time the command gets to
                end in, a duration such as `10s`. The machine checks it,
                so a slow host does not give up on a command sooner. Used
                with `wait`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait for
                the command to end; None waits without a limit. Used with
                `wait`.

        Returns:
            Process: The process. With `wait` it has ended and holds its
            output; without, it is pending and holds its id.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `command` is empty, is not a str or a sequence
                of str, or is too long for one request, or `user` is given
                with no `container`, or `net` with `container`.
            NotRunning: The run has no machine.
            Conflict: The run's disk executes no commands, or the request
                does not fit until the run executes.
            Timeout: The command did not end within `within` of machine
                time, or within `timeout`.
        """
        bound = _args.within(within)
        if bound is None:
            raise InvalidArgument("within must be a duration, such as '30m'.")
        if isinstance(command, str):
            words = _process.shell(command)
        elif all(isinstance(word, str) for word in command):
            words = builtins.list(command)
        else:
            raise InvalidArgument(
                f"command must be a str or a sequence of str, got {command!r}."
            )
        with self._client._connection() as client, _translated():
            if wait:
                found = _process.run(
                    client,
                    self._id,
                    words,
                    container=_containers.target(client, self._id, container or ""),
                    user=user or "",
                    net=_containers.target(client, self._id, net or ""),
                    within_ns=bound,
                    timeout_s=timeout,
                )
            else:
                found = _process.start(
                    client,
                    self._id,
                    words,
                    container=_containers.target(client, self._id, container or ""),
                    user=user or "",
                    net=_containers.target(client, self._id, net or ""),
                )
        return results.process(found)

    def put(
        self,
        source: "bytes | str | Path",
        path: str,
        *,
        container: Optional[str] = None,
        mode: int = 0o644,
        owner: Optional[tuple[int, int]] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.Process:
        """Put a file into the run, or into a container of it, as
        `machine put` does.

        A file of any size is put. The directories on the way to `path` are
        made, a file already there is replaced, and the file has its name
        only once all of it is written. The machine runs while the file
        goes in. The bytes are a recorded input, so a replay puts the same
        file, and the file is on the disk when this returns: a checkpoint
        taken after it has it.

        Example:
            >>> run.put("./app.conf", "/etc/app.conf")
            >>> run.put(b"#!/bin/sh\\necho hi\\n", "/usr/local/bin/hi", mode=0o755)
            >>> run.put("./seed.sql", "/docker-entrypoint-initdb.d/seed.sql",
            ...         container="compose-db-1")

        Args:
            source (bytes, str or Path): The bytes of the file, or the path
                of a file on this machine.
            path (str): The path the file gets, in the machine or in the
                container.
            container (str, optional): The container to put the file in,
                by its id, its name or a service with one running
                container; None for the machine itself. A container the
                machine does not know is refused before the machine runs.
            mode (int, optional): The mode of the file. Defaults to 0o644.
            owner (tuple of int, optional): The user and the group that
                own the file, as numbers, such as `(1000, 1000)`. None
                leaves it root's.
            within (str, optional): The machine time each stretch of the
                put gets, a duration such as `10s`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait for
                each stretch; None waits without a limit.

        Returns:
            Process: The put, ended, with the entry that recorded its end.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `path` is not the path of a file, or `source`
                is neither bytes nor a path.
            NotFound: `source` is a path with no file at it.
            NotRunning: The run has no machine.
            Conflict: The file was not put: the container is not running,
                a directory on the way is a file, or the disk is full. The
                message has the reason.
            Timeout: The put did not end within `within` or `timeout`.
        """
        bound = _args.within(within)
        if bound is None:
            raise InvalidArgument("within must be a duration, such as '30m'.")
        if isinstance(source, (bytes, bytearray)):
            opened: BinaryIO = io.BytesIO(bytes(source))
            size = len(source)
        elif isinstance(source, (str, Path)):
            try:
                opened = open(source, "rb")
            except FileNotFoundError:
                raise NotFound(f"No such file: {source}") from None
            size = os.fstat(opened.fileno()).st_size
        else:
            raise InvalidArgument(
                f"source must be bytes or the path of a file, got {source!r}."
            )
        with opened, self._client._connection() as client, _translated():
            found = _process.put(
                client,
                self._id,
                opened,
                size,
                path,
                container=_containers.target(client, self._id, container or ""),
                mode=mode,
                owner=owner,
                within_ns=bound,
                timeout_s=timeout,
            )
        done = results.process(found)
        if done.exit_code != 0:
            raise Conflict(
                f"Not put at {path}: {'; '.join(done.stderr) or done.state}."
            )
        return done

    def get(
        self,
        path: str,
        dest: "str | Path | None" = None,
        *,
        container: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.Got:
        """Get a file from the run, or from a container of it, as
        `machine get` does.

        A file of any size is read. It is read as it is now, with what
        programs wrote and the disk does not have yet, where `Run.fs` reads
        the disk at a checkpoint. The machine runs while the file comes
        out, so a file a program is writing can change on the way.

        Example:
            >>> run.get("/etc/hostname").data
            b'app\\n'
            >>> run.get("/var/lib/db/dump.sql", "./dump.sql",
            ...         container="compose-db-1").size
            1048576

        Args:
            path (str): The path of the file, in the machine or in the
                container.
            dest (str or Path, optional): The file on this machine to
                write. None returns the bytes in `Got.data`.
            container (str, optional): The container to get the file from,
                by its id, its name or a service with one running
                container; None for the machine itself. A container the
                machine does not know is refused before the machine runs.
            within (str, optional): The machine time each stretch of the
                get has, a duration such as `10s`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait for
                each stretch; None waits without a limit.

        Returns:
            Got: The file's size, mode and owner, and its bytes when no
            `dest` was given.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `path` is not the path of a file.
            NotRunning: The run has no machine.
            Conflict: The file was not read: there is none at the path, it
                is a directory, or the container is not running. The
                message has the reason.
            Timeout: The get did not end within `within` or `timeout`.
        """
        bound = _args.within(within)
        if bound is None:
            raise InvalidArgument("within must be a duration, such as '30m'.")
        held = io.BytesIO()
        with self._client._connection() as client, _translated():
            if dest is None:
                found = _process.get(
                    client,
                    self._id,
                    path,
                    held,
                    container=_containers.target(client, self._id, container or ""),
                    within_ns=bound,
                    timeout_s=timeout,
                )
            else:
                # A file already at `dest` is replaced by a whole one, or
                # is left as it was.
                found = _process.get_to_file(
                    client,
                    self._id,
                    path,
                    str(dest),
                    container=_containers.target(client, self._id, container or ""),
                    within_ns=bound,
                    timeout_s=timeout,
                )
        done = results.process(found.process)
        if done.exit_code != 0:
            raise Conflict(
                f"Not got from {path}: {'; '.join(done.stderr) or done.state}."
            )
        return results.Got(
            size=found.size,
            mode=found.mode,
            uid=found.uid,
            gid=found.gid,
            data=held.getvalue() if dest is None else None,
            process=done,
        )

    def core(
        self,
        pid: int,
        dest: "str | Path",
        *,
        container: Optional[str] = None,
        signal: int = 6,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.Got:
        """Get a core dump of a process of the run, as `machine core`
        does.

        The process dumps a core, which is written to `dest` for a
        debugger to read with the program of the process. Taking a core
        ends the process: it dies of `signal`. Fork the run first to keep
        it as it is, and take the core on the branch.

        Example:
            >>> branch = run.fork(name="core")
            >>> branch.core(412, "./app.core").size
            364544
            >>> branch.core(7, "./db.core", container="db")

        Args:
            pid (int): The pid of the process in the machine, or with
                `container` the pid it has in that container.
            dest (str or Path): The file on this machine to write.
            container (str, optional): The container the process is in,
                by its id, its name or a service with one running
                container; None for the machine itself.
            signal (int, optional): The signal the process dies of.
                Defaults to 6, SIGABRT. A process that handles it dumps
                no core.
            within (str, optional): The machine time each stretch has, a
                duration such as `10s`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait for
                each stretch; None waits without a limit.

        Returns:
            Got: The size of the core, with `data` None.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `pid` or `signal` is not a positive number.
            NotRunning: The run has no machine.
            Conflict: No core was taken: no such process, a process that
                handles the signal, the first process of a container, or
                a container that is not running. The message has the
                reason.
            Timeout: The core was not taken within `within` or `timeout`.
        """
        bound = _args.within(within)
        if bound is None:
            raise InvalidArgument("within must be a duration, such as '30m'.")
        with self._client._connection() as client, _translated():
            found = _process.core(
                client,
                self._id,
                pid,
                str(dest),
                container=_containers.target(client, self._id, container or ""),
                signal=signal,
                within_ns=bound,
                timeout_s=timeout,
            )
        done = results.process(found.process)
        if done.exit_code != 0:
            raise Conflict(f"No core of {pid}: {'; '.join(done.stderr) or done.state}.")
        return results.Got(
            size=found.size,
            mode=found.mode,
            uid=found.uid,
            gid=found.gid,
            data=None,
            process=done,
        )

    def profile(
        self,
        duration: str = "1s",
        *,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        to: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        pid: Optional[int] = None,
        container: Optional[str] = None,
        in_guest: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.Profile:
        """Sample what the run executes and return where the time went, as
        `machine profile` does.

        The machine samples each CPU at every timer interrupt, 250 times a
        second, and nothing runs in the run for it. Without `from_` the
        run is driven for `duration` and sampled as it runs. With `from_`
        a stretch the run already recorded is profiled: it is replayed on
        a branch, which is deleted after, and the run is not changed. A
        stretch profiled twice gives the same samples.

        Functions are named from the program files on the run's disk as it
        is when the stretch ends. No checkpoint is needed, and none is
        taken. A file not yet written to the disk is named by its file.

        Example:
            >>> found = run.profile("2s", container="db")
            >>> found.samples
            500
            >>> [(f.name, f.own) for f in found.functions[:2]]
            [('hash_bytes', 310), ('memcpy', 95)]
            >>> open("db.folded", "w").write("\\n".join(found.folded))
            >>> slow = run.profile(from_="^request", to="^reply")

        Args:
            duration (str, optional): The machine time to run and sample
                for, such as `500ms`. Defaults to `1s`. Not used with
                `from_`.
            from_ (str or Point or Place or Checkpoint, optional): The
                point a recorded stretch begins at, as `at` is for `fork`:
                `^name` a checkpoint, `#id` an entry, a virtual time. None
                profiles the run as it runs.
            to (str or Point or Place or Checkpoint, optional): The point
                the recorded stretch ends at; None for the end of the run's
                line.
            pid (int, optional): The one process to show, by its pid in
                the machine, or with `container` its pid there; None for
                every process.
            container (str, optional): The container to show, by its name
                or by the name of its Compose service; None for the
                machine itself.
            in_guest (bool, optional): Sample with a process in the run in
                place of the machine. It names the functions of the kernel
                and a program not yet written to the disk. The process
                is part of the run. Not with `from_`. Defaults to False.
            within (str, optional): The machine time a question to a
                container gets. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait; None
                waits without a limit.

        Returns:
            Profile: The samples, as stacks, as functions and as text.

        Raises:
            InvalidArgument: `duration` or `pid` is not valid, `to` is
                given without `from_`, or `in_guest` with it.
            NotRunning: The run has no machine, and no `from_` was given.
            Conflict: The profile was not taken: a container or a pid the
                run does not have, or a replay that did not reproduce the
                stretch. The message has the reason.
            Timeout: The profile did not end within `timeout`.
        """
        bound = _args.within(within)
        sampled = _args.within(duration)
        if bound is None or sampled is None:
            raise InvalidArgument("A duration is such as '1s' or '500ms'.")
        begins = self._point("from_", from_)
        ends = self._point("to", to)
        if begins is None and ends is not None:
            raise InvalidArgument("to ends a recorded stretch, which from_ begins.")
        if in_guest and begins is not None:
            raise InvalidArgument(
                "in_guest samples a run as it runs. A recorded stretch is"
                " sampled by the machine."
            )
        with self._client._connection() as client, _translated():
            if begins is not None:
                found = _profile.recorded(
                    client,
                    self._id,
                    begins,
                    ends,
                    pid=pid or 0,
                    container=container or "",
                    within_ns=bound,
                    timeout_s=timeout,
                )
            elif in_guest:
                found = _profile.in_guest(
                    client,
                    self._id,
                    duration_ms=max(sampled // _profile._NS_PER_MS, 1),
                    pid=pid or 0,
                    container=container or "",
                    within_ns=bound,
                    timeout_s=timeout,
                )
            else:
                found = _profile.profile(
                    client,
                    self._id,
                    duration_ms=max(sampled // _profile._NS_PER_MS, 1),
                    pid=pid or 0,
                    container=container or "",
                    within_ns=bound,
                    timeout_s=timeout,
                )
        return results.Profile(
            samples=found.samples,
            hz=found.hz,
            stacks=[
                results.ProfileStack(frames=builtins.list(one.frames), count=one.count)
                for one in sorted(
                    found.stacks, key=lambda one: (-one.count, one.frames)
                )
            ],
            functions=[
                results.ProfileFunction(name=one.name, own=one.self, total=one.total)
                for one in found.functions()
            ],
            folded=found.folded(),
            left=found.left,
        )

    @property
    def containers(self) -> "Containers":
        """Containers: The containers of the run's guest: list them, and
        stop, kill, start, pause, resume or throttle one."""
        return Containers(self)

    @property
    def net(self) -> "Net":
        """Net: The links between the run's containers: list them, and
        delay, lose, duplicate, corrupt, reorder or limit what one sends."""
        return Net(self)

    def process(self, id: str) -> results.Process:
        """Return a process of the run by its id, as `machine exec <run>
        proc` does.

        Example:
            >>> later = run.exec(["sleep", "5"], wait=False)
            >>> run.run(until="vtime=+10s")
            >>> run.process(later.id).exit_code
            0

        Args:
            id (str): The id `exec` returned.

        Returns:
            Process: The process as the run has recorded it so far. An id
            the run has taken no command for is `pending`.
        """
        with self._client._connection() as client, _translated():
            return results.process(_process.read(client, self._id, id))

    def processes(self) -> builtins.list[results.Process]:
        """Return the processes of the run, as `machine exec <run>` lists
        them.

        Returns:
            list[Process]: The processes the run has taken, in the order
            each started.
        """
        with self._client._connection() as client, _translated():
            return [
                results.process(found) for found in _process.processes(client, self._id)
            ]

    def checkpoint(self, name: Optional[str] = None) -> results.Checkpoint:
        """Save the machine's state where it is, as `machine checkpoint`
        does.

        A checkpoint is a point the run can be started from and branches
        forked from without replaying: `fork(at="^name")`. Checkpoint the
        states worth returning to, such as after boot.

        Example:
            >>> run.run(until="tasks")
            >>> run.checkpoint("booted")
            Checkpoint(name='booted', place=Place(run_id='...', entry_id=1843, ...), ...)

        Args:
            name (str, optional): The checkpoint's name; None names it by
                its entry id.

        Returns:
            Checkpoint: The checkpoint taken.

        Raises:
            AlreadyExists: The run has a checkpoint with this name.
            NotRunning: The run has no machine.
            Conflict: The run has recorded nothing to checkpoint yet.
        """
        with self._client._connection() as client, _translated():
            made = _branching.checkpoint(client, self._id, name or "")
            listed = _branching.lineage_checkpoints(client, self._id)
        for cp in listed:
            if cp.entry_id == made.entry_id and cp.name == made.name:
                return results.checkpoint(cp, self._id)
        # The checkpoint was written and the listing does not have it. Filling
        # in a date and a vtime here invented two facts, and they were wrong:
        # say what happened instead, since the checkpoint IS there and the next
        # read will show it.
        raise ApiError(
            f"Checkpoint {made.name} was written at entry {made.entry_id} and"
            " the listing does not have it yet. Read checkpoints again."
        )

    @overload
    def checkpoints(
        self,
        *,
        durable: bool = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[results.Checkpoint]: ...

    @overload
    def checkpoints(
        self,
        *,
        durable: bool = ...,
        since: "_when.When | None" = ...,
        until: "_when.When | None" = ...,
        sort: str = ...,
        reverse: bool = ...,
        limit: Optional[int] = ...,
        offset: int = ...,
        watch: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[builtins.list[results.Checkpoint], None, None]: ...

    def checkpoints(
        self,
        *,
        durable: bool = False,
        since: "_when.When | None" = None,
        until: "_when.When | None" = None,
        sort: str = "entry",
        reverse: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
        watch: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[results.Checkpoint] | Generator[builtins.list[results.Checkpoint], None, None]":
        """List the checkpoints this run can be started or forked from, as
        `machine checkpoints` does.

        A branch's list includes its ancestors' checkpoints up to where it
        was forked, since that history is its own; `Checkpoint.place.run_id`
        says which run recorded each. Every run has checkpoint `0`, its boot.
        Every matching checkpoint is returned unless `limit` is given.
        Answers with no session, from the account's catalog.

        With `watch`, it yields the list at once, then again each time it
        changes, such as a checkpoint taken or reaching shared storage.
        Leave the loop, or pass `timeout`, to stop watching.

        Example:
            >>> [c.name for c in run.checkpoints()]
            ['0', 'booted', 'done']
            >>> for cps in run.checkpoints(durable=True, watch=True, timeout=300):
            ...     if any(c.name == "done" for c in cps):
            ...         break

        Args:
            durable (bool, optional): Keep those in shared storage. Defaults
                to False.
            since (datetime or int or str, optional): Keep those taken at or
                after this time: a datetime, Unix seconds, a date or an age
                such as `1h`.
            until (datetime or int or str, optional): Keep those taken at or
                before this time.
            sort (str, optional): `entry`, `date` or `name`. `date` runs
                newest first, the others lowest first. Defaults to `entry`.
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
            list[Checkpoint]: The checkpoints; with `watch`, an iterator of
            such lists.

        Raises:
            InvalidArgument: `sort` or a time is not valid.
            NotFound: The run no longer exists.
        """
        _args.check_choice("sort", sort, _sorting.CHECKPOINT_SORT_COLS)

        def read() -> builtins.list[results.Checkpoint]:
            with self._client._catalog() as source:
                mine = self._branch_info(source)
            page = _branches.page_checkpoints(
                _branches.collect_checkpoints(mine),
                durable=durable,
                since=_when.unix_seconds(since),
                until=_when.unix_seconds(until),
                sort=sort,
                reverse=reverse,
                limit=limit,
                offset=offset,
            )
            return [results.checkpoint(cp, self._id) for cp in page.checkpoints]

        return _args.watched(read, _args.same, watch, timeout)

    def lineage(self) -> builtins.list["Run"]:
        """List this run's ancestors and itself, the root first, as
        `machine lineage` does.

        The lineage is the path of forks from the root to this run: the
        history this run inherited. Answers with no session.

        Example:
            >>> [r.name for r in branch.lineage()]
            ['app', 'app/try-1', 'app/try-1-retry']

        Returns:
            list[Run]: The root, each ancestor, and this run.

        Raises:
            NotFound: The run no longer exists.
        """
        with self._client._catalog() as source:
            listed = source.branch_list(scope_run_id=self._id)
        names = {b.run_id: b.name for b in listed}
        mine = next((b for b in listed if b.run_id == self._id), None)
        if mine is None:
            raise NotFound(f"No such run: {self._name or self._id}.")
        return [
            Run(self._client, e.run_id, names.get(e.run_id, "")) for e in mine.lineage
        ]

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
    ) -> builtins.list["Run"]: ...

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
    ) -> Generator[builtins.list["Run"], None, None]: ...

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
        """List the runs in this run's tree, newest first, as `machine
        branches` does for one run.

        The tree is the root this run descends from and every branch forked
        from it, this run included. Answers with no session, from the
        account's catalog. `Client.branches` lists every tree.

        Example:
            >>> [b.name for b in run.branches(depth=1)]
            ['app/try-0', 'app/try-1']

        Args:
            running (bool, optional): Keep those with a machine. Defaults to
                False.
            depth (int, optional): Keep those this many forks from the root;
                0 is the root.
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
        return self._client._branches(
            self._id,
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
        """List the session's operations on this run, newest first, as
        `machine ops` does for one run.

        An operation is a checkpoint, branch, switch or rewind. The list is
        every operation in every state; `states` keeps some. `Client.ops`
        lists every run's.

        Example:
            >>> run.ops(states=["failed"])
            [{'op_id': '17897...', 'op_type': 'checkpoint', 'state': 'failed', ...}]

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
            list[Op]: One record per operation; with
            `watch`, an iterator of such lists.

        Raises:
            InvalidArgument: A state, type or time is not valid, or the
                client has no session.
        """
        return self._client._ops(
            self._id,
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

    @property
    def breakpoints(self) -> "Breakpoints":
        """Breakpoints: The breakpoints, watchpoints and catchpoints of this
        run: list them, change one and delete one. A task of the run adds
        breakpoints and watchpoints, and this adds catchpoints."""
        return Breakpoints(self)

    @property
    def marks(self) -> "RunMarks":
        """RunMarks: The marks of this run's tree: list them, and add or
        remove this run's."""
        return RunMarks(self)

    def keep(self, keeping: bool = True) -> "Run":
        """Keep this run when a `with` block exits, instead of deleting it.

        A run created in a `with` block is deleted when the block exits, however
        it exits. Say this inside the block to keep one it has decided is worth
        keeping, such as a run that has just failed, and its tree is kept with it.
        It changes nothing for a run used without `with`, and nothing on the
        service: this is about what THIS handle does on the way out.

        Example:
            >>> with client.create(disk="app") as run:
            ...     result = run.fork(at="^booted").replay()
            ...     run.keep(not result.converged)

        Args:
            keeping (bool, optional): True to leave the run behind, False to let
                the block delete it again, which is how a run starts out.
                Defaults to True.

        Returns:
            Run: This run.
        """
        self._keep = keeping
        return self

    def sync(self) -> "Run":
        """Publish the run's checkpoints to shared storage, as `machine
        sync` does.

        A cloud run lives on its session's disk until it is published; once
        it is, any session can start it or fork from its checkpoints, and it
        survives the session's deletion. Stopping a run publishes it too.

        Returns:
            Run: This run.

        Raises:
            NotFound: The run no longer exists.
        """
        with self._client._connection() as client, _translated():
            _control.sync_checkpoints(client, self._id)
        return self

    def cancel(self, *, soft: bool = False) -> "Run":
        """Cancel the drive or replay in progress, as `machine cancel`
        does, such as one another program started. The machine stays
        up at the entry it reached; a replay stops before its next entry.

        Args:
            soft (bool, optional): Stop at the end of the dispatch in
                progress, which keeps the run repeatable. It takes at most
                the run's `dispatch_bound`. Without it the run stops now, at
                a point another recording does not stop at. Defaults to
                False.

        Returns:
            Run: This run.

        Raises:
            NotRunning: The run has no machine.
            Conflict: Nothing is in progress.
        """
        with self._client._connection() as client, _translated():
            _control.cancel(client, self._id, soft=soft)
        return self

    # -- driving ---------------------------------------------------------------

    def run(
        self,
        *,
        iters: Optional[int | str] = None,
        until: Optional[str] = None,
        within: Optional[str] = None,
        when: Optional[str] = None,
        mode: Optional[str] = None,
        fast: Optional[int | str] = None,
        step: Optional[int | str] = None,
        step_into: Optional[int | str] = None,
        fence: Optional[int | str] = None,
        limit: Optional[int | str] = None,
        backstop_limit: Optional[int] = None,
        retry: Optional[int] = None,
        vcpu: Optional[int | str] = None,
        vtime_rate: Optional[str] = None,
        vtime_add: Optional[int | str] = None,
        timer_slack: Optional[int | str] = None,
        irq_delay: Optional[str] = None,
        vtime_io_cost: Optional[int | str] = None,
        vtime_rdtsc_cost: Optional[int | str] = None,
        vtime_pause_cost: Optional[int | str] = None,
        task_sched: Optional[str] = None,
        task_slice: Optional[str] = None,
        task_int: Optional[str] = None,
        task_pin: Optional[str] = None,
        strategy: Optional[str] = None,
        seed: int = 0,
        irqblk: bool = False,
        rdtsc_exit: bool = False,
        idle_skip: bool = True,
        auto_checkpoints: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> results.RunResult:
        """Drive the machine and return what it did, as `machine run` does.

        A run executes in dispatches: each runs the guest on one vCPU for a
        number of conditional branches, then stops it and records an entry. One
        iteration is one dispatch, so 100 iterations on four vCPUs are 100
        dispatches, not 400. Without `until` this runs `iters` iterations,
        one by default; with it, as many as it takes for the predicate to
        hold, `iters` then bounding them. A task predicate such as
        `/app:exit` needs the task focused first, with `focus.add`.

        The options decide each dispatch: its mode, how long it runs, which
        vCPU, and how the guest's scheduler is steered. An option given as a
        span, `lo:hi[:strategy[:step]]`, draws a value per dispatch, so a
        run explores different interleavings; `strategy` and `seed` decide
        the draws, and the same seed draws the same values. `when` applies
        them only to the dispatches that match; `plan` takes a file of such
        clauses.

        Example:
            >>> run.run(iters=100)
            >>> run.focus.add("/app")
            >>> run.run(until="/app:exit", mode="step,fast", step="1:200",
            ...         strategy="random", seed=42)
            >>> run.run(until="/app:exit", when="/app:on", task_sched="keep,yield")
            >>> run.run(within="10s")
            >>> run.run(until="console~done", within="30s").reached
            False

        Args:
            iters (int or str, optional): Iterations, an upper bound with
                `until`, where running out leaves the result's `reached`
                False; 0 is unlimited, or a span to draw from.
            within (str, optional): How far the machine's clock may move, a
                duration such as `10s`. The machine checks it, so a slow
                host does not end the run sooner. With `until`, the first
                of the two to hold ends the run, and the clock running out
                leaves `reached` False. Without `until`, the run goes on
                until the clock has moved that far.
            until (str, optional): Drive until this predicate holds:
                `ready`, `tasks` once the guest OS is up, `stopped`, `halted`
                once the guest has ended, `paused`, `running`,
                `entry>=N`, `entry=+N`, `vtime>=T`, `vtime=+T` for a
                virtual time such as `10ms`, `console~REGEX` for text on the
                console or another source as in `ttyS1~REGEX`, or a task
                predicate `[who:]what` such as `/app:exit` or `tid=56:on`,
                where who is `/path`, `cgroup=path`, `comm=name`, `tgid=N`
                or `tid=N` and what is `exec`, `exit`, `sched`, `desched`,
                `on`, `kernel` or `off`.
                `tid=N:at=LOC` runs until that task reaches LOC in its own
                code, and `tgid=N:at=LOC` until any thread of the process
                does: an address, `NAME`, `PATH:NAME`, `FILE:LINE` or
                `$reg`, with an optional `+N` or `-N`. It takes one of the
                3 places the run's watchpoints use. `tid=N:write=LOC` runs
                until the bytes LOC names are written while that task is on
                a vCPU, and `tid=N:access=LOC` until they are read or
                written; `tgid=N` takes any thread of the process. A
                variable or an expression is watched at its size, and an
                address through a type, as in `*(int *)0x4c8b50`.
            when (str, optional): Apply the options only to the dispatches
                while this task state holds: `on`, `kernel` or `off`, with a
                who. A dispatch it excludes runs free.
            mode (str, optional): `fast`, `step`, `step-into`, `free`,
                `fence`, or a comma list to draw among, each with an optional
                `[weight]`. Defaults to `fast`.
                A `fence` dispatch runs fast and stops on an exact count of
                conditional branches, or earlier on the vCPU's next timer,
                where every recording of the same state stops.
            fast (int or str, optional): Branches for a `fast` dispatch,
                a span, overriding `limit`.
            step (int or str, optional): Steps for a `step` dispatch.
            step_into (int or str, optional): Steps for a `step-into`
                dispatch, which also steps into system calls and interrupts.
            fence (int or str, optional): Branches a `fence` dispatch stops
                at, a span, overriding `limit`.
            limit (int or str, optional): Conditional branches per dispatch
                under fast and fence, steps under step. 0 means no limit
                under fast, one step under step and the machine's fence
                under fence.
            backstop_limit (int, optional): Step modes only: instructions a
                dispatch may run without stepping before it ends; 0 for the
                machine's default.
            retry (int, optional): How many more times an entry the host
                interrupted is recorded again; 0 does not retry. Defaults
                to 2.
            vcpu (int or str, optional): The vCPU to dispatch, or a span to
                draw one per dispatch, such as `0:1` or `1:` for 1 through
                the last; -1 or None is every vCPU.
            vtime_rate (str, optional): The virtual clock's rate for this
                drive: `n`, `n/d`, or a span of either.
            vtime_add (int or str, optional): Nanoseconds to push the clock
                forward before each dispatch.
            timer_slack (int or str, optional): How late a timer may be in
                code that takes no exit, drawn per dispatch: a duration such
                as `20ms` or a span such as `0:20ms`, at most 1s.
            irq_delay (str, optional): How long to hold each interrupt
                before it is delivered, drawn per interrupt: a span such as
                `0:2ms`, at most 1s, for every interrupt, or
                `target=span,...` for some. A target is `timer`, `ipi`,
                `init`, `blk`, `rng`, `console`, `serial`, `keyboard`,
                `device` or `all`, and the most specific one wins. An
                interrupt is never delivered before one held earlier on
                its line, its MSI vector, or its vCPU's timer, IPI vector
                or startup.
            vtime_io_cost (int | str, optional): What an I/O, MMIO or
                hypercall exit costs on the clock, drawn per dispatch: a
                duration such as "2us", a span such as "1us:20us", or a
                weighted list, each 1ns to 1s. The run's own cost comes
                back when the drive ends.
            vtime_rdtsc_cost (int | str, optional): What an RDTSC costs,
                drawn the same way; without it the RDTSC cost follows the
                I/O cost.
            vtime_pause_cost (int | str, optional): What each PAUSE of a
                spin loop costs, drawn the same way.
            task_pin (str, optional): Where a task is placed when it next
                wakes, drawn per dispatch: vCPU sets such as "1-3", "free"
                or "none", with an optional `<tid>=` that names the task.
            task_sched (str, optional): A scheduler mark to draw per
                dispatch for the task on that vCPU: `keep`, `hold`, `yield`,
                `drain`, `drain-all`, as a comma list, each with an optional
                `[weight]`.
            task_slice (str, optional): That task's slice to draw per
                dispatch: `keep`, `default`, or a duration range such as
                `10us:600us`, as a comma list, each with an optional
                `[weight]`.
            task_int (str, optional): An interrupt to draw for that task per
                dispatch: `none`, `preempt`, as a comma list, each with an
                optional `[weight]`.
            strategy (str, optional): How spans draw when they name none:
                `random`, `golden`, `sweep`. Defaults to `random`.
            seed (int, optional): The seed of the `random` and `golden`
                draws. Defaults to 0.
            irqblk (bool, optional): Block interrupts while running.
                Defaults to False.
            rdtsc_exit (bool, optional): Record an entry for every RDTSC.
                Defaults to False.
            idle_skip (bool, optional): Skip virtual time forward while
                every vCPU is idle; False keeps it crawling. Defaults to True.
            auto_checkpoints (int, optional): Checkpoint every this many
                entries; 0 for never. Defaults to the run's setting.
            timeout (float, optional): Seconds to wait for the drive; None
                waits without a limit.

        Returns:
            RunResult: The iterations that ran and where each vCPU stopped.
            Its `reached` is False when `iters` ran out before `until` held,
            which is an answer about the guest rather than a failure.

        Raises:
            InvalidArgument: An option or predicate is not valid, or a task
                predicate names a task that is not focused.
            NotRunning: The run has no machine; `start` it first.
            Conflict: The machine is paused, busy with another command, or the
                guest has halted; `start` at a checkpoint to run on.
            Timeout: The drive did not finish within `timeout`.
        """
        with _translated():
            opts = _drive.RunOptions(
                iters=_args.span(iters),
                until=None if until is None else _args.predicate(until),
                new_text=True,
                within_ns=_args.within(within),
                when=None if when is None else _args.predicate(when, when=True),
                mode=_args.text(mode),
                fast=_args.span(fast),
                step=_args.span(step),
                step_into=_args.span(step_into),
                fence=_args.span(fence),
                limit=_args.span(limit),
                backstop_limit=backstop_limit,
                retry=_args.bounded("retry", retry, 0),
                vcpu=_args.vcpus(vcpu),
                vtime_rate=(
                    None if vtime_rate is None else _parsers.rate_span_type(vtime_rate)
                ),
                vtime_add=_args.span(vtime_add),
                timer_slack=_args.timer_slack_span(timer_slack),
                irq_delay=_args.irq_delay(irq_delay),
                vtime_io_cost=_args.text(vtime_io_cost),
                vtime_rdtsc_cost=_args.text(vtime_rdtsc_cost),
                vtime_pause_cost=_args.text(vtime_pause_cost),
                task_pin=_args.text(task_pin),
                task_sched=_args.text(task_sched),
                task_slice=_args.text(task_slice),
                task_int=_args.text(task_int),
                strategy=0 if strategy is None else _parsers.strategy_type(strategy),
                seed=seed,
                irqblk=irqblk,
                rdtsc_exit=rdtsc_exit,
                idle_skip=idle_skip,
                auto_checkpoints=auto_checkpoints,
                timeout_s=timeout,
            )
        return self._drive(opts)

    def plan(
        self,
        file: "str | Path | TextIO",
        *,
        iters: Optional[int | str] = None,
        until: Optional[str] = None,
        limit: Optional[int | str] = None,
        backstop_limit: Optional[int] = None,
        retry: Optional[int] = None,
        vcpu: Optional[int | str] = None,
        strategy: Optional[str] = None,
        seed: int = 0,
        irqblk: bool = False,
        rdtsc_exit: bool = False,
        idle_skip: bool = True,
        auto_checkpoints: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> results.RunResult:
        """Drive the machine by a plan, as `machine plan` does.

        A plan is a list of clauses, `when <predicate> {<axes>}` or
        `else {<axes>}`. An axis is a word and its choices, one drawn per
        dispatch: `mode step,fast`, `mode step[3],fast[1]` weighted,
        `vtime-rate 1:10`; `step=1:200` is the instruction limit the
        `step` mode draws. Every clause whose predicate holds matches, and
        each axis comes from the last matching clause that names it;
        `else` applies when no `when` matches. `machine plan -h` has the
        full grammar. A task predicate such as `/app:on` needs the task
        focused first, with `focus.add`; `on` alone is any focused task.
        Use a plan where one `when` is not enough, such as stepping one
        program and running the rest fast. The plan is a file, or an open
        stream such as `io.StringIO`, which keeps the plan in the program
        that uses it.

        Example:
            >>> run.plan("race.plan", until="/app:exit", seed=3)
            >>> run.plan(io.StringIO("when on {mode step}\\n"), iters=100)

        Args:
            file (str, Path or TextIO): The plan file, or a stream to read
                the plan from.
            iters (int or str, optional): Iterations, as for `run`.
            until (str, optional): Drive until this predicate holds, as for
                `run`.
            limit (int or str, optional): The default branches or steps
                per dispatch, as for `run`.
            backstop_limit (int, optional): As for `run`.
            retry (int, optional): As for `run`.
            vcpu (int or str, optional): As for `run`.
            strategy (str, optional): As for `run`.
            seed (int, optional): As for `run`. Defaults to 0.
            irqblk (bool, optional): As for `run`. Defaults to False.
            rdtsc_exit (bool, optional): As for `run`. Defaults to False.
            idle_skip (bool, optional): As for `run`. Defaults to True.
            auto_checkpoints (int, optional): As for `run`.
            timeout (float, optional): Seconds to wait for the drive; None
                waits without a limit.

        Returns:
            RunResult: The iterations that ran and where each vCPU stopped.

        Raises:
            InvalidArgument: The plan or an option is not valid.
            NotRunning: The run has no machine; `start` it first.
            Timeout: The drive did not finish within `timeout`.
        """
        with _translated():
            opts = _drive.RunOptions(
                iters=_args.span(iters),
                until=None if until is None else _args.predicate(until),
                limit=_args.span(limit),
                backstop_limit=backstop_limit,
                retry=_args.bounded("retry", retry, 0),
                vcpu=_args.vcpus(vcpu),
                strategy=0 if strategy is None else _parsers.strategy_type(strategy),
                seed=seed,
                irqblk=irqblk,
                rdtsc_exit=rdtsc_exit,
                idle_skip=idle_skip,
                auto_checkpoints=auto_checkpoints,
                plan_file=file if not isinstance(file, (str, Path)) else str(file),
                timeout_s=timeout,
            )
        return self._drive(opts)

    def _drive(self, opts: _drive.RunOptions) -> results.RunResult:
        with self._client._connection() as client, _translated():
            try:
                done = _drive.run(client, self._id, opts, name=self._name)
            except (_control.BudgetSpent, _control.VtimeSpent) as spent:
                # The iterations ran; the predicate just did not hold within
                # them, or within the machine time allowed. That is an answer
                # about the guest, not a failure of the call, so it is a
                # result whose `reached` is False. The host's clock running
                # out is still Timeout: nothing was answered there.
                done, reached = spent.result, False
            else:
                reached = True
            # Each clause's text, for the counts the result carries.
            done = _drive.attach_plan(done, client, self._id)
        return results.run_result(done, reached=reached)

    def replay(
        self,
        line: "Run | str | None" = None,
        *,
        to: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        trace: bool = False,
        retry: int = _replay.RETRY_DEFAULT,
        limit_margin: int = _replay.LIMIT_MARGIN_DEFAULT,
        instr_margin: int = _replay.LIMIT_MARGIN_DEFAULT,
    ) -> results.ReplayResult:
        """Re-execute a recorded line, checking every entry, as `machine
        replay` does.

        A branch forked at a checkpoint starts from its parent's state
        there; replay drives it forward through the recorded entries and
        compares each one's state, so it answers whether the recording
        reproduces. By default the line is the parent's; `line` names
        another run to follow, such as a sibling forked at the same point.
        A replay that does not reproduce is a result, not an exception:
        check `converged`, then `diverged` for what differs. `converged`
        covers the `replayed` entries only, so a branch forked at the tip
        replays nothing and proves nothing; record past the fork point
        first.

        Example:
            >>> run.checkpoint("booted")
            >>> kid = run.fork(at="^booted", name="check", start=False)
            >>> run.run(iters=200)
            >>> kid.start()
            >>> result = kid.replay()
            >>> result.converged, result.replayed
            (True, 200)

        Args:
            line (Run or str, optional): The run whose line to replay; None
                replays the parent's.
            to (str or Point or Place or Checkpoint, optional): The point of
                the line to replay to, as `at` is for `fork`: `^name` a
                checkpoint, `#id` an entry, a virtual time. It is resolved
                on the line replayed. None replays to the end of the line.
            trace (bool, optional): Replay one iteration at a time, slower
                and exact for tracing. Defaults to False.
            retry (int, optional): How many times to replay an entry again
                after it diverges or the host interrupts it, going back to
                the newest checkpoint before it each time; 0 does not retry.
                An entry that diverges the same way twice is not replayed
                again. Defaults to 2.
            limit_margin (int, optional): Branches short of each recorded
                stop where the branch counter interrupts; an interrupt that
                lands past the stop misses the entry. 0 uses the run's,
                else the host's. Defaults to 0.
            instr_margin (int, optional): The same in instructions, for a
                stop placed by an instruction count. 0 uses the run's, else
                the host's. Defaults to 0.

        Returns:
            ReplayResult: Whether it converged, and what differs if not.

        Raises:
            NotRunning: The branch has no machine; `start` it first.
            InvalidArgument: The run has no parent to replay, `line` is this
                run, `to` is not in the line, `retry` is below 0, or
                `limit_margin` is below 0.
            Conflict: A retry could not go back to the checkpoint before the
                entry that diverged; the message says where the run stands.
        """
        with self._client._connection() as client, _translated():
            ref = None
            if line is not None:
                ref = line.id if isinstance(line, Run) else line
            replayed = None
            if isinstance(to, (results.Place, results.Checkpoint)):
                replayed = _branching.replayed_line(client, self._id, ref)
            point = _point_text(client, replayed or self._id, "to", to)
            path, target = _branching.replay_plan(client, self._id, ref, point)
            try:
                done = _replay.replay(
                    client,
                    self._id,
                    target,
                    trace=trace,
                    path=path,
                    retry=retry,
                    limit_margin=limit_margin,
                    instr_margin=instr_margin,
                )
            except _replay.Diverged as diverged:
                return results.replay_result(diverged.result)
        return results.replay_result(done)

    def play(
        self,
        source: "Run | str | None" = None,
        *,
        entries: "int | tuple[int, int] | str | slice | None" = None,
        include: Optional[builtins.list[str]] = None,
        exclude: Optional[builtins.list[str]] = None,
    ) -> Optional[results.RunResult]:
        """Feed another run's recorded events into this machine, without
        checking them, as `machine play` does.

        Where `replay` verifies a recording, `play` reuses one: the recorded
        I/O, interrupts and timing drive this machine, which records its own
        entries as it goes. Use it to steer a branch through a recorded
        input and then diverge from it.

        Example:
            >>> branch.play(entries="1:100", exclude=["irq"])

        Args:
            source (Run or str, optional): The run whose entries to play;
                defaults to this branch's parent.
            entries (int, tuple[int, int], str or slice, optional): Which of
                the source's recorded entries, by entry id, in the grammar
                `entries` takes: `A:B` entries A through B, `A:` from A on,
                `:B` up to B, `-N` the last N. Ids outside the source's own
                log are refused. Any window of any source in the tree;
                nothing checks that this machine stands where they were
                recorded. Defaults to all of them.
            include (list[str], optional): Play only these event types:
                `run`, `irq`, `exception`, `random`, `device_in`,
                `block_done`, `input`, `exit_shutdown`, `system_shutdown`,
                `system_reset`, `checkpoint`, `cpuid`, `rng_seed`. The
                entries no type names are always played: the clock's rate
                and steps, the focus, the stops and the debugger's writes,
                which the `run` entries after them need.
            exclude (list[str], optional): Leave these event types out.

        Returns:
            RunResult: What the machine did; None when the source had
            nothing to play.

        Raises:
            InvalidArgument: An event type or `entries` is not valid.
            NotFound: No run matches `source`.
            Diverged: The machine could not follow the recording.
        """
        words = [f"+{t}" for t in include or []] + [f"-{t}" for t in exclude or []]
        with self._client._connection() as client, _translated():
            event_filter, rest = _parsers.parse_event_filter(words)
            if rest:
                raise InvalidArgument(f"Not an event type: {' '.join(rest)}")
            if source is None:
                source_id = _replay.play_source(client, self._id)
            elif isinstance(source, Run):
                source_id = source.id
            else:
                source_id = client.resolve_run_ref(source)
            start, count = _replay.play_window(
                client, source_id, _args.source_window(entries)
            )
            try:
                played = _replay.play(
                    client,
                    self._id,
                    source_id,
                    start=start,
                    count=count,
                    event_filter=event_filter,
                )
            except _replay.Diverged as diverged:
                raise Diverged(
                    f"play failed at entry {diverged.status.current_entry_id}"
                ) from None
        return None if played.result is None else results.run_result(played.result)

    def diff(self, other: "Run | str | None" = None, *, entry: int = 0) -> results.Diff:
        """Compare machine states component by component, as `machine -o
        json diff` does.

        Without `other`, it compares an entry this branch replayed with the
        entry it reproduced: the detail behind a replay that did not
        converge. With `other`, it compares two recordings side by side,
        such as two branches of one checkpoint that should agree.

        It compares the ONE pair of entries named, so two branches driven
        differently are compared at whatever each recorded there; entry
        numbers are not a common clock. To find where they parted, compare
        the entries the two share, walking back from the newest.

        Example:
            >>> result = kid.replay()
            >>> if not result.converged:
            ...     print(kid.diff(entry=result.entry_id).diverged)
            >>> run.diff("app/try-1:120", entry=118)

        Args:
            other (Run or str, optional): The run to compare with, as a
                `Run` or `name[:m]`, `m` its entry, which defaults to
                `entry`; None compares with what this branch replayed.
            entry (int, optional): The entry here; 0 for the newest.

        Returns:
            Diff: Whether the states match, and each component that does not.

        Raises:
            InvalidArgument: An entry is not in its run's log, or without
                `other` the entry was not replayed.
            NotFound: No run matches `other`.
        """
        with self._client._connection() as client, _translated():
            other_id, other_entry = "", 0
            if isinstance(other, Run):
                other_id = other.id
            elif other is not None:
                ref, _, at = other.partition(":")
                if at and not at.isdigit():
                    raise InvalidArgument(f"Bad entry {at!r} in {other!r}.")
                other_id = _branching.resolve_run_ref(client, ref, self._id)
                other_entry = int(at) if at else 0
            result = _control.compare_diff(
                client,
                self._id,
                entry_id=entry,
                other_run=other_id,
                other_entry=other_entry,
            )
        return results.Diff(
            converged=result.converged,
            entry_id=result.entry_id,
            diverged=tuple(dict(row) for row in result.diverged),
            memory_compared=result.memory_compared,
            disk_compared=result.disk_compared,
        )

    def dump(
        self,
        *,
        entry: Optional[int] = None,
        show: Optional[str] = None,
        mem: Optional[str] = None,
        vcpu: Optional[int] = None,
    ) -> dict[str, Any]:
        """Read the machine's state, as `machine -o json dump` does.

        Live from the machine, or with `entry` as it was recorded at that
        entry, which needs a run created with `side_dump=True` or
        `side_trace=True`. The answer is one JSON object per section.

        Example:
            >>> regs = run.dump(show="regs")
            >>> page = run.dump(mem="v0x400000", vcpu=0)

        Args:
            entry (int, optional): Read the state recorded at this entry;
                None reads the live machine.
            show (str, optional): A comma list of sections, such as
                `regs,code,stack,pt`, or `all`; None reads the machine state
                sections.
            mem (str, optional): Also read memory: `N` a memory cluster,
                `pADDR` a physical address, `vADDR` a virtual address through
                `vcpu`'s page tables.
            vcpu (int, optional): The vCPU whose registers and page tables
                to read; defaults to 0.

        Returns:
            dict[str, Any]: One JSON object per section.

        Raises:
            InvalidArgument: A section or address is not valid, the entry is
                not in the log, or the run records no state per entry.
            NotRunning: A live read, and the run has no machine.
        """
        with _translated():
            wanted = (
                None
                if show is None
                else _sidedump.parse_sections(
                    show,
                    _sidedump.SECTION_MAP,
                    "dump",
                    reject=_sidedump.DIFF_ONLY_SECTIONS,
                )
            )
            address = None if mem is None else _sidedump.parse_mem_address(mem)
        if address is not None and wanted is None:
            wanted = set()
        with self._client._connection() as client, _translated():
            return _control.dump_json(
                client,
                self._id,
                entry_id=entry,
                vcpu_id=vcpu,
                sections=wanted,
                mem=address,
            )

    def vcpu(self, *, vcpu: int = 0) -> results.Vcpu:
        """Read where one vCPU stopped, as `machine vcpu` does.

        `Run.state` answers what the machine is doing; this answers where a
        vCPU is, what the last drive did, and whether its predicate held.

        Example:
            >>> at = run.vcpu()
            >>> at.entry_id, at.exit, at.until_satisfied
            (200, 'INSTR_LIMIT', True)

        Args:
            vcpu (int, optional): The vCPU. Defaults to 0.

        Returns:
            Vcpu: Where it stopped, and what the last drive did.

        Raises:
            NotRunning: The run has no machine.
            InvalidArgument: The machine has no such vCPU.
        """
        with self._client._connection() as client, _translated():
            state = _control.vcpu_state(client, self._id, vcpu_id=vcpu)
            machine_state = _control.mstate(client, self._id)
        return results.vcpu(
            {
                "vcpu": dataclasses.asdict(state),
                "machine": dataclasses.asdict(machine_state),
            }
        )

    def wait(
        self,
        *,
        for_: str,
        interval: float = _wait.DEFAULT_INTERVAL_S,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Wait until the run satisfies a predicate, as `machine wait` does.

        It watches without driving: another program or thread runs the
        machine. To drive until a predicate holds, use `run(until=...)`.
        `Client.wait` waits for several runs, or every run.

        A task predicate is an EVENT, so it holds only for one that happens
        while the wait is on: waiting for `/app:exit` after the program has
        already exited runs out. The run states are conditions and hold
        whenever they are true.

        Example:
            >>> run.stop(wait=False)
            >>> run.wait(for_="stopped", timeout=60)

        Args:
            for_ (str): The predicate, as for `run`: `ready`, `running`,
                `paused`, `stopped`, `halted`, `tasks`, `entry>=N`,
                `entry=+N`, `vtime>=T`, `vtime=+T`, `console~REGEX` or
                another source as in `ttyS1~REGEX`, or a task predicate
                such as
                `/app:exit`. The trailing underscore avoids the Python
                keyword.
            interval (float, optional): Seconds between checks. Defaults
                to 1.
            timeout (float, optional): Seconds to wait; None waits without a
                limit, 0 checks once.

        Returns:
            Run: This run.

        Raises:
            InvalidArgument: `for_` is not a predicate.
            Timeout: It did not hold within `timeout`.
            NotRunning: The run stopped, so the predicate can no longer
                hold.
        """
        predicate = _args.predicate(for_)
        _args.positive("interval", interval)
        with self._client._connection() as client, _translated():
            _wait.poll(
                client, self._id, predicate, timeout_s=timeout, interval_s=interval
            )
        return self

    @property
    def output(self) -> "Output":
        """Output: What the guest printed, on its console and on every other
        source: read it, search it, and find the entry that printed any byte
        of it."""
        return Output(self)

    def _output_lines(
        self,
        lines: "int | str | None",
        entries: "int | tuple[int, int] | str | slice | None",
        follow: bool,
        timeout: Optional[float],
        source: str,
    ) -> "builtins.list[str] | Generator[str, None, None]":
        """The lines of the output, as `Output` is called for them."""
        if entries is not None and follow:
            raise InvalidArgument("Follow reads new output; entries reads old.")
        if follow:
            return self._output_followed(lines, timeout, source)
        return [line.text for line in self._output_records(lines, entries, source)]

    def _output_followed(
        self, lines: "int | str | None", timeout: Optional[float], source: str
    ) -> Generator[str, None, None]:
        selected = _args.source(source)
        with self._client._connection() as client, _translated():
            reader = self._output_reader(client, lines, selected, follow=True)
            for line in _args.followed(reader, lines, timeout):
                yield _args.line_text(line)

    def _output_records(
        self,
        lines: "int | str | None",
        entries: "int | tuple[int, int] | str | slice | None",
        source: str,
    ) -> "builtins.list[results.OutputLine]":
        """The lines of the output with the entries and the source of each,
        as `Output.lines` returns them."""
        selected = _args.source(source)
        if _args.lines_spec(lines) == _args.NO_LINES:
            return []
        with self._client._connection() as client, _translated():
            if entries is None:
                window = _args.window(
                    self._output_reader(client, lines, selected, labelled=True),
                    lines,
                )
                return results.output_lines(
                    window.lines, window.ranges, window.sources, window.start + 1
                )
            first, last = _args.entry_pair(entries)
            printed = _run_logs.read_console_entries(
                client, self._id, first, last, True, selected
            )
        found = results.output_lines(
            printed.lines, printed.entry_ranges, printed.sources, 1
        )
        spec = _args.lines_spec(lines)
        if spec is None:
            return found
        with _translated():
            start, count = _line_select.resolve_range(spec, len(found))
        return found[start : start + count]

    def _output_reader(
        self,
        client: _ipc.Client,
        lines: "int | str | None",
        source: "_console_source.Source",
        *,
        follow: bool = False,
        labelled: bool = False,
    ) -> _log_lines.ReadFn:
        """The line reader of what `source` printed, for the lines `lines`
        selects. `labelled` asks for the entries and the source of each."""
        spec = _args.lines_spec(lines)
        return _log_lines.console(
            client,
            self._id,
            None if spec == _args.NO_LINES else spec,
            follow=follow,
            with_entry_ranges=labelled,
            source=source,
        )

    @overload
    def entries(
        self,
        *,
        entries: "int | tuple[int, int] | str | slice | None" = ...,
        follow: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[results.LogEntry]: ...

    @overload
    def entries(
        self,
        *,
        entries: "int | tuple[int, int] | str | slice | None" = ...,
        follow: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[results.LogEntry, None, None]: ...

    def entries(
        self,
        *,
        entries: "int | tuple[int, int] | str | slice | None" = None,
        follow: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[results.LogEntry] | Generator[results.LogEntry, None, None]":
        """Read entries of the run's log, as `machine -o json entries` does.

        Each entry is one stop of a vCPU: its `event_name`, such as `RUN`,
        `IRQ` or `EXIT_SHUTDOWN`, the `vcpu`, the instruction address
        `rip`, the instruction and branch counts `ic` and `rcb`, the
        virtual time, and the hashes replay compares. A branch's log
        continues its parent's, so a branch reads its ancestors' entries
        before its own. With `follow`, it yields the entries, then each
        entry the run records after them.

        Example:
            >>> [e["entry_id"] for e in run.entries(entries="-3")]
            [198, 199, 200]
            >>> for entry in run.entries(entries="-1", follow=True, timeout=60):
            ...     if entry["event_name"] == "EXIT_SHUTDOWN":
            ...         break

        Args:
            entries (int, tuple[int, int], str or slice, optional): Which
                entries: an int or a pair for one entry or a range, `A:B` the
                ids A through B, `A:` from A on, `:B` up to B, `:` every
                entry, `-N` the last N, or a Python slice, which is half-open
                as Python's are: `slice(100, 200)` is 100 through 199 where
                `(100, 200)` is 100 through 200, and `slice(-5, None)` is the
                last 5. The last 20 by default; with `follow`, the last 10.
            follow (bool, optional): Yield new entries as they are recorded.
                Defaults to False.
            timeout (float, optional): With `follow`, end the generator after
                this many seconds, without raising; None follows until the
                caller leaves the loop.

        Returns:
            list[LogEntry]: One record per entry, oldest first;
            with `follow`, an iterator of them.

        Raises:
            InvalidArgument: `entries` is not valid.
        """
        selection = "-20" if entries is None else _args.entries_spec(entries)
        if follow:
            return self._followed_entries(
                "-10" if entries is None else selection, timeout
            )
        with self._client._connection() as client, _translated():
            return [
                results.log_entry(record)
                for record in _run_logs.entry_records(client, self._id, selection)
            ]

    def _followed_entries(
        self, selection: str, timeout: Optional[float]
    ) -> Generator[results.LogEntry, None, None]:
        with self._client._connection() as client, _translated():
            # Follow from the tip as it stands now, so a selection that yields
            # nothing does not make the follow read the whole log.
            here = _cursor.of_run(client, self._id)
            last = 0 if here is None else here.entry_id
            for record in _run_logs.entry_records(client, self._id, selection):
                last = max(last, record.entry_id)
                yield results.log_entry(record)
            deadline = None if timeout is None else time.monotonic() + timeout
            while deadline is None or time.monotonic() < deadline:
                time.sleep(_log_lines.FOLLOW_INTERVAL)
                for record in _run_logs.entry_records(client, self._id, f"{last + 1}:"):
                    last = record.entry_id
                    yield results.log_entry(record)

    def _read(self, source: str, lines: "int | str | None") -> builtins.list[str]:
        with self._client._connection() as client, _translated():
            window = _args.window(self._reader(client, source), lines)
            return [_args.line_text(line) for line in window.lines]

    def _followed(
        self, source: str, lines: "int | str | None", timeout: Optional[float]
    ) -> Generator[str, None, None]:
        with self._client._connection() as client, _translated():
            for line in _args.followed(self._reader(client, source), lines, timeout):
                yield _args.line_text(line)

    def _reader(self, client: _ipc.Client, source: str) -> _log_lines.ReadFn:
        """The line reader of `source`: the machine's `log`, or its stderr,
        `err`."""

        def read(
            start: int, count: int, have: Optional[int] = None
        ) -> _run_logs.LogLines:
            # A follow says how much of the last line it holds. These logs
            # are read again at each interval, whatever it holds.
            del have
            return _run_logs.read_log(
                client, self._id, source, start=start, count=count
            )

        return read

    @property
    def debug(self) -> "Debug":
        """Debug: The machine's own log and its stderr."""
        return Debug(self)

    @property
    def fs(self) -> "Fs":
        """Fs: Files on the run's disk, as a checkpoint saved them."""
        return Fs(self)

    @property
    def control(self) -> "Control":
        """Control: The commands issued to the run."""
        return Control(self)

    # -- the guest -------------------------------------------------------------

    @property
    def tasks(self) -> "Tasks":
        """Tasks: The guest's tasks: list them, and hold, yield, slice or pin
        the ones focus steers."""
        return Tasks(self)

    @property
    def focus(self) -> "Focus":
        """Focus: Which guest tasks the machine steers and reports on."""
        return Focus(self)

    def task(self, tid: int) -> "Task":
        """A task of the guest, to read as a debugger does.

        Args:
            tid (int): The task's id. A tgid is its first task.

        Returns:
            Task: That task, as it is now: every read of it is of the
            same task.

        Raises:
            NotFound: The machine has no task `tid`.
            NotRunning: The run has no machine.
        """
        checked = _args.tid(tid)
        with self._client._connection() as client, _translated():
            rows = client.tasks(self.id).rows
        for row in rows:
            if row.tid == checked:
                return Task(self, checked, row.start)
        raise NotFound(f"No tid {checked}")

    @property
    def vtime(self) -> "Vtime":
        """Vtime: The guest's virtual clock."""
        return Vtime(self)

    @property
    def rng(self) -> "Rng":
        """Rng: The machine's RNG device and its seed."""
        return Rng(self)

    @property
    def inject(self) -> "Faults":
        """Faults: Faults injected into the guest's system calls and kernel."""
        return Faults(self)

    @property
    def axis(self) -> "Axis":
        """Axis: The run's defaults of the draw axes."""
        return Axis(self)

    @property
    def clock(self) -> "Clock":
        """Clock: The guest's date."""
        return Clock(self)

    @property
    def irq(self) -> "Irq":
        """Irq: Interrupts the machine can inject into the guest."""
        return Irq(self)

    @property
    def syscalls(self) -> "Syscalls":
        """Syscalls: The guest kernel's system calls, and a task's counts."""
        return Syscalls(self)

    @property
    def ints(self) -> "Ints":
        """Ints: The guest kernel's interrupt vectors, and a task's counts."""
        return Ints(self)

    # -- identity --------------------------------------------------------------

    def _point(
        self,
        name: str,
        value: "str | results.Point | results.Place | results.Checkpoint | None",
    ) -> Optional[str]:
        """`value` as the point text this run resolves. A place is checked
        against this run's line first."""
        if isinstance(value, (results.Place, results.Checkpoint)):
            with self._client._connection() as client, _translated():
                return _point_text(client, self._id, name, value)
        return _point_text(None, self._id, name, value)

    def _branch(self, verb: Any, request: _branching.BranchRequest) -> "Run":
        """Fork, detach, go or switch, and hand back the run it leaves the
        machine on."""
        with self._client._connection() as client, _translated():
            made = verb(client, request)
        return Run(self._client, made.run_id, made.name)

    def _branch_info(self, source: _run_source.RunSource) -> _ipc.BranchInfo:
        """This run's node in its tree, with its lineage."""
        with _translated():
            listed = source.branch_list(scope_run_id=self._id)
        mine = next((b for b in listed if b.run_id == self._id), None)
        if mine is None:
            raise NotFound(f"No such run: {self._name or self._id}.")
        return mine

    def as_json(self) -> dict[str, Any]:
        """The run's id and name, and its listing when it has one, as a
        JSON-serializable dict; `describe` has the rest.

        Returns:
            dict[str, Any]: `id`, `name` and `listing`.
        """
        return {
            "id": self._id,
            "name": self._name,
            "listing": None if self.listing is None else self.listing.as_json(),
        }

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, *exc: object) -> None:
        if not self._keep:
            self.delete(recursive=True, force=True)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Run) and other._id == self._id

    def __hash__(self) -> int:
        return hash(self._id)

    def __repr__(self) -> str:
        return f"Run(name={self._name!r}, id={self._id!r})"


class Debug:
    """The machine's own log and its stderr, as `Run.debug`; not created
    directly. `machine debug log` and `machine debug err`.

    Example:
        >>> run.debug.err(lines=-20)
        >>> for line in run.debug.log(lines=0, follow=True, timeout=60):
        ...     print(line)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    @overload
    def log(
        self,
        *,
        lines: "int | str | None" = ...,
        follow: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[str]: ...

    @overload
    def log(
        self,
        *,
        lines: "int | str | None" = ...,
        follow: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[str, None, None]: ...

    def log(
        self,
        *,
        lines: "int | str | None" = None,
        follow: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[str] | Generator[str, None, None]":
        """Read the machine's own log, as `machine debug log` does.

        The machine logs what it did with each command: starts, dispatches,
        checkpoints and errors. It is the first place to look when a call
        failed without a clear reason.

        Args:
            lines (int or str, optional): Which lines, as for `Run.output`.
                All by default; with `follow`, the last 10.
            follow (bool, optional): Yield new lines as they are logged.
                Defaults to False.
            timeout (float, optional): With `follow`, end the generator after
                this many seconds, without raising; None follows until the
                caller leaves the loop.

        Returns:
            list[str]: The lines, without newlines; with `follow`, an
            iterator of them.

        Raises:
            InvalidArgument: `lines` is not valid.
        """
        if follow:
            return self._run._followed("log", lines, timeout)
        return self._run._read("log", lines)

    @overload
    def err(
        self,
        *,
        lines: "int | str | None" = ...,
        follow: Literal[False] = ...,
        timeout: Optional[float] = ...,
    ) -> builtins.list[str]: ...

    @overload
    def err(
        self,
        *,
        lines: "int | str | None" = ...,
        follow: Literal[True],
        timeout: Optional[float] = ...,
    ) -> Generator[str, None, None]: ...

    def err(
        self,
        *,
        lines: "int | str | None" = None,
        follow: bool = False,
        timeout: Optional[float] = None,
    ) -> "builtins.list[str] | Generator[str, None, None]":
        """Read what the machine wrote to stderr, as `machine debug err`
        does: crash text and fatal errors, the reason a run `died`.

        Args:
            lines (int or str, optional): Which lines, as for `Run.output`.
                All by default; with `follow`, the last 10.
            follow (bool, optional): Yield new lines as they are written.
                Defaults to False.
            timeout (float, optional): With `follow`, end the generator after
                this many seconds, without raising; None follows until the
                caller leaves the loop.

        Returns:
            list[str]: The lines, without newlines, empty for a machine that
            wrote none; with `follow`, an iterator of them.

        Raises:
            InvalidArgument: `lines` is not valid.
        """
        if follow:
            return self._run._followed("err", lines, timeout)
        return self._run._read("err", lines)


class Fs:
    """Files on the run's disk, as `Run.fs`; not created directly.

    The filesystem is read from the disk as a checkpoint saved it, never
    from the live machine. Every read names its checkpoint with `at`: there
    is no newest by default, since a listing that does not say how old it is
    reads as the machine's files now. The machine need not be running, and a
    branch shows its own writes. A file the machine has written and not yet
    flushed to its disk is not in a checkpoint; `Run.exec` reads the files
    of the live machine.

    Example:
        >>> [f.name for f in run.fs.ls("/var/log", at="^done")]
        ['app.log', 'syslog']
        >>> run.fs.cat("/var/log/app.log", at="^done").decode()
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def _checkpoint(self, client: _ipc.Client, at: "str | results.Checkpoint") -> int:
        """The entry of the checkpoint `at` names."""
        point = _point_text(client, self._run.id, "at", at)
        if not point:
            raise InvalidArgument(
                "at must name a checkpoint, such as '^booted': the disk is"
                " read where a checkpoint saved it."
            )
        return _fs.checkpoint_at(client, self._run.id, point)

    def ls(
        self, path: str = "/", *, at: "str | results.Checkpoint"
    ) -> builtins.list[results.File]:
        """List a directory on the run's disk, as `machine fs ls` does.

        Args:
            path (str, optional): The directory. Defaults to `/`.
            at (str or Checkpoint): The checkpoint to read at, as a point:
                `^name`, or `#id` for the checkpoint at an entry.

        Returns:
            list[File]: The directory's entries.

        Raises:
            NotFound: The path does not exist, or the run has no such
                point.
            Conflict: `at` is a point with no checkpoint at it.
        """
        with self._run._client._connection() as client, _translated():
            listing = _fs.list_path(
                client, self._run.id, path, entry_id=self._checkpoint(client, at)
            )
        return [results.file(e) for e in listing.entries]

    def cp(
        self,
        path: str,
        dest: "str | Path" = ".",
        *,
        at: "str | results.Checkpoint",
        recursive: bool = False,
    ) -> builtins.list[str]:
        """Copy a file or directory from the run's disk to this machine, as
        `machine fs cp` does.

        Example:
            >>> run.fs.cp("/var/log", "logs/", at="^done", recursive=True)
            ['logs/log/app.log', 'logs/log/syslog']

        Args:
            path (str): The file or directory on the run's disk.
            dest (str or Path, optional): Where to write it on this machine.
                Defaults to the current directory.
            at (str or Checkpoint): The checkpoint to read at, as a point:
                `^name`, or `#id` for the checkpoint at an entry.
            recursive (bool, optional): Copy a directory and everything in
                it. Defaults to False.

        Returns:
            list[str]: The files written.

        Raises:
            NotFound: The path does not exist, or the run has no such
                point.
            Conflict: `at` is a point with no checkpoint at it.
            InvalidArgument: `path` is a directory and `recursive` is False.
        """
        with self._run._client._connection() as client, _translated():
            written = _fs.cp_to_local(
                client,
                self._run.id,
                path,
                Path(dest),
                recursive=recursive,
                entry_id=self._checkpoint(client, at),
            )
        return [str(p) for p in written]

    def cat(self, path: str, *, at: "str | results.Checkpoint") -> bytes:
        """Read a file from the run's disk, as `machine fs cat` does.

        Args:
            path (str): The file.
            at (str or Checkpoint): The checkpoint to read at, as a point:
                `^name`, or `#id` for the checkpoint at an entry.

        Returns:
            bytes: The file's content.

        Raises:
            NotFound: The path does not exist, or the run has no such
                point.
            Conflict: `at` is a point with no checkpoint at it.
        """
        with self._run._client._connection() as client, _translated():
            return _fs.read_all(
                client, self._run.id, path, entry_id=self._checkpoint(client, at)
            )


class Control:
    """The commands issued to a run, as `Run.control`; not created directly.

    Example:
        >>> [c.text for c in run.control.log(lines=-3)]
        ['/s 5', '/checkpoint booted', '/fork']
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def log(
        self,
        *,
        lines: "int | str | None" = None,
        entries: "int | tuple[int, int] | str | slice | None" = None,
    ) -> builtins.list[results.Command]:
        """List the commands issued to the run across its lineage, oldest
        first, as `machine control log` does.

        Every command that drove or changed the machine is recorded,
        whichever program issued it, so the history says how a run got
        where it is.

        Args:
            lines (int or str, optional): Which commands: N command N, -N the
                last N, `A:B` the Ath through the Bth, `A:` from the Ath on.
                All by default.
            entries (int, tuple[int, int], str or slice, optional): Keep the
                commands issued at this entry, or at the first through the
                last.

        Returns:
            list[Command]: The commands.

        Raises:
            InvalidArgument: `lines` or `entries` is not valid.
        """
        span = None if entries is None else _args.entry_pair(entries)
        with self._run._client._connection() as client, _translated():
            history = _control_log.history(
                client, self._run.id, entries=span, lines=_args.lines_spec(lines)
            )
        return [
            results.Command(
                text=cmd.text,
                start_entry_id=cmd.start_entry_id,
                entry_id=cmd.entry_id,
                vcpu=cmd.vcpu,
            )
            for cmd in _control_log.commands(history.records)
        ]


class Breakpoints:
    """The breakpoints and watchpoints of a run, as `Run.breakpoints`; not
    created directly.

    The run log keeps them: a branch has the ones its parent had where it
    was forked, and a replay applies each add and delete again. A run holds
    3 at most. A run that reaches one ends there, and its result's `hits`
    names it.

    Example:
        >>> stop = run.task(412).breakpoint("main")
        >>> run.run(iters=200).hits[0].number == stop.number
        True
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def list(self) -> builtins.list[results.Breakpoint]:
        """List the breakpoints, watchpoints and catchpoints, by number, as
        `machine points` does, then the hardware stops a debugger in the
        guest holds, which have no number.

        Returns:
            list[Breakpoint]: Each one, by number.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.breakpoints(client, self._run.id)
        return [results.breakpoint(one) for one in found]

    def hits(self) -> builtins.list[results.Hit]:
        """The hits the run's line keeps, in the order they hit, as `machine
        break hits` lists them.

        Returns:
            list[Hit]: Each hit: its entry, its time, the stops that hit and
            a catchpoint's event.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.recorded_hits(client, self._run.id)
        return [results.hit(one) for one in found]

    def enable(
        self, number: int, enabled: bool = True, *, count: int = 0
    ) -> results.Breakpoint:
        """Enable a breakpoint, a watchpoint or a catchpoint, or disable it
        with `enabled=False`, as `machine break enable` and `disable` do.
        The machine does not arm a disabled one. With `count`, it is enabled
        for that many hits that count, then disabled, as `--count` and GDB's
        enable count; `count=1` is GDB's enable once.

        Args:
            number (int): The stop's number.
            enabled (bool, optional): Enable it, or disable it with False.
                Defaults to True.
            count (int, optional): The hits it is enabled for. Defaults to
                0, for no end.

        Returns:
            Breakpoint: The stop, changed.

        Raises:
            NotFound: The run has no stop of that number.
            InvalidArgument: A count below 0.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.enable(client, self._run.id, number, enabled, count)
        return results.breakpoint(found)

    def condition(self, number: int, condition: str = "") -> results.Breakpoint:
        """Set where a stop's hits count, as `machine break condition`
        does: comparisons of registers, variables and numbers, such as
        `"$rax == 0 && counter > 3"`. An empty condition clears it.

        Args:
            number (int): The stop's number.
            condition (str, optional): The condition. Defaults to none.

        Returns:
            Breakpoint: The stop, changed.

        Raises:
            InvalidArgument: Not a condition.
            NotFound: The run has no stop of that number.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.condition(client, self._run.id, number, condition)
        return results.breakpoint(found)

    def ignore(self, number: int, count: int) -> results.Breakpoint:
        """Pass a stop's next `count` hits that count, as `machine break
        ignore` does.

        Args:
            number (int): The stop's number.
            count (int): The hits to pass; 0 stops at the next.

        Returns:
            Breakpoint: The stop, changed.

        Raises:
            InvalidArgument: A count below 0.
            NotFound: The run has no stop of that number.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.ignore(client, self._run.id, number, count)
        return results.breakpoint(found)

    def catch(
        self,
        event: str,
        *values: str,
        in_: str = "",
        temporary: bool = False,
        condition: str = "",
        print: str = "",
        disabled: bool = False,
        ignore: int = 0,
        file: str = "",
        op: str = "",
    ) -> results.Breakpoint:
        """Add a catchpoint, as `machine catch add` does: a stop at an event
        of a task rather than an address.

        Example:
            >>> stop = run.breakpoints.catch("fork", in_="tgid=1")
            >>> run.run(iters=2000).hits[0].event
            'fork, tid 1 forked tgid 36'
            >>> run.breakpoints.catch("exit", in_="comm=cat").comm
            'cat'
            >>> stop = run.breakpoints.catch("io", file="/data/*.bin", op="write")

        Args:
            event (str): `exec`, `exit`, `fork`, `vfork`, `syscall`,
                `signal`, `load`, `unload`, `fault`, where an injected
                fault fires, or `io`, a disk request as its task submits
                it, before any disk fault decides on it; or `throw`, `catch` or
                `rethrow`, breakpoints in one tgid. An `exit` is a tgid's
                end, so it takes `tgid=N` and not `tid=N`. A `throw`,
                `catch` or `rethrow` is a breakpoint at the C++ runtime's
                function: it needs `tgid=N`, takes no values, and is one
                of the run's 64 breakpoints.
            *values (str): The system calls or signals, by name or number,
                four at most; or the one path of a load, unload or exec.
            in_ (str, optional): Whose events stop the run, as `--in` and
                `Faults.add` take it: `"tid=N"`, `"tgid=N"` or
                `"comm=NAME"`, the tasks `Tasks.exist` lists for it. An
                exec's comm is the new program's, an exit's the tgid
                leader's, its last task. A task renamed after exec is seen
                by its new comm at its next fork, exec or exit. Empty for
                any task's.
            temporary (bool, optional): Delete it at its first hit that
                counts. Defaults to False.
            condition (str, optional): Count a hit only where this holds.
            print (str, optional): Print this at a hit and go on.
            disabled (bool, optional): Add it disabled, as the machine does
                not arm it until it is enabled. Defaults to False.
            ignore (int, optional): Pass this many hits that count before
                one ends a run. Defaults to 0.
            file (str, optional): An `io` catchpoint's files, as `inject
                add --file` takes them: an absolute path, where `*` matches
                within a directory and `**` across them. A file matches
                whether it is opened or mapped before or after the
                catchpoint is added. Empty for any file.
            op (str, optional): An `io` catchpoint's operation, `read` or
                `write`. Empty for both.

        Returns:
            Breakpoint: The catchpoint, with its number.

        Raises:
            InvalidArgument: Not an event, a name the guest does not have,
                more values than a catchpoint matches, a file or an op for
                another event than `io`, or a run that holds 8
                catchpoints already.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.catch_add(
                client,
                self._run.id,
                event,
                list(values),
                scope=_breakpoints.catch_scope(
                    in_,
                    _breakpoints.catch_command(self._run.name),
                    event,
                    list(values),
                ),
                options=_breakpoints.Options(
                    temporary=temporary,
                    condition=condition,
                    print=print,
                    disabled=disabled,
                    ignore=ignore,
                ),
                file=file,
                op=op,
            )
        return results.breakpoint(found)

    def delete(self, number: int) -> None:
        """Delete a breakpoint, a watchpoint or a catchpoint, as `machine
        break delete` does. The run log keeps the delete.

        Args:
            number (int): Its number.

        Raises:
            NotFound: The run has no stop of that number.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            _breakpoints.delete(client, self._run.id, number)


class RunMarks:
    """The marks of a run's tree, as `Run.marks`; not created directly.

    A mark is a short second name, unique in the run's tree, that reads as
    `%name` wherever a run or a point is taken: `client.runs["%good"]`,
    `run.switch("%good")`, `run.fork(at="%good")`. Use it to keep track of
    the branches worth returning to while exploring. Marks answer with no
    session, from the account's catalog.

    Example:
        >>> branch.marks.add("found-race")
        >>> client.runs["%found-race"] == branch
        True
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def list(self) -> builtins.list[results.Mark]:
        """List the marks of the run's tree, as `machine marks list` does
        for one run.

        Returns:
            list[Mark]: Each mark and the run that carries it.
        """
        return self._run._client._marks(self._run.id)

    def add(self, mark: str) -> results.Mark:
        """Put a mark on the run, as `machine marks add` does.

        Marking twice is not an error.

        Args:
            mark (str): The mark, without the `%`.

        Returns:
            Mark: The mark, and the run that now carries it.

        Raises:
            AlreadyExists: Another run in the tree has this mark.
            InvalidArgument: `mark` is not a valid mark.
        """
        with self._run._client._catalog() as source, _translated():
            _branches.mark(source, self._run.id, mark, self._run.name)
        return results.Mark(name=mark, run_id=self._run.id, run_name=self._run.name)

    def remove(self, mark: str) -> None:
        """Take a mark off the run, as `machine marks remove` does.

        Args:
            mark (str): The mark, without the `%`.

        Raises:
            NotFound: The run does not carry this mark.
        """
        with self._run._client._catalog() as source, _translated():
            _branches.unmark(source, self._run.id, mark, self._run.name)


class Output:
    """What the guest printed, as `Run.output`; not created directly.

    Call it to read the lines, as `machine output` does. A branch's output
    continues its parent's, so everything here covers what its ancestors
    printed before it was forked.

    `search` finds a pattern and says which entry printed each match, and
    `entry` says which entry printed a given byte. A line is printed over a
    range of entries and a byte by exactly one, so these give the precise
    place in the run where something appeared. The search is done where the
    output is kept, so a large output is not downloaded to search it.

    Everything a run prints is one output, in the order it was printed, and
    each line has a source. Every reader here takes a `source`, and reads
    the console when it is given none. A source goes by the name the guest
    has for it:

    - `console`, the default: the run's console, whichever device that is.
    - `ttyS0` to `ttyS3`: the four serial ports. A program in the guest
      that writes to `/dev/ttyS1` prints on `ttyS1`.
    - `hvc0`: the virtio console.
    - `debugcon`: the firmware's debug port, which firmware prints on.
    - A path under `/output/`: an output file, or every file under a
      directory. A program writes its output files under the directory
      `MACHINE_OUTPUT_DIR` names, and a file `events.jsonl` that the service
      `app` writes there is `/output/keep/app/events.jsonl`.
    - `all`: every source, in the order the run printed.

    `sources` lists the ones that printed.

    What a program writes under `MACHINE_DROP_OUTPUT_DIR` is not stored,
    which is where output too verbose to keep goes. It is read by replaying
    a branch that keeps it:
    `run.fork(at="^before", keep_output="/output/drop/app/").replay()`.

    A line belongs to one source. With `all`, a source that prints while
    another's line is unfinished ends that line there, so the lines stay in
    the order they were printed and each has one source.

    Example:
        >>> run.run(until="console~login: ")
        >>> hit = run.output.find("login: ")
        >>> at_prompt = run.go(f"#{hit.entry_id}")
        >>> run.run(until="ttyS1~ready")
        >>> run.output(source="ttyS1", lines=-5)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    @overload
    def __call__(
        self,
        *,
        lines: "int | str | None" = ...,
        entries: "int | tuple[int, int] | str | slice | None" = ...,
        follow: Literal[False] = ...,
        timeout: Optional[float] = ...,
        source: str = ...,
    ) -> builtins.list[str]: ...

    @overload
    def __call__(
        self,
        *,
        lines: "int | str | None" = ...,
        entries: None = ...,
        follow: Literal[True],
        timeout: Optional[float] = ...,
        source: str = ...,
    ) -> Generator[str, None, None]: ...

    def __call__(
        self,
        *,
        lines: "int | str | None" = None,
        entries: "int | tuple[int, int] | str | slice | None" = None,
        follow: bool = False,
        timeout: Optional[float] = None,
        source: str = "console",
    ) -> "builtins.list[str] | Generator[str, None, None]":
        """Read what the guest printed on its console, or on another
        source, as `machine output` does.

        With `entries`, it returns what those entries printed, which ties
        output to the point in the run that produced it. With `follow`, it
        yields the lines, then each line the guest prints after them while
        another thread or program drives the run; a line still being printed
        is held back until it ends.

        Example:
            >>> run.output(lines=-20)
            ['[    0.000000] Linux version ...', ...]
            >>> run.output(entries=(150, 160))
            >>> for line in run.output(lines=0, follow=True, timeout=120):
            ...     if "login:" in line:
            ...         break
            >>> run.output(source="ttyS1")
            ['ready', ...]

        Args:
            lines (int or str, optional): Which lines: N line N, -N the last
                N, `A:B` the Ath through the Bth, `A:` from the Ath on, `:B` up
                to the Bth, 0 none. All by default; with `follow`, the last 10.
            entries (int, tuple[int, int], str or slice, optional): The entry, or
                the first and last entry, whose output to read; `lines` then
                selects from it.
            follow (bool, optional): Yield new lines as they are printed.
                Defaults to False.
            timeout (float, optional): With `follow`, end the generator after
                this many seconds, without raising; None follows until the
                caller leaves the loop.
            source (str, optional): The source to read, such as `ttyS1` or
                `all`: one of the names `Output` lists. Defaults to
                `console`.

        Returns:
            list[str]: The lines, without newlines; with `follow`, an
            iterator of them.

        Raises:
            InvalidArgument: `lines`, `entries` or `source` is not valid, or
                `entries` is given with `follow`.
        """
        return self._run._output_lines(lines, entries, follow, timeout, source)

    def lines(
        self,
        *,
        lines: "int | str | None" = None,
        entries: "int | tuple[int, int] | str | slice | None" = None,
        source: str = "console",
    ) -> builtins.list[results.OutputLine]:
        """Read lines with the source and the entries that printed each, as
        `machine output --wide` does.

        Calling the output returns the text alone. This returns a record a
        line, which says where the line came from: with `source="all"` it is
        how the output of several sources is told apart, in the order the
        run printed it.

        Example:
            >>> for line in run.output.lines(source="all", lines=-3):
            ...     print(line.entry_id, line.source, line.text)
            53966 ttyS3 ...
            53968 ttyS3 ...
            53998 ttyS0 serial-ports: done

        Args:
            lines (int or str, optional): Which lines, as for calling the
                output. All by default.
            entries (int, tuple[int, int], str or slice, optional): The entry, or
                the first and last entry, whose output to read; `lines` then
                selects from it.
            source (str, optional): The source to read, such as `ttyS1` or
                `all`: one of the names `Output` lists. Defaults to
                `console`.

        Returns:
            list[OutputLine]: The lines, oldest first.

        Raises:
            InvalidArgument: `lines`, `entries` or `source` is not valid.
        """
        return self._run._output_records(lines, entries, source)

    def sources(self) -> builtins.list[results.OutputSource]:
        """Return the sources that printed, as `machine output <run>
        sources` does.

        A reader takes a `source`. This says which sources there are to
        read, and how much each printed, without reading the output.

        Example:
            >>> for printed in run.output.sources():
            ...     print(printed.name, printed.lines)
            ttyS0 198
            ttyS1 10

        Returns:
            list[OutputSource]: The sources, in the order each first
            printed. An output file with nothing stored follows them, with
            `stored` False.
        """
        with self._run._client._connection() as client, _translated():
            return [
                results.output_source(printed)
                for printed in client.console_sources(self._run.id)
            ]

    def writes(self, *, source: str = "console") -> builtins.list[results.OutputWrite]:
        """Return what a source printed as bytes, a write at a time, as
        `machine output <run> bytes` does.

        A read by lines decodes text. This returns the bytes exactly as
        they were written, each write with the entry that made it, so an
        output file that is not text is read back unchanged.

        Example:
            >>> for write in run.output.writes(source="/output/keep/app"):
            ...     print(write.entry_id, write.task, len(write.data))
            1706 postgres 4096

        Args:
            source (str, optional): The source to read, as for calling
                `output`. Defaults to "console".

        Returns:
            list[OutputWrite]: The writes, in the order they were made.

        Raises:
            InvalidArgument: `source` is not a source.
        """
        printer = _args.source(source)
        with self._run._client._connection() as client, _translated():
            return [
                results.output_write(write)
                for write in client.output_writes(self._run.id, source=printer)
            ]

    def bytes(self, *, source: str = "console") -> builtins.bytes:
        """Return what a source printed as bytes, exactly as written.

        Example:
            >>> core = run.output.bytes(source="/output/keep/app/core.bin")

        Args:
            source (str, optional): The source to read, as for calling
                `output`. Defaults to "console".

        Returns:
            bytes: Every byte the source printed, in the order written.

        Raises:
            InvalidArgument: `source` is not a source.
        """
        return b"".join(write.data for write in self.writes(source=source))

    def entry(self, offset: int, *, source: str = "console") -> int:
        """Return the entry that printed one byte of the console, or of
        another source, as `machine output <run> entry` does.

        Example:
            >>> run.output.entry(48213)
            1706
            >>> run.output.entry(0, source="ttyS1")
            37491

        Args:
            offset (int): The byte's offset, counted from 0 within what the
                source printed.
            source (str, optional): The source, such as `ttyS1` or `all`:
                one of the names `Output` lists. Defaults to `console`.

        Returns:
            int: The entry.

        Raises:
            InvalidArgument: The offset is negative, or past what was
                printed, or `source` is not a source.
        """
        if offset < 0:
            raise InvalidArgument(f"A byte offset is 0 or more, not {offset}.")
        selected = _args.source(source)
        with self._run._client._connection() as client, _translated():
            return _run_logs.console_entry_at(
                client, self._run.id, offset, who=self._run.name, source=selected
            ).entry_id

    def search(
        self,
        pattern: str,
        *,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        to: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        most: Optional[int] = None,
        follow: bool = False,
        timeout: Optional[float] = None,
        source: str = "console",
    ) -> Generator[results.OutputMatch, None, None]:
        """Yield each match of a regular expression in the console, oldest
        first, as `machine output <run> search` does.

        A match is within a line, and comes with the entry that printed its
        first byte and that entry's place: `run.go(match.place)` stands
        where it began to be printed. With `from_` and `to` the search is
        of the output between two points, both included: a match begins at
        `from_` or after it and ends at `to` or before it. With `follow`,
        the generator goes on once the output is searched to its end and
        yields each match printed after that, while another thread or
        program drives the run. No match is missed between the two and none
        is yielded twice.

        Example:
            >>> for hit in run.output.search(r"error|panic"):
            ...     print(hit.entry_id, hit.place.point, hit.text)
            >>> hit = next(run.output.search("panic", from_=booted, to="1.5s"))
            >>> run.go(hit.place)
            >>> for hit in run.output.search("segfault", follow=True, timeout=60):
            ...     crash = run.fork(at=hit.place)
            ...     break
            >>> for hit in run.output.search("ready", source="all"):
            ...     print(hit.source, hit.text)

        Args:
            pattern (str): The regular expression.
            from_ (str or Point or Place or Checkpoint, optional): The point
                the search begins at, as `at` is for `fork`. None begins at
                the start of the output.
            to (str or Point or Place or Checkpoint, optional): The point it
                ends at. None ends at the end of the output. Not with
                `follow`.
            most (int, optional): End after this many matches.
            follow (bool, optional): Go on with what is printed next.
                Defaults to False.
            timeout (float, optional): With `follow`, end the generator after
                this many seconds, without raising; None follows until the
                caller leaves the loop.
            source (str, optional): The source to search, such as `ttyS1`
                or `all`: one of the names `Output` lists. Defaults to
                `console`. With `all`, a match is still within one source's
                line.

        Returns:
            Iterator[OutputMatch]: The matches, one at a time.

        Raises:
            InvalidArgument: `pattern` is not a regular expression or
                matches without any text, as `6*` does, `most` is less
                than 1, `source` is not a source, a point is not one of the
                run, `from_` is after `to`, or `to` is given with `follow`.
        """
        if most is not None and most < 1:
            raise InvalidArgument(f"most is 1 or more, not {most}.")
        selected = _args.source(source)
        with self._run._client._connection() as client, _translated():
            if follow and to is not None:
                _log_lines.refuse_follow_with_end(client, self._run.id, pattern)
            first, last = _branching.entry_range(
                client,
                self._run.id,
                _point_text(client, self._run.id, "from_", from_),
                _point_text(client, self._run.id, "to", to),
            )
            for match in _log_lines.console_search(
                client,
                self._run.id,
                pattern,
                first=first,
                last=last,
                most=most,
                follow=follow,
                timeout=timeout,
                source=selected,
            ):
                yield results.output_match(match)

    def find(
        self,
        pattern: str,
        *,
        from_: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        to: "str | results.Point | results.Place | results.Checkpoint | None" = None,
        source: str = "console",
    ) -> Optional[results.OutputMatch]:
        """Return the first match of a regular expression in the console,
        or in another source, or None when there is none. With `from_` it
        is the first match from that point. The matches after one are the
        ones `search` goes on to yield.

        Example:
            >>> hit = run.output.find("login: ", from_="^booted")
            >>> run.go(hit.place)
            >>> hits = run.output.search("login: ")
            >>> first, second = next(hits), next(hits)

        Args:
            pattern (str): The regular expression.
            from_ (str or Point or Place or Checkpoint, optional): The point
                to search from, as for `search`.
            to (str or Point or Place or Checkpoint, optional): The point to
                search to, as for `search`.
            source (str, optional): The source to search, as for `search`.
                Defaults to `console`.

        Returns:
            OutputMatch or None: The first match.

        Raises:
            InvalidArgument: `pattern` is not a regular expression or
                matches without any text, `source` is not a source, a point
                is not one of the run, or `from_` is after `to`.
        """
        return next(
            self.search(pattern, from_=from_, to=to, most=1, source=source), None
        )


#: Nanoseconds in a second: a stop's grace is whole seconds.
_NS_PER_S = 1_000_000_000


def _within_ns(within: str) -> int:
    """`within` as nanoseconds, or InvalidArgument."""
    bound = _args.within(within)
    if bound is None:
        raise InvalidArgument("within must be a duration, such as '30m'.")
    return bound


def _duration_ns(name: str, text: Optional[str]) -> Optional[int]:
    """A duration argument as nanoseconds, None for None."""
    if text is None:
        return None
    bound = _args.within(text)
    if bound is None:
        raise InvalidArgument(f"{name} must be a duration, such as '5s'.")
    return bound


class NetArp:
    """A container's neighbour table, as `Run.net.arp`; not created directly.

    The table maps each peer's address to the MAC that ARP resolved. The
    methods change it in the guest, so the run records each change and a
    replay makes it at the same point. A container that starts again
    begins with an empty table.

    Example:
        >>> run.net.arp.set("api", "db", "02:42:ac:12:00:99")
        >>> run.net.arp.flush("api")
        >>> [n.peer for n in run.net.arp.show("api")]
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def _change(
        self,
        name: str,
        verb: str,
        within: str,
        timeout: Optional[float],
        **words: Any,
    ) -> builtins.list[results.Neighbor]:
        with self._run._client._connection() as client, _translated():
            table = _net.arp_change(
                client,
                self._run.id,
                name,
                verb,
                within_ns=_within_ns(within),
                timeout_s=timeout,
                **words,
            )
        return [results.neighbor(one) for one in table]

    def show(
        self, name: str, *, within: str = "30m", timeout: Optional[float] = None
    ) -> builtins.list[results.Neighbor]:
        """List the neighbour table of container `name`, as `machine net
        arp show` does.

        Returns:
            list[Neighbor]: Its entries.
        """
        with self._run._client._connection() as client, _translated():
            table = _net.neighbors(
                client,
                self._run.id,
                name,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return [results.neighbor(one) for one in table]

    def set(
        self,
        name: str,
        peer: str,
        mac: str,
        *,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Neighbor]:
        """Map `peer`'s address to `mac` in container `name`'s table, for
        good: ARP never corrects it. As `machine net arp set` does.

        Returns:
            list[Neighbor]: The table as it is.

        Raises:
            InvalidArgument: `mac` is not a MAC address.
        """
        return self._change(name, "set", within, timeout, peer=peer, mac=mac)

    def stale(
        self,
        name: str,
        peer: str,
        *,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Neighbor]:
        """Mark `peer`'s entry in container `name`'s table stale: the kernel
        confirms it by ARP at the next packet. As `machine net arp stale`
        does.

        Returns:
            list[Neighbor]: The table as it is.

        Raises:
            InvalidArgument: The table has no entry for `peer`.
        """
        return self._change(name, "stale", within, timeout, peer=peer)

    def delete(
        self,
        name: str,
        peer: str,
        *,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Neighbor]:
        """Delete `peer`'s entry from container `name`'s table, as `machine
        net arp delete` does.

        Returns:
            list[Neighbor]: The table as it is.
        """
        return self._change(name, "delete", within, timeout, peer=peer)

    def flush(
        self, name: str, *, within: str = "30m", timeout: Optional[float] = None
    ) -> builtins.list[results.Neighbor]:
        """Delete every entry of container `name`'s table, as `machine net
        arp flush` does.

        Returns:
            list[Neighbor]: The table as it is.
        """
        return self._change(name, "flush", within, timeout)

    def reachable(
        self,
        name: str,
        reachable: str,
        *,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Neighbor]:
        """Set how long container `name` keeps an entry reachable after ARP
        confirms it, such as `1s`, as `machine net arp reachable` does.

        Returns:
            list[Neighbor]: The table as it is.
        """
        return self._change(
            name,
            "reachable",
            within,
            timeout,
            reachable_ns=_duration_ns("reachable", reachable),
        )


class Net:
    """The links between a run's containers, as `Run.net`; not created
    directly.

    A link connects a container to a peer, and a fault acts on the packets
    the container sends. Call it to list each container's links, as
    `machine net` does. The other methods put a fault on every packet a
    container sends, or on the packets a match takes: those `to` a peer,
    of a `proto`, of a `port`, the TCP SYNs that open a connection with
    `syn`. The other links stay as they are. A fault draws from a seed, so
    a replay acts on the same packets. The run records each change and a
    replay makes it at the same point; a checkpoint keeps a link's faults,
    and a container started again gets its faults back.

    Example:
        >>> run.net.delay("db", "100ms", jitter="20ms")
        >>> run.net.loss("api", "100%", to="db", port=5432, for_="5s")
        >>> run.net.hold("db", for_="5s")
        >>> run.net.reset("api", to="db")
        >>> run.run(within="30s")
        >>> run.net.heal()
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    @property
    def arp(self) -> NetArp:
        """The neighbour tables of the run's containers."""
        return NetArp(self._run)

    def __call__(
        self, *, within: str = "30m", timeout: Optional[float] = None
    ) -> builtins.list[results.Link]:
        """List each running container's whole link, then each match with a
        fault of its own, as `machine net` does.

        Args:
            within (str, optional): The machine time the listing gets to
                end in, a duration such as `10s`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait; None
                waits without a limit.

        Returns:
            list[Link]: The links.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The run's disk runs no containers.
            Timeout: The listing did not end within `within` or `timeout`.
        """
        with self._run._client._connection() as client, _translated():
            found = _net.links(
                client, self._run.id, within_ns=_within_ns(within), timeout_s=timeout
            )
        return [results.link(one) for one in found]

    @staticmethod
    def _match(
        to: Optional[str], proto: Optional[str], port: Optional[int], syn: bool
    ) -> Any:
        with _translated():
            return _net.match_of(to, proto, port, syn)

    def _change(
        self,
        name: str,
        fault: Any,
        match: Any,
        seed: Optional[int],
        for_: Optional[str],
        within: str,
        timeout: Optional[float],
    ) -> results.LinkChange:
        with self._run._client._connection() as client, _translated():
            changed = _net.change(
                client,
                self._run.id,
                name,
                fault,
                match=match,
                seed=seed,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.link_change(changed)

    def delay(
        self,
        name: str,
        delay: str,
        *,
        jitter: Optional[str] = None,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Delay the packets a container sends, as `machine net delay` does.

        Each packet waits `delay`, give or take `jitter`. A delay of `0`
        takes the delay away. The link keeps its other faults.

        Args:
            name (str): The container, by its id, its name or its service's.
            delay (str): The delay, such as `100ms`, or `0`.
            jitter (str, optional): How much each delay differs by, at most,
                such as `20ms`; the link's jitter when omitted.
            to (str, optional): The packets to this container alone.
            proto (str, optional): The packets of `tcp`, `udp` or `icmp`
                alone, or the ARP frames with `arp`.
            port (int, optional): The packets with this source or
                destination port alone.
            syn (bool, optional): The TCP SYNs that open a connection alone.
            seed (int, optional): The seed of the draws; the link's seed,
                or one drawn, when omitted.
            for_ (str, optional): Machine time after which what the change
                set is set back, such as `30s`.
            log (bool, optional): Write each decision of the fault to the
                container's file under `/output/drop/.machine/net`, which the run
                drops unless it keeps it; the link's setting when omitted.
            within (str, optional): Machine time the change gets to end in.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            LinkChange: The link as it was and as it is.

        Raises:
            InvalidArgument: A duration, a protocol or a port is not one,
                the jitter is more than the delay or more than 2.147483647s,
                or the container has 15 matches already.
            NotFound: No running container by `name` or `to`.
        """
        delay_ns = 0 if delay == "0" else _duration_ns("delay", delay)
        jitter_ns = 0 if jitter == "0" else _duration_ns("jitter", jitter)
        if not delay_ns and jitter is None:
            jitter_ns = 0
        fault = _net.Fault(delay_ns=delay_ns, jitter_ns=jitter_ns, log=log)
        match = self._match(to, proto, port, syn)
        return self._change(name, fault, match, seed, for_, within, timeout)

    def _share(
        self,
        field: str,
        name: str,
        share: str,
        match: Any,
        seed: Optional[int],
        for_: Optional[str],
        within: str,
        timeout: Optional[float],
        log: Optional[bool] = None,
    ) -> results.LinkChange:
        with _translated():
            ppb = _net.share_of(share)
        fault = dataclasses.replace(_net.share_fault(field, ppb), log=log)
        return self._change(name, fault, match, seed, for_, within, timeout)

    def loss(
        self,
        name: str,
        share: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Lose a share of the packets a container sends, such as `10%`, as
        `machine net loss` does; `0%` takes the loss away. The arguments are
        `delay`'s.

        Returns:
            LinkChange: The link as it was and as it is.
        """
        match = self._match(to, proto, port, syn)
        return self._share("loss", name, share, match, seed, for_, within, timeout, log)

    def duplicate(
        self,
        name: str,
        share: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Send a share of a container's packets twice, such as `5%`, as
        `machine net duplicate` does. The arguments are `delay`'s.

        Returns:
            LinkChange: The link as it was and as it is.
        """
        match = self._match(to, proto, port, syn)
        return self._share(
            "duplicate", name, share, match, seed, for_, within, timeout, log
        )

    def corrupt(
        self,
        name: str,
        share: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Change a bit of a share of the packets a container sends, such
        as `1%`, as `machine net corrupt` does. The arguments are `delay`'s.

        Returns:
            LinkChange: The link as it was and as it is.
        """
        match = self._match(to, proto, port, syn)
        return self._share(
            "corrupt", name, share, match, seed, for_, within, timeout, log
        )

    def reorder(
        self,
        name: str,
        share: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Send a share of a container's packets ahead of the delayed ones,
        such as `25%`, as `machine net reorder` does. The link needs a
        delay. The arguments are `delay`'s.

        Returns:
            LinkChange: The link as it was and as it is.

        Raises:
            InvalidArgument: The link has no delay.
        """
        match = self._match(to, proto, port, syn)
        return self._share(
            "reorder", name, share, match, seed, for_, within, timeout, log
        )

    def rate(
        self,
        name: str,
        rate: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        for_: Optional[str] = None,
        log: Optional[bool] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange:
        """Limit how fast a container sends, such as `1mbit` or `500kbit`,
        as `machine net rate` does; `0` takes the limit away. A rate is a
        multiple of `8bit`. The arguments are `delay`'s.

        Returns:
            LinkChange: The link as it was and as it is.
        """
        with _translated():
            bps = _net.rate_of(rate)
        fault = _net.Fault(rate_bps=bps, log=log)
        match = self._match(to, proto, port, syn)
        return self._change(name, fault, match, None, for_, within, timeout)

    @overload
    def hold(
        self,
        name: str,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.LinkChange: ...

    @overload
    def hold(self, name: None = None) -> None: ...

    def hold(
        self,
        name: Optional[str] = None,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> Optional[results.LinkChange]:
        """Hold the packets a container sends until a release, as `machine
        net hold` does: they then leave together, in the order they were
        sent. The link holds 1000 full-size packets and drops the ones past
        them. `for_` releases them after that machine time; the other
        arguments are `delay`'s.

        With no `name`, every link is held at the instruction the machine is
        stopped at, and all networking between the containers stops there;
        a replay holds at the same point. `release()` with no name lets
        every packet go. `for_` needs a `name`.

        Returns:
            LinkChange: The link as it was and as it is; None with no
            `name`.

        Raises:
            InvalidArgument: A match or `for_` is given without `name`.
        """
        match = self._match(to, proto, port, syn)
        if name is None and match != _net.Match():
            raise InvalidArgument("A match names packets one container sends.")
        if name is None and for_ is not None:
            raise InvalidArgument("The hold of every link ends with a release.")
        with self._run._client._connection() as client, _translated():
            if name is None:
                _net.hold_all(client, self._run.id)
                return None
            changed = _net.hold(
                client,
                self._run.id,
                name,
                match=match,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.link_change(changed)

    def release(
        self,
        name: Optional[str] = None,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Link]:
        """Release the packets a container's links hold, those a match
        takes or every one, or every container's with no `name`, as
        `machine net release` does. The links keep their other faults.

        Returns:
            list[Link]: The links released, as they were.

        Raises:
            InvalidArgument: A match is given without `name`.
        """
        match = self._match(to, proto, port, syn)
        with self._run._client._connection() as client, _translated():
            if name is None:
                _net.hold_off(client, self._run.id)
            released = _net.release(
                client,
                self._run.id,
                name,
                match=match,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return [results.link(one) for one in released]

    def reset(
        self,
        name: str,
        *,
        to: Optional[str] = None,
        port: Optional[int] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> int:
        """Reset a container's TCP connections, those to `to` and of
        `port` or every one, as `machine net reset` does: the container's
        side of each is closed, and the peer receives a reset. A listening
        socket stays open.

        Args:
            name (str): The container, by its id, its name or its service's.
            to (str, optional): The connections to this container alone.
            port (int, optional): The connections with this local or
                remote port alone.
            within (str, optional): Machine time the reset gets to end in.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            int: How many connections were reset.
        """
        match = self._match(to, None, port, False)
        with self._run._client._connection() as client, _translated():
            return _net.reset(
                client,
                self._run.id,
                name,
                match=match,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )

    def partition(
        self,
        name: str,
        other: str,
        *,
        one_way: bool = False,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.LinkChange]:
        """Cut the link between two containers, as `machine net partition`
        does: everything each sends the other is lost, or the packets of
        `proto`, `port` or `syn` alone.

        Args:
            name (str): One container of the two.
            other (str): The other.
            one_way (bool, optional): Cut what `name` sends `other` alone.
            for_ (str, optional): Machine time after which both links are
                set back together, such as `10s`.
            within (str, optional): Machine time the change gets to end in.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            list[LinkChange]: Each link cut, as it was and as it is.
        """
        match = self._match(None, proto, port, syn)
        with self._run._client._connection() as client, _translated():
            cut = _net.partition(
                client,
                self._run.id,
                name,
                other,
                one_way=one_way,
                match=match,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return [results.link_change(one) for one in cut]

    def heal(
        self,
        name: Optional[str] = None,
        *,
        to: Optional[str] = None,
        proto: Optional[str] = None,
        port: Optional[int] = None,
        syn: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> builtins.list[results.Link]:
        """Take the faults off a container's links, every one or those a
        match takes, or off every container's links with no `name`, as
        `machine net heal` does. A packet the link delays already still
        leaves at its time.

        Returns:
            list[Link]: The links healed, as they were.

        Raises:
            InvalidArgument: A match is given without `name`.
        """
        match = self._match(to, proto, port, syn)
        with self._run._client._connection() as client, _translated():
            if name is None:
                _net.hold_off(client, self._run.id)
            healed = _net.heal(
                client,
                self._run.id,
                name,
                match=match,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return [results.link(one) for one in healed]


class Containers:
    """The containers of a run's guest, as `Run.containers`; not created
    directly.

    Call it to list them, as `machine containers` does. A container is
    named by its own name, or by its Compose service's when the service has
    one container: the names `exec`, `put` and `get` take as `container`.

    The other methods put a fault on one container while the others run, as
    a node of a cluster stops, dies, freezes or starves. Each acts by
    commands the guest runs, so the run records it and a replay acts at the
    same point. What ends a fault later, a start after `restart` or a thaw
    after `for_`, is a process that waits that machine time in the guest.

    Example:
        >>> run.containers.kill("db", restart="5s")
        >>> run.run(within="10s")
        >>> [c.state for c in run.containers() if c.service == "db"]
        ['running']
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(
        self, *, within: str = "30m", timeout: Optional[float] = None
    ) -> builtins.list[results.Container]:
        """List the containers of the run and the Compose service of each,
        as `machine containers` does.

        Stopped containers are listed too. The machine runs until the list
        is read, and the list is of the run as it is then.

        Example:
            >>> [(c.service, c.name, c.state) for c in run.containers()]
            [('app', 'compose-app-1', 'running'), ('db', 'compose-db-1', 'running')]

        Args:
            within (str, optional): The machine time the listing gets to
                end in, a duration such as `10s`. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait; None
                waits without a limit.

        Returns:
            list[Container]: The containers, those of a service together.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The run's disk runs no containers, or they could not
                be listed.
            Timeout: The listing did not end within `within` or `timeout`.
        """
        bound = _within_ns(within)
        with self._run._client._connection() as client, _translated():
            found = _containers.containers(
                client, self._run.id, within_ns=bound, timeout_s=timeout
            )
            found = _containers.with_running(
                client, self._run.id, found, within_ns=bound, timeout_s=timeout
            )
        return [results.container(one) for one in found]

    def stop(
        self,
        name: str,
        *,
        grace: str = "10s",
        restart: str = "never",
        fresh: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Stop a container, as `machine containers stop` does: SIGTERM to
        its processes, and SIGKILL after `grace`.

        Args:
            name (str): The container, by its id, its name or its service's.
            grace (str, optional): Machine time between SIGTERM and
                SIGKILL, whole seconds. Defaults to `10s`.
            restart (str, optional): Start it again after this machine
                time, `0` at once, or `never`. Defaults to `never`.
            fresh (bool, optional): Start it again from its image, its
                writable layer discarded, as Compose recreates a service.
                Defaults to False.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            ContainerFault: What was done.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running or paused, an argument is
                not a duration, or `fresh` without a restart, of a
                container no service started, or on a disk that does not
                take it.
        """
        grace_ns = _duration_ns("grace", grace) or 0
        with self._run._client._connection() as client, _translated():
            acted = _containers.stop(
                client,
                self._run.id,
                name,
                grace_s=-(-grace_ns // _NS_PER_S),
                restart_ns=_containers.restart_of(restart),
                fresh=fresh,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def kill(
        self,
        name: str,
        *,
        restart: str = "never",
        fresh: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Kill a container, as `machine containers kill` does: SIGKILL to
        every process in it, no cleanup.

        Args:
            name (str): The container, by its id, its name or its service's.
            restart (str, optional): Start it again after this machine
                time, `0` at once, or `never`. Defaults to `never`.
            fresh (bool, optional): Start it again from its image, its
                writable layer discarded, as Compose recreates a service.
                Defaults to False.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            ContainerFault: What was done.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running or paused, `restart` is not
                a duration, or `fresh` without a restart, of a container no
                service started, or on a disk that does not take it.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.kill(
                client,
                self._run.id,
                name,
                restart_ns=_containers.restart_of(restart),
                fresh=fresh,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def crash(
        self,
        name: str,
        path: str = "",
        *,
        restart: str = "never",
        fresh: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Cut a container as a power cut cuts a node, as `machine
        containers crash` does: its volumes lose the writes they had not
        synced, and it dies, while the others run on.

        A crash cuts the volumes the project's `x-machine` names, each a
        filesystem of its own. A volume keeps what was synced and loses the
        writes still in memory, and replays its journal when the container
        starts again. The cut is when the volumes shut down, after the
        container is frozen: a write the kernel finishes between the two is
        kept.

        Example:
            >>> run.containers.crash("db", restart="0").volumes
            ('compose_data',)

        Args:
            name (str): The container, by its id, its name or its service's.
            path (str, optional): A volume's path in the container, to cut
                it alone. Empty for every volume a crash cuts.
            restart (str, optional): Start it again after this machine
                time, `0` at once, or `never`. Defaults to `never`.
            fresh (bool, optional): Start it again from its image, its
                writable layer discarded. Defaults to False.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Returns:
            ContainerFault: What was done, with the volumes cut.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running or paused, it has no volume
                a crash cuts, `path` is none of them, a volume it would cut
                is mounted by another running container too, or `restart`
                or `fresh` is refused as for `kill`.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.crash(
                client,
                self._run.id,
                name,
                path=path,
                restart_ns=_containers.restart_of(restart),
                fresh=fresh,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def start(
        self,
        name: str,
        *,
        fresh: bool = False,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Start a container that is down, as `machine containers start`
        does; from its image, its writable layer discarded, with `fresh`.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is running or paused, or `fresh` of a
                container no service started or on a disk that does not
                take it.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.start(
                client,
                self._run.id,
                name,
                fresh=fresh,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def pause(
        self,
        name: str,
        *,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Freeze a container, as `machine containers pause` does.

        Args:
            name (str): The container, by its id, its name or its service's.
            for_ (str, optional): Thaw it after this machine time. None
                leaves it frozen until `resume`.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, or `for_` is not a duration.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.pause(
                client,
                self._run.id,
                name,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def resume(
        self, name: str, *, within: str = "30m", timeout: Optional[float] = None
    ) -> results.ContainerFault:
        """Thaw a frozen container, as `machine containers resume` does.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not paused.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.resume(
                client,
                self._run.id,
                name,
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def throttle(
        self,
        name: str,
        *,
        cpus: float,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Limit the CPU of a container, as `machine containers throttle`
        does. The limit holds when the container starts again, until it is lifted or the container is recreated.

        Args:
            name (str): The container, by its id, its name or its service's.
            cpus (float): The CPUs it may use, such as 0.1; 0 for the
                container's own limit.
            for_ (str, optional): Set back the limit it had after this
                machine time.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, `cpus` is below 0, or
                `for_` is not a duration.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.throttle(
                client,
                self._run.id,
                name,
                cpus=cpus,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def memory(
        self,
        name: str,
        size: str,
        *,
        soft: bool = False,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Limit the memory of a container, as `machine containers memory`
        does. A limit below what its processes use makes the guest kernel
        reclaim, and its out-of-memory killer kill a process of the
        container when it cannot. With `soft`, past the size the kernel
        reclaims and slows its processes, and kills none. The limit holds when the container starts again, until it is lifted or the container is recreated.

        Example:
            >>> run.containers.memory("db", "64m", for_="30s")

        Args:
            name (str): The container, by its id, its name or its service's.
            size (str): The memory it may use, such as `256m` or `1g`; `0`
                for the container's own limit.
            for_ (str, optional): Set back the limit it had after this
                machine time.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, `size` is not a size, or
                `for_` is not a duration.
        """
        with _translated():
            mb = 0 if size.strip() == "0" else _parsers.size_mb_type(size)
        with self._run._client._connection() as client, _translated():
            acted = _containers.memory(
                client,
                self._run.id,
                name,
                mb=mb,
                soft=soft,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def pids(
        self,
        name: str,
        count: int,
        *,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Limit the processes and threads of a container, as `machine
        containers pids` does. Past the limit, a fork or a new thread in the
        container fails with EAGAIN. The limit holds when the container starts again, until it is lifted or the container is recreated.

        Example:
            >>> run.containers.pids("db", 50, for_="30s")

        Args:
            name (str): The container, by its id, its name or its service's.
            count (int): The processes and threads it may have; 0 for the
                container's own limit.
            for_ (str, optional): Set back the limit it had after this
                machine time.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, `count` is below 0, or
                `for_` is not a duration.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.pids(
                client,
                self._run.id,
                name,
                count=count,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def ports(
        self,
        name: str,
        low: int,
        high: int,
        *,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Narrow the ephemeral ports of a container's network to `low`
        through `high`, as `machine containers ports` does. Once every port
        is taken for one destination, a connect to it fails with
        EADDRNOTAVAIL. The range ends at the
        container's next start.

        Example:
            >>> run.containers.ports("api", 40000, 40009, for_="2m")

        Args:
            name (str): The container, by its id, its name or its service's.
            low (int): The first port.
            high (int): The last port. Use 32768 and 60999, the kernel's
                range, to set back the container's own range.
            for_ (str, optional): Set back the range it had after this
                machine time.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, the range is not one, or
                it is on the machine's network or another container's.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.ports(
                client,
                self._run.id,
                name,
                low=low,
                high=high,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)

    def files(
        self,
        name: str,
        count: int,
        *,
        for_: Optional[str] = None,
        within: str = "30m",
        timeout: Optional[float] = None,
    ) -> results.ContainerFault:
        """Limit the files each process of a container may hold open, as
        `machine containers files` does. Past the limit, an open, a socket
        or an accept fails with EMFILE. The processes running now take the
        limit, and what they start; a process docker exec starts takes the
        container's own. The limit holds when the container starts again, until it is lifted or the container is recreated.

        Example:
            >>> run.containers.files("api", 64)

        Args:
            name (str): The container, by its id, its name or its service's.
            count (int): The files each process may hold open; 0 for the
                container's own limit.
            for_ (str, optional): Set back the limit it had after this
                machine time.
            within (str, optional): The machine time the command gets to
                end in. Defaults to `30m`.
            timeout (float, optional): Seconds of this host to wait.

        Raises:
            NotFound: No such container.
            InvalidArgument: It is not running, `count` is below 0, or the
                disk was built before file limits.
        """
        with self._run._client._connection() as client, _translated():
            acted = _containers.files(
                client,
                self._run.id,
                name,
                count=count,
                for_ns=_duration_ns("for_", for_),
                within_ns=_within_ns(within),
                timeout_s=timeout,
            )
        return results.container_fault(acted)


class Tasks:
    """The guest's tasks, as `Run.tasks`; not created directly.

    Call it to list them, as `machine tasks` does. A task is a guest thread,
    named by its tid; its process is its tgid. The table is read from the
    running guest kernel, so it needs a guest that is up:
    `run.run(until="tasks")`.

    The steering methods mark a task, and the machine's scheduler acts on
    the mark at each scheduling decision for the tasks focus steers: `hold`
    keeps one off the CPU, `yield_` makes it give way, `release` clears
    both, `slice` sets how long it runs, and `pin` where. Marks are how a
    program forces an interleaving it wants to test.

    Example:
        >>> run.focus.add("/app")
        >>> writer = next(t for t in run.tasks(focused=True) if t.comm == "writer")
        >>> run.tasks.hold(writer.tid)
        >>> run.run(until="/app:exit")
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(
        self, *, vcpu: Optional[int] = None, focused: bool = False
    ) -> builtins.list[results.TaskInfo]:
        """List the guest's tasks, as `machine tasks` does.

        Example:
            >>> [t.comm for t in run.tasks(focused=True)]
            ['test', 'writer']

        Args:
            vcpu (int, optional): Keep those on this vCPU.
            focused (bool, optional): Keep those focus steers. Defaults to
                False.

        Returns:
            list[Task]: One record per task.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            table = client.tasks(self._run.id)
        return [
            results.task(row) for row in _tasks.as_json(table, vcpu, focused)["tasks"]
        ]

    def cgroups(self, *, vcpu: Optional[int] = None) -> builtins.list[results.Cgroup]:
        """List the cgroups the guest's tasks are in, as `machine tasks
        cgroups` does; `cgroup=P` focuses one.

        Args:
            vcpu (int, optional): Keep the tasks on this vCPU.

        Returns:
            list[Cgroup]: One record per cgroup, with its threads and their
            programs.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            table = client.tasks(self._run.id)
        return [
            results.cgroup(row)
            for row in _tasks.cgroups_as_json(table, vcpu)["cgroups"]
        ]

    def exist(self, who: str) -> builtins.list[results.TaskInfo]:
        """Assert that the machine runs what `who` names, as `machine tasks
        exist` does, and return those tasks. It reads the task table and
        changes nothing.

        Example:
            >>> run.tasks.exist("/usr/bin/app")
            >>> run.inject.add("syscall", "openat", "ENOENT", in_="/usr/bin/app")

        Args:
            who (str): In `Faults.add`'s `in_` words: a program by the
                absolute path of the file it runs, `"comm=NAME"`,
                `"tid=N"` or `"tgid=N"`.

        Returns:
            list[Task]: The tasks it names, at least one.

        Raises:
            NotFound: No task of the machine is what `who` names now.
            InvalidArgument: `who` names none of the four.
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            table = _tasks.exist(
                client, self._run.id, who, f"machine tasks {self._run.name}"
            )
        return [results.task(row) for row in _tasks.as_json(table)["tasks"]]

    def exits(self) -> builtins.list[results.Exit]:
        """List the guest tasks that have exited, oldest first, as `machine
        tasks exits` does, with each one's exit code or signal. Use it to
        tell how a test program ended.

        Example:
            >>> [e.outcome for e in run.tasks.exits()]
            ['status 0', 'killed by SIGSEGV']
            >>> [e.status for e in run.tasks.exits() if e.tgid == 412]
            [1]

        Returns:
            list[Exit]: One record per exited task, oldest first.

        Raises:
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            history = client.task_exits(self._run.id)
        return [results.exit_row(row) for row in _tasks.exits_as_json(history)["exits"]]

    def hold(self, tid: int) -> None:
        """Take a thread off the runqueue until it is released, as `machine
        tasks hold` does, so the others run past it.

        Args:
            tid (int): The thread.

        Raises:
            InvalidArgument: No focused task has this tid.
            NotRunning: The run has no machine.
        """
        self._mark(tid, "hold", 0)

    def signal(self, tid: int, signal: str | int) -> None:
        """Send a task a signal, as `machine tasks signal` does: the guest
        sends it at its next entry from user mode, and a replay sends it at
        the same instruction.

        Args:
            tid (int): The task.
            signal (str | int): The signal, SIGTERM, TERM or 15.

        Raises:
            InvalidArgument: Not a signal.
            NotRunning: The run has no machine.
        """
        number = _signals.number_of(str(signal))
        if number is None:
            raise InvalidArgument(f"Not a signal: {signal}")
        self._mark(tid, "signal", number)

    def yield_(self, tid: int) -> None:
        """Put a thread behind its peers at each scheduling decision, as
        `machine tasks yield` does. The trailing underscore avoids the
        Python keyword.

        Args:
            tid (int): The thread.

        Raises:
            InvalidArgument: No focused task has this tid.
            NotRunning: The run has no machine.
        """
        self._mark(tid, "yield", 0)

    def release(self, tid: int) -> None:
        """Undo a thread's hold or yield, as `machine tasks release` does.

        Args:
            tid (int): The thread.

        Raises:
            InvalidArgument: No focused task has this tid.
            NotRunning: The run has no machine.
        """
        self._mark(tid, "release", 0)

    def slice(self, tid: int, duration: str) -> None:
        """Set how long a thread runs between scheduling decisions, as
        `machine tasks slice` does. A short slice switches tasks often.

        Args:
            tid (int): The thread.
            duration (str): The slice, such as `2ms` or `500us`; `default`
                restores the kernel's.

        Raises:
            InvalidArgument: The duration is not valid, or no focused task
                has this tid.
            NotRunning: The run has no machine.
        """
        with _translated():
            units = _parsers.slice_units_type(duration)
        self._mark(tid, "slice", units)

    def pin(self, tid: int, vcpus: str) -> None:
        """Place a thread on one or more vCPUs when it next wakes, as
        `machine tasks pin` does, so two tasks share one vCPU or never do.

        Args:
            tid (int): The thread.
            vcpus (str): A set such as `0`, `0-2` or `0,3`; `free` lets the
                guest choose again, and `none` keeps it off every vCPU.

        Raises:
            InvalidArgument: The set is not valid, or no focused task has
                this tid.
            NotRunning: The run has no machine.
        """
        with _translated():
            mask = _parsers.pin_type(vcpus)
        self._mark(tid, "pin", mask)

    def _mark(self, tid: int, verb: str, value: int) -> None:
        checked = _args.tid(tid)
        with self._run._client._connection() as client, _translated():
            client.task_mark(self._run.id, tid=checked, verb=verb, value=value)


class Task:
    """A task of the guest, as `Run.task` gives it; not created directly.

    The reads a debugger makes of a task: its registers, the memory of
    its tgid, its stack, and the symbols and files of its tgid. A
    task is read on a vCPU or off one, the same way, with the machine
    stopped. Every read is of the same task: one that exited is
    `NotFound`, though another task has its id now.

    The symbols come from the program files on the machine's disk.

    Example:
        >>> task = run.task(412)
        >>> hex(task.registers()["rip"])
        '0x401ac6'
        >>> task.read(task.address("counter"), 4)
        b'*\\x00\\x00\\x00'
        >>> [frame.function for frame in task.backtrace()]
        ['worker', 'start_thread', 'clone3']
    """

    def __init__(self, run: Run, tid: int, start: int) -> None:
        self._run = run
        self._tid = tid
        self._start = start

    @property
    def tid(self) -> int:
        """int: The task's id."""
        return self._tid

    @property
    def start(self) -> int:
        """int: When the task started, in nanoseconds since the guest
        booted, by the guest's own clock."""
        return self._start

    def registers(self) -> results.Registers:
        """Read the task's registers, as `machine regs --all` does.

        Returns:
            Registers: Every register by its name, and where they were read.

        Raises:
            NotFound: The task exited.
            InvalidArgument: The task is a kernel task.
            Conflict: The machine is running.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            found = _debugger.registers(client, self._run.id, self._tid, self._start)
        return results.registers(found)

    def address(self, location: str) -> int:
        """The address a location names in the task's tgid, as `machine
        mem` takes one: a number, `$rsp`, `main` or `PATH:main`, moved by
        `+N` or `-N`, or a source line `FILE:LINE` of a program built with
        debug information, at its first code.

        Args:
            location (str): The location.

        Returns:
            int: The address.

        Raises:
            NotFound: No symbol has the name.
            InvalidArgument: More than one file has the name: name the
                file. Or the source line has no code.
        """
        with self._run._client._connection() as client, _translated():
            return _debugger.location(
                client, self._run.id, self._tid, location, start=self._start
            )

    def read(self, address: int, size: int) -> bytes:
        """Read `size` bytes at `address` of the task's tgid, as
        `machine mem` does.

        Args:
            address (int): Where to read, an address of the tgid.
            size (int): How many bytes.

        Returns:
            bytes: The bytes, fewer when the read reached an address that
            is not mapped.

        Raises:
            InvalidArgument: Not one byte at `address` is mapped, or it is
                guest kernel memory.
        """
        if size < 1:
            raise InvalidArgument(f"size must be 1 or more, got {size}.")
        with self._run._client._connection() as client, _translated():
            found = _debugger.read(
                client, self._run.id, self._tid, address, size, self._start
            )
        return found.data

    def set(self, **registers: Union[int, str]) -> results.Registers:
        """Write registers of the task, as `machine regs NAME=VALUE` does,
        and read them back. A write is part of the run: a replay makes it
        again at the same point.

        Example:
            >>> task.set(rax=0, rip="main+4")["rip"] == task.address("main+4")
            True

        Args:
            **registers: Each register by its name, and its value, an int
                or a str: a number, a negative one as C stores it, or a
                location as `address` takes one.

        Returns:
            Registers: Every register by its name, as they now are.

        Raises:
            InvalidArgument: A register that is not written, such as cs or
                xmm0, a value no register holds, or a task entering or
                leaving the kernel.
            Conflict: The machine is running, or records nothing.
        """
        if not registers:
            raise InvalidArgument("Register required.")
        values = {name: str(value) for name, value in registers.items()}
        with self._run._client._connection() as client, _translated():
            found = _debugger.set_registers(
                client, self._run.id, self._tid, values, self._start
            )
        return results.registers(found)

    def write(self, address: int, data: bytes) -> None:
        """Write `data` at `address` of the task's tgid, as `machine
        mem LOC=VALUE` does. A write is part of the run: a replay makes it
        again at the same point.

        Args:
            address (int): Where to write, an address of the tgid.
            data (bytes): What to write, 1 byte to 1 MiB.

        Raises:
            InvalidArgument: A page the tgid cannot write: read-only, or
                shared until the tgid writes it, or `data` is empty or
                longer than 1 MiB. Nothing is written then.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            _debugger.write(client, self._run.id, self._tid, address, data, self._start)

    def flip(self, address: int, mask: int, size: int = 1) -> tuple[bytes, bytes]:
        """Flip the bits `mask` sets in the `size` bytes at `address` of the
        task's tgid, as `machine mem LOC^=MASK` does. The flip is a write
        and is part of the run.

        Example:
            >>> task.flip(task.address("counter"), 0x10, 4)
            (b'*\x00\x00\x00', b':\x00\x00\x00')

        Args:
            address (int): Where the value is, an address of the tgid.
            mask (int): The bits to flip, within `size` bytes.
            size (int, optional): The value's width, 1, 2, 4 or 8 bytes.
                Defaults to 1.

        Returns:
            tuple[bytes, bytes]: The value's bytes before and after.

        Raises:
            InvalidArgument: A width other than 1, 2, 4 or 8, a mask of 0
                or past the width, or a page the tgid cannot write.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            _addr, before, after = _debugger.flip_value(
                client,
                self._run.id,
                self._tid,
                str(address),
                hex(mask),
                size,
                self._start,
            )
        return before, after

    def fill(self, address: int, pattern: bytes, length: int) -> None:
        """Write `pattern` again and again over `length` bytes at `address`
        of the task's tgid, the last copy cut to fit, as `machine mem LOC
        --fill` does. The writes are part of the run.

        Args:
            address (int): The first byte to write, an address of the
                task's tgid.
            pattern (bytes): The bytes written again and again; not empty.
            length (int): How many bytes to write, 1 or more.

        Example:
            >>> task.fill(task.address("buf"), b"\xde", 4096)

        Raises:
            InvalidArgument: An empty pattern, a length below 1, or a page
                the tgid cannot write.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            _debugger.fill(
                client, self._run.id, self._tid, address, pattern, length, self._start
            )

    def symbol(self, address: int) -> Optional[results.Symbol]:
        """The symbol `address` is in, as `machine symbols <address>` finds.

        Args:
            address (int): An address of the task's tgid.

        Returns:
            Symbol, optional: The symbol, with how far into it the address
            is; None when no file of the tgid names one there.
        """
        with self._run._client._connection() as client, _translated():
            (found,) = _debugger.symbols_at(
                client, self._run.id, self._tid, [address], self._start
            )
        return results.symbol(found) if found else None

    def symbols(
        self,
        name: Optional[str] = None,
        *,
        regex: Optional[str] = None,
        variables: bool = False,
        file: Optional[str] = None,
    ) -> builtins.list[results.Symbol]:
        """Find symbols of the task's tgid, as `machine symbols` does:
        each symbol of a name, or each function, or variable, whose name a
        regular expression matches.

        Args:
            name (str, optional): A name, `NAME` or `PATH:NAME`.
            regex (str, optional): A regular expression in place of a name.
            variables (bool, optional): Find variables by `regex`, not
                functions. Defaults to False.
            file (str, optional): The file to look in by `regex`, by its
                path or its file name. Defaults to every file.

        Returns:
            list[Symbol]: Each symbol found.

        Raises:
            InvalidArgument: Neither or both of `name` and `regex`,
                `variables` or `file` without `regex`, a regular expression
                that is not one, or more matches than one answer holds.
        """
        if (name is None) == (regex is None):
            raise InvalidArgument("Name or regex required, not both.")
        if regex is None and (variables or file):
            raise InvalidArgument("variables and file go with regex.")
        with self._run._client._connection() as client, _translated():
            if regex is not None:
                found = _debugger.symbols_matching(
                    client,
                    self._run.id,
                    self._tid,
                    regex,
                    variables,
                    file,
                    self._start,
                )
            else:
                found = _debugger.symbols_named(
                    client, self._run.id, self._tid, name or "", self._start
                )
        return [results.symbol(one) for one in found]

    def backtrace(self) -> builtins.list[results.Frame]:
        """The frames of the task's stack, the innermost first, as
        `machine backtrace` lists them.

        Returns:
            list[Frame]: One record per frame.
        """
        with self._run._client._connection() as client, _translated():
            found = _debugger.backtrace(client, self._run.id, self._tid, self._start)
        return [results.frame(one) for one in found]

    def print(self, expression: str, frame: int = 0) -> results.Value:
        """The value of `expression` in a frame of the task, as
        `machine print` prints it: a variable, a global or a $register,
        with `.m`, `->m`, `[i]`, `*`, `&`, casts, arithmetic and
        comparisons. `TARGET = VALUE` writes VALUE at TARGET and returns
        TARGET's value after it; the run keeps the write.

        Args:
            expression (str): The expression.
            frame (int): The frame, as `backtrace` numbers them; 0 is the
                innermost.

        Returns:
            Value: The expression, its type and its value.

        Raises:
            NotFound: No variable, member or type has the name.
            InvalidArgument: Not an expression, or not a value of its kind.
        """
        with self._run._client._connection() as client, _translated():
            evaluate = (
                _variables.assign
                if _variables.is_assignment(expression)
                else _variables.value
            )
            found = evaluate(
                client, self._run.id, self._tid, expression, frame, self._start
            )
        return results.value(found)

    def locals(
        self, frame: int = 0, args: bool = False
    ) -> builtins.list[results.Variable]:
        """The local variables of a frame of the task, or its arguments, as
        `machine locals` lists them.

        Args:
            frame (int): The frame, as `backtrace` numbers them.
            args (bool): The function's arguments instead of its locals.

        Returns:
            list[Variable]: Each with its type and value.
        """
        with self._run._client._connection() as client, _translated():
            found = _variables.frame(
                client, self._run.id, self._tid, frame, start=self._start
            )
            chosen = found.params if args else found.locals
            shown = [
                _variables.shown(
                    client, self._run.id, self._tid, one, frame, self._start
                )
                for one in chosen
            ]
        return [results.Variable(name, kind, text) for name, kind, text in shown]

    def libraries(self) -> builtins.list[results.Library]:
        """The program and the libraries of the task's tgid, as
        `machine libs` lists them.

        Returns:
            list[Library]: One record per file, the program first.
        """
        with self._run._client._connection() as client, _translated():
            found = _debugger.libraries(client, self._run.id, self._tid, self._start)
        return [results.library(one) for one in found]

    def step(
        self,
        count: int = 1,
        *,
        line: bool = False,
        over: bool = False,
        skip: Sequence[str] = (),
    ) -> results.Stepped:
        """Step the task, as `machine step` does: `count` instructions,
        as stepi, or source lines with `line`, as step. With `over` a call
        and all it calls are one step, as nexti and next take them.

        The machine runs until the task is in its own code, then steps
        it; other tasks run meanwhile. A breakpoint the run reaches ends
        the step.

        Example:
            >>> task.step(line=True, over=True).source
            'init.c:108'

        Args:
            count (int, optional): The steps to make. Defaults to 1.
            line (bool, optional): Step source lines. Defaults to False.
            over (bool, optional): Step over calls. Defaults to False.
            skip (Sequence[str], optional): Functions or source files, by
                name, a line step steps out of, as GDB's skip does.

        Returns:
            Stepped: Where the task is, and the breakpoints that ended
            the step early.

        Raises:
            InvalidArgument: A count below 1, or a line step where the
                program has no line tables.
            Conflict: The machine is running, or records nothing.
        """
        if count < 1:
            raise InvalidArgument(f"count must be 1 or more, got {count}.")
        with self._run._client._connection() as client, _translated():
            if line:
                found = _stepping.lines(
                    client,
                    self._run.id,
                    self._tid,
                    count,
                    over,
                    tuple(skip),
                    start=self._start,
                )
            else:
                found = _stepping.instructions(
                    client, self._run.id, self._tid, count, over, self._start
                )
        return results.stepped(found)

    def finish(self) -> results.Stepped:
        """Run until the task's function returns to the one that called
        it, as `machine step --out` and GDB's finish do. A recursive call
        returns to its own caller, not to a deeper frame's.

        Returns:
            Stepped: Where the task returned to.

        Raises:
            InvalidArgument: The task is in its outermost frame.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            found = _stepping.finish(client, self._run.id, self._tid, self._start)
        return results.stepped(found)

    def back(
        self,
        count: int = 1,
        *,
        line: bool = False,
        over: bool = False,
        out: bool = False,
        timeout: Optional[float] = None,
    ) -> "Run":
        """Go back over the task's code, onto a branch, as `machine step
        --back` does: `count` executions of the task, as reverse-stepi;
        lines with `line`, as reverse-step; over calls with `over` as
        well, as reverse-next; one instruction over calls with `over`
        alone, as reverse-nexti; to the call of its function with `out`,
        as reverse-finish. The run stays as it was.

        The moves over code go back over what the run recorded one
        instruction at a time: a stretch the task was stepped through.
        Back past it, the move stops where the history ends.

        Example:
            >>> task.step(3, line=True)
            >>> earlier = task.back(line=True)
            >>> earlier.task(task.tid).backtrace()[0].source
            'values.c:62'

        Args:
            count (int, optional): The moves to make. Defaults to 1.
            line (bool, optional): Go back source lines. Defaults to False.
            over (bool, optional): Go back over calls. Defaults to False.
            out (bool, optional): Go back to the call of the function.
                Defaults to False.
            timeout (float, optional): Seconds to wait for the branch.

        Returns:
            Run: The branch the machine is on.

        Raises:
            InvalidArgument: A count below 1, `out` with `line`, `over` or a
                count, no history before now, or a line move where the
                program has no line tables.
        """
        if out and (line or over or count != 1):
            raise InvalidArgument("out takes no count, line or over.")
        request = _branching.BranchRequest(
            run_id=self._run.id, run_name=self._run.name, timeout_s=timeout
        )
        with self._run._client._connection() as client, _translated():
            if line or over or out:
                went = _reverse.over_code(
                    client, request, self._tid, count, line=line, over=over, out=out
                )
            else:
                went = _reverse.over_entries(client, request, count, tid=self._tid)
        return Run(self._run._client, went.switched.run_id, went.switched.name)

    def call(self, function: str, *arguments: int | str) -> results.Called:
        """Call `function` in the task, as `machine call` and GDB's call do,
        and return what it returned. Each argument is an integer, or an
        expression as `print` takes one that is an integer or a pointer, up
        to six. The task's registers are put back after the call; what the
        function wrote stays written. The run keeps the call.

        Example:
            >>> task.call("report", "&shapes[1]", 1, "title").returned
            '-5'

        Args:
            function (str): A function by its name, or a location.
            *arguments (int | str): Its arguments.

        Returns:
            Called: The value returned, or the breakpoints that abandoned
            the call.

        Raises:
            InvalidArgument: More than six arguments.
            NotFound: No such function.
        """
        with self._run._client._connection() as client, _translated():
            found = _stepping.call(
                client,
                self._run.id,
                self._tid,
                function,
                tuple(str(one) for one in arguments),
                self._start,
            )
        return results.called(found)

    def return_(self, value: Optional[int | str] = None) -> results.Frame:
        """Return from the task's innermost function without running the
        rest of it, as `machine return` and GDB's return do. The task goes
        on after the call, in its caller, with the caller's stack pointer
        and the registers the function saved. The run keeps the write.

        Args:
            value (int | str, optional): What rax holds: a number, or a
                location as `address` takes one. Defaults to rax as it is.

        Returns:
            Frame: The task's innermost frame now.

        Raises:
            InvalidArgument: The function has no call frame rules.
        """
        with self._run._client._connection() as client, _translated():
            _debugger.pop(
                client,
                self._run.id,
                self._tid,
                None if value is None else str(value),
                self._start,
            )
            (now, *_) = _debugger.backtrace(
                client, self._run.id, self._tid, self._start
            )
        return results.frame(now)

    def breakpoint(
        self,
        location: str,
        *,
        temporary: bool = False,
        condition: str = "",
        print: str = "",
        disabled: bool = False,
        ignore: int = 0,
        hardware: bool = False,
    ) -> results.Breakpoint:
        """Add a breakpoint in the task's tgid, as `machine break add`
        does. It stops the run before the instruction at `location` runs,
        in this tgid alone.

        Args:
            location (str): A location as `address` takes one: `NAME`,
                `FILE:LINE`, `*ADDRESS`, `$register`, moved by `+N`.
            temporary (bool, optional): Delete it at its first hit that
                counts. Defaults to False.
            condition (str, optional): Count a hit only where this holds,
                as `Breakpoints.condition` takes one. Defaults to none.
            print (str, optional): Print this at a hit and go on, as GDB's
                dprintf does: text with operands in braces, such as
                `"n={counter} p={$rdi:x}"`. Defaults to none: a hit ends
                the run.
            disabled (bool, optional): Add it disabled, as the machine does
                not arm it until it is enabled. Defaults to False.
            ignore (int, optional): Pass this many hits that count before
                one ends a run. Defaults to 0.
            hardware (bool, optional): Put it in one of the 3 debug
                registers, as GDB's hbreak does, for code the program
                allocated or reads. Defaults to False: an int3 in the
                tgid's own copy of its code.

        Returns:
            Breakpoint: The breakpoint, with its number.

        Raises:
            InvalidArgument: A location the tgid has no address for, a
                run that holds 64 already, or with `hardware`, 3 already.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.break_add(
                client,
                self._run.id,
                self._tid,
                location,
                _breakpoints.Options(
                    temporary=temporary,
                    condition=condition,
                    print=print,
                    disabled=disabled,
                    ignore=ignore,
                    hardware=hardware,
                ),
                self._start,
            )
        return results.breakpoint(found)

    def watch(
        self,
        location: str,
        *,
        size: Optional[int] = None,
        on: str = "write",
        temporary: bool = False,
        condition: str = "",
        print: str = "",
        disabled: bool = False,
        ignore: int = 0,
    ) -> results.Breakpoint:
        """Add a watchpoint in the task's tgid, as `machine watch add`
        does. It stops the run after an instruction writes the bytes, after
        a write that changes them with `on="change"`, after a read with
        `on="read"`, or after either with `on="access"`.

        Args:
            location (str): A location as `address` takes one. A variable
                is watched at its size.
            size (int, optional): The bytes to watch, 1, 2, 4 or 8, at an
                address that is a multiple of it. Required for a location
                that is not a variable.
            on (str, optional): `write`, `change`, `read` or `access`.
                A `read` hit counts only where the bytes did not change.
                Defaults to `write`.
            temporary (bool, optional): Delete it at its first hit that
                counts. Defaults to False.
            condition (str, optional): Count a hit only where this holds,
                as `Breakpoints.condition` takes one. Defaults to none.
            print (str, optional): Print this at a hit and go on, as GDB's
                dprintf does: text with operands in braces, such as
                `"n={counter} p={$rdi:x}"`. Defaults to none: a hit ends
                the run.
            disabled (bool, optional): Add it disabled, as the machine does
                not arm it until it is enabled. Defaults to False.
            ignore (int, optional): Pass this many hits that count before
                one ends a run. Defaults to 0.

        Returns:
            Breakpoint: The watchpoint, with its number.

        Raises:
            InvalidArgument: No size, a size the hardware does not watch, an
                address that is not a multiple of it, `on` not one of the
                four, or a run that holds 3 already.
            Conflict: The machine is running, or records nothing.
        """
        with self._run._client._connection() as client, _translated():
            found = _breakpoints.watch_add(
                client,
                self._run.id,
                self._tid,
                location,
                size,
                on,
                _breakpoints.Options(
                    temporary=temporary,
                    condition=condition,
                    print=print,
                    disabled=disabled,
                    ignore=ignore,
                ),
                self._start,
            )
        return results.breakpoint(found)


class Focus:
    """Which guest tasks the machine steers, as `Run.focus`; not created
    directly.

    Call it to read the focus, as `machine focus` does. Focus is the set of
    tasks a program cares about: task marks, the task options of `Run.run`
    and task predicates act on the focused tasks, and the machine reports
    their scheduling. An entry is a program's `/path`, a `cgroup=P`, a
    `comm=NAME` that takes each process with a task of that comm, or a
    process `tgid=N`; a `/path` keeps matching new processes of that
    program as they start. Focus a program before the checkpoint its
    branches start from, and they inherit it. Each call returns the focus
    as a JSON document.

    Example:
        >>> run.focus.add("/app", syscalls=True)
        >>> run.run(until="/app:exit")
        >>> run.focus().entries
        ('/app',)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self) -> results.FocusInfo:
        """Read the focus, as `machine focus` does.

        Returns:
            FocusInfo: The entries, the processes they matched, and the marks in
            force.

        Raises:
            NotRunning: The run has no machine.
        """
        return self._op(_ipc.FOCUS_OP_SHOW, [])

    def add(
        self, entry: str, *, syscalls: bool = False, ints: bool = False
    ) -> results.FocusInfo:
        """Steer another program, as `machine focus add` does.

        Args:
            entry (str): Which tasks: a program's `/path`, `cgroup=P`,
                `comm=NAME`, or a process `tgid=N`.
            syscalls (bool, optional): Count their system calls, for
                `Run.syscalls`. Defaults to False.
            ints (bool, optional): Count their interrupts, for `Run.ints`.
                Defaults to False.

        Returns:
            FocusInfo: The focus after the change.

        Raises:
            InvalidArgument: `entry` is not valid.
            NotRunning: The run has no machine.
        """
        flags = [word for word, on in (("+syscalls", syscalls), ("+ints", ints)) if on]
        with _translated():
            _parsers.focus_entry_type(entry)
            count_flags = _parsers.parse_hist_flags(flags)
        return self._op(_ipc.FOCUS_OP_ADD, [_args.focus_entry(entry)], count_flags)

    def remove(self, entry: str) -> results.FocusInfo:
        """Stop steering one entry, as `machine focus remove` does. The
        tasks it matched keep running, unsteered.

        Args:
            entry (str): The entry, as it was added.

        Returns:
            FocusInfo: The focus after the change.

        Raises:
            NotFound: The focus has no such entry.
            NotRunning: The run has no machine.
        """
        return self._op(_ipc.FOCUS_OP_REMOVE, [_args.focus_entry(entry)])

    def clear(self) -> results.FocusInfo:
        """Steer nothing, as `machine focus clear` does.

        Returns:
            FocusInfo: The focus after the change, with no entries.

        Raises:
            NotRunning: The run has no machine.
        """
        return self._op(_ipc.FOCUS_OP_SET, [])

    def _op(
        self, op: int, entries: builtins.list[str], count_flags: int = 0
    ) -> results.FocusInfo:
        with self._run._client._connection() as client, _translated():
            state = client.focus(
                self._run.id, op=op, entries=entries, count_flags=count_flags
            )
            doc = _focus.document(client, self._run.id, state)
        return results.focus(doc)


class Irq:
    """Interrupts the machine can inject, as `Run.irq`; not created directly.

    Call it to inject one, as `machine irq` does. The interrupt is queued
    and delivered at the machine's next dispatch, and recorded in the run's
    log like any other, so a replay delivers it at the same point.

    Example:
        >>> run.irq("preempt")
        Injected(vector=245, vcpu=0, nmi=False)
        >>> [v.name for v in run.irq.vectors()][:2]
        ['local_timer', 'preempt']
        >>> [(i.source, i.held) for i in run.irq.pending().interrupts]
        [('blk', True)]
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(
        self,
        vector: "int | str",
        *,
        vcpu: "int | str | None" = None,
        nmi: bool = False,
    ) -> results.Injected:
        """Inject an interrupt into the guest, as `machine irq` does.

        Args:
            vector (int or str): The vector, by number 32-255 or by name,
                such as `preempt`.
            vcpu (int or str, optional): The vCPU to deliver it to, or
                `all`; defaults to the vCPU of the last entry.
            nmi (bool, optional): Deliver a non-maskable interrupt instead.
                Defaults to False.

        Returns:
            Injected: The vector, the vCPU it went to, and whether it was an
            NMI.

        Raises:
            InvalidArgument: The vector or vCPU does not exist.
            NotRunning: The run has no machine.
        """
        with _translated():
            number = _parsers.vector_type(str(vector))
            target = None if vcpu is None else _parsers.irq_vcpu_type(str(vcpu))
        with self._run._client._connection() as client, _translated():
            sent_to = client.irq(
                self._run.id,
                vector=number,
                vcpu_id=target,
                delivery_mode=_ipc.IRQ_DM_NMI if nmi else _ipc.IRQ_DM_FIXED,
            )
        return results.Injected(
            vector=number,
            vcpu=None if sent_to == _ipc.IRQ_ALL_VCPUS else sent_to,
            nmi=nmi,
        )

    def vectors(self) -> builtins.list[results.Vector]:
        """List the vectors that can be injected by name.

        Returns:
            list[Vector]: One record per vector, with what arrives on it.
        """
        return [results.Vector(number=v, name=n) for n, v in _vectors.NAMES.items()]

    def pending(self) -> results.PendingInterrupts:
        """List every interrupt the machine has raised and not yet
        delivered, as `machine irqs` does: the ones due at the next
        dispatch, then the ones `irq_delay` holds, each with its due.

        Returns:
            PendingInterrupts: The machine time they were read at, and each
            interrupt.

        Raises:
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            pending = client.irqs(self._run.id)
        return results.pending_interrupts(_irqs.as_json(pending))


class Syscalls:
    """The guest kernel's system calls, as `Run.syscalls`; not created
    directly.

    Call it to list the kernel's own table, read from the running guest, so
    it is exact for that kernel; use the names in task predicates such as
    `/app:syscall=openat`. `counts` reads how often a task made each one.

    Example:
        >>> [c.name for c in run.syscalls("open")]
        ['open', 'openat']
        >>> run.focus.add("/app", syscalls=True)
        >>> run.run(until="/app:exit")
        >>> run.syscalls.counts("/app").by_tgid[0]
        Count(number=0, name='read', count=412)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self, name: Optional[str] = None) -> builtins.list[results.Syscall]:
        """List the guest kernel's system calls, as `machine syscalls` does.

        Args:
            name (str, optional): Keep those whose name contains this; None
                lists every system call.

        Returns:
            list[Syscall]: One record per system call.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet; `run(until="tasks")`
                first.
        """
        with self._run._client._connection() as client, _translated():
            rows = _ksym.syscalls_as_json(client.ksym(self._run.id), name or "")
        return [results.syscall(row) for row in rows]

    def counts(self, task: str) -> results.Counts:
        """Count the system calls a task made, as `machine syscalls <task>`
        does.

        Counting is off until armed, so it costs nothing unasked: arm it
        with `focus.add(entry, syscalls=True)`, drive the run, then read the
        counts.

        Args:
            task (str): Whose calls: a tid, `tgid=N`, or a focused `/path`,
                `cgroup=P` or `comm=NAME`.

        Returns:
            Counts: The tgid's counts, and the tid's when a tid named
            one, most taken first.

        Raises:
            InvalidArgument: The task is not focused, or is not a task.
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            hist, names = _hist.of_subject(client, self._run.id, _subject(task))
            return results.counts(_hist.syscalls_as_json(hist, names))


class Ints:
    """The guest kernel's interrupt vectors, as `Run.ints`; not created
    directly.

    As `Run.syscalls`, for interrupts: call it to list the kernel's vectors,
    and `counts` to read how many of each a task took. Arm counting with
    `focus.add(entry, ints=True)`.

    Example:
        >>> [v.name for v in run.ints("timer")]
        ['local_timer']
        >>> run.ints.counts("/app").by_tgid[0]
        Count(number=236, name='local_timer', count=18)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self, name: Optional[str] = None) -> builtins.list[results.Vector]:
        """List the guest kernel's interrupt vectors, as `machine ints` does.

        Args:
            name (str, optional): Keep those whose name contains this; None
                lists every vector.

        Returns:
            list[Vector]: One record per vector.

        Raises:
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            rows = _ksym.ints_as_json(client.ksym(self._run.id), name or "")
        return [results.vector(row) for row in rows]

    def counts(self, task: str) -> results.Counts:
        """Count the interrupts a task took, as `machine ints <task>` does.

        Args:
            task (str): Whose interrupts: a tid, `tgid=N`, or a focused
                `/path`, `cgroup=P` or `comm=NAME`.

        Returns:
            Counts: The tgid's counts, and the tid's when a tid named
            one, most taken first.

        Raises:
            InvalidArgument: The task is not focused, or is not a task.
            NotRunning: The run has no machine.
            Conflict: The guest kernel is not up yet.
        """
        with self._run._client._connection() as client, _translated():
            hist, names = _hist.of_subject(client, self._run.id, _subject(task))
            return results.counts(_hist.ints_as_json(hist, names))


def _subject(task: str) -> str:
    """A task the counts are read for, refused when it names none."""
    if not _hist.is_subject(task):
        raise InvalidArgument(
            f"Not a task: {task!r}."
            " Name a tid, tgid=N, or a focused /path, cgroup=P or comm=NAME."
        )
    return task


class Vtime:
    """The guest's virtual clock, as `Run.vtime`; not created directly.

    Call it to read the clock, as `machine vtime` does. The guest sees
    virtual time, which advances with the instructions it executes at a
    rate, not with the host's clock, so a run's timing replays exactly.

    Example:
        >>> clock = run.vtime()
        >>> clock.rate_num, clock.rate_den
        (10, 1)
        >>> run.vtime.rate("3/2")
        >>> run.vtime.add("1ms")
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self) -> results.VtimeInfo:
        """Read the clock, as `machine vtime` does.

        Returns:
            VtimeInfo: The clock's time, its base, its rate and its exit
                costs.

        Raises:
            NotRunning: The run has no machine.
        """
        return self._op(_parsers.VtimeWords(op=_ipc.VTIME_OP_GET))

    def rate(self, rate: str) -> results.VtimeInfo:
        """Set how fast virtual time runs, as `machine vtime rate` does.

        A higher rate makes timers fire after fewer instructions, which
        shortens a guest's sleeps and timeouts. A vCPU can have its own, so
        its work costs more or less time than the others'.

        Example:
            >>> run.vtime.rate("10,1=40")   # vCPU 1's work costs 4 times as much

        Args:
            rate (str): Virtual nanoseconds per conditional branch, `n` or
                `n/d`, for every vCPU, which clears each vCPU's own; and
                `N=rate` for vCPU N's own, in a comma list.

        Returns:
            VtimeInfo: The clock after the change.

        Raises:
            InvalidArgument: `rate` is not `n`, `n/d` or `N=rate`, or names
                a vCPU the machine does not have.
            NotRunning: The run has no machine.
        """
        with _translated():
            rates = _parsers.vtime_rates_type(rate)
        return self._op(_parsers.VtimeWords(op=_ipc.VTIME_OP_RATE, rates=rates))

    def add(self, ns: int | str) -> results.VtimeInfo:
        """Push the clock forward, as `machine vtime add` does, such as to
        fire a timer without executing up to it.

        Args:
            ns (int | str): Time to add: a duration such as "1s" or "250ms",
                or nanoseconds; at least 1ns.

        Returns:
            VtimeInfo: The clock after the change.

        Raises:
            InvalidArgument: `ns` is not a time, or is less than 1ns.
            NotRunning: The run has no machine.
        """
        with _translated():
            add_ns = _parsers.vtime_add_type(str(ns))
        return self._op(_parsers.VtimeWords(op=_ipc.VTIME_OP_ADD, add_ns=add_ns))

    def cost(
        self,
        *,
        io: Optional[int | str] = None,
        rdtsc: Optional[int | str] = None,
        pause: Optional[int | str] = None,
    ) -> results.VtimeInfo:
        """Set what each exit costs on the clock, as `machine vtime cost`
        does. The rate prices the guest's computing; these price its
        waiting, and their ratio decides whether a spin or an I/O
        completion wins a race. The run records the change, so a replay
        makes it at the same point.

        Example:
            >>> run.vtime.cost(io="20us", pause=50)
            >>> run.vtime.cost(rdtsc="io")

        Args:
            io (int | str, optional): An I/O, MMIO or hypercall exit, as a
                duration such as "2us" or nanoseconds, 1ns to 1s.
            rdtsc (int | str, optional): An RDTSC; "io" makes it follow the
                I/O cost again, as it does until it is given.
            pause (int | str, optional): Each PAUSE a spin loop executes.

        Returns:
            VtimeInfo: The clock after the change.

        Raises:
            InvalidArgument: No cost is given, or one is not 1ns to 1s.
            NotRunning: The run has no machine.
        """
        given = {"io": io, "rdtsc": rdtsc, "pause": pause}
        with _translated():
            words = _parsers.vtime_cost_words(
                [
                    f"{name}={value}"
                    for name, value in given.items()
                    if value is not None
                ]
            )
        return self._op(words)

    def _op(self, words: _parsers.VtimeWords) -> results.VtimeInfo:
        with self._run._client._connection() as client, _translated():
            state = _vtime.send(client, self._run.id, words)
        return results.vtime(_vtime.as_json(state))


def _listed(
    value: Union[str, int, Sequence[Union[str, int]]],
) -> tuple[Union[str, int], ...]:
    """One name or number as a list of one; a list as it is."""
    if isinstance(value, (str, int)):
        return (value,)
    return tuple(value)


class Faults:
    """Faults injected into the guest, as `Run.inject`; not created directly.

    Call it to list the faults, as `machine inject` does. A fault is in a
    subsystem: `syscall`, or one of the kernel's own sites, `disk`,
    `disk-timeout`, `slab`, `pages`, `usercopy` and `futex-access`. A
    syscall fault names system calls, a set of errors and a delay: the
    calls it fires on are held for the delay, then return one of the errors
    without running, or run when it has none. A site fault fails the site
    its own way. The guest kernel decides at each call, drawing from the
    fault's seed, so a replay acts on the same calls the same way. A
    checkpoint keeps the faults, and a fork has its parent's.

    Each fault counts the calls it matches on its own. The first fault that
    fires on a call acts on it. A fault on a program, by its path, matches
    its calls from its exec on, so it can be added before the program
    starts. `Tasks.exist` checks that a program runs first.

    Example:
        >>> run.inject.add("syscall", "connect", "ECONNREFUSED", in_="/usr/bin/app", times=1)
        >>> run.inject.add("syscall", ["read", "write"], "valid", in_="/usr/bin/app", pick=1)
        >>> run.inject.add("syscall", "read", delay="0:20ms", in_="/usr/bin/app")
        >>> run.inject.add("disk", in_="/usr/bin/app", prob=0.01)
        >>> run.inject()[0].fired
        1
        >>> run.inject.clear()
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self) -> tuple[results.Fault, ...]:
        """List the faults, as `machine inject` does.

        Returns:
            tuple[Fault, ...]: Each fault, with the calls it matched and
                fired on.

        Raises:
            Conflict: The guest has not started its kernel yet.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _faults.read(client, self._run.id)
        return results.faults(_faults.as_json(shown))

    def add(
        self,
        subsystem: str,
        syscalls: Union[str, int, Sequence[Union[str, int]]] = (),
        errors: Union[str, int, Sequence[Union[str, int]]] = (),
        *,
        in_: str = "",
        delay: str = "",
        after: int = 0,
        times: int = 0,
        prob: float = 1.0,
        pick: int = 0,
        seed: Optional[int] = None,
        file: str = "",
        op: str = "",
        sectors: Optional[tuple[int, int]] = None,
        bytes: int = 0,
        on: str = "",
        off: str = "",
        ret: int = 0,
        at: int = 0,
    ) -> tuple[results.Fault, ...]:
        """Inject a fault into a subsystem of the guest, as `machine inject
        add` does.

        For `function`, the second operand is a function's name: a call the
        fault fires on returns `ret` to its caller without running, as
        `run.inject.add("function", "malloc", in_="/usr/bin/app", prob=0.01)`
        fails one malloc in 100. The function is found in a running task
        `in_` names, or in task `at`'s files.

        For `syscall`, each system call gets its own fault. Each call it
        fires on is held for a delay drawn from `delay`, then returns one
        error of the set, drawn, or runs when there are none. A system call
        glibc's function of the same name does not make, `open` for
        `open()`, is added with a `UserWarning` that names the one it makes.
        A kernel site takes no system calls, errors, delay or pick: it
        fails its own way. The run records the faults, so a replay adds
        them at the same point.

        Args:
            subsystem (str): `"syscall"`, or a kernel site: `"disk"`, a
                block request fails with EIO; `"disk-timeout"`, its
                completion is dropped and the request never completes;
                `"slab"` and `"pages"`, an allocation fails; `"usercopy"`,
                a copy to or from the program's memory fails;
                `"futex-access"`, a futex's access to it fails. Faults of
                the disk itself: `"disk-corrupt"`, a read or write has
                bytes changed; `"disk-torn"`, a write stores its first
                sectors alone; `"disk-lost"`, a write stores nothing. Each
                request succeeds. A disk request is the submitting task's:
                a program submits its reads and synchronous writes, and the
                kernel's flusher its buffered ones.
            syscalls (str | int | Sequence[str | int]): For `syscall`, the
                system calls, by name or number.
            errors (str | int | Sequence[str | int]): The set of errors: a
                name such as `"ENOENT"`, a number, `"valid"` for the errors
                the system call's man page documents, `"all"` for every
                error Linux names, and `"-EINTR"` to take one out. Empty
                with a `delay`: the call runs after it.
            in_ (str): Whose calls it acts on, as `--in` takes it; required. A
                program by the absolute path of the file it runs, its EXE
                in `Tasks`, takes its tasks and the processes it forks
                without exec, from its exec on. `"comm=NAME"` takes the
                tasks of that comm, `"tid=N"` one task, `"tgid=N"` the tasks
                of one process, such as a `Process`'s `pid`, and `"all"`
                every task in the machine.
            delay (str): How long to hold each call it fires on, as
                `--delay` takes it: `"5ms"`, or `"0:20ms"` for a time drawn
                from the span; at most a second, on the guest's clock. ""
                for none.
            after (int): Let this many matching calls through first.
            times (int): Fire on at most this many calls, then let the rest
                through; 0 for no end.
            prob (float): The chance a matching call fires it, 0 to 1, in
                steps of a millionth. 0 fires on no call and counts the
                matches.
            pick (int): Pick this many of the errors at random when the
                fault is added; each system call picks its own. 0 keeps all.
            seed (int | None): Seed the fault's draws, 0 to 2**32 - 1. None
                draws a random seed.
            file (str): For a disk subsystem, the requests of a file's
                data: an absolute path, where `*` matches within a
                directory and `**` across them. A file matches whether it
                is opened or mapped before or after the fault is added.
                "" for any file.
            op (str): For a disk subsystem, `"read"` or `"write"`; "" for
                both, or writes for `disk-torn` and `disk-lost`.
            sectors (tuple[int, int] | None): For a disk subsystem, the
                requests that touch these 512-byte sectors of the disk,
                the first and the last. None for the whole disk.
            bytes (int): For `disk-corrupt`, how many bytes of a request
                change, 1 to 4096. 0 for 1.
            on (str): Make the fault cycle on the machine's clock: `"2s"`
                keeps it active for 2 seconds from when it is added, then
                it rests for `off`, and the cycle repeats. Resting, it
                matches no call. Whole milliseconds, 1ms to a day. "" for no
                cycle.
            off (str): The machine time the fault rests in each cycle, such
                as `"5s"`; given with `on`.
            ret (int): For `function`, the value a call the fault fires on
                returns to its caller, as `--return` takes it.
            at (int): For `function`, the tid of a task whose files hold
                the function, as `--at` takes it. 0 finds it in a task
                `in_` names.

        Returns:
            tuple[Fault, ...]: The faults after the add.

        Raises:
            InvalidArgument: A system call or an error names none, a number
                is out of range, `in_` is missing or names none, a path is
                not absolute or passes 255 bytes, a comm passes 15, the set
                holds more than 30 errors, neither errors nor a delay is
                given, a delay passes a second, `on` or `off` is given
                without the other, or the faults would pass 16.
            Conflict: The guest has not started its kernel yet, the machine
                is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        command = f"machine inject {self._run.name}"
        with self._run._client._connection() as client, _translated():
            delay_lo, delay_hi = _faults.delay_of(delay)
            function = subsystem == _faults.FUNCTION
            request = _faults.Request(
                syscalls=() if function else tuple(str(s) for s in _listed(syscalls)),
                site="" if subsystem == _faults.SYSCALL else subsystem,
                errors=",".join(str(e) for e in _listed(errors)),
                scope=(
                    _faults.scope_of(in_, _faults.scope_hint(command))
                    if in_
                    else _faults.Scope()
                ),
                after=after,
                times=times,
                ppm=_faults.prob_ppm(prob),
                pick=pick,
                seed=seed,
                delay_lo=delay_lo,
                delay_hi=delay_hi,
                op=op,
                sectors=sectors,
                file=file,
                bytes=bytes,
                on_ms=_faults.cycle_ms_of(on),
                off_ms=_faults.cycle_ms_of(off),
                function=str(syscalls) if function else "",
                ret=ret,
                at=at,
            )
            shown = _faults.add(
                client,
                self._run.id,
                request,
                command,
                f"machine syscalls {self._run.name}",
            )
        # A fault on a syscall a C library's function does not make: added,
        # and said, as `machine inject add syscall` says it.
        for note in shown.notes:
            warnings.warn(note, UserWarning, stacklevel=2)
        return results.faults(_faults.as_json(shown))

    def delete(self, number: int) -> tuple[results.Fault, ...]:
        """Delete a fault, as `machine inject delete` does.

        Args:
            number (int): The fault's number, as the list gives it.

        Returns:
            tuple[Fault, ...]: The faults after the delete.

        Raises:
            InvalidArgument: No fault has that number.
            Conflict: The machine is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _faults.delete(
                client, self._run.id, number, f"machine inject {self._run.name}"
            )
        return results.faults(_faults.as_json(shown))

    def clear(self) -> tuple[results.Fault, ...]:
        """Delete every fault, as `machine inject clear` does.

        Returns:
            tuple[Fault, ...]: No faults.

        Raises:
            Conflict: The machine is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _faults.clear(client, self._run.id)
        return results.faults(_faults.as_json(shown))


#: How the SDK lists a run's containers: the way out of a change that
#: needs one.
_CONTAINERS_CALL: Final[str] = "run.containers()"


class Clock:
    """The guest's date, as `Run.clock`; not created directly.

    Call it to read the date, as `machine clock` does. The date is what
    `time()` and `gettimeofday` return in the guest. A change moves it for
    every process, as `date -s` or an NTP step does; elapsed time, the TSC and
    timers measured in elapsed time do not move. The guest kernel applies a
    change when the run next executes, and a replay applies it at the same
    point.

    Example:
        >>> run.clock.jump("+59m50s")
        >>> run.run(within="30s", until="console~token refresh")
        >>> run.clock().offset
        3590000000000
        >>> run.clock.jump("-2s", for_="1m")
        >>> run.clock.reset()
        >>> run.clock.jump("+1h", container="db")
        >>> run.clock.reset(container="db")

    A change with `container` moves one container's date alone, as an
    offset from the guest's date. Every process of the container reads it,
    and the container keeps it across a restart. A container is found with
    the machine stopped, and the machine does not run. An id is preferred:
    it is known as soon as Docker creates the container, while a name is
    known shortly after the container starts.
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def _target(
        self, client: Any, container: Optional[str], stopped: bool = False
    ) -> Optional[_containers.Running]:
        """The container `container` names; None for the guest's date."""
        if container is None:
            return None
        return _containers.resolve(client, self._run.id, container, stopped=stopped)

    def _info(
        self, client: Any, state: Any, one: Optional[_containers.Running]
    ) -> results.ClockInfo:
        """The date as the SDK returns it, each container by its name."""
        shown = None
        if one is None and state.containers:
            shown = _clock.labels(client.containers(self._run.id))
        return results.clock(_clock.as_json(state, one, shown))

    def __call__(self, *, container: Optional[str] = None) -> results.ClockInfo:
        """Read the guest's date, as `machine clock` does, or one
        container's.

        Args:
            container (str, optional): The container to read, by its id,
                its name, or a service with one running container. A
                stopped container with an offset is found by its id or its
                name.

        Returns:
            ClockInfo: The date, its offset from machine time, a pending
                undo and changes not taken yet.

        Raises:
            NotFound: The machine knows no container by `container`.
            Conflict: The guest has not started its kernel yet.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container, stopped=True)
            state = _clock.read(client, self._run.id)
            return self._info(client, state, one)

    def jump(
        self,
        by: str,
        *,
        for_: Optional[str] = None,
        container: Optional[str] = None,
        slew: Optional[str] = None,
    ) -> results.ClockInfo:
        """Step the guest's date, as `machine clock jump` does.

        Args:
            by (str): The step, such as `+5s`, `-2h` or `59m50s`; no sign is
                forward.
            for_ (str, optional): Machine time after which the step is undone,
                such as `30s`. The undo restores the offset in force before.
            container (str, optional): The container whose date steps, by
                its id, its name, or a service with one running container;
                the guest's date when omitted.
            slew (str, optional): With `container`, the machine time the
                container's date moves over instead of in one step, such
                as `30s`; with `for_`, it slews back over the same time.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `by` or `for_` is not a duration.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container)
            state = _clock.jump(
                client, self._run.id, by, for_, "run.run()", one, slew, _CONTAINERS_CALL
            )
            return self._info(client, state, one)

    def set(
        self,
        date: str,
        *,
        for_: Optional[str] = None,
        container: Optional[str] = None,
        slew: Optional[str] = None,
    ) -> results.ClockInfo:
        """Set the guest's date, as `machine clock set` does.

        Args:
            date (str): The date with its zone, such as `2027-03-28T00:59:50Z`.
            for_ (str, optional): Machine time after which the change is
                undone, such as `1m`.
            container (str, optional): The container whose date is set, by
                its id, its name, or a service with one running container;
                the guest's date when omitted.
            slew (str, optional): With `container`, the machine time the
                container's date moves over instead of in one step, such
                as `30s`; with `for_`, it slews back over the same time.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `date` is not a date with a zone, or is before
                1970.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container)
            state = _clock.set_(
                client,
                self._run.id,
                date,
                for_,
                "run.run()",
                one,
                slew,
                _CONTAINERS_CALL,
            )
            return self._info(client, state, one)

    def drift(
        self,
        rate: str,
        *,
        container: str,
        for_: Optional[str] = None,
    ) -> results.ClockInfo:
        """Make a container's date gain or lose time, as `machine clock
        drift` does.

        The container's date gains `rate` of each second of machine time.
        No read sees a jump, and a timer armed after agrees with it.
        `drift("0")` stops it and keeps the offset it reached.

        Example:
            >>> run.clock.drift("+200ppm", container="db")
            >>> run.clock.drift("0", container="db")

        Args:
            rate (str): The rate, such as `+200ppm`, `-1.5ppm` or `300ppb`;
                `0` stops it.
            container (str): The container whose date drifts, by its id, its
                name, or a service with one running container.
            for_ (str, optional): Machine time after which the rate in force
                before is back, such as `1h`.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `rate` is not a rate, or is past 50%.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container)
            state = _clock.drift(
                client,
                self._run.id,
                rate,
                for_,
                "run.run()",
                "run.vtime()",
                one,
                _CONTAINERS_CALL,
            )
            return self._info(client, state, one)

    def jitter(
        self,
        choices: str,
        *,
        every: Optional[str] = None,
        seed: Optional[int] = None,
        for_: Optional[str] = None,
        container: Optional[str] = None,
    ) -> results.ClockInfo:
        """Step the guest's date to a drawn offset each period, as `machine
        clock jitter` does.

        Each period of machine time the date is set to a value drawn from
        `choices`, around the date the jitter leaves alone, as NTP steps a
        clock. The first step is taken when the guest takes the command. A
        jump or a set moves the date under the jitter.

        Example:
            >>> run.clock.jitter("-200ms:200ms", every="10m", seed=42)
            >>> run.clock.jitter("-1s[1],0[8],1s[1]", every="1m", container="db")
            >>> run.clock.jitter("off")

        Args:
            choices (str): The choices, such as `-200ms:200ms`, or
                `-1s[1],0[8],1s[1]` with weights; `off` stops the jitter.
            every (str, optional): Machine time between two steps, such as
                `10m`. Required unless `choices` is `off`.
            seed (int, optional): The seed the steps are drawn from; one is
                drawn at random when omitted, and shown in the result.
            for_ (str, optional): Machine time after which the jitter in
                force before is back, such as `5m`.
            container (str, optional): The container whose date jitters, by
                its id, its name, or a service with one running container;
                the guest's date when omitted.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            NotFound: The machine knows no container by `container`.
            InvalidArgument: `choices` is not a list of choices, or `every`
                is missing or shorter than 1ms.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container, stopped=choices == _clock.JITTER_OFF)
            state = _clock.jitter(
                client, self._run.id, choices, every, seed, for_, "run.run()", one
            )
            return self._info(client, state, one)

    def leap(self, which: str) -> results.ClockInfo:
        """Insert or delete a second at the next UTC midnight of the guest's
        date, as `machine clock leap` does.

        The guest kernel inserts 23:59:60 or skips 23:59:59, as an NTP
        daemon's leap flag asks it to, and every container moves with the
        guest's date.

        Example:
            >>> run.clock.set("2026-12-31T23:59:50Z")
            >>> run.clock.leap("insert")

        Args:
            which (str): `insert`, `delete`, or `off` to cancel a leap
                pending.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            InvalidArgument: `which` is none of the three.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        if which not in _clock.LEAPS:
            raise InvalidArgument(f"Not a leap: {which}. Use insert, delete or off.")
        with self._run._client._connection() as client, _translated():
            state = _clock.leap(client, self._run.id, which, "run.run()")
            return self._info(client, state, None)

    def reset(self, *, container: Optional[str] = None) -> results.ClockInfo:
        """Step the guest's date and every container's back to machine time,
        as `machine clock reset` does, or return one container to the guest's
        date.

        Args:
            container (str, optional): The container that returns to the
                guest's date, by its id, its name, or a service with one
                running container. A stopped container with an offset is
                found by its id or its name.

        Returns:
            ClockInfo: The date as it stands, the change waiting.

        Raises:
            NotFound: The machine knows no container by `container`.
            Conflict: The machine is read-only, a run is in progress, or 8
                changes wait for the run to execute.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            one = self._target(client, container, stopped=True)
            state = _clock.reset(client, self._run.id, "run.run()", one)
            return self._info(client, state, one)


class Axis:
    """The run's defaults of the draw axes, as `Run.axis`; not created directly.

    Call it to list the defaults, as `machine axis` does. A default is a plan
    clause the run keeps: every run, exec and plan draws from it until it is
    unset. A run's own options and a plan's clauses replace a default for the
    axes they name. A default with a `when` replaces one with no condition
    where the predicate holds, and of two that hold, the one set last wins. A
    checkpoint keeps the defaults, and a fork has its parent's.

    Example:
        >>> run.axis.set("task-sched hold")
        >>> run.axis.set("when /usr/bin/app:on mode step vtime-rate 40")
        >>> run.axis()[0].clause
        'task-sched hold'
        >>> run.axis.unset("task-sched")
        >>> run.axis.clear()
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self) -> tuple[results.AxisDefault, ...]:
        """List the defaults, as `machine axis` does.

        Returns:
            tuple[AxisDefault, ...]: Each default, in the order drives draw
                them.

        Raises:
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _axis.read(client, self._run.id)
        return results.axis_defaults(_axis.as_json(shown))

    def set(self, clause: str) -> tuple[results.AxisDefault, ...]:
        """Set a default, as `machine axis set` does.

        The default replaces the values its axes had under the same
        condition. A clause that names several axes draws them together.

        Args:
            clause (str): A plan clause, such as `task-sched hold` or
                `when /usr/bin/app:on mode step vtime-rate 40`.

        Returns:
            tuple[AxisDefault, ...]: The defaults after the change.

        Raises:
            InvalidArgument: `clause` is not a plan clause, or the run holds
                256 defaults.
            Conflict: The machine is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _axis.set_(client, self._run.id, shlex.split(clause))
        return results.axis_defaults(_axis.as_json(shown))

    def unset(
        self, axes: str, *, every: bool = False
    ) -> tuple[results.AxisDefault, ...]:
        """Remove defaults, as `machine axis unset` does.

        Args:
            axes (str): The axes to remove, such as `mode task-sched`, after a
                `when` predicate for the defaults under that condition.
            every (bool): Remove the axes' defaults under every condition.

        Returns:
            tuple[AxisDefault, ...]: The defaults after the change.

        Raises:
            InvalidArgument: `axes` names no axis, or names a condition with
                `every`.
            Conflict: The machine is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _axis.unset(client, self._run.id, shlex.split(axes), every=every)
        return results.axis_defaults(_axis.as_json(shown))

    def clear(self) -> tuple[results.AxisDefault, ...]:
        """Remove every default, as `machine axis clear` does.

        Returns:
            tuple[AxisDefault, ...]: No defaults.

        Raises:
            Conflict: The machine is read-only, or a run is in progress.
            NotRunning: The run has no machine.
        """
        with self._run._client._connection() as client, _translated():
            shown = _axis.clear(client, self._run.id)
        return results.axis_defaults(_axis.as_json(shown))


class Rng:
    """The machine's RNG device, as `Run.rng`; not created directly.

    Call it to read the seed, as `machine rng` does. The device gives the
    guest its random bytes from a seed, so a run's random numbers replay
    exactly, and a branch seeded from another number draws others.

    Example:
        >>> run.rng().seed
        42
        >>> run.rng.seed(7)
    """

    def __init__(self, run: Run) -> None:
        self._run = run

    def __call__(self) -> results.RngInfo:
        """Read the seed, as `machine rng` does.

        Returns:
            RngInfo: The seed, and whether the guest reseeds at once.

        Raises:
            InvalidArgument: The run was created without an RNG device.
            NotRunning: The run has no machine.
        """
        return self._read(None)

    def seed(self, seed: int) -> results.RngInfo:
        """Seed the device again, as `machine rng seed` does.

        The run records the seed, so a replay seeds the device at the same
        point. A guest kernel that reads the seed reseeds its own generator
        at once.

        Args:
            seed (int): The seed, 0 to 2**64 - 1.

        Returns:
            RngInfo: The device after the seed.

        Raises:
            InvalidArgument: `seed` is out of range, or the run was created
                without an RNG device.
            NotRunning: The run has no machine.
        """
        with _translated():
            checked = _parsers.rng_seed_type(str(seed))
        return self._read(checked)

    def _read(self, seed: Optional[int]) -> results.RngInfo:
        with self._run._client._connection() as client, _translated():
            state = client.rng(self._run.id, seed)
        return results.rng(_rng.as_json(state))
