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

# ClickHouse server with GDB, in two targets:
#
#   docker build --target gdb -t clickhouse-gdb:26.8.31.2 .
#   docker build --target gdb-debug -t clickhouse-gdb-debug:26.8.31.2 .
#
# gdb has the binary's symbol table: function names, no arguments or lines.
# gdb-debug adds the server's debug information: arguments, locals, types and
# source lines. The server's image is Ubuntu 22.04, whose GDB 12 reads the
# DWARF 5 the debug information is in.

FROM --platform=linux/amd64 clickhouse/clickhouse-server:26.8.31.2 AS gdb
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gdb procps \
 && rm -rf /var/lib/apt/lists/*
COPY start.sh gdb.sh session.gdb /examples/gdb/
ENTRYPOINT ["/examples/gdb/start.sh"]

# The debug information must be of the server's exact version: GDB loads
# /usr/lib/debug/usr/bin/clickhouse.debug only when its build-id matches.
FROM gdb AS gdb-debug
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      clickhouse-common-static-dbg=$(clickhouse local -q "SELECT version()") \
 && rm -rf /var/lib/apt/lists/*
