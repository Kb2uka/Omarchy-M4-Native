#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kazuhide Takahashi
"""Read the 15-inch M4 Air's (J715) speaker ID, which picks its AID38 filter set.

The J715 ADT's first amplifier (i2c3 audio-speaker@38) names two GPIOs on the
AP pinctrl (0x39a000000) as function-speaker-id0 and function-speaker-id1:
pins 4 and 13, and lists 0 and 3 as the only valid IDs. Firmware leaves both
as pulled-up inputs that no Linux driver claims. One J715 whose macOS reports
speaker ID 3 reads 1 on both, so the ID is pin 4 as bit 0 and pin 13 as bit 1.
With only 0 and 3 valid the bit order cannot matter, and any other reading is
refused, so no filter set is chosen.

The lines are requested as-is: no direction, bias or drive flags. The Apple
pinctrl driver has no request hook, so such a request writes no register. The
request is released at once. Prints JSON; exit 1 on a refusal.
"""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import sys

BOARD = b'apple,j715'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
CHIP_LABEL = '39a000000.pinctrl'
PINS = (4, 13)  # function-speaker-id0, function-speaker-id1
VALID_IDS = (0, 3)
CONSUMER = b'm4-j715-speaker-id'

# linux/gpio.h, uAPI v2 (stable ABI).
GPIO_V2_LINES_MAX = 64
GPIO_V2_LINE_NUM_ATTRS_MAX = 10


class GpioChipInfo(ctypes.Structure):
    _fields_ = [('name', ctypes.c_char * 32), ('label', ctypes.c_char * 32), ('lines', ctypes.c_uint32)]


class GpioV2LineAttribute(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint32), ('padding', ctypes.c_uint32), ('value', ctypes.c_uint64)]


class GpioV2LineConfigAttribute(ctypes.Structure):
    _fields_ = [('attr', GpioV2LineAttribute), ('mask', ctypes.c_uint64)]


class GpioV2LineConfig(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint64), ('num_attrs', ctypes.c_uint32), ('padding', ctypes.c_uint32 * 5),
                ('attrs', GpioV2LineConfigAttribute * GPIO_V2_LINE_NUM_ATTRS_MAX)]


class GpioV2LineRequest(ctypes.Structure):
    _fields_ = [('offsets', ctypes.c_uint32 * GPIO_V2_LINES_MAX), ('consumer', ctypes.c_char * 32),
                ('config', GpioV2LineConfig), ('num_lines', ctypes.c_uint32),
                ('event_buffer_size', ctypes.c_uint32), ('padding', ctypes.c_uint32 * 5), ('fd', ctypes.c_int32)]


class GpioV2LineValues(ctypes.Structure):
    _fields_ = [('bits', ctypes.c_uint64), ('mask', ctypes.c_uint64)]


def ioc(direction, number, structure):
    return (direction << 30) | (ctypes.sizeof(structure) << 16) | (0xB4 << 8) | number


GPIO_GET_CHIPINFO_IOCTL = ioc(2, 0x01, GpioChipInfo)
GPIO_V2_GET_LINE_IOCTL = ioc(3, 0x07, GpioV2LineRequest)
GPIO_V2_LINE_GET_VALUES_IOCTL = ioc(3, 0x0E, GpioV2LineValues)


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def board(root):
    try:
        return BOARD in (root / COMPATIBLE).read_bytes().split(b'\0')
    except OSError:
        return False


def find_chip(root, ioctl):
    """The /dev/gpiochipN whose label is the AP pinctrl, opened read-only."""
    found = []
    for path in sorted((root / 'dev').glob('gpiochip*')):
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        info = GpioChipInfo()
        try:
            ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, info)
        except OSError:
            os.close(fd)
            continue
        if info.label.decode() == CHIP_LABEL:
            found.append((path, fd, info.lines))
        else:
            os.close(fd)
    require(len(found) == 1, f'Expected one {CHIP_LABEL} GPIO chip, found {len(found)}')
    path, fd, lines = found[0]
    if max(PINS) >= lines:
        os.close(fd)
        raise Refused(f'{path} has {lines} lines')
    return path, fd


def read_pins(fd, ioctl):
    """Request PINS as-is, read both values and release them."""
    request = GpioV2LineRequest()
    for index, pin in enumerate(PINS):
        request.offsets[index] = pin
    request.num_lines = len(PINS)
    request.consumer = CONSUMER
    request.config.flags = 0  # as-is: no direction, bias or drive change
    ioctl(fd, GPIO_V2_GET_LINE_IOCTL, request)
    try:
        values = GpioV2LineValues(mask=(1 << len(PINS)) - 1)
        ioctl(request.fd, GPIO_V2_LINE_GET_VALUES_IOCTL, values)
    finally:
        os.close(request.fd)
    return [(values.bits >> index) & 1 for index in range(len(PINS))]


def run(root, ioctl=None):
    ioctl = ioctl or fcntl.ioctl
    report = {'board': board(root), 'pins': dict(zip(map(str, PINS), (None, None))), 'speaker_id': None}
    require(report['board'], 'This is not a J715 MacBook Air')
    path, fd = find_chip(root, ioctl)
    try:
        report['chip'] = path.name
        bits = read_pins(fd, ioctl)
    finally:
        os.close(fd)
    report['pins'] = dict(zip(map(str, PINS), bits))
    speaker_id = bits[0] | bits[1] << 1
    require(speaker_id in VALID_IDS, f'Speaker ID pins read {bits}; only {VALID_IDS} are valid')
    report['speaker_id'] = speaker_id
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.root == Path('/') and os.geteuid() != 0:
        print('m4-j715-speaker-id: run with sudo', file=sys.stderr)  # /dev/gpiochip* are root-only
        return 1
    try:
        report = run(args.root)
    except (Refused, OSError) as error:
        print('m4-j715-speaker-id: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
