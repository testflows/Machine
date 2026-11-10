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
"""Inject faults: fail a program's system calls and its disk writes.

A syscall fault makes a guest system call return an error without running.
The guest kernel decides at each call, so the program sees the error the
way a real failure reaches it, and a replay fails the same calls. Each
fault counts the calls it matched and the calls it fired on.

A disk fault fails a block request of the guest's disk. A request is the
submitting task's, so a write a program syncs is that program's.

    python fault_injection.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()


def show(process: machine.Process) -> None:
    """Print what a command printed, in order, and its state."""
    print(f"$ {process.command}")
    for event in process.events:
        if event.stream != "status":
            print(f"  {event.stream}: {event.text}")
    print(f"  {process.state}, exit code {process.exit_code}")


with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)

        # A loop that reads a file each second of machine time, started and
        # left running. The shell opens the file itself, so the opens are
        # its tgid's.
        loop = run.exec(
            "while true; do read name < /etc/hostname && echo read $name; sleep 1; done",
            wait=False,
        )
        run.run(within="1s")
        loop = run.process(loop.id)
        # A check before the fault: refused with NotFound when nothing runs.
        shell = run.tasks.exist(f"tgid={loop.pid}")
        print(f"loop runs as tgid {loop.pid}, comm {shell[0].comm}")

        # Fail the loop's next three opens, then let the rest through. A C
        # library opens a file with open or openat; each gets its own fault.
        run.inject.add(
            "syscall", ["open", "openat"], "ENOENT", in_=f"tgid={loop.pid}", times=3
        )
        run.run(within="5s")
        show(run.process(loop.id))
        for fault in run.inject():
            print(
                f"fault {fault.number}: {fault.syscall} {','.join(fault.errors)}"
                f" in tgid {fault.tgid}, matched {fault.matched}, fired {fault.fired}"
            )
        run.inject.clear()

        # A write dd syncs is dd's own request, so the fault takes it.
        run.inject.add("disk", in_="comm=dd", file="/var/data.bin", op="write")
        show(
            run.exec(
                "dd if=/dev/zero of=/var/data.bin bs=4096 count=1 conv=fsync",
                within="10s",
            )
        )
        (fault,) = run.inject()
        print(
            f"fault {fault.number}: {fault.site} {fault.op} of {fault.file},"
            f" matched {fault.matched}, fired {fault.fired}"
        )
