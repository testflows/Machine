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
"""Replay a recording and check that it reproduces.

A branch forked at a checkpoint starts from its parent's state there.
`replay` drives it through the parent's recorded entries and compares each
one's state. A replay that does not reproduce is a result, not an
exception: its `diverged` names the parts of the machine that differ.

    python replay_verify.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--iters", type=int, default=200, help="Iterations to record")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.checkpoint("booted")
        check = run.fork(at="^booted", name="check", start=False)

        # The recording to verify: everything the run does after `booted`.
        run.run(iters=args.iters)

        check.start()
        result = check.replay()
        print(f"replayed {result.replayed} entries, converged: {result.converged}")
        for part in result.diverged:
            print(f"  {part['section']}: {part['a']} != {part['b']}")
