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
"""Force an interleaving by hand: hold one thread while another runs.

Focus names the program whose threads the machine steers. A held thread
stays off the CPU at every scheduling decision until it is released, so the
other threads run past it: the order a race needs, forced instead of hoped
for.

    python force_interleaving.py --disk app --program /usr/bin/app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--program", required=True, help="Program to steer, by path")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk, cpus=2) as run:
        run.run(until="tasks", timeout=300)
        run.focus.add(args.program)
        # Until the program's second thread exists.
        run.run(until=f"{args.program}:on", mode="free", timeout=300)
        while len(run.tasks(focused=True)) < 2:
            run.run(iters=10, mode="free")
        first, second = run.tasks(focused=True)[:2]

        run.tasks.hold(first.tid)
        print(f"held {first.tid}; {second.tid} runs past it")
        run.run(until=f"tid={second.tid}:exit", timeout=300)
        run.tasks.release(first.tid)

        run.run(until=f"{args.program}:exit", timeout=300)
        for task in run.tasks.exits():
            print(f"{task.tid}: {task.outcome}")
