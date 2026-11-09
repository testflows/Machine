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
"""Crash one container as a power cut crashes a node, while the others run.

A crash cuts the volumes the project's `x-machine` block names, each a
filesystem of its own. A volume keeps what was synced and loses the writes
still in memory, so a program's recovery meets what a real power cut leaves.

The example builds a Compose project of two services from busybox:1.36,
which the local Docker must hold. `app` mounts the volume `data` at /data.
The disk is deleted at the end.

    python container_crash.py --name container-crash
"""

import argparse
import pathlib
import tempfile

import testflows.machine.v1 as machine

COMPOSE = """\
services:
  app:
    image: busybox:1.36
    command: ["sh", "-c", "echo app ready; exec sleep 2147483647"]
    volumes: [data:/data]
  other:
    image: busybox:1.36
    command: ["sh", "-c", "echo other ready; exec sleep 2147483647"]
volumes:
  data: {}
x-machine:
  volumes:
    data: 256M
"""

parser = argparse.ArgumentParser()
parser.add_argument("--name", required=True, help="Name to build the disk as")
args = parser.parse_args()

client = machine.Client()


def build(name: str) -> machine.Disk:
    """Build the disk from the project, written to a directory of its own."""
    with tempfile.TemporaryDirectory() as project:
        (pathlib.Path(project) / "compose.yaml").write_text(COMPOSE)
        return client.disks.build(name, compose=project)


def show(run: machine.Run, command: str) -> None:
    """Execute a command in container app and print what it printed."""
    done = run.exec(command, container="app", within="30s")
    print(f"app$ {command}: exit {done.exit_code}")
    for line in done.stdout + done.stderr:
        print(f"  {line}")


disk = build(args.name)
print(f"built {disk.name}")
try:
    with client.sessions.create(cpus=1) as session:
        client.sessions.use(session)
        with client.create(disk=disk.name) as run:
            run.run(until="console~app ready", within="120s", timeout=600)
            for c in run.containers():
                print(f"  {c.service}  {c.name}  {c.state}")

            show(run, "echo synced > /data/kept && sync")
            show(run, "echo in memory > /data/lost")
            show(run, "ls /data")

            crashed = run.containers.crash("app", restart="0")
            print(f"crashed {crashed.container.name}, volumes cut: {crashed.volumes}")
            run.run(within="5s")
            for c in run.containers():
                print(f"  {c.service}  {c.name}  {c.state}  {c.status}")

            # The synced file is kept; the write still in memory is lost.
            show(run, "ls /data")
            show(run, "cat /data/kept")
finally:
    # Deleted after the run that booted it, which went with its block.
    client.disks.delete(disk.name)
    print(f"deleted {disk.name}")
