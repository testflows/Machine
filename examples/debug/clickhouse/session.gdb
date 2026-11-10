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
#
# A GDB session on clickhouse-server: a breakpoint where a query is executed,
# stepi, next, finish, a hardware breakpoint, a hardware watchpoint and
# detach. Positions print as symbol+offset, which does not depend on where
# ASLR put the binary, so two sessions compare line by line. The queries come
# from start.sh, one a second.
#
#   gdb -batch -x session.gdb -p $(pidof clickhouse-server)

set pagination off
set confirm off
set print thread-events off
set width 0
# The query profiler's timers, and the server's own signals.
handle SIGUSR1 SIGUSR2 SIG34 SIG35 SIG36 nostop noprint pass

echo == break\n
break DB::executeQuery
continue
echo == at the breakpoint\n
x/i $pc
bt 3
# The function's entry, for the hardware breakpoint below: by its name it
# is every inlined copy too, more places than there are registers.
set $query = $pc
delete

echo == stepi 20\n
set $i = 0
while $i < 20
  stepi
  x/i $pc
  set $i = $i + 1
end

echo == next 3\n
next
next
next
x/i $pc

echo == finish\n
finish
x/i $pc

echo == hbreak\n
hbreak *$query
continue
x/i $pc
bt 1

echo == watch\n
delete
# A hardware watchpoint on the caller's frame pointer slot: written as the
# callee returns, so the next write is a fixed instruction.
watch -l *(long *)$sp
continue
x/i $pc

echo == detach\n
delete
detach
