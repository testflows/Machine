#!/bin/sh
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

# Run Bombadil's terminal examples one after another.
#
#   run.sh [time limit] [example ...]
#
# The time limit defaults to 20s and the examples to Bombadil's five: cat,
# nano, tb, btop and tetris. nano-eventually is nano with the property of
# nano-eventually.patch, named to run. Each example writes its screen log,
# every screen Bombadil saw and each action it took, and its trace under
# MACHINE_OUTPUT_DIR, where the machine keeps them with the run.
#
# tb runs before btop. btop's specification types random keys, and btop can
# kill a process from its menu: run first, the TigerBeetle cluster tb needs
# is done with before btop could end it. Bombadil's own btop.ts runs btop in
# its own PID namespace instead, which a container cannot mount /proc for.
limit="${1:-20s}"
[ $# -gt 0 ] && shift
examples="${*:-cat nano tb btop tetris}"
out="${MACHINE_OUTPUT_DIR:-/tmp}/bombadil"
mkdir -p "$out"

# tb tests TigerBeetle's REPL, so a one-replica cluster runs for it. Its log
# goes to the machine's output and to /tmp: a file under MACHINE_OUTPUT_DIR
# is stored as it is written and reads back empty, so the wait for the
# cluster reads the copy in /tmp.
tigerbeetle_start() {
  data=/tmp/0_0.tigerbeetle
  log=/tmp/tigerbeetle.log
  rm -f "$data"
  : > "$log"
  { tigerbeetle format --cluster=0 --replica=0 --replica-count=1 --development "$data" 2>&1 \
    && exec tigerbeetle start --addresses=3000 --development "$data" 2>&1; } \
    | tee "$log" > "$out/tigerbeetle.log" &
  pid=$!
  until grep -q "listening on" "$log"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "tigerbeetle did not start"
      tail -n 5 "$log"
      return 1
    fi
    sleep 0.1
  done
}

# The cluster starts first, before any example runs: formatting its 1 GiB
# write-ahead log is most of the time a machine spends on this project, so
# a checkpoint at the line below holds a formatted cluster, and every run
# forked from it starts there.
tigerbeetle=""
case " $examples " in
  *" tb "*)
    if tigerbeetle_start; then
      tigerbeetle=up
      echo "=== tigerbeetle: listening"
    fi
    ;;
esac

for example in $examples; do
  case "$example" in
    cat|nano|btop) program="$example" ;;
    nano-eventually) program="nano" ;;
    tetris) program="/usr/games/bastet" ;;
    tb)
      if [ -z "$tigerbeetle" ]; then
        echo "=== bombadil: tb skipped, tigerbeetle did not start"
        continue
      fi
      program="tigerbeetle repl --cluster=0 --addresses=3000"
      ;;
    *) echo "unknown example: $example"; exit 1 ;;
  esac
  echo "=== bombadil: $example, $limit"
  bombadil terminal test \
    --specification "/bombadil/examples/$example.ts" \
    --time-limit "$limit" \
    --output-path "$out/$example.bin" --output-path-overwrite \
    $program > "$out/$example.screen.log" 2>&1
  echo "=== bombadil: $example exited $?"
done
