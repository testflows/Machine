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
"""Read files from a run's disk, as a checkpoint saved them.

The disk is read from the checkpoint, not the live machine, so what a test
wrote is readable after the run stopped, and every branch shows its own
writes. Every read names its checkpoint.

    python read_fs.py --disk app --path /var/log --dest logs
"""

import argparse

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--path", default="/var/log", help="Directory to read")
parser.add_argument("--dest", default="files", help="Where to copy it")
args = parser.parse_args()

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.checkpoint("done")
        run.stop()

        for f in run.fs.ls(args.path, at="^done"):
            print(f"{f.kind:5} {f.size:>10}  {f.name}")
        files = [f for f in run.fs.ls(args.path, at="^done") if f.kind == "file"]
        if files:
            first = f"{args.path}/{files[0].name}"
            print(first, "ends:", run.fs.cat(first, at="^done")[-200:])
        copied = run.fs.cp(args.path, args.dest, at="^done", recursive=True)
        print(f"copied {len(copied)} files to {args.dest}")
