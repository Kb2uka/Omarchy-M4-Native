#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# (root) gate.sh open|close: raise or restore the boost-gate trip (the 20 C trip on cpu-die-thermal).
Z=$(dirname $(grep -lx cpu-die-thermal /sys/class/thermal/thermal_zone*/type))
for t in $Z/trip_point_*_temp; do
  v=$(cat $t)
  if [ "$1" = open ] && [ "$v" = 20000 ]; then echo 150000 > $t; echo "opened $(basename $t)"; fi
  if [ "$1" = close ] && [ "$v" = 150000 ]; then echo 20000 > $t; echo "closed $(basename $t)"; fi
done
