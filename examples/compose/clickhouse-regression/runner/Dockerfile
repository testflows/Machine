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

# The suite needs Python, TestFlows and a docker CLI with the compose plugin.
# It needs no daemon of its own: it drives the machine's, over the socket the
# project binds.
FROM docker:27-cli AS cli

FROM python:3.12-slim
# The v2 plugin only. No `docker-compose` shim: the suite runs `docker compose`
# and reads its output.
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=cli /usr/local/libexec/docker/cli-plugins/docker-compose \
     /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=cli /usr/local/libexec/docker/cli-plugins/docker-buildx \
     /usr/local/libexec/docker/cli-plugins/docker-buildx
RUN pip install --no-cache-dir testflows==2.4.19 duckdb
