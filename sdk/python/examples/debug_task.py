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
"""Stop a program at a system call and read its task as a debugger does.

A catchpoint stops the run at an event of a task: a system call, an exec,
a fork, a signal. The run ends where it hits, and the task that made it
stays as it was, so its registers, memory and stack can be read. The run
log keeps the catchpoint, so a replay stops at the same point.

    python debug_task.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--file", default="/etc/hostname", help="File for cat to open")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)

        # A C library opens a file with open or openat: catch both, in any
        # task named cat.
        stop = run.breakpoints.catch("syscall", "open", "openat", in_="comm=cat")
        print(f"catchpoint {stop.number}: {stop.kind} {', '.join(stop.names)}")

        cat = run.exec(["cat", args.file], wait=False)
        done = run.run(within="10s")
        (hit,) = done.hits
        print(f"hit {hit.number} at entry {done.entry_id}: {hit.event}")

        # The event names the task last: "..., in tid N".
        task = run.task(int(hit.event.rpartition(" ")[2]))
        regs = task.registers()
        print(f"tid {regs.tid}, tgid {regs.tgid}, registers read {regs.where}")
        for name in ("orig_rax", "rdi", "rsi", "rip", "rsp"):
            print(f"  {name:8} {regs[name]:#x}")

        # The path is the first argument of open and the second of openat.
        path = regs["rdi"] if regs["orig_rax"] == 2 else regs["rsi"]
        name = task.read(path, 64).split(b"\0")[0].decode()
        print(f"opening {name!r}")

        for frame in task.backtrace():
            where = frame.function or "??"
            print(f"  #{frame.level} {frame.address:#x} {where} {frame.path or ''}")

        # Deleted, the run goes on and cat ends as it would have.
        run.breakpoints.delete(stop.number)
        run.run(within="1s")
        print("cat printed:", run.process(cat.id).stdout)
