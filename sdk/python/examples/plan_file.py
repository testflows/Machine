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
"""Drive a run by a plan: different options while different code runs.

A plan is a list of `when` and `else` clauses. Each dispatch takes its
options from the clauses whose predicates hold, the last one naming an
option winning, and from `else` when no `when` holds. This one steps the
program in short, drawn slices while it runs its own code, and runs
everything else fast. The plan is written here and passed as a stream; a
plan file works the same way.

    python plan_file.py --disk app --program /usr/bin/app
"""

import argparse
import io

import testflows.machine.v1 as machine

parser = argparse.ArgumentParser()
parser.add_argument("--disk", required=True, help="Disk to boot, by name")
parser.add_argument("--program", required=True, help="Program to step, by path")
parser.add_argument("--seed", type=int, default=0, help="Seed of the draws")
args = parser.parse_args()

# A predicate with no program names any focused task.
PLAN = """\
when on {mode step step=1:200}
else {mode fast fast=1k}
"""

client = machine.Client()

with client.sessions.create(cpus=1) as session:
    client.sessions.use(session)
    with client.create(disk=args.disk) as run:
        run.run(until="tasks", timeout=300)
        run.focus.add(args.program)
        done = run.plan(io.StringIO(PLAN), until=f"{args.program}:exit", seed=args.seed)
        print(f"{done.iterations} iterations, to entry {done.entry_id}")
        # How many dispatches each clause took.
        state = run.state()
        for clause, tally in zip(state.plan, state.plan_tally):
            print(f"  {clause}  {tally}")
