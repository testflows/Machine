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

# PostgreSQL server, in two targets:
#
#   docker build --target server -t postgresql-server:17.11 .
#   docker build --target debug -t postgresql-debug:17.11 .
#
# server is the server as it ships: its symbol table, function names and no
# arguments or lines. debug adds the server's debug information from PGDG's
# postgresql-17-dbgsym: arguments, locals, types and source lines. Both carry
# GDB, for a session of GDB inside the machine; the machine's own debugger
# needs none.

FROM --platform=linux/amd64 postgres:17.11 AS server
# A machine has no one to type a password: the server trusts local clients.
ENV POSTGRES_HOST_AUTH_METHOD=trust
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      gdb procps \
 && rm -rf /var/lib/apt/lists/*
COPY start.sh gdb.sh /examples/debug/
ENTRYPOINT ["/examples/debug/start.sh"]

# The debug information must be of the server's exact build: a debugger
# takes /usr/lib/debug/.build-id/NN/REST.debug only when its build id is the
# server's. PG_VERSION is the image's package version, 17.11-1.pgdg13+2.
FROM server AS debug
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      postgresql-17-dbgsym="$PG_VERSION" \
 && rm -rf /var/lib/apt/lists/*
