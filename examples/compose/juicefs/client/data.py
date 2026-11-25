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

# 64 MiB of compressible data that is full of literals, as in the test that
# came with the fix: random hex, a number and a repeated path on each line.
# zstd stores random data almost as is and never reaches the broken code.
import random
import sys

SIZE = 64 << 20

r = random.Random(1)
lines, n = [], 0
while n < SIZE:
    line = (
        f"{r.getrandbits(64):016x}{r.getrandbits(64):016x}"
        f"\t{r.randrange(1 << 20)}\tsome/shared/prefix/dir/file-{n}\n"
    ).encode()
    lines.append(line)
    n += len(line)
sys.stdout.buffer.write(b"".join(lines)[:SIZE])
