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
"""Checkpoint a booted run and fork branches from it.

Every run has checkpoint 1, its first entry. A named checkpoint is a later point
worth returning to. A branch forked at a checkpoint starts from exactly that
state and runs alongside the run it came from, so one boot serves every
branch. Each branch stops once it has run, so the root and one branch are
all that run at once, and two vCPUs serve any number of branches.

    python checkpoint_and_fork.py --disk app --branches 3
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--branches", type=int, default=3, help="Branches to fork")
args = parser.parse_args()

client = machine.Client()

# The root and the branch running at the time: a vCPU each.
with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    # Leaving the block deletes the root and every branch with it.
    with client.create(disk=args.disk) as root:
        root.run(until="tasks", timeout=300)
        print("at boot:", [c.name for c in root.checkpoints()])
        booted = root.checkpoint("booted")
        at = booted.place
        print(f"checkpoint {booted.name} at entry {at.entry_id}, {at.point}")

        # Every branch starts from the booted state; none boots again.
        for i in range(args.branches):
            branch = root.fork(at=booted, name=f"try-{i}")
            done = branch.run(iters=100)
            print(f"{branch.name} ran to entry {done.entry_id}")
            # Stopped, it keeps everything it recorded and frees its vCPU.
            branch.stop()

        for run in client.runs(root, sort="name"):
            print(f"  {run.name}  {run.listing.state}")
        print("lineage:", [r.name for r in branch.lineage()])
        # A branch lists its ancestor's checkpoints as its own history, 0
        # included.
        print("checkpoints:", [c.name for c in branch.checkpoints()])
