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
"""A CI job: one session per job, runs labeled by commit, an exit code.

The job signs in with TESTFLOWS_MACHINE_API_KEY from the CI's secrets. Its
session is deleted however the job ends. A failing run is kept, labeled
with the commit, so it can be replayed after the job; a passing one is
deleted. The exit code is the verdict.

    TESTFLOWS_MACHINE_API_KEY=... python ci_job.py --disk app \\
        --program /usr/bin/app --commit $GITHUB_SHA
"""

import argparse
import os
import sys

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--program", required=True, help="Test program, by path")
parser.add_argument("--commit", required=True, help="The commit under test")
args = parser.parse_args()

if "TESTFLOWS_MACHINE_API_KEY" not in os.environ:
    sys.exit("TESTFLOWS_MACHINE_API_KEY is not set.")

client = machine.Client()
commit = args.commit[:12]
failed = []

with client.sessions.create(f"ci-{commit}", cpus=1) as session:
    client.sessions.use(session)
    run = client.create(f"ci-{commit}", disk=args.disk, labels={"commit": commit})
    run.run(until="tasks", timeout=300)
    run.focus.add(args.program)
    run.run(until=f"{args.program}:exit", timeout=1800)

    failed = [e for e in run.tasks.exits() if e.exit_code]
    if failed:
        run.stop()
        print(f"FAIL {failed[-1].outcome}; kept {run.name} for replay")
    else:
        run.delete(stop=True)
        print("PASS")

sys.exit(1 if failed else 0)
