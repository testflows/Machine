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

# Bombadil's terminal examples, with the programs they test. Bombadil is
# Antithesis's property-based tester for web and terminal UIs, MIT-licensed:
# https://github.com/antithesishq/bombadil. The binary and the specifications
# are fetched from its release, pinned.
ARG BOMBADIL_VERSION=0.8.0
ARG BOMBADIL_SHA256=d9d1d2832cb23c874744a4f20526d2064ce8571c31f8ecd01ddf8e1c44da4814
# The tb example tests TigerBeetle's REPL.
ARG TIGERBEETLE_VERSION=0.17.9
ARG TIGERBEETLE_SHA256=af71f2c0057e3b409bf79940fa94894b738187b0d4f1e711097bca15df5d8cd4

FROM ubuntu:24.04 AS fetch
ARG BOMBADIL_VERSION
ARG BOMBADIL_SHA256
ARG TIGERBEETLE_VERSION
ARG TIGERBEETLE_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates unzip patch \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /bombadil \
      "https://github.com/antithesishq/bombadil/releases/download/v${BOMBADIL_VERSION}/bombadil-x86_64-linux" \
 && echo "${BOMBADIL_SHA256}  /bombadil" | sha256sum -c - \
 && chmod +x /bombadil
RUN mkdir /examples \
 && for spec in cat nano btop tetris tb; do \
      curl -fsSL -o "/examples/${spec}.ts" \
        "https://raw.githubusercontent.com/antithesishq/bombadil/v${BOMBADIL_VERSION}/examples/${spec}.ts"; \
    done
# nano's property with eventually in place of next: see the README.
COPY nano-eventually.patch /nano-eventually.patch
RUN cp /examples/nano.ts /examples/nano-eventually.ts \
 && patch /examples/nano-eventually.ts /nano-eventually.patch
RUN curl -fsSL -o /tigerbeetle.zip \
      "https://github.com/tigerbeetle/tigerbeetle/releases/download/${TIGERBEETLE_VERSION}/tigerbeetle-x86_64-linux.zip" \
 && echo "${TIGERBEETLE_SHA256}  /tigerbeetle.zip" | sha256sum -c - \
 && unzip -q /tigerbeetle.zip -d / \
 && chmod +x /tigerbeetle

FROM ubuntu:24.04
# btop refuses to start without a UTF-8 locale.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
# bastet is the tetris the tetris example plays.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nano btop bastet \
 && rm -rf /var/lib/apt/lists/*
COPY --from=fetch /bombadil /usr/local/bin/bombadil
COPY --from=fetch /tigerbeetle /usr/local/bin/tigerbeetle
COPY --from=fetch /examples /bombadil/examples
COPY run.sh /bombadil/run.sh
WORKDIR /root
ENTRYPOINT ["/bin/sh", "/bombadil/run.sh"]
