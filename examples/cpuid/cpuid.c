/*
 * Copyright 2026 Katteli Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * CPUID: what the machine tells a program about the processor it runs on.
 *
 * It reads three things with the CPUID instruction and prints each as the
 * bytes the processor returned:
 *
 *   - the hypervisor bit, leaf 1 ECX bit 31, which a program checks before it
 *     trusts the leaf below;
 *   - the hypervisor vendor, leaf 0x40000000, 12 bytes in EBX, ECX and EDX;
 *   - the processor brand, leaves 0x80000002 to 0x80000004, 48 bytes.
 *
 * Nothing in it depends on timing, so every run prints the same thing on the
 * same machine. On a computer with no hypervisor the bit is clear and the
 * vendor leaf is not read, since such a processor returns whatever its last
 * basic leaf held.
 */

#include <cpuid.h>
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define LEAF_FEATURES		1u
#define FEATURES_ECX_HYPERVISOR	(1u << 31)
#define LEAF_HYPERVISOR		0x40000000u
#define LEAF_EXTENDED_MAX	0x80000000u
#define LEAF_BRAND_FIRST	0x80000002u
#define LEAF_BRAND_LAST		0x80000004u

#define REGISTER_BYTES		4
#define VENDOR_BYTES		(3 * REGISTER_BYTES)
#define BRAND_LEAVES		(LEAF_BRAND_LAST - LEAF_BRAND_FIRST + 1)
#define BRAND_BYTES		(BRAND_LEAVES * 4 * REGISTER_BYTES)
#define ROW_BYTES		16

/* Print bytes as `hd` does: offset, hex in two groups of eight, then text. */
static void dump(const unsigned char *data, size_t length)
{
	for (size_t row = 0; row < length; row += ROW_BYTES) {
		size_t end = row + ROW_BYTES < length ? row + ROW_BYTES : length;
		size_t i;

		printf("%08zx ", row);
		for (i = row; i < row + ROW_BYTES; i++) {
			if (i == row + ROW_BYTES / 2)
				printf(" ");
			if (i < end)
				printf(" %02x", data[i]);
			else
				printf("   ");
		}
		printf("  |");
		for (i = row; i < end; i++)
			putchar(isprint(data[i]) ? data[i] : '.');
		printf("|\n");
	}
}

static void put_register(unsigned char *out, unsigned int value)
{
	memcpy(out, &value, REGISTER_BYTES);
}

static void print_vendor(void)
{
	unsigned int eax, ebx, ecx, edx;
	unsigned char vendor[VENDOR_BYTES];

	__cpuid(LEAF_HYPERVISOR, eax, ebx, ecx, edx);
	put_register(vendor + 0 * REGISTER_BYTES, ebx);
	put_register(vendor + 1 * REGISTER_BYTES, ecx);
	put_register(vendor + 2 * REGISTER_BYTES, edx);

	printf("CPUID 0x%08x  hypervisor vendor, EBX:ECX:EDX\n", LEAF_HYPERVISOR);
	dump(vendor, sizeof(vendor));
}

static int print_brand(void)
{
	unsigned int eax, ebx, ecx, edx;
	unsigned char brand[BRAND_BYTES];
	unsigned char *at = brand;

	__cpuid(LEAF_EXTENDED_MAX, eax, ebx, ecx, edx);
	if (eax < LEAF_BRAND_LAST) {
		printf("CPUID 0x%08x  no processor brand\n", LEAF_BRAND_FIRST);
		return 1;
	}

	for (unsigned int leaf = LEAF_BRAND_FIRST; leaf <= LEAF_BRAND_LAST; leaf++) {
		__cpuid(leaf, eax, ebx, ecx, edx);
		put_register(at + 0 * REGISTER_BYTES, eax);
		put_register(at + 1 * REGISTER_BYTES, ebx);
		put_register(at + 2 * REGISTER_BYTES, ecx);
		put_register(at + 3 * REGISTER_BYTES, edx);
		at += 4 * REGISTER_BYTES;
	}

	printf("CPUID 0x%08x to 0x%08x  processor brand, EAX:EBX:ECX:EDX\n",
	       LEAF_BRAND_FIRST, LEAF_BRAND_LAST);
	dump(brand, sizeof(brand));
	return 0;
}

int main(void)
{
	unsigned int eax, ebx, ecx, edx;
	int hypervisor;

	__cpuid(LEAF_FEATURES, eax, ebx, ecx, edx);
	hypervisor = (ecx & FEATURES_ECX_HYPERVISOR) != 0;
	printf("CPUID 0x%08x  ECX bit 31, hypervisor present: %s\n\n",
	       LEAF_FEATURES, hypervisor ? "yes" : "no");

	if (hypervisor) {
		print_vendor();
		printf("\n");
	}
	return print_brand();
}
