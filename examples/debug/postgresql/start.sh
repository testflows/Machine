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
# The container's entrypoint: the image's own server, its version on the
# console once it answers, then one client session that sends a query every
# second. The session is one backend process for as long as the machine
# runs, so a breakpoint in the query path is reached in the same task each
# time, without sending a query.

docker-entrypoint.sh postgres "$@" &
server=$!

until pg_isready -q -U postgres; do
    if ! kill -0 "$server" 2>/dev/null; then
        echo "postgresql: the server exited" >&2
        exit 1
    fi
    sleep 1
done
echo "postgresql: $(psql -U postgres -At -c 'SELECT version()')"

while kill -0 "$server" 2>/dev/null; do
    echo "SELECT count(*) FROM generate_series(1, 100000);"
    sleep 1
done | psql -U postgres -q >/dev/null 2>&1 &
# Ready once the session's backend runs: a checkpoint at ready holds it.
until pgrep -f '^postgres: postgres postgres \[local\]' >/dev/null; do
    sleep 1
done
echo "postgresql: ready"
wait "$server"
