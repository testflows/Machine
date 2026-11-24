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

# The Jepsen control node. The test is built into one jar with all its
# dependencies, because a machine reaches no network to fetch them.
FROM clojure:temurin-21-lein-bookworm AS build

WORKDIR /jepsen
COPY project.clj .
RUN lein deps
COPY src src
RUN lein uberjar

# The control node runs the jar alone, so it needs a Java runtime, not the
# JDK and the build's dependency cache. The runtime keeps every module:
# Jepsen's random numbers come from jdk.random, which a JRE image leaves out.
RUN jlink --add-modules ALL-MODULE-PATH --strip-java-debug-attributes --no-man-pages \
          --no-header-files --output /java

FROM debian:bookworm-slim
COPY --from=build /java /opt/java
ENV PATH=/opt/java/bin:$PATH

# Jepsen runs git at its start to record the test's revision, and fails
# without the binary even outside a repository. It downloads the nodes' logs
# with scp at its end.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /jepsen
COPY --from=build /jepsen/target/etcd-machine.jar .
COPY start.sh .
CMD ["sh", "start.sh"]
