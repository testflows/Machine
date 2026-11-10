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

# The testflows/machine-examples image: every program, static, at
# /examples/<name>. A machine is x86_64, so the image is linux/amd64 only.
FROM --platform=linux/amd64 debian:bookworm-slim AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev make file \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY Makefile ./
COPY hello hello
COPY races races
COPY starvation starvation
COPY stress stress
RUN make check

# Nothing but the programs: they are static, so they need no libraries, and
# a disk built from this image carries only what it runs.
FROM scratch
ARG VERSION=dev
LABEL org.opencontainers.image.title="TestFlows™ Machine examples" \
      org.opencontainers.image.description="Programs to run on TestFlows™ Machine" \
      org.opencontainers.image.url="https://testflows.com/machine/" \
      org.opencontainers.image.source="https://github.com/testflows/Machine-Examples" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Katteli Inc." \
      org.opencontainers.image.version="${VERSION}"
COPY --from=build /src/bin/ /examples/
COPY LICENSE /LICENSE
ENTRYPOINT ["/examples/hello-world"]
