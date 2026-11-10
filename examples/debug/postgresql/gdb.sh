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
#
# Attach GDB to the backend of start.sh's client session, the process that
# runs its query every second.
#
#   gdb.sh -ex CMD ...   run the commands, then detach
#   gdb.sh -x FILE       run the GDB script, then detach
#   gdb.sh               read GDB commands from standard input
#
# Run it as the server's user, postgres: the container has no
# CAP_SYS_PTRACE, so only a process of the same user can attach.

pid=$(pgrep -f '^postgres: postgres postgres \[local\]' | head -n 1)
if [ -z "$pid" ]; then
    echo "the client session's backend is not running" >&2
    exit 1
fi
# Commands given end the session; with none GDB reads them from its input.
batch=
if [ "$#" -gt 0 ]; then
    batch=-batch
fi
exec gdb $batch -nx -q -ex "set pagination off" -ex "set confirm off" \
    -ex "set print thread-events off" -p "$pid" "$@"
