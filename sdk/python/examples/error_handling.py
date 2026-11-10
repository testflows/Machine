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
"""Errors are conditions, and each condition has its recovery.

Every exception derives from `machine.Error` and names a condition, not a
resource: `NotFound`, `Forbidden`, `InvalidArgument`, `NotRunning`,
`Conflict`, `Timeout` and the rest mean the same thing for a run, a session
or a disk. This meets each of those and recovers from the ones that have
a next step.

    python error_handling.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

try:
    client.runs["no-such-run"]
except machine.NotFound as error:
    print("NotFound:", error)

try:
    extra = client.sessions.create(cpus=64, wait=False)
except machine.Forbidden as error:
    print("Forbidden:", error)
else:
    extra.delete()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        try:
            run.run(until="not-a-predicate")
        except machine.InvalidArgument as error:
            print("InvalidArgument:", error)

        run.stop()
        try:
            run.run(iters=10)
        except machine.NotRunning as error:
            print("NotRunning:", error, "-> start it")
            run.start()
            run.run(iters=10)

        run.pause()
        try:
            run.run(iters=10)
        except machine.Conflict as error:
            print("Conflict:", error, "-> resume it")
            run.resume()
            run.run(iters=10)

        try:
            run.wait(for_="stopped", timeout=5)
        except machine.Timeout as error:
            # A Timeout is also a TimeoutError.
            print("Timeout:", error, "-> the run is still up; stop it")
            run.stop()
