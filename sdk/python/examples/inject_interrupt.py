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
"""Inject an interrupt, and replay it at the same point.

An injected interrupt is recorded in the run's log like any other event, so
a replay delivers it at the same entry: the recording with the injection
reproduces as exactly as one without.

    python inject_interrupt.py --disk app --vector preempt
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--vector", default="preempt", help="Vector, by name or number")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        print("vectors:", [v.name for v in run.irq.vectors()])
        run.checkpoint("before")
        check = run.fork(at="^before", name="check", start=False)

        sent = run.irq(args.vector)
        print(f"queued vector {sent.vector} for vcpu {sent.vcpu}")
        run.run(iters=50)

        check.start()
        result = check.replay()
        print(f"replay with the interrupt converged: {result.converged}")
