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

# Make the key the control node logs in with and authorize it on the nodes,
# which read /keys/authorized_keys from the same volume. Then run the test.
set -e

mkdir -p /root/.ssh
ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519
cp /root/.ssh/id_ed25519.pub /keys/authorized_keys
chmod 644 /keys/authorized_keys

exec java -Xmx1g -jar etcd-machine.jar test \
  --nodes n1,n2,n3 --username root --ssh-private-key /root/.ssh/id_ed25519 \
  --time-limit 30 --concurrency 6 "$@"
