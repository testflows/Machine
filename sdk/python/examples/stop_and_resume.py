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
"""Stop, start and kill a run, and what each keeps.

`stop` checkpoints the tip, so `start` resumes exactly where the run was.
`kill` does not; a machine idle between drives still saves its tip. `start`
with `at` comes up at a checkpoint and replays back to the tip. `sync`
publishes the run to shared storage, where it outlives the session.

    python stop_and_resume.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.checkpoint("booted")
        run.run(iters=100)
        print("tip:", run.now().entry_id)

        run.stop()
        print("after stop:", run.describe().state)
        run.start()
        print("resumed at:", run.now().entry_id)

        run.kill()
        print("after kill:", run.describe().state)
        run.start()
        print("started at:", run.now().entry_id)

        # Up at the checkpoint, then replayed forward to the same tip.
        run.start(at="booted", force=True)
        print("replayed to:", run.now().entry_id)

        run.sync()
        print("durable:", [c.name for c in run.checkpoints(durable=True)])
