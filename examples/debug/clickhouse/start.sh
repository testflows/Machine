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
# The container's entrypoint: the image's own server, its version and the
# source it was built from on the console once it answers, then a query every
# second, so a breakpoint in the query path is reached without sending one.

/entrypoint.sh "$@" &
server=$!

until clickhouse-client -q "SELECT 1" >/dev/null 2>&1; do
    if ! kill -0 "$server" 2>/dev/null; then
        echo "clickhouse: the server exited" >&2
        exit 1
    fi
    sleep 1
done
# The commit the server was built from: the source GDB's lines and frames
# name.
commit=$(clickhouse-client -q \
    "SELECT value FROM system.build_options WHERE name = 'GIT_HASH'")
echo "clickhouse: $(clickhouse-client -q 'SELECT version()'), commit $commit"
echo "clickhouse: source https://github.com/ClickHouse/ClickHouse/tree/$commit"
echo "clickhouse: ready"

while kill -0 "$server" 2>/dev/null; do
    clickhouse-client -q "SELECT sum(number) FROM numbers(1000000)" >/dev/null 2>&1
    sleep 1
done
wait "$server"
