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
"""Stop a run in one session and start it in another.

A run lives in the account, not in the session that ran it. Stopping it
publishes it to shared storage, so it outlives its session and any later
session starts it where it stopped.

    python resume_elsewhere.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as first:
    client.sessions.use(first)
    run = client.create(disk=args.disk)
    run.run(until="tasks", timeout=300)
    run.stop()
    print(f"{run.name} stopped at {run.now().entry_id} in {first.name}")

# The first session is gone. With no session, the account still lists it.
print("in the account:", run.describe().state)

with client.sessions.create(cpus=1) as second:
    client.sessions.use(second)
    run.start()
    print(f"{run.name} started at {run.now().entry_id} in {second.name}")
    run.delete(stop=True)
