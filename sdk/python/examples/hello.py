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
"""Hello, TestFlows Machine: create a run, boot it, read its console.

A run is a machine booting a disk, and it lives in a session, a
machine the account rents. The session is created for the example and
deleted at the end, since it costs from the moment it is created. Sign in
first with `machine login`, or set TESTFLOWS_MACHINE_API_KEY.

    python hello.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

# Leaving a `with` block deletes what it created: here the session.
with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        # A run executes only when driven. `tasks` holds once the machine's
        # kernel is up.
        run.run(until="tasks", timeout=300)
        for line in run.output(lines=-20):
            print(line)
