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
"""Watch a listing change and follow a console while another thread drives.

A listing called with `watch=True` yields each time it changes, as
`machine runs -w` repaints. A log called with `follow=True` yields each new
line, as `machine output -f` prints. Both only read, so the run is driven
from a second thread.

    python watch_and_follow.py --disk app
"""

import argparse
import threading

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        driver = threading.Thread(
            target=run.run, kwargs={"until": "tasks", "timeout": 300}, daemon=True
        )
        driver.start()

        # The console as the machine boots, for two minutes.
        for line in run.output(lines=0, follow=True, timeout=120):
            print("console:", line)
        driver.join()

        # The run as listed each time it changes, while it is paused and
        # resumed. `listing` is the row the listing returned.
        changes = client.runs(run, watch=True, timeout=60)
        (listed,) = next(changes)
        print("state:", listed.listing.state)
        run.pause()
        (listed,) = next(changes)
        print("state:", listed.listing.state)
        run.resume()
        (listed,) = next(changes)
        print("state:", listed.listing.state)
        changes.close()
