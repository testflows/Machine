# Builds every example program as a static x86_64 binary in bin/, which is what
# `machine disks build --binary` takes, and what the testflows/machine-examples
# image carries.
#
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

CC = gcc
# -static because `--binary` refuses anything asking for an interpreter: the
# image it writes has no loader and no libraries. -pthread because most of
# these programs are about what threads do to each other.
CFLAGS = -O2 -static -pthread

# One directory per kind of example. compose/ holds projects, not programs.
GROUPS = hello races starvation stress

SOURCES = $(wildcard $(addsuffix /*.c,$(GROUPS)))
BINARIES = $(patsubst %.c,bin/%,$(notdir $(SOURCES)))

.PHONY: all clean check list

all: $(BINARIES)
	@echo "Built: $(BINARIES)"

# One rule per group, because the sources are in several directories and the
# binaries land in one.
define build_group
bin/%: $(1)/%.c | bin
	$$(CC) $$(CFLAGS) $$< -o $$@
endef
$(foreach group,$(GROUPS),$(eval $(call build_group,$(group))))

bin:
	mkdir -p bin

# What `--binary` checks before it packs anything, asked here so a binary that
# would be refused is caught by the build that made it rather than by the
# upload that carries it.
check: all
	@for b in $(BINARIES); do \
	  file -b "$$b" | grep -q "statically linked" \
	    || { echo "$$b: not static"; exit 1; }; \
	  file -b "$$b" | grep -q "x86-64" \
	    || { echo "$$b: not x86_64"; exit 1; }; \
	  echo "$$b: static x86_64"; \
	done

list:
	@echo $(SOURCES) | tr ' ' '\n'

clean:
	rm -rf bin
