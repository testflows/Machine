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
"""Preload before a burst of starts, offload to free the session's storage.

Runs and disks live in the account's shared storage and are copied to a
session when a run starts there. Preloading copies them ahead of
time, so a start does not wait for the download; offloading drops the
session's copy, and the next start copies it back. `storage.show` says what
the account holds.

    python preload_and_offload.py --disk app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    client.preload.disk(args.disk)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.stop()

        print("offloadable:", [c.name for c in client.offload.list().runs])
        run.offload()
        run.preload()
        run.start()
        print("started again at", run.now().entry_id)

    kept = client.offload.list()
    print("session holds:", len(kept.runs), "runs,", len(kept.disks), "disks")

stored = client.storage.show()
print(f"account storage: {stored.bytes} bytes of {stored.quota_bytes}")
print(f"  a prune would free {stored.prunable_bytes}")
