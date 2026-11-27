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
"""Turn a Bombadil run in a machine into events, a recording and a place to go.

Bombadil writes every screen it samples and every action it takes to its
screen log, and the machine records which entry printed each line. This
script reads one example's screen log from a run and ties the two together:
each action and each property violation gets Bombadil's time and the entry
of the machine where its screen was.

    python bombadil_events.py bombadil --example nano

lists the violations, each with the command that forks the run at its
screen. Add:

    --events nano.tsv        every action and violation: time, entry, event
    --cast nano.cast         the screens as an asciinema recording, with a
                             marker at each violation to jump to
    --show 1                 the screen at the first violation

Play the recording with `asciinema play nano.cast`, or in asciinema-player,
which lists the markers and jumps to each.
"""

import argparse
import json
import re
from dataclasses import dataclass, field

import testflows.machine.v1 as machine

CLEAR = "\x1b[2J\x1b[H"
ACTION = re.compile(r"^(\d+):(\d\d)\.(\d{3}) (.+)$")
VIOLATED = re.compile(r"^(\S+) was violated:$")
HOWEVER = re.compile(r"^however at (\d+):(\d\d)\.(\d{3}):$")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def seconds(match: re.Match) -> float:
    """Bombadil's time, MM:SS.mmm, in seconds."""
    minutes, secs, millis = (int(part) for part in match.groups()[:3])
    return minutes * 60 + secs + millis / 1000


@dataclass
class Screen:
    """One screen Bombadil sampled: its rows, the entry of the machine that
    printed its first row, and the time of the action taken on it."""

    entry: int
    rows: list = field(default_factory=list)
    time: float = 0.0


@dataclass
class Violation:
    """A property Bombadil found violated: where it reported it, and the
    time and screen it names."""

    name: str
    reported_entry: int
    text: list = field(default_factory=list)
    time: float = None
    screen: Screen = None


def parse(lines):
    """Split a screen log into screens, actions and violations."""
    screens, actions, violations = [], [], []
    screen = violation = None
    for line in lines:
        text = line.text
        if text.startswith(CLEAR):
            screen = Screen(entry=line.entry_id, rows=[text[len(CLEAR) :]])
            screens.append(screen)
            # Bombadil reports a violation, then prints the screen that
            # violated it: the screen at the time the report names.
            for pending in violations:
                if pending.screen is None:
                    pending.screen = screen
            violation = None
            continue
        action = ACTION.match(text)
        if action:
            if screen and not screen.time:
                screen.time = seconds(action)
            actions.append((seconds(action), line.entry_id, action.group(4)))
            screen = violation = None
            continue
        violated = VIOLATED.match(text)
        if violated:
            violation = Violation(violated.group(1), line.entry_id, [text])
            violations.append(violation)
            screen = None
            continue
        if violation is not None:
            violation.text.append(text)
            however = HOWEVER.match(text)
            if however:
                violation.time = seconds(however)
            continue
        if screen is not None:
            screen.rows.append(text)
    return screens, actions, violations


def entry_of(violation):
    """The entry to go to for a violation: where its screen was printed,
    or where it was reported when no screen followed, as when the program
    exited."""
    return violation.screen.entry if violation.screen else violation.reported_entry


def write_events(path, actions, violations):
    """Write every action and violation, oldest first: time, entry, event."""
    rows = [(t, entry, f"action {what}") for t, entry, what in actions]
    rows += [(v.time or 0.0, entry_of(v), f"violation {v.name}") for v in violations]
    with open(path, "w") as out:
        out.write("time\tentry\tevent\n")
        for t, entry, what in sorted(rows):
            out.write(f"{t:.3f}\t{entry}\t{what}\n")


def write_cast(path, screens, violations):
    """Write the screens as an asciinema v2 recording, each at its
    Bombadil time, with a marker at each violation."""
    width = max(len(ANSI.sub("", row)) for s in screens for row in s.rows)
    height = max(len(s.rows) for s in screens)
    start = next(s.time for s in screens if s.time)
    events = [
        (s.time - start, "o", CLEAR + "\r\n".join(s.rows))
        for s in screens
        if s.time
    ]
    events += [
        (v.time - start, "m", f"{v.name} violated, entry {entry_of(v)}")
        for v in violations
        if v.time is not None
    ]
    with open(path, "w") as out:
        out.write(json.dumps({"version": 2, "width": width, "height": height}))
        out.write("\n")
        for event in sorted(events, key=lambda e: (e[0], e[1] == "o")):
            out.write(json.dumps([round(event[0], 3), event[1], event[2]]))
            out.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="The run, by name")
    parser.add_argument(
        "--example", required=True,
        help="The example: cat, nano, nano-eventually, tb, btop or tetris",
    )
    parser.add_argument("--session", help="The session; defaults to the only one")
    parser.add_argument("--events", help="Write every event to this file")
    parser.add_argument("--cast", help="Write an asciinema recording to this file")
    parser.add_argument("--show", type=int, help="Print the screen at violation N")
    args = parser.parse_args()

    client = machine.Client()
    client.sessions.use(args.session)
    run = client.runs.get(args.run)
    source = f"/output/keep/app/bombadil/{args.example}.screen.log"
    screens, actions, violations = parse(run.output.lines(source=source))

    print(f"{args.example}: {len(screens)} screens, {len(actions)} actions, "
          f"{len(violations)} violations")
    for number, v in enumerate(violations, 1):
        at = entry_of(v)
        when = f"{v.time:.3f}s" if v.time is not None else "a time Bombadil did not say"
        print(f"  {number}. {v.name} at {when}, entry {at}")
        print(f"     machine fork {args.run} --at '#{at}' --name {args.example}-violation-{number}")

    if args.events:
        write_events(args.events, actions, violations)
        print(f"Events written to {args.events}")
    if args.cast:
        write_cast(args.cast, screens, violations)
        print(f"Recording written to {args.cast}")
    if args.show is not None:
        if not 1 <= args.show <= len(violations):
            parser.error(f"--show takes 1 to {len(violations)}")
        v = violations[args.show - 1]
        if v.screen:
            print(f"\nThe screen at violation {args.show}, entry {v.screen.entry}:")
            print("\n".join(v.screen.rows))
        print("\n".join(v.text))


if __name__ == "__main__":
    main()
