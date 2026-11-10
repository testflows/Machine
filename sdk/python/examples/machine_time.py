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
"""Machine time (vtime): the machine's clock runs on instructions, not the host's.

The machine reads machine time, which advances with the instructions it
executes at a rate, so a run's timing replays exactly whatever the host was
doing. A higher rate fires timers after fewer instructions; `add` moves the
clock forward, firing a timer without executing up to it.

    python machine_time.py --disk app
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
        clock = run.vtime()
        print(f"vtime {clock.vtime} ns at {clock.rate_num}/{clock.rate_den}")

        run.run(iters=100)
        print("after 100 iterations:", run.vtime().vtime)

        run.vtime.rate("20/1")
        run.run(iters=100)
        print("after 100 more at 20/1:", run.vtime().vtime)

        # One second of machine time, without executing through it.
        print("after add:", run.vtime.add("1s").vtime)
