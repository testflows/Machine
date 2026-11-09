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
"""Disks: build a disk, boot a run from it, rename it and delete it.

A disk is what a run boots, kept in the account and named. A run keeps the
disk's content: renaming the disk does not change what the run boots, and
deleting it is refused while runs still boot it.

    python disks.py --binary build/app --name app
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--binary", required=True, help="Static executable to run")
parser.add_argument("--name", required=True, help="Name to build it as")
args = parser.parse_args()

client = machine.Client()

# Disks belong to the account: no session is needed to build or list them.
disk = client.disks.build(args.name, binary=args.binary)
print(f"built {disk.name}: {disk.size_bytes} bytes, {disk.id}")
print("disks:", [d.name for d in client.disks()])

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=disk.name):
        renamed = client.disks.rename(disk.name, f"{disk.name}-old")
        print(f"renamed to {renamed.name}; the run boots the same content")
        try:
            client.disks.delete(renamed.name)
        except machine.Conflict as refused:
            print(f"refused: {refused}")

# The run went with its block, so nothing boots the disk any more.
client.disks.delete(renamed.name)
print("deleted:", [d.name for d in client.disks(deleted=True)])
