#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Kernel error and warning lines that appear in boot NEW but not in boot BASE,
# with numbers masked so addresses, timings and counters do not count as new.
# usage: errdiff.sh BASE NEW   (journalctl boot offsets, e.g. -5 -1)
norm() { journalctl -k -b "$1" -p warning --no-pager -o cat | sed -E 's/0x[0-9a-f]+/H/g; s/[0-9]+/N/g' | sort -u; }
echo "boot $1: $(journalctl -k -b "$1" -p err --no-pager -o cat | wc -l) err, $(journalctl -k -b "$1" -p warning --no-pager -o cat | wc -l) warning lines"
echo "boot $2: $(journalctl -k -b "$2" -p err --no-pager -o cat | wc -l) err, $(journalctl -k -b "$2" -p warning --no-pager -o cat | wc -l) warning lines"
echo "--- kinds only in boot $2:"
comm -13 <(norm "$1") <(norm "$2")
