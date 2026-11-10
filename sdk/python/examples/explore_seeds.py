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
"""Explore a program's schedules: one branch per seed, failures kept.

Every branch starts from the same booted checkpoint and runs the program
under a different draw of dispatch lengths, so each explores a different
interleaving. A branch whose program is killed by a signal is marked
`%bug-N` and kept, since its run log is the finding; the others are deleted.

    python explore_seeds.py --disk app --program /usr/bin/app --trials 8
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--program", required=True, help="Program to explore, by path")
parser.add_argument("--trials", type=int, default=8, help="Branches to run")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    # Not in a `with` block: the root holds the kept branches.
    root = client.create(disk=args.disk)
    root.run(until="tasks", timeout=300)
    root.focus.add(args.program)
    root.checkpoint("booted")

    for seed in range(args.trials):
        branch = root.fork(at="^booted", name=f"seed-{seed}")
        branch.run(
            until=f"{args.program}:exit",
            mode="step,fast",
            step="1:200",
            seed=seed,
            timeout=600,
        )
        killed = [e for e in branch.tasks.exits() if e.signal]
        if killed:
            branch.marks.add(f"bug-{seed}")
            branch.stop()
            print(f"seed {seed}: {killed[-1].outcome}, kept as %bug-{seed}")
        else:
            branch.delete(stop=True)
            print(f"seed {seed}: clean")

    root.stop()
    print("findings:", [m.run_name for m in root.marks.list()])
    print(f"delete them with client.runs[{root.name!r}].delete(recursive=True)")
