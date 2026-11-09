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
"""Checks and conversions of arguments, shared by the SDK's modules."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional, TypeVar

from testflows.machine._core import console_source
from testflows.machine._core import drive
from testflows.machine._core import ipc
from testflows.machine._core import log_lines
from testflows.machine._core import machines
from testflows.machine._core import parsers
from testflows.machine._core import poll
from testflows.machine._core import timespec
from testflows.machine._core import wait
from testflows.machine._core import wire

from .errors import InvalidArgument, _translated

T = TypeVar("T")
R = TypeVar("R")

#: The `lines` that selects none: the `-n` grammar has no spelling for it.
NO_LINES = "0"

#: The host CPUs a machine may be pinned to, the bounds the CLI holds
#: `--pin-cpu` to. -1 pins nowhere, which lets the machine pick.
MIN_PIN_CPU = -1
MAX_PIN_CPU = 1024


def watched(
    read: Callable[[], R],
    present: Callable[[R], T],
    watch: bool,
    timeout: Optional[float],
) -> "T | Generator[T, None, None]":
    """`present(read())`, or with `watch` that of each changed `read()` until
    `timeout`.

    Changes are judged on what `read` returns, not on what `present` makes of
    it: a handle compares by identity, so a run whose state changed is the
    same handle, and a watch over handles would never see the change.
    """
    if watch:
        changes = poll.changes(read, interval=poll.WATCH_INTERVAL, timeout=timeout)
        return (present(value) for value in changes)
    return present(read())


def same(value: T) -> T:
    """`value`: the `present` of a read whose values are what it returns."""
    return value


def span(value: "int | str | None") -> Optional[parsers.Span]:
    """A drawn value, `n` or `lo:hi[:strategy[:step]]`, or None."""
    return None if value is None else parsers.span_type(str(value))


def timer_slack_span(value: "int | str | None") -> Optional[parsers.Span]:
    """A drawn timer slack for `Run.run`, `2ms` or `0:2ms[:strategy[:step]]`,
    at most a second, or None. InvalidArgument, naming the argument, when it
    is not. `timer_slack` below is the run's own, one value for every run."""
    if value is None:
        return None
    try:
        return parsers.timer_slack_type(str(value))
    except argparse.ArgumentTypeError as exc:
        raise InvalidArgument(f"timer_slack: {exc}") from None


def irq_delay(value: Optional[str]) -> Optional[str]:
    """`[target=]span,...`, each span at most a second, or None.
    InvalidArgument, naming the argument, when it is not."""
    if value is None:
        return None
    try:
        return parsers.irq_delay_type(str(value))
    except argparse.ArgumentTypeError as exc:
        raise InvalidArgument(f"irq_delay: {exc}") from None


def text(value: "int | str | None") -> Optional[str]:
    """A clause word's value as text, which the run's plan grammar checks
    when the run sends it: a number of nanoseconds becomes its digits."""
    return None if value is None else str(value)


def within(value: Optional[str]) -> Optional[int]:
    """A bound in machine time, a duration such as `10s`, in nanoseconds;
    None for none. InvalidArgument when it is no duration."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidArgument(
            f"within must be a duration as a str, such as '10s', got {value!r}."
        )
    try:
        return drive.within(value)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None


def limit_margin(value: Optional[int]) -> Optional[int]:
    """Branches short of a stop where the branch counter interrupts; None for
    the default. InvalidArgument outside 1 to the largest the run keeps."""
    return bounded("limit_margin", value, 1, wire.LIMIT_MARGIN_MAX)


def timer_slack(value: Optional[str]) -> Optional[int]:
    """How late a timer may be, `0` or a duration such as `20ms`, in
    nanoseconds; None for the default. InvalidArgument when it is neither."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidArgument(
            f"timer_slack must be a duration as a str, such as '20ms', got {value!r}."
        )
    if value.strip() == "0":
        return 0
    try:
        return parsers.parse_duration_ns(value)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None


def dispatch_bound(value: Optional[str]) -> Optional[int]:
    """The host time a fast dispatch may run, `0` for the default or a
    duration such as `500ms` or `30s`, in nanoseconds; None for the default."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidArgument(
            f"dispatch_bound must be a duration as a str, such as '500ms', got {value!r}."
        )
    try:
        return parsers.dispatch_bound_ns(value)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None


def stop_count(name: str, value: Optional[int]) -> Optional[int]:
    """An instruction margin or fast backstop, 0 for the default; None for
    the default too. InvalidArgument past what the counter holds."""
    return bounded(name, value, 0, wire.FAST_BACKSTOP_MAX)


def irq_wait(value: Optional[int]) -> Optional[int]:
    """Branches a dispatch runs while an interrupt waits, 0 or None for the
    default. InvalidArgument past what the counter holds."""
    return bounded("irq_wait", value, 0, wire.IRQ_WAIT_MAX)


def bounded(
    name: str, value: Optional[int], lo: int, hi: Optional[int] = None
) -> Optional[int]:
    """`value`, refused when it is outside the bounds the CLI holds it to.

    One check, so a number the service would refuse is refused here with the
    range in the message, rather than travelling to fail there.
    """
    if value is None:
        return None
    if value < lo or (hi is not None and value > hi):
        limit = f"{lo}-{hi}" if hi is not None else f"{lo} or more"
        raise InvalidArgument(f"{name} must be {limit}, got {value}.")
    return value


def positive(name: str, value: float) -> float:
    """`value`, refused when it is not above zero: an interval of 0 would spin
    and a negative one names no time at all."""
    if value <= 0:
        raise InvalidArgument(f"{name} must be more than 0, got {value}.")
    return value


def tid(value: int) -> int:
    """A thread id, checked as the CLI checks it."""
    with _translated():
        return int(parsers.tid_type(str(value)))


def focus_entry(value: str) -> str:
    """One focus entry, checked as the CLI checks it."""
    with _translated():
        return str(parsers.focus_entry_type(value))


def disk_ref(value: Any) -> str:
    """A disk by name, from a name or from the `Disk` itself.

    A run, a session and a disk are all named by their object wherever
    their name is taken, so a program never has to remember which.
    """
    name = getattr(value, "name", None)
    return str(value) if name is None else str(name)


def vcpus(value: "int | str | None") -> Optional[parsers.Span]:
    """A vCPU or a span of them, `n`, `lo:hi`, `lo:` or `:hi`, or None."""
    return None if value is None else parsers.vcpu_span_type(str(value))


def predicate(text: str, *, when: bool = False) -> wait.Predicate:
    """The predicate `text` names; InvalidArgument naming the spelling to
    use when it is not one. `when` refuses it as a state."""
    try:
        return wait.parse(text, when=when)
    except wait.UseOn as exc:
        raise InvalidArgument(f"{exc} Write {exc.replacement}") from None
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None


def check_choice(option: str, value: Optional[str], choices: tuple[str, ...]) -> None:
    """InvalidArgument when `value` is given and not one of `choices`."""
    if value is not None and value not in choices:
        raise InvalidArgument(
            f"{option} must be one of {', '.join(choices)}, got {value!r}."
        )


@dataclass(frozen=True)
class OpFilters:
    """The state and type filters an operations listing sends, None for all."""

    states: Optional[list[str]]
    types: Optional[list[str]]


def op_filters(
    op_id: Optional[int],
    states: Optional[list[str]],
    types: Optional[list[str]],
    type_choices: tuple[str, ...],
    state_choices: tuple[str, ...] = machines.OP_STATE_CHOICES,
) -> OpFilters:
    """Check and resolve an operations listing's filters. An `op_id` names
    one operation, so it sends neither."""
    for term in states or []:
        check_choice("state", term.removeprefix("^"), state_choices)
    for kind in types or []:
        check_choice("type", kind.removeprefix("^"), type_choices)
    if op_id is not None:
        return OpFilters(states=None, types=None)
    return OpFilters(
        states=machines.resolve_op_states(states, state_choices),
        types=machines.resolve_op_types(types, type_choices),
    )


def lines_spec(lines: "int | str | None") -> Optional[str]:
    """`lines` in the range grammar: an int N is line N, -N the last N, and 0
    `NO_LINES`, which starts at the end of the log."""
    if lines is None:
        return None
    if isinstance(lines, int):
        return NO_LINES if lines == 0 else str(lines)
    return lines


def window(
    read: log_lines.ReadFn, lines: "int | str | None", *, follow: bool = False
) -> log_lines.Window:
    """The lines `lines` selects, read; 0 is none, at the end of the log."""
    spec = lines_spec(lines)
    if spec == NO_LINES:
        return log_lines.end(read)
    return log_lines.read_window(read, spec, follow=follow)


def source(name: str) -> console_source.Source:
    """The source of output `name` names: `console`, `all`, a serial port
    such as `ttyS1`, `hvc0`, `debugcon`, or an output file or a directory
    of them by its path."""
    try:
        return console_source.parse(name)
    except ValueError:
        raise InvalidArgument(
            f"No such source: {name}. A source is one of {console_source.NAMES}."
        ) from None


def point(name: str, value: Optional[str]) -> Optional[str]:
    """A point given as text, as an argument named `name` takes one: a str,
    or None for the argument's default. An int is refused, since a bare
    number and an entry are different points: an entry is `"#300"`, and a
    time is a `Point`."""
    if isinstance(value, str) and not value.strip():
        # Read as no point, an empty one forked here and now.
        raise InvalidArgument(f"{name}: {timespec.EMPTY_POINT}.")
    if value is None or isinstance(value, str):
        return value
    raise InvalidArgument(
        f"{name} must be a point: a str such as '^booted' or '#300', a Point,"
        f" a Place or a Checkpoint, got {value!r}."
    )


def stored(
    keep: "Sequence[str] | str | None", drop: "Sequence[str] | str | None"
) -> ipc.Output:
    """What a run stores of its output besides what the directories say:
    the sources `keep` and `drop` name, each one name or several. None
    leaves a list to the run this one comes from, and an empty list is a
    list of none."""
    kept = None if keep is None else _names(keep)
    dropped = None if drop is None else _names(drop)
    for name in (*(kept or ()), *(dropped or ())):
        _stored_name(name)
    try:
        return ipc.Output.of(kept, dropped)
    except ValueError as exc:
        raise InvalidArgument(str(exc)) from None


def _stored_name(name: str) -> None:
    """Check one name of what is kept or dropped: a source, or a pattern
    that can match one, such as `/output/drop/*/*.log`. The word that is
    a list of none on a command line is no name here: an empty list says
    it."""
    if name == console_source.STORED_NONE:
        raise InvalidArgument(
            f"No such source: {name}. An empty list keeps or drops none."
        )
    if not console_source.is_pattern(name):
        source(name)
        return
    try:
        console_source.check_stored(name)
    except ValueError:
        raise InvalidArgument(
            f"No source matches: {name}. {console_source.PATTERNS}."
        ) from None


def _names(value: "Sequence[str] | str | None") -> Sequence[str]:
    """One name, several or none, as several."""
    if value is None:
        return ()
    return (value,) if isinstance(value, str) else value


def line_text(line: str) -> str:
    """A log line without the carriage return a serial console ends it with."""
    return line.rstrip("\r")


def followed(
    read: log_lines.ReadFn, lines: "int | str | None", timeout: Optional[float]
) -> Generator[str, None, None]:
    """The lines `lines` selects, then each line written after them."""
    first = window(read, lines, follow=True)
    yield from first.lines
    yield from log_lines.follow(
        read,
        first.next_line,
        first.partial,
        interval=log_lines.FOLLOW_INTERVAL,
        timeout=timeout,
    )


#: How every reader of the SDK spells which entries it wants. One grammar, so
#: `entries`, `output` and `play` cannot come to mean different things by the
#: same words.
ENTRIES_GRAMMAR = (
    "N or (a, b) for one entry or a range, `A:B` for A through B, `A:` from A"
    " on, `:B` up to B, `:` for every entry, `-N` for the last N, `-A:-B` from"
    " the Ath from the end through the Bth, and a Python slice, which is"
    " half-open as Python's are"
)


def entries_spec(entries: "int | tuple[int, int] | str | slice") -> str:
    """The entries `entries` names, in the one grammar every reader takes.

    Returned in the form the machine's log reader takes, so the readers that
    want a pair and the readers that want a selection parse one spelling.
    """
    if isinstance(entries, int):
        return f"{entries}:{entries}"
    if isinstance(entries, slice):
        return _slice_spec(entries)
    if isinstance(entries, tuple):
        first, last = entries
        return f"{int(first)}:{int(last)}"
    text = entries.strip()
    if not text:
        raise InvalidArgument(f"Entries required: {ENTRIES_GRAMMAR}.")
    if text.startswith("-"):
        return _from_the_end(text)
    if ":" not in text:
        one = _count(text, text)
        return f"{one}:{one}"
    first_text, _, last_text = text.partition(":")
    first = _count(first_text, text) if first_text else 0
    if not last_text:
        return f"{first}:"
    return f"{first}:{_count(last_text, text)}"


def _from_the_end(text: str) -> str:
    """`-A`, `-A:` and `-A:-B`: entries counted from the end, both ends
    included, -1 the last."""
    first_text, colon, last_text = text.partition(":")
    first = _count(first_text[1:], text)
    if not colon or not last_text:
        return f"-{first}"
    if not last_text.startswith("-"):
        raise InvalidArgument(
            f"Not entries: {text!r}. Both ends count from the end, as in"
            " '-10:-2', or neither, as in '100:200'."
        )
    last = _count(last_text[1:], text)
    if last > first:
        raise InvalidArgument(f"Not entries: {text!r}. It ends before it starts.")
    return f"-{first}:-{last}"


def _slice_spec(window: slice) -> str:
    """A Python slice as the entries grammar spells it.

    HALF-OPEN, the way every Python slice is: `slice(100, 200)` is 100 through
    199, where `(100, 200)` and `"100:200"` are 100 through 200. Each spelling
    is right in its own convention -- the tuple and the string are the
    machine's, which is what its messages and the CLI print, and the slice is
    Python's, which is what `[100:200]` means in the language it is written in.

    `slice(-N, None)` is the last N, as `[-N:]` is, and `slice(-A, -B)` stops
    before the Bth from the end, as `[-A:-B]` does. A negative at one end only
    names nothing the log reader can read without the run's length, and a
    step asks for a stride it does not walk.
    """
    if window.step is not None and window.step != 1:
        raise InvalidArgument(
            f"A step of {window.step} is not an entry range: entries come one"
            " after another."
        )
    start, stop = window.start, window.stop
    if start is not None and start < 0:
        if stop is None:
            return f"{start}"
        if stop >= 0:
            raise InvalidArgument(
                f"slice({start}, {stop}) counts one end from the end and the"
                " other from the start: count both from the end, or neither."
            )
        if stop <= start:
            raise InvalidArgument(
                f"slice({start}, {stop}) names no entry: a range ends after"
                " where it starts."
            )
        return f"{start}:{stop - 1}"
    if stop is not None and stop < 0:
        raise InvalidArgument(
            f"slice({start}, {stop}) counts one end from the end and the other"
            " from the start: count both from the end, or neither."
        )
    first = 0 if start is None else int(start)
    if stop is None:
        return f"{first}:"
    last = int(stop) - 1
    if last < first:
        raise InvalidArgument(
            f"slice({start}, {stop}) names no entry: a range ends at or after"
            " where it starts."
        )
    return f"{first}:{last}"


def source_window(
    entries: "int | tuple[int, int] | str | slice | None",
) -> Optional[str]:
    """A window of a source log by entry id, in the entries grammar."""
    if entries is None:
        return None
    return entries_spec(entries)


def entry_pair(entries: "int | tuple[int, int] | str | slice") -> tuple[int, int]:
    """The first and last entry `entries` names, both included.

    `-N`, the last N, comes back as a negative first entry, and `-A:-B` as a
    negative first and last, which the reader turns into ids against the
    run's tip.
    """
    spec = entries_spec(entries)
    if spec.startswith("-"):
        first_text, _, last_text = spec.partition(":")
        return int(first_text), int(last_text) if last_text else _LAST_ENTRY
    first_text, _, last_text = spec.partition(":")
    return int(first_text), int(last_text) if last_text else _LAST_ENTRY


#: The open end of a range, past any entry a run records.
_LAST_ENTRY = (1 << 63) - 1


def _count(text: str, whole: str) -> int:
    """One number of the entries grammar, refused when it is not one."""
    try:
        value = int(text)
    except ValueError:
        raise InvalidArgument(
            f"Not entries: {whole!r}. Name {ENTRIES_GRAMMAR}."
        ) from None
    if value < 0:
        raise InvalidArgument(f"Not entries: {whole!r}. An entry is 0 or more.")
    return value
