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
#   docker build --target gdb -t clickhouse-gdb:24.8.14.39 .
#   docker build --target gdb-debug -t clickhouse-gdb-debug:24.8.14.39 .
#
# gdb has the binary's symbol table: function names, no arguments or lines.
# gdb-debug adds the server's debug information: arguments, locals, types and
# source lines.
#
# The server image is Ubuntu 20.04, whose GDB 9.2 does not read the DWARF 5
# the debug information is in. The server binary is static, so it runs here
# on Ubuntu 26.04 and its GDB 17.

FROM --platform=linux/amd64 clickhouse/clickhouse-server:24.8.14.39 AS server

# The debug information must be of the server's exact version: GDB loads
# /usr/lib/debug/usr/bin/clickhouse.debug only when its build-id matches.
FROM server AS debug-info
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      clickhouse-common-static-dbg=$(clickhouse local -q "SELECT version()") \
 && rm -rf /var/lib/apt/lists/*

FROM --platform=linux/amd64 ubuntu:26.04 AS gdb
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gdb procps \
 && rm -rf /var/lib/apt/lists/*
# The user and the directories the server image has.
RUN groupadd -r -g 101 clickhouse \
 && useradd -r -u 101 -g clickhouse -d /var/lib/clickhouse -s /bin/false clickhouse \
 && mkdir -p /var/lib/clickhouse /var/log/clickhouse-server /docker-entrypoint-initdb.d \
 && chown clickhouse:clickhouse /var/lib/clickhouse /var/log/clickhouse-server
COPY --from=server /usr/bin/clickhouse /usr/bin/clickhouse
RUN for tool in server client local; do ln -s clickhouse /usr/bin/clickhouse-$tool; done
COPY --from=server /etc/clickhouse-server /etc/clickhouse-server
COPY --from=server /etc/clickhouse-client /etc/clickhouse-client
COPY --from=server /entrypoint.sh /entrypoint.sh
ENV TZ=UTC CLICKHOUSE_CONFIG=/etc/clickhouse-server/config.xml
COPY start.sh gdb.sh session.gdb /examples/gdb/
ENTRYPOINT ["/examples/gdb/start.sh"]

FROM gdb AS gdb-debug
COPY --from=debug-info /usr/lib/debug/usr/bin/clickhouse.debug /usr/lib/debug/usr/bin/clickhouse.debug
