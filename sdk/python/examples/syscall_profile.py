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
"""Profile a program's system calls and interrupts.

Counting is off until a focus entry arms it, so a run pays nothing for it
unasked. Armed, the machine counts each call and interrupt of the program's
threads, and `syscalls` and `ints` read the counts by name, most frequent
first.

    python syscall_profile.py --disk app --program /usr/bin/app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--program", required=True, help="Program to profile, by path")
parser.add_argument("--top", type=int, default=10, help="Rows to print")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.focus.add(args.program, syscalls=True, ints=True)
        run.run(until=f"{args.program}:exit", mode="free", timeout=600)

        # `counts` reads a task's histogram; calling the group lists the
        # kernel's own table.
        calls = run.syscalls.counts(args.program)
        interrupts = run.ints.counts(args.program)
        print("system calls:")
        for row in calls.by_tgid[: args.top]:
            print(f"  {row.count:>8}  {row.name}")
        print("interrupts:")
        for row in interrupts.by_tgid[: args.top]:
            print(f"  {row.count:>8}  {row.name}")
