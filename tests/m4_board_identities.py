# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The M4 board table (packaging/m4-boards.json) as test fixtures.

Tests that check a board gate import the real board identities from here, so
every gate is tested against the same sibling M4 Macs.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / 'packaging/m4-boards.json'
TABLE = json.loads(TABLE_PATH.read_text())
BOARDS = TABLE['boards']
SOC = TABLE['soc']['compatible']
QUALIFIED = [board for board in BOARDS if board['support'] == 'qualified']
REFUSED = [board for board in BOARDS if board['support'] == 'refused']


# The J713 board name on another SoC (M2): a gate that checks only the board accepts it.
FOREIGN_SOC = ('J713 on T8112', b'apple,j713\0apple,t8112\0apple,arm-platform\0')
# Names that only start with the qualified board or SoC name: a substring or whole-word
# match (the hyphen ends the word) accepts them; only a whole-name match refuses them.
NEAR_MISS_BOARD = ('near-miss board', b'apple,j713-x\0apple,t8132\0apple,arm-platform\0')
NEAR_MISS_SOC = ('near-miss SoC', b'apple,j713\0apple,t8132-x\0apple,arm-platform\0')


def identity(board):
    """The root compatible list m1n1 hands Linux on that Mac (upstream t8132-<board>.dts)."""
    return f"{board['compatible']}\0{SOC}\0apple,arm-platform\0".encode()


def refused_identities():
    """(label, compatible) pairs a gate that checks both board and SoC must refuse."""
    return [(board['target'], identity(board)) for board in REFUSED] + [FOREIGN_SOC, NEAR_MISS_BOARD, NEAR_MISS_SOC]
