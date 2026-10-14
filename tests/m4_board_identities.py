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


def identity(board):
    """The root compatible list m1n1 hands Linux on that Mac (upstream t8132-<board>.dts)."""
    return f"{board['compatible']}\0{SOC}\0apple,arm-platform\0".encode()
