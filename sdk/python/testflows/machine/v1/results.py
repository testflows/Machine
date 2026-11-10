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
"""What the SDK's calls return: plain records, each with `as_json`."""

from __future__ import annotations

import dataclasses
import datetime
from dataclasses import dataclass, field
from typing import Any, Optional, overload

from testflows.machine._core import fs as _fs
from testflows.machine._core import branches as _branches
from testflows.machine._core import breakpoints as _breakpoints
from testflows.machine._core import branching as _branching
from testflows.machine._core import control as _control
from testflows.machine._core import debugger as _debugger
from testflows.machine._core import hashes as _hashes
from testflows.machine._core import ipc as _ipc
from testflows.machine._core import machines as _machines
from testflows.machine._core import stepping as _stepping
from testflows.machine._core import variables as _variables
from testflows.machine._core import timespec as _timespec

from .errors import InvalidArgument

_BYTES_PER_MB = 1024 * 1024


class _Record:
    """A record's `as_json`, written once."""

    def as_json(self) -> dict[str, Any]:
        """The fields as a JSON-serializable dict.

        Returns:
            dict[str, Any]: One key per attribute.
        """
        # Every subclass is a dataclass; this base is not, so say so to the
        # type checker rather than to `asdict`.
        record: Any = self
        return dataclasses.asdict(record)


@dataclass(frozen=True, order=True, init=False)
class Point(_Record):
    """A time in a run's history: the machine's clock, in nanoseconds.

    A point means the same time on every run. Each run resolves it to its
    own entry, the first one at the last time at or before the point. Add
    or subtract a duration to move it: an int of nanoseconds, a str such as
    `"10ms"`, or a `timedelta`. Subtract one point from another for the
    nanoseconds between them.

    Example:
        >>> booted = run.checkpoint("booted")
        >>> later = booted.place.point + "10ms"
        >>> branch = run.fork(at=later)
        >>> str(Point("1.5s") - "500ms")
        '1.000000000s'

    Args:
        vtime (int or str): Nanoseconds, or a time such as `"1.194s"`.

    Attributes:
        vtime (int): The machine's clock, in nanoseconds.
    """

    vtime: int

    def __init__(self, vtime: "int | str") -> None:
        if isinstance(vtime, str):
            if vtime.startswith("-"):
                raise InvalidArgument(
                    f"A point is a time of the run, never before 0: {vtime}."
                )
            ns = _nanoseconds(vtime)
        elif isinstance(vtime, int) and not isinstance(vtime, bool):
            ns = vtime
        else:
            raise InvalidArgument(
                f"A point is nanoseconds or a time such as '1.194s', got {vtime!r}."
            )
        if ns < 0:
            raise InvalidArgument(f"A point is never before 0: {ns} ns.")
        object.__setattr__(self, "vtime", ns)

    def __add__(self, duration: "int | str | datetime.timedelta") -> "Point":
        return Point(self.vtime + _nanoseconds(duration))

    def __radd__(self, duration: "int | str | datetime.timedelta") -> "Point":
        return self + duration

    @overload
    def __sub__(self, other: "Point") -> int: ...

    @overload
    def __sub__(self, other: "int | str | datetime.timedelta") -> "Point": ...

    def __sub__(self, other: "Point | int | str | datetime.timedelta") -> "Point | int":
        if isinstance(other, Point):
            return self.vtime - other.vtime
        return Point(self.vtime - _nanoseconds(other))

    def __str__(self) -> str:
        return _timespec.render_vtime(self.vtime)


def _nanoseconds(duration: "int | str | datetime.timedelta") -> int:
    """A duration in nanoseconds, signed: an int is nanoseconds already, a
    str is a duration such as `"10ms"` or `"-1.5s"`."""
    if isinstance(duration, datetime.timedelta):
        return (duration // datetime.timedelta(microseconds=1)) * _timespec.NS_PER_US
    if isinstance(duration, str):
        try:
            ns = _timespec.parse_duration(duration)
        except _timespec.SpecError as exc:
            raise InvalidArgument(str(exc)) from None
        return -ns if duration.startswith("-") else ns
    if isinstance(duration, int) and not isinstance(duration, bool):
        return duration
    raise InvalidArgument(
        "A duration is nanoseconds, a str such as '10ms' or a timedelta,"
        f" got {duration!r}."
    )


@dataclass(frozen=True)
class Place(_Record):
    """One entry of one run: where the run is, or where it was.

    A method that takes a point takes a place on the run that recorded it
    and on its branches forked after it. Anywhere else it is refused,
    since past a fork another run's entry of that id is another state. An
    entry a branch replayed is the branch's own: the branch recorded it
    again, and another run's place for it is refused there too. Use
    `place.point` to take the same time on another run.

    Attributes:
        run_id (str): The run that recorded the entry: this run, or for an
            entry before it was forked an ancestor.
        entry_id (int): The entry.
        point (Point): Its time.
        vtime_offset (int): Orders the entries that share one time, from 0.
    """

    run_id: str
    entry_id: int
    point: Point
    vtime_offset: int


@dataclass(frozen=True)
class Checkpoint(_Record):
    """A saved machine state a run can be started, forked or detached from.

    A checkpoint is named by `^name` wherever a point is taken, such as
    `run.fork(at="^boot")`, and is taken itself in place of the name. A
    branch lists its ancestors' checkpoints as its own, since its history is
    theirs up to where it was forked.

    Attributes:
        name (str): The checkpoint's name; an unnamed one is named by its
            entry id.
        place (Place): The entry the state was saved at, and the run that
            recorded it: this run, or for an inherited checkpoint an
            ancestor.
        date (int): When it was taken, in Unix seconds.
        durable (bool): Whether it is in shared storage, so another session
            can start from it.
    """

    name: str
    place: Place
    date: int
    durable: bool


@dataclass(frozen=True)
class Entry(_Record):
    """Where one vCPU stopped at the end of a `Run.run`.

    Attributes:
        vcpu (int): The vCPU.
        entry_id (int): The entry it recorded when it stopped.
        exit (str): Why it stopped, by its exit name: `INSTR_LIMIT` when
            the dispatch ran its instructions, `IO`, `MMIO`, `HLT`,
            `PAUSE`, `HYPERCALL`, `SHUTDOWN` and so on. When the log recorded a
            different event, it follows in parentheses: `IO (EXIT_SHUTDOWN)`
            for a guest that reset through the keyboard controller.
        rip (int): The guest instruction address it stopped at.
    """

    vcpu: int
    entry_id: int
    exit: str
    rip: int


@dataclass(frozen=True)
class Clause(_Record):
    """One clause a drive drew from, and how often it applied.

    Attributes:
        clause (str): The clause as text, such as `when /app:on mode step`.
        matched (int): The iterations the clause applied to. The rest of
            the drive's iterations ran free of it.
    """

    clause: str
    matched: int


@dataclass(frozen=True)
class RunResult(_Record):
    """What `Run.run`, `Run.plan` or `Run.play` did.

    Attributes:
        iterations (int): The iterations that ran. With `until`, the count
            it took for the predicate to hold.
        entry_id (int): The newest entry recorded; 0 when none was.
        entries (tuple[Entry, ...]): Where each vCPU stopped.
        reached (bool): Whether the drive got what it was asked for. False
            only when `until` was given with an `iters` bound and the
            iterations ran out first; True whenever no predicate was named.
        vtime_elapsed (int): How far the run's virtual time moved, in
            nanoseconds. An iteration has no fixed length, so this is the
            measure of how far the drive went; 0 for `Run.play`.
        clauses (tuple[Clause, ...]): The clauses the drive drew from, each
            with the iterations it applied to: the `when` of `Run.run`, or
            the clauses of a plan. Empty for a drive that named none.
        hits (tuple[Breakpoint, ...]): The breakpoints and watchpoints the
            run ended at. Empty when none ended it.
        printed (tuple[str, ...]): The lines the stops that print printed,
            in the order they hit.
    """

    iterations: int
    entry_id: int
    entries: tuple[Entry, ...]
    reached: bool = True
    vtime_elapsed: int = 0
    clauses: tuple[Clause, ...] = ()
    hits: tuple["Breakpoint", ...] = ()
    printed: tuple[str, ...] = ()


@dataclass(frozen=True)
class Miss(_Record):
    """One crossing of an entry that did not reproduce it.

    Attributes:
        entry_id (int): The entry the crossing missed.
        rip (int): Where the machine stood when it stopped.
        rcb (int): The branches it had retired.
        regs_hash (int): Its registers, hashed.
    """

    entry_id: int
    rip: int
    rcb: int
    regs_hash: int


@dataclass(frozen=True)
class ReplayResult(_Record):
    """Whether `Run.replay` reproduced what the line it followed recorded.

    A replay that does not reproduce the recording is a result, not an
    exception: check `converged`, then `diverged` for what differs and
    `Run.diff` for detail.

    Attributes:
        converged (bool): Whether every replayed entry matched.
        entry_id (int): Where the replay stopped.
        replayed (int): The entries it re-executed.
        diverged (tuple[dict[str, str], ...]): One `{section, a, b}` object
            per machine component whose state differs, such as registers or
            memory; empty when it converged.
        misses (tuple[Miss, ...]): Every crossing that missed, first to
            last, including one a retry then crossed; empty when every entry
            reproduced the first time.
        retry_stopped (str): Why a retry that `retry` allowed did not
            happen: the run could not go back to retry from, and may have
            been stopped or rebuilt trying. Empty when every retry ran.
        overshoot (str): How far the replay passed the recorded stop, and
            the margin it was armed with, when a margin too small for the
            counter's skid is why it diverged. Empty otherwise. A larger
            `limit_margin` or `instr_margin` replays it.
    """

    converged: bool
    entry_id: int
    replayed: int
    diverged: tuple[dict[str, str], ...]
    misses: tuple[Miss, ...] = ()
    retry_stopped: str = ""
    overshoot: str = ""


@dataclass(frozen=True)
class Diff(_Record):
    """Two machine states compared at an entry, as `Run.diff` compared them.

    Attributes:
        converged (bool): Whether the two states match.
        entry_id (int): The entry compared.
        diverged (tuple[dict[str, str], ...]): One `{section, a, b}` object
            per machine component whose state differs; empty when they
            match. `section` names the component: `regs` the registers,
            `mem_hash` the guest memory, `rcb` the retired branch count,
            and the rest a device or a part of the vCPU's state.
        memory_compared (bool): Whether the guest memory was compared.
            False when one entry hashed it and the other did not; then
            `converged` says nothing about memory.
        disk_compared (bool): Whether the guest's disk was compared, on the
            same terms as `memory_compared`.
    """

    converged: bool
    entry_id: int
    diverged: tuple[dict[str, str], ...]
    memory_compared: bool
    disk_compared: bool


@dataclass(frozen=True)
class File(_Record):
    """One entry of a directory on a run's disk, as `Fs.ls` lists it.

    Attributes:
        name (str): The entry's name in its directory.
        kind (str): `file`, `dir`, `link` or `other`.
        size (int): The size in bytes.
        mode (int): The POSIX mode bits.
        mtime (int): The modification time, in Unix seconds.
    """

    name: str
    kind: str
    size: int
    mode: int
    mtime: int


@dataclass(frozen=True)
class Listing(_Record):
    """A run as the `runs` listing showed it: a snapshot, not a live read.

    `Client.runs` fills `Run.listing` with one; a run reached any other way
    has none. `Run.describe` and `Run.state` read the run again.

    Attributes:
        state (str): The run's state when listed: `running`, `paused`,
            `halted` once the guest shut itself down, `stopped`, `unsaved`,
            `killed`, `died`, `terminated` and so on.
        cpus (int): vCPUs.
        mem (int): Guest memory in MB.
        disk (str): The disk it boots.
        created_at (int): When it was created, in Unix seconds.
        depth (int, optional): Forks from its root; 0 for a root. None in a
            `roots=True` listing.
        running (int, optional): In a `roots=True` listing, how many of the
            tree's runs are running; None otherwise.
        runs (int, optional): In a `roots=True` listing, how many runs the
            tree has; None otherwise.
        disk_bytes (int, optional): With `size=True`, bytes on the
            session's disk; None when the run is not there or unmeasured.
        ram_bytes (int, optional): With `size=True`, bytes resident in its
            machine; None when it is not running or unmeasured.
        local (str, optional): With `size=True`, the state of the session's
            copy: `offloadable`, `in use`, `preloading`, `offloading` or
            `not synced`.
    """

    state: str
    cpus: int
    mem: int
    disk: str
    created_at: int
    depth: Optional[int]
    running: Optional[int]
    runs: Optional[int]
    disk_bytes: Optional[int]
    ram_bytes: Optional[int]
    local: Optional[str]


@dataclass(frozen=True)
class State(_Record):
    """What a run's machine is doing, as `Run.state` read it.

    Attributes:
        state (str): `running`, `paused`, `halted`, `read-only` and so on.
            A `halted` guest shut itself down: the machine still reads and
            forks, and a drive is refused until `start` at a checkpoint.
            A run with no machine reads as the run list shows it, `stopped`
            for one that was stopped.
        activity (str): What it is busy with: `idle`, `running`,
            `checkpointing`, `replaying` and so on. Empty for a run with no
            machine.
        cpus (int): vCPUs.
        vcpu (int): The vCPU that recorded the newest entry.
        mem (int): Guest memory in MB.
        entry_id (int): The newest entry.
        vtime (int): The run's virtual time there, in nanoseconds.
        vtime_offset (int): Orders the entries that share one `vtime`.
        host_cpu (int): The host CPU the machine is on; -1 before it is
            placed.
        switch_prev_tid (int): At a guest scheduling decision, the task
            leaving the CPU; 0 when the machine is not at one.
        switch_next_tid (int): The task taking the CPU there.
        plan (tuple[str, ...]): The clauses the last drive drew from, one
            line each.
        plan_tally (tuple[str, ...]): How many dispatches each clause
            matched, in the order of `plan`.
    """

    state: str
    activity: str
    cpus: int
    vcpu: int
    mem: int
    entry_id: int
    vtime: int
    vtime_offset: int
    host_cpu: int
    switch_prev_tid: int
    switch_next_tid: int
    plan: tuple[str, ...]
    plan_tally: tuple[str, ...]


@dataclass(frozen=True)
class RunInfo(_Record):
    """How a run was created, and where it came from, as `Run.describe`
    read it.

    The options are named as `Client.create` takes them, so a program can
    pass them back, or pass the run as `like`.

    Attributes:
        id (str): The run's id.
        name (str): The run's name; a branch's is `root/branch`.
        state (str): The state when read, as `Listing.state` names it.
        cpus (int): vCPUs.
        mem (int): Guest memory in MB.
        mem_type (str): Guest memory backing: `auto` or `hugepages`.
        mem_mode (str): How a cold restore maps memory: `auto`, `map` or
            `lazy`.
        disk_id (str): The content hash of the disk it boots, which stays
            the same when the disk is renamed. `Listing.disk` and
            `Client.create` take the NAME; this is what the run holds.
        fs_sync (bool): Whether the root filesystem is mounted `sync`.
        fs_dirsync (bool): Whether it is mounted `dirsync`.
        rng (bool): Whether the guest has a virtio RNG.
        rng_seed (int): The RNG's seed.
        limit (int): Conditional branches a fast dispatch may run, steps in
            step mode; 0 for no limit.
        vtime_base (int): The guest's clock at boot, in Unix seconds.
        vtime_rate (str): How fast virtual time runs against instructions,
            `n/d`.
        vtime_io_cost (int): Virtual nanoseconds each I/O exit costs.
        vtime_rdtsc_cost (int): Virtual nanoseconds each RDTSC exit costs.
        vtime_pause_cost (int): Virtual nanoseconds each PAUSE costs.
        auto_checkpoints (int): Checkpoints every this many entries; 0 for
            never.
        side_dump (bool, optional): Whether each entry records a state dump,
            which `Run.dump(entry=...)` reads; None when the log does not
            say.
        side_trace (bool, optional): Whether each entry records a trace.
        mem_hash (bool, optional): Whether each entry hashes the machine's
            memory; None when the session does not say.
        disk_hash (bool, optional): Whether each entry hashes the machine's
            disk; None when the session does not say.
        parent_id (str): The run a branch was forked from; empty for a root.
        parent_entry_id (int): The entry it was forked at; 0 for a root.
        labels (dict[str, str]): The run's labels.
        keep_output (list[str]): The names of output the run stores though
            its directory drops it.
        drop_output (list[str]): The names of output the run does not
            store.
        rdtsc_exit (bool): Whether every RDTSC is recorded as an entry.
        repeatable (bool): Whether the run records so that recording it
            again repeats it entry by entry.
        repeats_to (int, optional): A repeatable run's first entry whose
            stop another recording need not make, where recording it again
            from the start stops repeating; None when it repeats to its tip
            or is not repeatable.
        host_breaks (tuple[int, ...]): The entries among those that the
            host's clock placed, a dispatch bound or an immediate cancel:
            no recording of the same state stops there again.
        limit_margin (int): How many branches short of each stop the branch
            counter interrupts; 0 for the host's.
        timer_slack (int): How late, in nanoseconds, a timer may be in code
            that takes no exit.
        instr_margin (int): How many instructions short of a stop placed by
            an instruction count the instruction counter interrupts; 0 for
            the host's.
        dispatch_bound (int): The host time, in nanoseconds, a fast
            dispatch may run before it stops; 0 for the default, 1s.
        fast_backstop (int): How many instructions a fast dispatch may run
            with no other stop; 0 for the default, 100,000,000.
        irq_wait (int): How many branches a dispatch runs while an
            interrupt waits for the guest to take it; 0 for the default, 200.
        guest_kernel (str): The version of the kernel the run boots;
            empty for a run that boots firmware.
        machine_abi (int, optional): The oldest machine ABI the run's
            entries were recorded under, fixed when the run was created;
            None when it was not read.
        machine_abi_max (int, optional): The newest machine ABI the run's
            entries were recorded under; a machine replays the run only if
            it supports every ABI from `machine_abi` to this one. None when
            it was not read.
    """

    id: str
    name: str
    state: str
    cpus: int
    mem: int
    mem_type: str
    mem_mode: str
    disk_id: str
    fs_sync: bool
    fs_dirsync: bool
    rng: bool
    rng_seed: int
    limit: int
    vtime_base: int
    vtime_rate: str
    vtime_io_cost: int
    vtime_rdtsc_cost: int
    vtime_pause_cost: int
    auto_checkpoints: int
    side_dump: Optional[bool]
    side_trace: Optional[bool]
    mem_hash: Optional[bool]
    disk_hash: Optional[bool]
    parent_id: str
    parent_entry_id: int
    labels: dict[str, str] = field(default_factory=dict)
    keep_output: list[str] = field(default_factory=list)
    drop_output: list[str] = field(default_factory=list)
    rdtsc_exit: bool = False
    repeatable: bool = False
    repeats_to: Optional[int] = None
    host_breaks: tuple[int, ...] = ()
    limit_margin: int = 0
    timer_slack: int = 0
    instr_margin: int = 0
    dispatch_bound: int = 0
    fast_backstop: int = 0
    irq_wait: int = 0
    guest_kernel: str = ""
    machine_abi: Optional[int] = None
    machine_abi_max: Optional[int] = None


@dataclass(frozen=True)
class Mark(_Record):
    """A mark on a run, as `Marks.list` lists it.

    A mark is a short second name, unique in its run's tree, that reads as
    `%name` wherever a run or a point is taken.

    Attributes:
        name (str): The mark, without the `%`.
        run_id (str): The run that carries it.
        run_name (str): That run's name.
    """

    name: str
    run_id: str
    run_name: str


@dataclass(frozen=True)
class Command(_Record):
    """One command issued to a run, as `Control.log` lists it.

    Every command that drove or changed the machine is recorded in its log
    with the entries it covered, whichever program issued it.

    Attributes:
        text (str): The command as the control REPL spells it, such as
            `/s 5` or `/checkpoint booted`.
        start_entry_id (int): The first entry it recorded.
        entry_id (int): The last entry it recorded.
        vcpu (str): For a drive, the vCPU it dispatched, `*` for every
            vCPU; empty for any other command.
    """

    text: str
    start_entry_id: int
    entry_id: int
    vcpu: str


@dataclass(frozen=True)
class Exit(_Record):
    """A guest task that has exited, as `Tasks.exits` lists it.

    `exit_code` is the kernel's encoding, with `signal` and `status` beside
    it: code checking for a crash reads the pair, code checking a program's
    own status reads `status`.

    Attributes:
        tid (int): The thread that exited.
        tgid (int): Its process.
        exit_code (int): The kernel's wait status, as the guest reports it.
        signal (int): The signal that killed it, 0 if none did.
        status (int): The status it exited with, 0 when a signal killed it.
        outcome (str): How it ended, as `wait` reports it: `status 0`,
            `status 1`, `killed by SIGSEGV`.
        comm (str): The task's name, as the task listing showed it while
            the task was alive.
    """

    tid: int
    tgid: int
    exit_code: int
    signal: int
    status: int
    outcome: str
    comm: str = ""


@dataclass(frozen=True)
class OutputMatch(_Record):
    """One match of a pattern in a run's output, as `Output.search`
    yields it.

    Attributes:
        entry_id (int): The entry that printed the first byte of the match.
        last_entry_id (int): The entry that printed its last byte, where
            the match is complete.
        line (int): The line the match is on, counted from 1.
        offset (int): The offset of its first byte in the output, counted
            from 0.
        length (int): Its length in bytes.
        text (str): The line it is on, the whole of it even where the line
            goes on past the end of the search.
        column (int): The character of `text` the match begins at.
        column_end (int): The character of `text` it ends before.
        in_range (int): How many characters of `text` were printed inside
            the search's range: all of them but for a line its end cuts.
        source (str): The source that printed it, such as `ttyS0`. The line
            and the offset count within the sources that were searched.
        place (Place): `entry_id` as a place, with its time:
            `run.go(match.place)` stands where the match began to be
            printed.
    """

    entry_id: int
    last_entry_id: int
    line: int
    offset: int
    length: int
    text: str
    column: int
    column_end: int
    in_range: int
    source: str
    place: Place


def output_match(found: Any) -> OutputMatch:
    """The public form of one match the search reported."""
    return OutputMatch(
        entry_id=found.entry_id,
        last_entry_id=found.last_entry_id,
        line=found.line,
        offset=found.offset,
        length=found.length,
        text=found.text,
        column=found.column,
        column_end=found.column_end,
        in_range=found.in_range,
        source=found.source.name,
        place=Place(
            found.recorder, found.entry_id, Point(found.vtime), found.vtime_offset
        ),
    )


@dataclass(frozen=True)
class OutputSource(_Record):
    """One source that printed in a run, as `Output.sources` returns it.

    Attributes:
        name (str): The source, such as `ttyS1`: what `source=` takes.
        bytes (int): How many bytes it printed.
        lines (int): How many lines, counted in its own output, an
            unfinished last line included.
        entry_id (int): The entry that printed its first byte; 0 when
            nothing is stored.
        last_entry_id (int): The entry that printed its last byte; 0 when
            nothing is stored.
        stored (bool): Whether the run stored any byte of it. An output
            file the run drops, or one that was not written, is listed
            with nothing stored: its name is what `keep_output` takes.
    """

    name: str
    bytes: int
    lines: int
    entry_id: int
    last_entry_id: int
    stored: bool


@dataclass(frozen=True)
class OutputWrite(_Record):
    """Bytes one entry wrote to one source, as `Output.writes` returns it.

    Attributes:
        entry_id (int): The entry that wrote them.
        source (str): The source they were written to, such as
            `/output/keep/app/core.bin`.
        offset (int): Where the bytes begin in what the source printed,
            counted from 0. Each file of a directory is counted on its own.
        data (bytes): The bytes, exactly as written.
        tgid (int): The process that wrote them, as the guest numbers it;
            0 for bytes no task wrote, as a device's are.
        tid (int): The thread that wrote them; 0 for no task.
        task (str): The name the task had when it wrote, such as
            `postgres`; empty for no task.
    """

    entry_id: int
    source: str
    offset: int
    data: bytes
    tgid: int
    tid: int
    task: str


def output_write(write: Any) -> OutputWrite:
    """The public form of one write."""
    return OutputWrite(
        entry_id=write.entry_id,
        source=write.source.name,
        offset=write.offset,
        data=write.data,
        tgid=write.tgid,
        tid=write.tid,
        task=write.task,
    )


def output_source(printed: Any) -> OutputSource:
    """The public form of what one source printed."""
    return OutputSource(
        name=printed.source.name,
        bytes=printed.size,
        lines=printed.lines,
        entry_id=printed.entry_id,
        last_entry_id=printed.last_entry_id,
        stored=printed.stored,
    )


@dataclass(frozen=True)
class OutputLine(_Record):
    """One line of a run's output, as `Output.lines` returns it.

    Attributes:
        text (str): The line, without its newline.
        source (str): The source that printed it, such as `ttyS0`.
        line (int): Its number, counted from 1 within the sources that were
            read.
        entry_id (int): The entry that printed its first byte. A point:
            `run.go(f"#{line.entry_id}")` stands there.
        last_entry_id (int): The entry that printed its last byte, where
            the line is complete.
    """

    text: str
    source: str
    line: int
    entry_id: int
    last_entry_id: int


def output_lines(
    lines: list[str],
    ranges: list[tuple[int, int]],
    sources: list[Any],
    first_line: int,
) -> list[OutputLine]:
    """The public form of lines read with the entries and the source of
    each, numbered from `first_line`."""
    return [
        OutputLine(
            text=text.rstrip("\r"),
            source=source.name,
            line=first_line + index,
            entry_id=first,
            last_entry_id=last,
        )
        for index, (text, (first, last), source) in enumerate(
            zip(lines, ranges, sources)
        )
    ]


@dataclass(frozen=True)
class Sent(_Record):
    """What a send queued, as `Run.send` returns it.

    Attributes:
        queued (int): How many bytes were queued.
        device (str): The device they are for, or the path of the file.
        process (str or None): For a path, the id of the process that
            writes the file: `Run.process` says whether the bytes were
            written. None for a device.
    """

    queued: int
    device: str
    process: Optional[str]


@dataclass(frozen=True)
class ProfileStack(_Record):
    """One stack a profile sampled.

    Attributes:
        frames (list[str]): The name of the program, then its frames, the
            outermost first. A frame of the kernel ends in `_[k]`.
        count (int): How many samples had this stack.
    """

    frames: list[str]
    count: int


@dataclass(frozen=True)
class ProfileFunction(_Record):
    """One function of a profile.

    Attributes:
        name (str): The function.
        own (int): The samples the function itself was running in.
        total (int): The samples it was anywhere on the stack of.
    """

    name: str
    own: int
    total: int


@dataclass(frozen=True)
class Profile(_Record):
    """What `Run.profile` sampled.

    Attributes:
        samples (int): How many samples were taken. 0 for a run that was
            idle.
        hz (int): Samples a second on each CPU.
        stacks (list[ProfileStack]): Every stack, the most sampled first.
        functions (list[ProfileFunction]): Every function, the one most
            run in first.
        folded (list[str]): The stacks as text, `a;b;c <count>` a line:
            what flame graph tools read.
        left (bool): Whether the stretch had more samples than one profile
            takes. The profile is then of the first of them.
    """

    samples: int
    hz: int
    stacks: list[ProfileStack]
    functions: list[ProfileFunction]
    folded: list[str]
    left: bool


@dataclass(frozen=True)
class Container(_Record):
    """One container of a run, as `Run.containers` lists it.

    Attributes:
        name (str): The name of the container.
        service (str): The Compose service it is of, or an empty str for a
            container no service started.
        project (str): The Compose project of that service, or an empty
            str.
        state (str): `running`, `exited`, `created` or `paused`.
        status (str): The state with how long, such as `Up 2 minutes`.
        image (str): The image it runs.
        id (str): The id of the container.
        addresses (tuple[str, ...]): Its address on each network it is on;
            empty for a container that does not run.
        links (tuple[str, ...]): The interface of the machine at the other
            end of each of its links, which a capture names with `-i`;
            empty for a container that does not run, or one on the
            machine's network, on none or on another container's.
        cpus (float): The CPUs its cgroup allows, as `throttle` sets them;
            0 for no limit.
        memory_mb (int): The megabytes of memory its cgroup allows, as
            `memory` sets them; 0 for no limit.
        memory_soft_mb (int): The megabytes past which its cgroup reclaims
            and slows it, as `memory --soft` sets them; 0 for none.
        pids (int): The processes and threads it may have, as `pids` sets
            them; 0 for no limit.
        ports (str): The ephemeral ports of its network, as `ports` sets
            them, such as `40000-40009`; "" for the kernel's own range.
        files (int): The files each process may hold open, as `files` sets
            them; 0 for the container's own limit.
    """

    name: str
    service: str
    project: str
    state: str
    status: str
    image: str
    id: str
    addresses: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    cpus: float = 0.0
    memory_mb: int = 0
    memory_soft_mb: int = 0
    pids: int = 0
    ports: str = ""
    files: int = 0


@dataclass(frozen=True)
class ContainerFault(_Record):
    """A fault put on a container, as the methods of `Run.containers`
    return it.

    Attributes:
        verb (str): `stop`, `kill`, `crash`, `start`, `pause`, `resume`,
            `throttle` or `memory`.
        container (Container): The container as it was found, before the
            fault.
        after (int or None): Nanoseconds of the machine's time until what
            ends the fault acts: a start, a thaw, a limit set back. 0 when
            it acted at once, None when nothing ends it.
        process (str or None): The id of the process that ends it later,
            to read with `Run.process`; None when nothing waits.
        volumes (tuple of str): The volumes a crash cut, by name; empty
            for any other fault.
    """

    verb: str
    container: Container
    after: Optional[int]
    process: Optional[str]
    volumes: tuple[str, ...] = ()


def container(one: Any) -> Container:
    """A container, from the engine's."""
    return Container(
        name=one.name,
        service=one.service,
        project=one.project,
        state=one.state,
        status=one.status,
        image=one.image,
        id=one.id,
        addresses=one.addresses,
        links=one.links,
        cpus=one.cpus,
        memory_mb=one.memory_mb,
        memory_soft_mb=one.memory_soft_mb,
        pids=one.pids,
        ports=one.ports,
        files=one.files,
    )


def container_fault(acted: Any) -> ContainerFault:
    """A fault put on a container, from the engine's."""
    return ContainerFault(
        verb=acted.verb,
        container=container(acted.container),
        after=acted.after_ns,
        process=acted.later.id if acted.later else None,
        volumes=tuple(acted.volumes),
    )


@dataclass(frozen=True)
class Got(_Record):
    """A file read out of a run, as `Run.get` returns it.

    Attributes:
        size (int): How many bytes the file has.
        mode (int): The mode of the file, such as 0o644.
        uid (int): The user that owns it, as a number.
        gid (int): The group that owns it, as a number.
        data (bytes or None): The bytes of the file, when no destination
            was given. None when they were written to one.
        process (Process): The process that read the file in the run.
    """

    size: int
    mode: int
    uid: int
    gid: int
    data: Optional[bytes]
    process: "Process"


def sent(queued: Any) -> Sent:
    """The public form of what a send queued."""
    return Sent(
        queued=queued.queued,
        device=queued.device,
        process=queued.process_id or None,
    )


@dataclass(frozen=True)
class ProcessEvent(_Record):
    """One event of a process: a line it printed, or a change of its state.

    Attributes:
        stream (str): `stdout` or `stderr` for a line the command printed,
            `status` for a change of state.
        text (str): The line. A status line is `command <arguments>`,
            `started <pid>`, `exited <code>`, `killed <signal>` or
            `refused <reason>`.
        entry_id (int): The entry that recorded its first byte. A point:
            `run.go(f"#{event.entry_id}")` stands there.
        last_entry_id (int): The entry that recorded its last byte.
    """

    stream: str
    text: str
    entry_id: int
    last_entry_id: int


@dataclass(frozen=True)
class Process(_Record):
    """A command executed in a run, as `Run.exec` returns it.

    A process is read from what the run recorded, so it is a value: read it
    again with `Run.process` after the run has executed further.

    Attributes:
        id (str): The id of the process, which `Run.process` takes.
        state (str): `pending` until the run has taken the command, then
            `running`, and `exited`, `killed` or `refused` once it ended.
        command (str): The command and its arguments.
        pid (int or None): Its process id in the machine; None until it
            started.
        exit_code (int or None): Its exit status; None unless it exited.
        signal (int or None): The signal that ended it; None unless one did.
        reason (str): Why the command was refused; empty unless it was.
        started_entry (int or None): The entry that recorded its start.
        ended_entry (int or None): The entry that recorded its end.
        stdout (list[str]): The lines it printed on its standard output.
        stderr (list[str]): The lines it printed on its standard error.
        events (list[ProcessEvent]): Every line it printed and every change
            of its state, in the order recorded.
    """

    id: str
    state: str
    command: str
    pid: Optional[int]
    exit_code: Optional[int]
    signal: Optional[int]
    reason: str
    started_entry: Optional[int]
    ended_entry: Optional[int]
    stdout: list[str]
    stderr: list[str]
    events: list[ProcessEvent]

    @property
    def exits(self) -> list[ProcessEvent]:
        """list[ProcessEvent]: The event that ended the process, in a list;
        empty until it ended."""
        if self.ended_entry is None:
            return []
        return [event for event in self.events if event.stream == "status"][-1:]

    def exited_by(self, entry_id: int) -> bool:
        """Whether the process had ended at or before an entry.

        Args:
            entry_id (int): The entry to ask about.

        Returns:
            bool: True when the process ended at or before `entry_id`.
        """
        return self.ended_entry is not None and self.ended_entry <= entry_id


def process(found: Any) -> Process:
    """The public form of a process the engine read."""
    events = [
        ProcessEvent(
            stream=event.stream,
            text=event.text,
            entry_id=event.entry_id,
            last_entry_id=event.last_entry_id,
        )
        for event in found.events
    ]
    return Process(
        id=found.id,
        state=found.state,
        command=found.command,
        pid=found.pid,
        exit_code=found.exit_code,
        signal=found.signal,
        reason=found.reason,
        started_entry=found.started_entry,
        ended_entry=found.ended_entry,
        stdout=[event.text for event in events if event.stream == "stdout"],
        stderr=[event.text for event in events if event.stream == "stderr"],
        events=events,
    )


def exit_row(row: dict[str, Any]) -> Exit:
    """The public form of one exit the machine reported."""
    return Exit(
        tid=int(row["tid"]),
        tgid=int(row["tgid"]),
        exit_code=int(row["exit_code"]),
        signal=int(row["signal"]),
        status=int(row["status"]),
        outcome=str(row["outcome"]),
        comm=str(row["comm"]),
    )


#: A directory entry's file type -> the word `File.kind` says it with.
_KINDS: dict[int, str] = {
    _fs.FT_REG: "file",
    _fs.FT_DIR: "dir",
    _fs.FT_LNK: "link",
}


def file(entry: _ipc.FsEntry) -> File:
    """The public form of a directory entry."""
    return File(
        name=_fs.decode_name(entry.name),
        kind=_KINDS.get(entry.file_type, "other"),
        size=entry.size,
        mode=entry.mode,
        mtime=entry.mtime,
    )


def replay_result(done: _control.ReplayResult) -> ReplayResult:
    """The public form of a replay's result."""
    return ReplayResult(
        converged=done.converged,
        entry_id=done.entry_id,
        replayed=done.replayed,
        diverged=tuple(dict(row) for row in done.diverged),
        misses=tuple(
            Miss(entry_id=m.entry_id, rip=m.rip, rcb=m.rcb, regs_hash=m.regs_hash)
            for m in done.misses
        ),
        retry_stopped=done.retry_stopped,
        overshoot=done.overshoot,
    )


def run_result(done: _control.RunResult, *, reached: bool = True) -> RunResult:
    """The public form of a drive's result."""
    entries = tuple(
        Entry(vcpu=e.vcpu_id, entry_id=e.entry_id, exit=e.exit_reason_str, rip=e.rip)
        for e in done.entries
    )
    return RunResult(
        iterations=done.iterations_done,
        entry_id=max((e.entry_id for e in entries), default=0),
        entries=entries,
        reached=reached,
        vtime_elapsed=done.run_vtime,
        clauses=tuple(
            Clause(clause=c.clause, matched=c.matched) for c in done.plan_clauses
        ),
        hits=tuple(breakpoint(one) for one in done.hits),
        printed=tuple(done.printed),
    )


def checkpoint(cp: _branches.CheckpointEntry, run_id: str) -> Checkpoint:
    """The public form of a listed checkpoint of `run_id`."""
    return Checkpoint(
        name=cp.name,
        place=Place(
            cp.ancestor_run_id or run_id, cp.entry_id, Point(cp.vtime), cp.vtime_offset
        ),
        date=cp.date,
        durable=cp.durable,
    )


@dataclass(frozen=True)
class VcpuHashes(_Record):
    """One vCPU's state hashes, as `Run.hash` reads them.

    Attributes:
        vcpu (int): The vCPU.
        state (int): The hash of the whole machine, with this vCPU's state.
        regs_hash (int): Its general registers and flags: the hash a replay
            converges on.
        parts (dict[str, int]): Each part of its state by name: regs,
            events, xsave, debug, msrs, lapic, timers, pic and ioapic.
    """

    vcpu: int
    state: int
    regs_hash: int
    parts: dict[str, int]


@dataclass(frozen=True)
class Hashes(_Record):
    """The state hashes of a machine as it stood, as `Run.hash` reads them.

    Each hash compares with another machine's read at the same point,
    such as a recording's and its replay's. It does not compare with a
    recorded entry's: an entry hashes the machine before its exit is
    handled.

    Attributes:
        entry_id (int): The newest entry when the hashes were read.
        mem (int): All of guest memory.
        disk (int): The disk.
        devices (dict[str, int]): Each device by name: serial, i8042, rtc,
            rng, blk, console, pci and vesa.
        irq_pending (int): The interrupts `irq_delay` holds, with when
            each is due. 0 when it holds none.
        vcpus (tuple[VcpuHashes, ...]): Each vCPU's.
        vcpu_now (bool): The vCPU hashes are of each vCPU now. False on a
            host whose kernel cannot compute them: they are of each vCPU's
            last exit.
    """

    entry_id: int
    mem: int
    disk: int
    devices: dict[str, int]
    irq_pending: int
    vcpus: tuple[VcpuHashes, ...]
    vcpu_now: bool


def hashes(h: _ipc.MachineHashes) -> Hashes:
    """The public form of a machine's state hashes."""
    return Hashes(
        entry_id=h.entry_id,
        mem=h.mem_hash,
        disk=h.disk_hash,
        devices=dict(zip(_hashes.DEVICES, h.devices)),
        irq_pending=h.irq_pending,
        vcpus=tuple(
            VcpuHashes(
                vcpu=v.vcpu,
                state=v.state_hash,
                regs_hash=v.regs_hash,
                parts=dict(zip(_hashes.KERNEL, v.kernel)),
            )
            for v in h.vcpus
        ),
        vcpu_now=h.vcpu_now,
    )


def place(p: _branching.Place) -> Place:
    """The public form of an entry of a run's line."""
    return Place(p.run_id, p.entry_id, Point(p.vtime), p.vtime_offset)


def place_of(p: "Place | Checkpoint") -> _branching.Place:
    """The engine's form of a place, or of the place a checkpoint is at."""
    at = p.place if isinstance(p, Checkpoint) else p
    return _branching.Place(at.run_id, at.entry_id, at.point.vtime, at.vtime_offset)


def listing(row: _machines.RunRootRow | _machines.RunTreeRow) -> Listing:
    """The public form of a `runs` listing row."""
    if isinstance(row, _machines.RunRootRow):
        depth, running, runs = None, row.running_count, row.total_count
    else:
        depth, running, runs = row.depth, None, None
    return Listing(
        state=row.state,
        cpus=row.nrcpus,
        mem=row.ram_size // _BYTES_PER_MB,
        disk=row.disk,
        created_at=row.created_at,
        depth=depth,
        running=running,
        runs=runs,
        disk_bytes=row.disk_bytes,
        ram_bytes=row.ram_bytes,
        local=row.local,
    )


def state(info: _machines.MachineState) -> State:
    """The public form of a machine's state."""
    return State(
        state=info.state,
        activity=info.activity,
        cpus=info.nrcpus,
        vcpu=info.current_vcpu,
        mem=info.ram_size // _BYTES_PER_MB,
        entry_id=info.entry_id,
        vtime=info.vtime,
        vtime_offset=info.vtime_offset,
        host_cpu=info.pinned_cpu,
        switch_prev_tid=info.switch_prev_tid,
        switch_next_tid=info.switch_next_tid,
        plan=tuple(info.plan),
        plan_tally=tuple(info.plan_tally),
    )


def run_info(d: _ipc.DescribeInfo) -> RunInfo:
    """The public form of a run's description."""
    fs_sync, fs_dirsync = _machines.fs_flags_from_cmdline(d.kernel_cmdline)
    return RunInfo(
        id=d.run_id,
        name=d.name,
        state=d.state,
        cpus=d.nrcpus,
        mem=d.ram_size // _BYTES_PER_MB,
        mem_type=d.mem_type,
        mem_mode=d.mem_mode,
        disk_id=d.disk,
        fs_sync=fs_sync,
        fs_dirsync=fs_dirsync,
        rng=d.virtio_rng,
        rng_seed=d.rng_seed,
        limit=d.instruction_limit,
        vtime_base=d.vtime_base,
        vtime_rate=f"{d.vtime_rate_num}/{d.vtime_rate_den}",
        vtime_io_cost=d.vtime_io_cost,
        vtime_rdtsc_cost=d.vtime_rdtsc_cost,
        vtime_pause_cost=d.vtime_pause_cost,
        auto_checkpoints=d.auto_checkpoint_interval,
        side_dump=d.side_dump,
        side_trace=d.side_trace,
        mem_hash=d.mem_hash,
        disk_hash=d.disk_hash,
        parent_id=d.parent_run_id,
        parent_entry_id=d.parent_entry_id,
        labels=_machines.user_labels(d.labels),
        keep_output=list(d.output.keep),
        drop_output=list(d.output.drop),
        rdtsc_exit=d.rdtsc_exit,
        repeatable=d.repeatable,
        repeats_to=d.repeats_to if d.repeatable else None,
        host_breaks=d.host_breaks,
        limit_margin=d.limit_margin,
        timer_slack=d.timer_slack,
        instr_margin=d.instr_margin,
        dispatch_bound=d.dispatch_bound,
        fast_backstop=d.fast_backstop,
        irq_wait=d.irq_wait,
        guest_kernel=d.guest_kernel,
        machine_abi=d.machine_abi,
        machine_abi_max=d.machine_abi_max,
    )


@dataclass(frozen=True)
class TaskInfo(_Record):
    """One guest task, as `Tasks.__call__` lists it.

    A task is what the guest kernel schedules: a thread of a program, or
    the kernel's own. Its tgid names its thread group, the process it
    belongs to, so a program of one task has one row with both equal.

    Attributes:
        tid (int): The task's id.
        tgid (int): Its thread group's id.
        comm (str): The name the kernel knows it by.
        exe (str): The program it runs, as a path.
        cgroup (str): The cgroup it is in.
        start (int): When it started, in nanoseconds since the guest
            booted, by the guest's own clock. A tid is reused once its task
            exits; `tid` and `start` name one task.
        kernel (bool): Whether it is the kernel's own, which has no program
            and is never focused.
        focused (bool): Whether the focus covers it.
        held (bool): Whether a hold keeps it off the runqueue.
        yielded (bool): Whether a yield puts it behind its peers.
        pinned (bool): Whether it is pinned to a vCPU set.
        slice_ns (int): The slice it runs for, in nanoseconds; 0 for the
            kernel's own.
        on_cpu (bool): Whether it is on a vCPU right now.
        cpu (int, optional): Which vCPU, when it is on one.
        running (str, optional): `user` or `kernel`, when it is on one.
    """

    tid: int
    tgid: int
    comm: str
    exe: str
    cgroup: str
    start: int
    kernel: bool
    focused: bool
    held: bool
    yielded: bool
    pinned: bool
    slice_ns: int
    on_cpu: bool
    cpu: Optional[int]
    running: Optional[str]


@dataclass(frozen=True)
class Cgroup(_Record):
    """One cgroup of the guest's tasks, as `Tasks.cgroups` lists it.

    Attributes:
        path (str): The cgroup's path.
        tids (tuple[int, ...]): The threads in it.
        comms (tuple[str, ...]): Their names, sorted.
    """

    path: str
    tids: tuple[int, ...]
    comms: tuple[str, ...]


@dataclass(frozen=True)
class Op(_Record):
    """One operation of the session on a run, as `ops` lists it.

    An operation is a checkpoint, a branch or a switch: work the session
    carried out on a run, with how it ended.

    Attributes:
        op_id (int): The operation's id, unique in the session.
        type (str): `checkpoint`, `branch`, `switch` and so on.
        state (str): `running`, `unfinished`, `done` or `failed`.
        run_id (str): The run it acted on.
        target (str): What it produced, in its own words: the branch a fork
            created, the checkpoint a checkpoint named, the run a switch
            moved away from.
        entry_id (int): The entry it anchored at; 0 before that is known.
        started_at (int): When it started, in Unix seconds.
        error (str): Why it failed; empty when it did not.
    """

    op_id: int
    type: str
    state: str
    run_id: str
    target: str
    entry_id: int
    started_at: int
    error: str


@dataclass(frozen=True)
class StorageOp(_Record):
    """One operation on the account's storage, as `Storage.ops` lists it.

    A storage operation is a measurement, a prune, the check of one disk or
    the check of every stored object. It acts on the account, so it names no
    run.

    Attributes:
        op_id (int): The operation's id.
        type (str): `measure`, `prune`, `verify` or `check`.
        state (str): `pending`, `running`, `done` or `failed`.
        started_at (int): When it started, in Unix seconds.
        error (str): Why it failed; empty when it did not.
        progress (str): How far a running check has got, as
            `checked/objects`; empty for anything else.
    """

    op_id: int
    type: str
    state: str
    started_at: int
    error: str
    progress: str = ""


@dataclass(frozen=True)
class FocusInfo(_Record):
    """The tasks a run steers, as `Focus.__call__` reads them.

    Attributes:
        entries (tuple[str, ...]): What is focused: a program's `/path`, a
            `cgroup=P`, a `comm=NAME`, or a process `tgid=N`.
        counting (tuple[tuple[str, ...], ...]): What each entry counts, in the
            order of `entries`: "+syscalls", "+ints", both or neither.
        tgids (tuple[int, ...]): The processes the entries matched.
        held (tuple[int, ...]): The threads a hold keeps off the runqueue.
        yielded (tuple[int, ...]): The threads a yield puts behind.
        sliced (tuple[dict[str, int], ...]): The threads with a slice, each
            `{tid, units}`.
        pinned (tuple[int, ...]): The threads with a vCPU set to run on.
        marks (dict[str, int]): How many threads each mark is on, `held`,
            `yield` and `sliced`, counted by the machine. It includes threads
            that have exited, which the lists above no longer name.
        switches (int): Guest scheduling decisions seen while focused.
        io_blocks (int): Times a focused task blocked on I/O.
        generation (int): Rises whenever the focus changes, so two reads
            that agree can be told from two scans that happened to.
        truncated (bool): Whether more tasks matched than the machine can
            carry.
    """

    entries: tuple[str, ...]
    counting: tuple[tuple[str, ...], ...]
    tgids: tuple[int, ...]
    held: tuple[int, ...]
    yielded: tuple[int, ...]
    sliced: tuple[dict[str, int], ...]
    pinned: tuple[int, ...]
    marks: dict[str, int]
    switches: int
    io_blocks: int
    generation: int
    truncated: bool


@dataclass(frozen=True)
class VtimeInfo(_Record):
    """The guest's virtual clock, as `Vtime.__call__` read it.

    Attributes:
        vtime (int): The clock now, in nanoseconds since `base`.
        base (int): Where the clock started, in Unix seconds.
        rate_num (int): The rate's numerator.
        rate_den (int): The rate's denominator, so the rate is num/den of
            real time.
        io_cost (int): Nanoseconds an I/O, MMIO or hypercall exit adds to
            the clock.
        rdtsc_cost (int): Nanoseconds an RDTSC adds: the I/O cost while it
            follows it.
        pause_cost (int): Nanoseconds each PAUSE of a spin loop adds.
        rdtsc_follows_io (bool): Whether the RDTSC cost is the I/O cost.
        vcpu_rates (dict[int, tuple[int, int]]): Each vCPU that has its own
            rate, to that rate as (num, den); empty when all run at the
            machine's.
    """

    vtime: int
    base: int
    rate_num: int
    rate_den: int
    io_cost: int = 0
    rdtsc_cost: int = 0
    pause_cost: int = 0
    rdtsc_follows_io: bool = True
    vcpu_rates: dict[int, tuple[int, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class RngInfo(_Record):
    """The machine's RNG device, as `Rng.__call__` read it.

    Attributes:
        seed (int): The seed the device's generator is from: the run's
            `rng_seed`, or the last seed given since.
        guest_reseeds (bool): Whether the guest kernel reseeds its own
            generator from a seed at once. A guest kernel that does not
            takes it at its own next reseed.
    """

    seed: int
    guest_reseeds: bool


@dataclass(frozen=True)
class Vcpu(_Record):
    """Where a vCPU stopped, as `Run.vcpu` read it.

    `Run.state` answers what the machine is doing; this answers where one
    vCPU is and what the last drive did.

    Attributes:
        vcpu (int): The vCPU this describes.
        entry_id (int): The entry it recorded last.
        exit (str): Why it stopped, such as `INSTR_LIMIT` or `IO`.
        rip (int): The instruction address there.
        rcx (int): RCX there, which a REP loop counts down.
        cs_base (int): The code segment's base there.
        ic (int): Instructions in that dispatch.
        rcb (int): Retired conditional branches in it.
        total_ic (int): Instructions since the run began.
        regs_hash (int): The register hash replay compares.
        vtime (int): The virtual time there, in nanoseconds.
        vtime_offset (int): Orders the entries that share one `vtime`.
        iteration (int): The dispatch number within the last drive.
        iterations_done (int): Iterations the last drive ran.
        iterations_total (int): Iterations it was asked for.
        running (bool): Whether a drive is in flight now.
        ready (bool): Whether the machine accepts commands.
        checkpointing (bool): Whether a checkpoint is being written.
        until_satisfied (bool): Whether the last drive's predicate held.
        halted (bool): Whether the guest has shut itself down.
        synced (bool): Whether the tip is checkpointed, so it can be
            restored rather than replayed to.
    """

    vcpu: int
    entry_id: int
    exit: str
    rip: int
    rcx: int
    cs_base: int
    ic: int
    rcb: int
    total_ic: int
    regs_hash: int
    vtime: int
    vtime_offset: int
    iteration: int
    iterations_done: int
    iterations_total: int
    running: bool
    ready: bool
    checkpointing: bool
    until_satisfied: bool
    halted: bool
    synced: bool


@dataclass(frozen=True)
class Syscall(_Record):
    """One system call the guest kernel has, as `Run.syscalls` lists them.

    Attributes:
        number (int): Its number in the guest's table.
        name (str): Its name, such as `openat`.
    """

    number: int
    name: str


@dataclass(frozen=True)
class Vector(_Record):
    """One interrupt vector the guest kernel has, as `Run.ints` lists them.

    Attributes:
        number (int): The vector, 32-255.
        name (str): What arrives on it, such as `preempt`.
    """

    number: int
    name: str


@dataclass(frozen=True)
class PendingInterrupt(_Record):
    """One interrupt raised and not yet delivered, as `Irq.pending` lists them.

    Attributes:
        type (str): `PIN`, `MSI`, `TIMER_LAPIC`, `INIT`, `SIPI` or `IPI`.
        source (str): What raised it, as an `irq_delay` target names it:
            `timer`, `ipi`, `init`, `blk`, `rng`, `console`, `serial` or
            `keyboard`.
        vcpu (int): The vCPU it goes to.
        gsi (int): A pin's line, else 0.
        level (int): A pin's level, else 0.
        vector (int): An MSI's, an IPI's or a SIPI's vector, else 0.
        held (bool): Held by `irq_delay` until `due`. Otherwise it is
            delivered at the next dispatch.
        due (int): The machine time it is delivered at, in nanoseconds;
            0 when it is not held.
    """

    type: str
    source: str
    vcpu: int
    gsi: int
    level: int
    vector: int
    held: bool
    due: int


@dataclass(frozen=True)
class ClockJitter(_Record):
    """A jitter of the guest's date or a container's, as `Clock.jitter` set it.

    Attributes:
        choices (str): The choices, as `Clock.jitter` takes them.
        every (int): The machine time between two steps, in nanoseconds.
        seed (int): The seed the steps are drawn from.
        draws (int): The steps drawn so far.
        now (int): The draw in force, in nanoseconds from the date without it.
    """

    choices: str
    every: int
    seed: int
    draws: int
    now: int


@dataclass(frozen=True)
class ClockContainer(_Record):
    """A container whose date is off the guest's, as `ClockInfo` lists it.

    Attributes:
        id (str): The container's id, as `docker ps` prints it.
        name (str): The container's name; its id until the name is known.
        date (int): The container's date now, in nanoseconds since the epoch.
        date_text (str): The same, as `Clock.set` takes it.
        offset (int): How far its date is from the guest's, in nanoseconds.
        running (bool): Whether the container runs; a stopped one takes
            its offset again when it starts.
        pending (bool): Whether a `for_` undo is still to come.
        pending_vtime (int): The machine time it comes at; 0 for none.
        pending_offset (int): The offset it restores; 0 is the guest's date.
        jitter (ClockJitter, optional): Its jitter; None for none.
        drift (int): Its drift, in parts per billion of machine time.
        slew (int): The rate a slew adds until it arrives, in parts per
            billion; 0 for none.
        slew_to (int): The offset the slew arrives at, in nanoseconds.
        slew_vtime (int): The machine time it arrives at; 0 for none.
    """

    id: str
    name: str
    date: int
    date_text: str
    offset: int
    running: bool
    pending: bool
    pending_vtime: int
    pending_offset: int
    jitter: Optional[ClockJitter]
    drift: int
    slew: int
    slew_to: int
    slew_vtime: int


@dataclass(frozen=True)
class Link(_Record):
    """A container's link, as `Net.__call__` lists it: the faults on the
    packets the container sends to every peer, or on those a match takes.

    Attributes:
        container (str): The container that sends.
        service (str): Its Compose service; "" for none.
        peer (str): The container the packets go to; "" for any.
        proto (str): The protocol of the packets, `tcp`, `udp` or `icmp`,
            or `arp` for ARP frames; "" for any.
        port (int): Their source or destination port; 0 for any.
        syn (bool): Whether the match takes the TCP SYNs that open a
            connection alone.
        delay (int): Nanoseconds each packet waits.
        jitter (int): Nanoseconds each packet's delay differs by, at most.
        loss (int): The share of the packets lost, in parts per billion.
        duplicate (int): The share sent twice, in parts per billion.
        corrupt (int): The share with a bit changed, in parts per billion.
        reorder (int): The share sent ahead of delayed ones, in parts per
            billion.
        rate (int): Bits a second the link sends at most; 0 for no limit.
        held (bool): Whether the link holds its packets until a release.
        log (bool): Whether each decision of the fault goes to the
            container's file under `/output/drop/.machine/net`.
        seed (int): The seed the link draws from; 0 for a link with no
            fault that draws.
        unapplied (str): On a container's whole link, why its faults did
            not take when it last started; "" when they did.
        sent (int): The packets the fault sent on since it began, each
            duplicate counted once more.
        dropped (int): The packets it dropped since it began.
        queued (int): The packets it holds now, delayed or held: neither
            sent nor dropped yet.
    """

    container: str
    service: str
    peer: str
    proto: str
    port: int
    syn: bool
    delay: int
    jitter: int
    loss: int
    duplicate: int
    corrupt: int
    reorder: int
    rate: int
    held: bool
    log: bool
    seed: int
    unapplied: str
    sent: int
    dropped: int
    queued: int


def link(found: Any) -> Link:
    """The public form of a link."""
    return Link(
        container=found.container,
        service=found.service,
        peer=found.peer,
        proto=found.proto,
        port=found.port,
        syn=found.syn,
        delay=found.delay_ns,
        jitter=found.jitter_ns,
        loss=found.loss_ppb,
        duplicate=found.duplicate_ppb,
        corrupt=found.corrupt_ppb,
        reorder=found.reorder_ppb,
        rate=found.rate_bps,
        held=found.held,
        log=found.log,
        seed=found.seed,
        unapplied=found.unapplied,
        sent=found.sent,
        dropped=found.dropped,
        queued=found.queued,
    )


@dataclass(frozen=True)
class Neighbor(_Record):
    """An entry of a container's neighbour table, as `NetArp.show` lists it.

    Attributes:
        container (str): The container whose table it is.
        interface (str): The interface the entry is on, such as `eth0`.
        address (str): The peer's address.
        mac (str): The MAC the address maps to; `-` for none yet.
        state (str): `reachable`, `stale`, `delay`, `probe`, `failed`,
            `incomplete`, `permanent`, `noarp`, or `none` for an entry the
            kernel keeps no state of.
        peer (str): The container the address belongs to; "" for none.
    """

    container: str
    interface: str
    address: str
    mac: str
    state: str
    peer: str


def neighbor(found: Any) -> Neighbor:
    """The public form of a neighbour table entry."""
    return Neighbor(
        container=found.container,
        interface=found.interface,
        address=found.address,
        mac=found.mac,
        state=found.state,
        peer=found.peer,
    )


@dataclass(frozen=True)
class LinkChange(_Record):
    """What a change of a link did, as `Net`'s methods return it.

    Attributes:
        before (Link): The link as it was.
        after (Link): The link as it is.
        process (str, optional): The process that sets the link back after
            `for_`, which `Run.process` reads; None when it stays.
        for_ (int, optional): Nanoseconds of machine time until then.
    """

    before: Link
    after: Link
    process: Optional[str] = None
    for_: Optional[int] = None


def link_change(changed: Any) -> LinkChange:
    """The public form of a change of a link."""
    return LinkChange(
        before=link(changed.before),
        after=link(changed.after),
        process=changed.later.id if changed.later else None,
        for_=changed.for_ns,
    )


@dataclass(frozen=True)
class ClockRefused(_Record):
    """A clock change the guest kernel refused, as `Clock.__call__` names
    it. A change waits until the run next executes, so a refusal is read
    after the run that takes it.

    Attributes:
        command (str): The change, as `machine clock` takes it, such as
            `clock set 1970-01-01T00:00:01Z`.
        error (str): The errno, such as `ERANGE`.
        reason (str): Why the guest kernel refused it.
    """

    command: str
    error: str
    reason: str


@dataclass(frozen=True)
class ClockInfo(_Record):
    """The guest's date, as `Clock.__call__` reads it.

    Attributes:
        date (int): The guest's date now, in nanoseconds since the epoch.
        date_text (str): The same, as `Clock.set` takes it, such as
            `2027-03-28T00:59:50Z`.
        offset (int): How far the date is from machine time, in nanoseconds.
        pending (bool): Whether a `for_` undo is still to come.
        pending_vtime (int): The machine time it comes at; 0 for none.
        pending_offset (int): The offset it restores.
        waiting (int): Changes requested that the guest takes when the run
            next executes.
        error (str): The errno of the last `for_` undo the guest kernel
            could not make, such as `ERANGE`; "" for none.
        refused (tuple[ClockRefused, ...]): The changes of the guest's last
            pass that it refused, oldest first.
        containers (tuple[ClockContainer, ...]): Each container whose date
            is off the guest's, or the one container asked for.
        jitter (ClockJitter, optional): The guest's jitter; None for none.
        leap (str, optional): The leap second pending, `insert` or
            `delete`; None for none.
        leap_at (int): The UTC midnight it falls at, in nanoseconds since
            the epoch; 0 for none.
    """

    date: int
    date_text: str
    offset: int
    pending: bool
    pending_vtime: int
    pending_offset: int
    waiting: int
    error: str
    containers: tuple[ClockContainer, ...]
    jitter: Optional[ClockJitter]
    leap: Optional[str]
    leap_at: int
    refused: tuple[ClockRefused, ...] = ()


@dataclass(frozen=True)
class AxisDefault(_Record):
    """One default of the draw axes, as `Axis.__call__` lists them.

    Attributes:
        number (int): Its place in the order drives draw them, from 1.
        clause (str): The clause, as a plan file writes it, such as
            `when /app:on task-sched hold`.
        axes (tuple[str, ...]): The axes it names, such as `task-sched`.
        conditional (bool): Whether it applies only where its `when` holds.
    """

    number: int
    clause: str
    axes: tuple[str, ...]
    conditional: bool


@dataclass(frozen=True)
class Fault(_Record):
    """One syscall fault of a machine, as `Faults.__call__` lists them.

    Attributes:
        number (int): Its number in the list, from 1, which
            `Faults.delete` takes.
        site (str): The kernel site it fails, such as `disk`; "" for a
            syscall fault.
        syscall (str): The system call, as the guest kernel names it; ""
            for a site fault.
        nr (int): The system call's number, or the site's.
        errors (tuple[str, ...]): The errors its calls return, such as
            `ENOENT`, one drawn for each call it fires on; empty for a
            fault that only delays.
        errs (tuple[int, ...]): The errors' numbers.
        tid (int): The task whose calls it acts on; 0 for any.
        tgid (int): The tgid whose tasks' calls it acts on; 0 for any.
        path (str): The program whose tasks' calls it acts on; "" for any.
        comm (str): The comm whose tasks' calls it acts on; "" for any.
        after (int): How many matching calls go through first.
        times (int): How many calls it fires on at most; 0 for no end.
        prob (float): The chance a matching call fires it.
        seed (int): The seed of its draws.
        delay_ns (tuple[int, int]): The span a call is held for before
            its error, low and high nanoseconds; (0, 0) for none.
        matched (int): The calls it matched.
        fired (int): The calls it fired on: held, failed, or both.
        op (str): A disk fault's operation, `read` or `write`; "" for both.
        sectors (tuple[int, int] | None): A disk fault's sectors, the first
            and the last; None for the whole disk.
        file (str): A disk fault's file pattern; "" for any file.
        bytes (int): How many bytes of a request a `disk-corrupt` fault
            changes; 0 for another fault.
        on_ms (int): How long a cycling fault is active in each cycle, in
            milliseconds of machine time; 0 for a fault that does not cycle.
        off_ms (int): How long it then rests.
        active (bool): Whether a cycling fault is in an active window now;
            True for a fault that does not cycle.
        left_ns (int): The machine time left in that window, in
            nanoseconds; 0 for a fault that does not cycle.
        function (str): A function fault's function; "" for another.
        returns (int or None): The value a function fault returns in place
            of running; None for another fault.
    """

    number: int
    site: str
    syscall: str
    nr: int
    errors: tuple[str, ...]
    errs: tuple[int, ...]
    tid: int
    tgid: int
    path: str
    comm: str
    after: int
    times: int
    prob: float
    seed: int
    delay_ns: tuple[int, int]
    matched: int
    fired: int
    op: str
    sectors: Optional[tuple[int, int]]
    file: str
    bytes: int
    on_ms: int = 0
    off_ms: int = 0
    active: bool = True
    left_ns: int = 0
    function: str = ""
    returns: Optional[int] = None


@dataclass(frozen=True)
class PendingInterrupts(_Record):
    """Every interrupt a machine has raised and not yet delivered, as
    `Irq.pending` read them: the ones due at the next dispatch first, then
    the held ones in the order they were held.

    Attributes:
        vtime (int): The machine time when they were read, in nanoseconds.
        interrupts (tuple[PendingInterrupt, ...]): Each one.
    """

    vtime: int
    interrupts: tuple[PendingInterrupt, ...]


@dataclass(frozen=True)
class Count(_Record):
    """How often one system call or vector was taken, as the counts list it.

    Attributes:
        number (int): The system call's number, or the vector.
        name (str): Its name.
        count (int): How many times it was taken.
    """

    number: int
    name: str
    count: int


@dataclass(frozen=True)
class Counts(_Record):
    """What a task crossed, as `Syscalls.counts` and `Ints.counts` read it.

    Counting is opt-in: focus the program with `syscalls=True` or
    `ints=True` before the drive, or both lists are empty.

    Attributes:
        by_tgid (tuple[Count, ...]): The counts over every task of the
            tgid, most taken first.
        by_tid (tuple[Count, ...]): The counts of the one task, when a
            `tid` named it; empty otherwise.
    """

    by_tgid: tuple[Count, ...]
    by_tid: tuple[Count, ...]


@dataclass(frozen=True)
class Injected(_Record):
    """An interrupt the machine queued, as `Run.irq` injected it.

    Attributes:
        vector (int): The vector delivered.
        vcpu (int, optional): The vCPU it went to; None for every vCPU.
        nmi (bool): Whether it was delivered as a non-maskable interrupt.
    """

    vector: int
    vcpu: Optional[int]
    nmi: bool


@dataclass(frozen=True)
class LogEntry(_Record):
    """One entry of a run's log, as `Run.entries` reads them.

    An entry is one stop of a vCPU. The fields here are valid for every
    kind; what else an entry carries depends on its `event`, and `text` is
    the machine's own rendering of that detail.

    Attributes:
        entry_id (int): The entry's id, rising through the run.
        event (int): The event's number.
        event_name (str): Its name, such as `RUN`, `IRQ` or `EXIT_SHUTDOWN`.
        vcpu (int): The vCPU that stopped.
        flags (int): The entry's flag bits.
        flag_names (tuple[str, ...]): Those bits by name.
        ic (int): Instructions in the dispatch.
        rcb (int): Retired conditional branches in it.
        total_ic (int): Instructions since the run began.
        vtime (int): The virtual time there, in nanoseconds.
        vtime_offset (int): Orders the entries that share one `vtime`.
        rip (int): The instruction address in force there.
        regs_hash (int): The register hash replay compares.
        iter (int): The dispatch number within its drive.
        data_len (int): Bytes of event detail behind `text`.
        text (str): The entry as the machine renders it.
    """

    entry_id: int
    event: int
    event_name: str
    vcpu: int
    flags: int
    flag_names: tuple[str, ...]
    ic: int
    rcb: int
    total_ic: int
    vtime: int
    vtime_offset: int
    rip: int
    regs_hash: int
    iter: int
    data_len: int
    text: str


@dataclass(frozen=True)
class Cleanup(_Record):
    """What `Client.cleanup` found and cleared.

    Attributes:
        cleaned (int): Runs whose stale state was cleared.
        orphans (int): Machines that were running with no run to own them.
        run_ids (tuple[str, ...]): The runs cleaned.
    """

    cleaned: int
    orphans: int
    run_ids: tuple[str, ...]


@dataclass(frozen=True)
class Copy(_Record):
    """One run or disk a session holds, as `Offload.list` lists it.

    Attributes:
        id (str): The run id, or the disk id.
        name (str): Its name; empty for a disk kept by id alone.
        disk_bytes (int): What it takes on the session's storage.
        state (str): `offloadable` when nothing needs it there, else why it
            is kept.
    """

    id: str
    name: str
    disk_bytes: int
    state: str


@dataclass(frozen=True)
class Copies(_Record):
    """What a session holds, as `Offload.list` read it.

    Attributes:
        runs (tuple[Copy, ...]): The run folders.
        disks (tuple[Copy, ...]): The disk files.
    """

    runs: tuple[Copy, ...]
    disks: tuple[Copy, ...]


@dataclass(frozen=True)
class Rewound(_Record):
    """Where a run stands after `Run.rewind`, and what it discarded.

    Attributes:
        entry_id (int): The entry the run is back at, and its tip now.
        entries_dropped (int): Entries the discarded tail held.
    """

    entry_id: int
    entries_dropped: int


@dataclass(frozen=True)
class Prune(_Record):
    """A prune of the account's storage, as `Storage.prune` reports it.

    Attributes:
        op_id (int): The operation carrying it out.
        state (str): `requested`, `running`, `done` or `failed`.
        freed_bytes (int): What it freed, once it is done.
        started_at (str, optional): When it started, ISO 8601.
        error (str): Why it failed; empty when it did not.
    """

    op_id: int
    state: str
    freed_bytes: int
    started_at: Optional[str]
    error: str


@dataclass(frozen=True)
class StorageCheck(_Record):
    """A check of every object in the account's storage, as `Storage.check`
    reports it.

    Each stored object is read back and compared with its name, which is the
    SHA-256 of what it holds. The counts are filled in as the check goes.

    Attributes:
        op_id (int): The operation carrying it out.
        state (str): `requested`, `running`, `done` or `failed`.
        objects (int): The objects the storage listed.
        checked (int): Of those, the ones read so far.
        gone (int): The ones deleted while the check ran, which were not read.
        failed (int): The ones that could not be read, or read as something
            else.
        bytes (int): The bytes read.
        unreadable (list[str]): Each failed object: its key, what was wrong,
            and the storage's id for the failed request.
        started_at (str, optional): When it started, ISO 8601.
        finished_at (str, optional): When it ended, ISO 8601; None while it
            runs.
        error (str): Why it failed; empty when it did not.
    """

    op_id: int
    state: str
    objects: int
    checked: int
    gone: int
    failed: int
    bytes: int
    unreadable: list[str]
    started_at: Optional[str]
    finished_at: Optional[str]
    error: str


@dataclass(frozen=True)
class StorageInfo(_Record):
    """What the account stores, as `Storage.show` read it.

    The sizes are what the last measurement found; `state` says whether one
    is under way.

    Every size is in BYTES. `disks_bytes` is what the account's disks add
    up to; the rest is what the last measurement found in the store behind
    them, where a run and a disk share whatever content they have in common.

    Attributes:
        state (str): `ready` once measured, `measuring` while it is, `stale`
            when the sizes are old and no measurement is running.
        quota_bytes (int): What the plan allows.
        disks_bytes (int): What the account's disks add up to.
        bytes (int): What the store holds in all.
        pack_bytes (int): Of that, what the packs hold.
        referenced_bytes (int): What a run or a disk still refers to.
        prunable_bytes (int): What a prune would free: the packs less what is
            referenced.
        run_bytes (int): What the runs refer to.
        disk_bytes (int): What the disks refer to.
        measured_at (str, optional): When it was measured, ISO 8601; None
            before the first measurement.
        op_id (int): The measurement's operation id.
        prune (Prune, optional): The prune under way, or the last one; None
            when there has been none.
        failed_at (str, optional): When the newest measurement failed, ISO
            8601; None when the newest one did not fail. The sizes are then
            the last ones measured.
        failed_error (str): Why it failed; empty when it did not.
    """

    state: str
    quota_bytes: int
    disks_bytes: int
    bytes: int
    pack_bytes: int
    referenced_bytes: int
    prunable_bytes: int
    run_bytes: int
    disk_bytes: int
    measured_at: Optional[str]
    op_id: int
    prune: Optional[Prune]
    failed_at: Optional[str] = None
    failed_error: str = ""


@dataclass(frozen=True)
class Transfer(_Record):
    """One disk transfer, as `Disks.transfers` lists it.

    Attributes:
        transfer_id (str): The transfer's id.
        op (str): `upload`.
        name (str): The disk's name.
        sha256 (str): The disk's content hash.
        state (str): `reserving`, `transferring`, `committed`, `aborted` or
            `reclaiming`.
        size_bytes (int): The disk's size.
        bytes_done (int): Bytes transferred so far.
        rate_bps (int): The last measured rate, in bytes a second.
        created_at (str): When it started, ISO 8601.
        progress_at (str, optional): When it last advanced, ISO 8601.
        stopped_at (str, optional): When it ended, ISO 8601; None while it
            runs.
        error (str): Why it aborted; empty when it did not.
    """

    transfer_id: str
    op: str
    name: str
    sha256: str
    state: str
    size_bytes: int
    bytes_done: int
    rate_bps: int
    created_at: str
    progress_at: Optional[str]
    stopped_at: Optional[str]
    error: str


def task(row: dict[str, Any]) -> TaskInfo:
    """The public form of one task the machine reported."""
    cpu = row.get("cpu")
    running = row.get("next")
    return TaskInfo(
        tid=int(row["tid"]),
        tgid=int(row["tgid"]),
        comm=str(row["comm"]),
        exe=str(row.get("exe") or ""),
        cgroup=str(row.get("cgroup") or ""),
        start=int(row.get("start") or 0),
        kernel=bool(row.get("kernel")),
        focused=bool(row.get("focus")),
        held=bool(row.get("held")),
        yielded=bool(row.get("yield")),
        pinned=bool(row.get("pinned")),
        slice_ns=int(row.get("slice_ns") or 0),
        on_cpu=bool(row.get("on_cpu")),
        cpu=None if cpu is None else int(cpu),
        running=None if running is None else str(running),
    )


def cgroup(row: dict[str, Any]) -> Cgroup:
    """The public form of one cgroup grouping."""
    return Cgroup(
        path=str(row["cgroup"]),
        tids=tuple(int(t) for t in row.get("tids") or ()),
        comms=tuple(str(c) for c in row.get("comms") or ()),
    )


@dataclass(frozen=True)
class Registers(_Record):
    """The user registers of a guest task, as `Task.registers` reads
    them, and where they were read.

    Index it by a register's name for its value: `regs["rip"]`. The names
    are GDB's: rax to r15, rip, eflags, the segments and their bases,
    orig_rax, st0 to st7 and the x87 control registers, mxcsr, xmm0 to
    xmm15, and ymm0 to ymm15 on a guest with AVX. A value is unsigned.

    Example:
        >>> regs = run.task(412).registers()
        >>> hex(regs["rip"]), regs.where
        ('0x401ac6', 'user')

    Attributes:
        tid (int): The task.
        tgid (int): Its tgid.
        start (int): When it started, in nanoseconds since the guest
            booted, by the guest's own clock.
        where (str): `user` for a task on a vCPU in its own code,
            `kernel` for one on a vCPU in the kernel, whose user registers
            are the ones it entered with, and `saved` for a task off
            every vCPU, whose registers are the ones it saved.
        cpu (int, optional): The vCPU it is on, None when it is on none.
        values (dict[str, int]): Every register by its name.
    """

    tid: int
    tgid: int
    start: int
    where: str
    cpu: Optional[int]
    values: dict[str, int]

    def __getitem__(self, name: str) -> int:
        return self.values[name]


def registers(found: _debugger.Registers) -> Registers:
    """The public form of a task's registers."""
    return Registers(
        tid=found.tid,
        tgid=found.tgid,
        start=found.start,
        where=found.where.name.lower(),
        cpu=None if found.where == _ipc.TaskWhere.SAVED else found.cpu,
        values={one.name: one.value for one in found.registers},
    )


@dataclass(frozen=True)
class Symbol(_Record):
    """A function or a variable of a tgid, at an address of it.

    Attributes:
        address (int): The address asked about, or where the symbol begins.
        name (str): The symbol's name.
        offset (int): How far into the symbol `address` is.
        size (int): The symbol's size in bytes, 0 when its file says none.
        function (bool): A function, not a variable.
        path (str): The file that names it.
        source (str, optional): The source file `address` is in, None when
            the program was built without debug information.
        line (int, optional): The line of `source`.
    """

    address: int
    name: str
    offset: int
    size: int
    function: bool
    path: str
    source: Optional[str]
    line: Optional[int]


def symbol(found: _debugger.Symbol) -> Symbol:
    """The public form of a symbol."""
    return Symbol(
        address=found.addr,
        name=found.name,
        offset=found.offset,
        size=found.size,
        function=found.function,
        path=found.path,
        source=found.source,
        line=found.line,
    )


@dataclass(frozen=True)
class Frame(_Record):
    """One frame of a task's stack, as `Task.backtrace` lists them.

    Attributes:
        level (int): 0 for the innermost frame, 1 for its caller, and on.
        address (int): The address the frame executes at.
        stack (int): Its stack pointer.
        function (str, optional): The function it is in, None where no file
            the tgid maps names it.
        offset (int, optional): How far into the function.
        path (str, optional): The file the function is in.
        source (str, optional): The source file the frame executes in, None
            when the program was built without debug information.
        line (int, optional): The line of `source`.
    """

    level: int
    address: int
    stack: int
    function: Optional[str]
    offset: Optional[int]
    path: Optional[str]
    source: Optional[str]
    line: Optional[int]


@dataclass(frozen=True)
class Value(_Record):
    """The value of an expression in a frame of a task, as `Task.print`
    returns it.

    Attributes:
        expression (str): The expression, as it was given.
        type (str): Its type, as C writes it.
        value (str): The value, printed by the type: a struct by its members,
            an array by its elements, a pointer in hex.
        address (int, optional): Where it is, None for a value in a register
            or one an `&` or a cast made.
    """

    expression: str
    type: str
    value: str
    address: Optional[int] = None


def value(found: _variables.Value) -> Value:
    """The public form of a value."""
    return Value(
        expression=found.expression,
        type=found.type.spelled,
        value=found.text,
        address=found.address,
    )


@dataclass(frozen=True)
class Variable(_Record):
    """A local variable or an argument of a frame, as `Task.locals` lists
    them.

    Attributes:
        name (str): Its name.
        type (str): Its type, as C writes it.
        value (str): Its value, printed by the type, or why it is not read
            in angle brackets.
    """

    name: str
    type: str
    value: str


def frame(found: _debugger.Frame) -> Frame:
    """The public form of a stack frame."""
    return Frame(
        level=found.level,
        address=found.ip,
        stack=found.sp,
        function=found.function,
        offset=found.offset,
        path=found.path,
        source=found.source,
        line=found.line,
    )


@dataclass(frozen=True)
class Library(_Record):
    """The program or a library of a tgid, as `Task.libraries` lists
    them.

    Attributes:
        start (int): The first address the file is mapped at.
        end (int): The address after its last.
        offset (int): Where in the file the mapping begins.
        path (str): The file's path.
        on_disk (bool): The machine's disk has the file, so its symbols
            are read.
    """

    start: int
    end: int
    offset: int
    path: str
    on_disk: bool


@dataclass(frozen=True)
class Breakpoint(_Record):
    """A breakpoint or a watchpoint of a run, as `Run.breakpoints` lists
    them.

    Attributes:
        number (int): Its number. A number names one stop for the life of
            the run.
        kind (str): `breakpoint`, `watch write` or `watch access`.
        address (int): The address it stops at, or the first byte it
            watches.
        size (int): The bytes it watches; 1 for a breakpoint.
        tgid (int): The tgid it stops.
        program (str): The tgid's program.
        location (str): The location as it was given.
        symbol (str, optional): The symbol at the address.
        source (str, optional): The source line at the address, as
            `FILE:LINE`.
        enabled (bool): The machine arms it.
        temporary (bool): Its first hit that counts deletes it.
        hits (int): The hits that counted.
        ignore (int): The hits it passes before one ends a run.
        condition (str): Where a hit counts; empty for everywhere.
        print (str): What a hit prints in place of ending a run; empty
            for a stop that ends it.
        tid (int): A catchpoint's task; 0 for any task of its tgid.
        comm (str): A catchpoint's comm, the tasks of that name; empty for
            any.
        names (tuple[str, ...]): What a catchpoint names: its system
            calls or signals, or its path.
        event (str): The event a catchpoint stopped a run at, as the run
            says it; empty in a listing.
        hit_tid (int): In a hit of a breakpoint or a watchpoint, the tid
            it hit in; 0 in a listing.
        error (str): In a hit, why its condition was not tested or its
            print not made, which ended the run there; empty when
            nothing failed.
        old (int, optional): In a hit of a watchpoint, the value it
            watched before the write; None where it kept none.
        new (int, optional): In a hit of a watchpoint, the value now.
        disable_after (int): The hits that count left before it is
            disabled, as `Breakpoints.enable(count=N)` sets them; 0 for none.
        unplaced (str): Why a breakpoint of a tgid is not in the program,
            such as "not placed: the address maps no file"; empty once it
            is, and before the run has run since it was added.
    """

    number: int
    kind: str
    address: int
    size: int
    tgid: int
    program: str
    location: str
    symbol: Optional[str]
    source: Optional[str]
    enabled: bool = True
    temporary: bool = False
    hits: int = 0
    ignore: int = 0
    condition: str = ""
    print: str = ""
    tid: int = 0
    names: tuple[str, ...] = ()
    event: str = ""
    hit_tid: int = 0
    error: str = ""
    old: Optional[int] = None
    new: Optional[int] = None
    disable_after: int = 0
    comm: str = ""
    unplaced: str = ""


@dataclass(frozen=True)
class Stepped(_Record):
    """Where a step left a task, as `Task.step` and `Task.finish`
    return it.

    Attributes:
        tid (int): The task.
        steps (int): The steps it made. Fewer than asked when a breakpoint
            ended the step.
        address (int): The instruction it is at.
        symbol (str, optional): The symbol of the address.
        source (str, optional): The source line of the address, as
            `FILE:LINE`.
        hits (tuple[Breakpoint, ...]): The breakpoints and watchpoints that
            ended the step. Empty when it made all its steps.
        printed (tuple[str, ...]): The lines the stops that print printed
            while it ran.
        returned (int, optional): After `Task.finish`, rax, the register
            a function returns an integer or a pointer in; None after a
            step.
        line (int, optional): The number of the source line it is at.
        text (str, optional): The text of that line, from the source file
            where it is on this machine; None where it is not.
        caught (bool): An exception left the frame the step waited to
            return to, and the task is at the catch that took it. A
            finish then returns no value.
    """

    tid: int
    steps: int
    address: int
    symbol: Optional[str]
    source: Optional[str]
    hits: tuple["Breakpoint", ...] = ()
    printed: tuple[str, ...] = ()
    returned: Optional[int] = None
    line: Optional[int] = None
    text: Optional[str] = None
    caught: bool = False


@dataclass(frozen=True)
class Called(_Record):
    """What a call of a function in a task returned, as `Task.call`
    returns it.

    Attributes:
        tid (int): The task.
        function (str): The function called.
        returned (str, optional): The value it returned, as GDB prints it
            by the function's return type. None for a void function, and
            for a call a breakpoint abandoned.
        hits (tuple[Breakpoint, ...]): The breakpoints and watchpoints that
            abandoned the call. Empty when it returned.
    """

    tid: int
    function: str
    returned: Optional[str]
    hits: tuple["Breakpoint", ...]


def called(found: _stepping.Called) -> Called:
    """The public form of a call."""
    return Called(
        tid=found.tid,
        function=found.function,
        returned=found.returned,
        hits=tuple(breakpoint(one) for one in found.hits),
    )


def stepped(found: _stepping.Stepped) -> Stepped:
    """The public form of a step."""
    return Stepped(
        tid=found.tid,
        steps=found.steps,
        address=found.rip,
        symbol=found.symbol,
        source=found.source,
        hits=tuple(breakpoint(one) for one in found.hits),
        printed=tuple(found.printed),
        returned=found.returned,
        line=found.line_number,
        text=found.line_text,
        caught=found.caught,
    )


@dataclass(frozen=True)
class Hit(_Record):
    """A hit the run log keeps, as `Breakpoints.hits` lists them.

    Attributes:
        entry_id (int): The entry the run it ended ended at.
        vtime (int): Its machine time, in nanoseconds.
        number (int): The stop that hit.
        kind (str): What it is: `breakpoint`, `watch write`, `catch fork`.
        event (str): The event a catchpoint among them stopped at; empty
            for none.
    """

    entry_id: int
    vtime: int
    number: int
    kind: str
    event: str


def hit(found: _breakpoints.RecordedHit) -> Hit:
    """The public form of a hit the run log keeps."""
    return Hit(
        entry_id=found.entry_id,
        vtime=found.vtime,
        number=found.number,
        kind=found.kind,
        event=found.event,
    )


def breakpoint(found: _breakpoints.Breakpoint) -> Breakpoint:
    """The public form of a breakpoint or a watchpoint."""
    return Breakpoint(
        number=found.number,
        kind=found.kind,
        address=found.address,
        size=found.size,
        tgid=found.tgid,
        program=found.program,
        location=found.location,
        symbol=found.symbol,
        source=found.source,
        enabled=found.enabled,
        temporary=found.temporary,
        hits=found.hits,
        ignore=found.ignore,
        condition=found.condition,
        print=found.print,
        tid=found.tid,
        comm=found.comm,
        names=found.names,
        event=found.event,
        hit_tid=found.hit_tid,
        error=found.error,
        old=found.old,
        new=found.new,
        disable_after=found.disable_after,
        unplaced=found.unplaced,
    )


def library(found: _debugger.Library) -> Library:
    """The public form of the program or a library of a tgid."""
    return Library(
        start=found.start,
        end=found.end,
        offset=found.offset,
        path=found.path,
        on_disk=found.on_disk,
    )


def op(row: dict[str, Any]) -> Op:
    """The public form of one of the session's operations."""
    return Op(
        op_id=int(row["op_id"]),
        type=str(row["op_type"]),
        state=str(row["state"]),
        run_id=str(row.get("run_id") or ""),
        target=str(row.get("name") or ""),
        entry_id=int(row["entry"]) if row.get("entry") else 0,
        started_at=int(row.get("started_at") or 0),
        error=str(row.get("error") or ""),
    )


def storage_op(row: dict[str, Any]) -> StorageOp:
    """The public form of one storage operation."""
    return StorageOp(
        op_id=int(row["op_id"]),
        type=str(row["op_type"]),
        state=str(row["state"]),
        started_at=int(row.get("started_at") or 0),
        error=str(row.get("error") or ""),
        progress=str(row.get("progress") or ""),
    )


def focus(doc: dict[str, Any]) -> FocusInfo:
    """The public form of a run's focus."""
    return FocusInfo(
        entries=tuple(str(e) for e in doc.get("entries") or ()),
        counting=tuple(tuple(str(w) for w in c) for c in doc.get("counting") or ()),
        tgids=tuple(int(t) for t in doc.get("tgids") or ()),
        held=tuple(int(t) for t in doc.get("held") or ()),
        yielded=tuple(int(t) for t in doc.get("yield") or ()),
        sliced=tuple(dict(s) for s in doc.get("sliced") or ()),
        pinned=tuple(int(p) for p in doc.get("pinned") or ()),
        marks={str(k): int(v) for k, v in (doc.get("marks") or {}).items()},
        switches=int(doc.get("switches") or 0),
        io_blocks=int(doc.get("io_blocks") or 0),
        generation=int(doc.get("generation") or 0),
        truncated=bool(doc.get("truncated")),
    )


def pending_interrupts(doc: dict[str, Any]) -> PendingInterrupts:
    """The public form of a machine's pending interrupts."""
    return PendingInterrupts(
        vtime=int(doc.get("vtime") or 0),
        interrupts=tuple(
            PendingInterrupt(
                type=str(i["type"]),
                source=str(i["source"]),
                vcpu=int(i["vcpu"]),
                gsi=int(i["gsi"]),
                level=int(i["level"]),
                vector=int(i["vector"]),
                held=bool(i["held"]),
                due=int(i["due"]),
            )
            for i in doc.get("pending") or ()
        ),
    )


def clock(doc: dict[str, Any]) -> ClockInfo:
    """The public form of the guest's date."""
    return ClockInfo(
        date=int(doc["date"]),
        date_text=str(doc["date_text"]),
        offset=int(doc["offset"]),
        pending=bool(doc["pending"]),
        pending_vtime=int(doc["pending_vtime"]),
        pending_offset=int(doc["pending_offset"]),
        waiting=int(doc["requested"]) - int(doc["applied"]),
        error=str(doc["error"]),
        containers=tuple(
            ClockContainer(
                id=str(one["id"]),
                name=str(one["name"]),
                date=int(one["date"]),
                date_text=str(one["date_text"]),
                offset=int(one["offset"]),
                running=bool(one["running"]),
                pending=bool(one["pending"]),
                pending_vtime=int(one["pending_vtime"]),
                pending_offset=int(one["pending_offset"]),
                jitter=_clock_jitter(one["jitter"]),
                drift=int(one["drift"]),
                slew=int(one["slew"]),
                slew_to=int(one["slew_to"]),
                slew_vtime=int(one["slew_vtime"]),
            )
            for one in doc["containers"]
        ),
        jitter=_clock_jitter(doc["jitter"]),
        leap=doc["leap"],
        leap_at=int(doc["leap_at"]),
        refused=tuple(ClockRefused(**one) for one in doc["refused"]),
    )


def _clock_jitter(doc: Optional[dict[str, Any]]) -> Optional[ClockJitter]:
    if doc is None:
        return None
    return ClockJitter(
        choices=str(doc["choices"]),
        every=int(doc["every"]),
        seed=int(doc["seed"]),
        draws=int(doc["draws"]),
        now=int(doc["now"]),
    )


def axis_defaults(rows: list[dict[str, Any]]) -> tuple[AxisDefault, ...]:
    """The public form of a run's defaults of the draw axes."""
    return tuple(
        AxisDefault(
            number=int(r["number"]),
            clause=str(r["clause"]),
            axes=tuple(str(a) for a in r["axes"]),
            conditional=bool(r["conditional"]),
        )
        for r in rows
    )


def faults(rows: list[dict[str, Any]]) -> tuple[Fault, ...]:
    """The public form of a machine's syscall faults."""
    return tuple(
        Fault(
            number=int(r["number"]),
            site=str(r["site"]),
            syscall=str(r["syscall"]),
            nr=int(r["nr"]),
            errors=tuple(str(e) for e in r["errors"]),
            errs=tuple(int(e) for e in r["errs"]),
            tid=int(r["tid"]),
            tgid=int(r["tgid"]),
            path=str(r["path"]),
            comm=str(r["comm"]),
            after=int(r["after"]),
            times=int(r["times"]),
            prob=float(r["prob"]),
            seed=int(r["seed"]),
            delay_ns=(int(r["delay_ns"][0]), int(r["delay_ns"][1])),
            matched=int(r["matched"]),
            fired=int(r["fired"]),
            op=str(r["op"]),
            sectors=(
                (int(r["sectors"][0]), int(r["sectors"][1]))
                if r["sectors"] is not None
                else None
            ),
            file=str(r["file"]),
            bytes=int(r["bytes"]),
            on_ms=int(r["on_ms"]),
            off_ms=int(r["off_ms"]),
            active=bool(r["active"]),
            left_ns=int(r["left_ns"]),
            function=str(r["function"]),
            returns=None if r["return"] is None else int(r["return"]),
        )
        for r in rows
    )


def rng(doc: dict[str, Any]) -> RngInfo:
    """The public form of the RNG device's seed."""
    return RngInfo(
        seed=int(doc.get("seed") or 0), guest_reseeds=bool(doc.get("guest_reseeds"))
    )


def vtime(doc: dict[str, Any]) -> VtimeInfo:
    """The public form of the guest's clock."""
    return VtimeInfo(
        vtime=int(doc.get("vtime") or 0),
        base=int(doc.get("vtime_base") or 0),
        rate_num=int(doc.get("rate_num") or 0),
        rate_den=int(doc.get("rate_den") or 0),
        io_cost=int(doc.get("io_cost") or 0),
        rdtsc_cost=int(doc.get("rdtsc_cost") or 0),
        pause_cost=int(doc.get("pause_cost") or 0),
        rdtsc_follows_io=bool(doc.get("rdtsc_follows_io", True)),
        vcpu_rates={
            int(r["vcpu"]): (int(r["num"]), int(r["den"]))
            for r in doc.get("vcpu_rates") or []
        },
    )


def vcpu(doc: dict[str, Any]) -> Vcpu:
    """The public form of where a vCPU stopped."""
    one = dict(doc.get("vcpu") or {})
    machine = dict(doc.get("machine") or {})
    return Vcpu(
        vcpu=int(one.get("last_vcpu_id") or 0),
        entry_id=int(one.get("last_entry_id") or 0),
        exit=str(one.get("last_exit_reason_str") or ""),
        rip=int(one.get("last_rip") or 0),
        rcx=int(one.get("last_rcx") or 0),
        cs_base=int(one.get("last_cs_base") or 0),
        ic=int(one.get("last_instruction_count") or 0),
        rcb=int(one.get("last_retired_cond_branches") or 0),
        total_ic=int(one.get("last_total_ic") or 0),
        regs_hash=int(one.get("last_regs_hash") or 0),
        vtime=int(one.get("last_vtime") or 0),
        vtime_offset=int(one.get("last_vtime_offset") or 0),
        iteration=int(one.get("last_iteration") or 0),
        iterations_done=int(one.get("iterations_done") or 0),
        iterations_total=int(one.get("iterations_total") or 0),
        running=bool(one.get("run_pending")),
        ready=bool(one.get("ready")),
        checkpointing=bool(one.get("checkpoint_active")),
        until_satisfied=bool(one.get("until_satisfied")),
        halted=bool(machine.get("halted")),
        synced=bool(machine.get("synced")),
    )


def syscall(row: dict[str, Any]) -> Syscall:
    """The public form of one of the guest kernel's system calls."""
    return Syscall(number=int(row["nr"]), name=str(row["name"]))


def vector(row: dict[str, Any]) -> Vector:
    """The public form of one of the guest kernel's interrupt vectors."""
    return Vector(number=int(row["vector"]), name=str(row["name"]))


def _count(row: dict[str, Any]) -> Count:
    """One row of a histogram."""
    return Count(
        number=int(row.get("number") or 0),
        name=str(row.get("name") or ""),
        count=int(row.get("count") or 0),
    )


def counts(doc: dict[str, Any]) -> Counts:
    """The public form of what a task crossed."""
    return Counts(
        by_tgid=tuple(_count(r) for r in doc.get("by_tgid") or ()),
        by_tid=tuple(_count(r) for r in doc.get("by_tid") or ()),
    )


def log_entry(record: Any) -> LogEntry:
    """The public form of one run log entry."""
    return LogEntry(
        entry_id=record.entry_id,
        event=record.event,
        event_name=record.event_name,
        vcpu=record.vcpu,
        flags=record.flags,
        flag_names=tuple(record.flag_names),
        ic=record.ic,
        rcb=record.rcb,
        total_ic=record.total_ic,
        vtime=record.vtime,
        vtime_offset=record.vtime_offset,
        rip=record.rip,
        regs_hash=record.regs_hash,
        iter=record.iter,
        data_len=record.data_len,
        text=record.text,
    )


def cleanup(doc: dict[str, Any]) -> Cleanup:
    """The public form of a cleanup's outcome."""
    return Cleanup(
        cleaned=int(doc.get("cleaned") or 0),
        orphans=int(doc.get("orphans") or 0),
        run_ids=tuple(str(r) for r in doc.get("run_ids") or ()),
    )


def _copy(row: dict[str, Any]) -> Copy:
    """One run or disk a session holds."""
    return Copy(
        id=str(row.get("run_id") or row.get("disk_id") or ""),
        name=str(row.get("name") or ""),
        disk_bytes=int(row.get("disk_bytes") or 0),
        state=str(row.get("local") or ""),
    )


def copies(doc: dict[str, Any]) -> Copies:
    """The public form of what a session holds."""
    return Copies(
        runs=tuple(_copy(r) for r in doc.get("runs") or ()),
        disks=tuple(_copy(d) for d in doc.get("disks") or ()),
    )


def prune(doc: dict[str, Any]) -> Prune:
    """The public form of a prune of the account's storage."""
    return Prune(
        op_id=int(doc.get("op_id") or 0),
        state=str(doc.get("state") or ""),
        freed_bytes=int(doc.get("freed_bytes") or 0),
        started_at=_str_or_none(doc.get("started_at")),
        error=str(doc.get("error") or ""),
    )


def storage_check(doc: dict[str, Any]) -> StorageCheck:
    """The public form of a check of the account's storage."""
    return StorageCheck(
        op_id=int(doc.get("op_id") or 0),
        state=str(doc.get("state") or ""),
        objects=int(doc.get("objects") or 0),
        checked=int(doc.get("checked") or 0),
        gone=int(doc.get("gone") or 0),
        failed=int(doc.get("failed") or 0),
        bytes=int(doc.get("bytes") or 0),
        unreadable=[str(line) for line in doc.get("unreadable") or []],
        started_at=_str_or_none(doc.get("started_at")),
        finished_at=_str_or_none(doc.get("finished_at")),
        error=str(doc.get("error") or ""),
    )


def storage(doc: dict[str, Any]) -> StorageInfo:
    """The public form of what the account stores."""
    running = doc.get("prune")
    return StorageInfo(
        state=str(doc.get("state") or ""),
        quota_bytes=int(doc.get("quota_bytes") or 0),
        disks_bytes=int(doc.get("disks_bytes") or 0),
        bytes=int(doc.get("bytes") or 0),
        pack_bytes=int(doc.get("packs") or 0),
        referenced_bytes=int(doc.get("referenced") or 0),
        prunable_bytes=int(doc.get("prunable") or 0),
        run_bytes=int(doc.get("runs") or 0),
        disk_bytes=int(doc.get("disks") or 0),
        measured_at=_str_or_none(doc.get("measured_at")),
        op_id=int(doc.get("op_id") or 0),
        prune=prune(running) if isinstance(running, dict) else None,
        failed_at=_str_or_none(doc.get("failed_at")),
        failed_error=str(doc.get("failed_error") or ""),
    )


def transfer(row: dict[str, Any]) -> Transfer:
    """The public form of one disk transfer."""
    return Transfer(
        transfer_id=str(row.get("transfer_id") or ""),
        op=str(row.get("op") or ""),
        name=str(row.get("name") or ""),
        sha256=str(row.get("sha256") or ""),
        state=str(row.get("state") or ""),
        size_bytes=int(row.get("size_bytes") or 0),
        bytes_done=int(row.get("bytes_done") or 0),
        rate_bps=int(row.get("rate_bps") or 0),
        created_at=str(row.get("created_at") or ""),
        progress_at=_str_or_none(row.get("progress_at")),
        stopped_at=_str_or_none(row.get("stopped_at")),
        error=str(row.get("error") or ""),
    )


def _str_or_none(value: Any) -> Optional[str]:
    """A string field that the service may leave out."""
    return None if value is None else str(value)


@dataclass(frozen=True)
class Recovery(_Record):
    """What `Delete.cleanup` resumed, as it reports it.

    A delete that was interrupted leaves a marker; cleanup finishes those.

    Attributes:
        recovered (int): Deletes it finished.
        skipped (int): Markers it left alone, such as one a live delete
            still owns.
        errors (tuple[str, ...]): What went wrong, one message each.
    """

    recovered: int
    skipped: int
    errors: tuple[str, ...]


def recovery(doc: dict[str, Any]) -> Recovery:
    """The public form of a cleanup's outcome."""
    return Recovery(
        recovered=int(doc.get("recovered") or 0),
        skipped=int(doc.get("skipped") or 0),
        errors=tuple(str(e) for e in doc.get("errors") or ()),
    )
