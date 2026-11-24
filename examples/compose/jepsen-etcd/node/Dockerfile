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

# A Jepsen DB node: sshd for the control node, and etcd already on disk,
# because a machine reaches no network to download it.
FROM debian:bookworm-slim

ARG ETCD_VERSION=v3.5.17

# Jepsen stops etcd with killall, from psmisc.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      openssh-server sudo iptables iproute2 procps psmisc curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Jepsen partitions with iptables: nft, Debian's default, or legacy with
# --build-arg IPTABLES=legacy. The machine's kernel has both.
ARG IPTABLES=nft
RUN update-alternatives --set iptables /usr/sbin/iptables-${IPTABLES} \
 && update-alternatives --set ip6tables /usr/sbin/ip6tables-${IPTABLES}

RUN mkdir -p /opt/etcd /run/sshd \
 && curl -fsSL "https://github.com/etcd-io/etcd/releases/download/${ETCD_VERSION}/etcd-${ETCD_VERSION}-linux-amd64.tar.gz" \
    | tar -xz -C /opt/etcd --strip-components=1

# The control node logs in as root with a key it makes at its start, which
# it shares with the nodes through the ssh-keys volume.
RUN sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config \
 && echo 'AuthorizedKeysFile /keys/authorized_keys' >> /etc/ssh/sshd_config \
 && ssh-keygen -A

CMD ["/usr/sbin/sshd", "-D", "-e"]
