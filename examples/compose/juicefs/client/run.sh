#!/bin/bash
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

# Write data.bin into JuiceFS in appends, so its chunks hold many slices,
# then read it back cold, through a fresh read-only mount with an empty
# cache, and compare. Repeats ROUNDS times. Prints one verdict line a round.
set -u
JFS=/usr/local/bin/juicefs
META=redis://redis:6379/1
SRC=/data.bin
ROUNDS=${ROUNDS:-5}

log() { echo "jfs | $*"; }

# Mount META at DIR with the remaining options, or stop the test: writes to an
# unmounted directory land on local disk and prove nothing.
mount_or_die() {
  local dir=$1; shift
  if ! $JFS mount -d --no-usage-report "$@" $META $dir >/tmp/mount-$(basename $dir).log 2>&1; then
    log "mount of $dir failed:"
    tail -5 /tmp/mount-$(basename $dir).log | sed 's/^/jfs |   /'
    exit 2
  fi
}

until $JFS status $META >/dev/null 2>&1 || $JFS format --storage s3 \
    --bucket http://rustfs:9000/jfs --access-key jfsadmin --secret-key jfssecret123 \
    --compress zstd --block-size 4096 --trash-days 0 $META vol >/tmp/format.log 2>&1; do
  sleep 1
done
log "$($JFS version | head -1), volume formatted"

want=$(sha256sum < $SRC | cut -c1-16)
size=$(stat -c %s $SRC)
bad=0
for round in $(seq 1 $ROUNDS); do
  mkdir -p /w /r
  mount_or_die /w --cache-dir /cache-w
  f=/w/file-$round
  # One open fd, appends of irregular sizes, 13 KiB to 1 MiB, with a flush
  # between them, as a slow network writer does. Irregular sizes end slices
  # inside a zstd block, where the overrun of #7555 reaches the next slice.
  exec 3>>$f
  off=0
  while [ $off -lt $size ]; do
    n=$(( (RANDOM * 32768 + RANDOM) % (1048576 - 13312) + 13312 ))
    [ $((off + n)) -gt $size ] && n=$((size - off))
    tail -c +$((off + 1)) $SRC | head -c $n >&3
    sync $f
    off=$((off + n))
  done
  exec 3>&-
  slices=$($JFS info $f 2>/dev/null | grep -c -E '^\|\s+[0-9]+ \|')
  $JFS umount /w
  # Cold read: a fresh read-only mount, which never compacts, and no cache.
  # 4 MiB reads, so one read spans several slices.
  rm -rf /cache-r
  mount_or_die /r --read-only --cache-dir /cache-r
  got=$(dd if=/r/file-$round bs=4M status=none | sha256sum | cut -c1-16)
  if [ "$got" = "$want" ]; then
    log "round $round: clean ($slices slice rows)"
  else
    bad=$((bad + 1))
    first=$(cmp /r/file-$round $SRC 2>/dev/null | awk '{print $5}' | tr -d ,)
    diffs=$(cmp -l /r/file-$round $SRC 2>/dev/null | wc -l)
    log "round $round: CORRUPT sha $got != $want, first byte $first, $diffs bytes differ ($slices slice rows)"
  fi
  $JFS umount /r
done
log "done: $bad of $ROUNDS rounds corrupt"
