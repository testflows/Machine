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

# A JuiceFS client built from a release tag, with the test and its data,
# because a machine reaches no network.
#
# JuiceFS compresses with DataDog/zstd, which is cgo: CGO_ENABLED=1 builds
# the client a release ships. A build without cgo uses another zstd and does
# not have the bug.
FROM golang:1.25-bookworm AS juicefs
ARG JUICEFS_VERSION=v1.3.1
RUN git clone --depth 1 --branch ${JUICEFS_VERSION} https://github.com/juicedata/juicefs /src
WORKDIR /src
RUN CGO_ENABLED=1 go build -o /juicefs .

FROM python:3.12-slim AS data
COPY data.py /data.py
RUN python3 /data.py > /data.bin

FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates fuse3 procps \
 && rm -rf /var/lib/apt/lists/*
COPY --from=juicefs /juicefs /usr/local/bin/juicefs
COPY --from=data /data.bin /data.bin
COPY run.sh /run.sh
CMD ["/run.sh"]
