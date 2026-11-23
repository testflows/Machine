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
"""Force a missed wakeup: hold the waiter in its race window.

The waiter in examples/starvation/missed-wakeup.c drops the mutex and takes
it again before it waits. A signal sent between the two is lost. The
program stops the waiter at that point, holds it there, runs the signaler
past its signal, then releases the waiter. The original program fails on
the first try; a fixed one passes.

    gcc -O0 -g -static -pthread -o missed-wakeup missed-wakeup.c
    python missed_wakeup.py ./missed-wakeup --window 76 --signal 109
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("binary", help="The missed-wakeup program, built with -g")
parser.add_argument("--window", type=int, default=76, help="The waiter's line in the window")
parser.add_argument("--signal", type=int, default=109, help="The line after the signal")
args = parser.parse_args()

source = "missed-wakeup.c"
client = machine.Client()

with client.sessions.create(cpus=2) as session:
    client.sessions.use(session)
    disk = client.disks.build("mw", binary=args.binary, command=["1"])
    with client.create("mw", disk=disk.name, cpus=2) as run:
        run.focus.add("comm=missed-wakeup")
        run.run(until="comm=missed-wakeup:exec")
        run.run(until="comm=missed-wakeup:sched")
        main = run.tasks(focused=True)[0].tid

        # Stop the waiter in its window and hold it there.
        stop = run.task(main).breakpoint(f"{source}:{args.window}")
        waiter = run.run(iters=0).hits[0].hit_tid
        run.tasks.hold(waiter)
        run.breakpoints.delete(stop.number)

        # The signaler alone reaches the signal line: run it past its signal.
        run.run(until=f"tgid={main}:at={source}:{args.signal}", within="60s")
        run.tasks.release(waiter)

        run.run(until="console~(FAILURE: missed|SUCCESS:)")
        print(next(line for line in run.output() if "FAILURE:" in line or "SUCCESS:" in line))
    # The run is deleted with its block; the disk stays in the account until deleted.
    client.disks.delete(disk)
