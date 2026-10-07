#!/usr/bin/env python3
"""Check the pinned radio patch against files from a supplied kernel tree."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

DRIVER = Path('drivers/net/wireless/broadcom/brcm80211/brcmfmac')


def uncomment(source):
    source = source.replace(chr(92) + '\n', '')
    # Preserve quoted literals and line boundaries while removing C comments.
    token = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*'
    return re.sub(token, lambda m: (' ' + '\n' * m[0].count('\n'))
                  if m[0].startswith(('/',)) else m[0], source)


def firmware_section(tree):
    source = uncomment((tree / DRIVER / 'pcie.c').read_text())
    prefix, marker, _ = source.partition('#define BRCMF_PCIE_FW_UP_TIMEOUT')
    assert marker, 'pinned firmware section boundary missing'
    # The pinned table is unconditional. Fail closed if preprocessing could
    # hide or redefine an entry rather than trying to evaluate C directives.
    directives = re.findall(r'^[^\S\n]*(?:#|%:)[^\S\n]*(\w+)[^\S\n]*([^\n]*)', prefix, re.MULTILINE)
    assert all(kind == 'include' or
               (kind == 'define' and body.startswith('brcmf_err('))
               for kind, body in directives), 'conditional or redefined firmware table'
    return prefix


def firmware_entries(source):
    entries = []
    for arguments in re.findall(r'BRCMF_FW_ENTRY\s*\(([^)]*)\)', source):
        match = re.fullmatch(r'(\w+_CHIP_ID),\s*(0x[0-9A-Fa-f]+),\s*(\w+)', arguments.strip())
        assert match, ('unsupported firmware entry syntax', arguments)
        entries.append(match.groups())
    return entries


def check(tree, original=None):
    source = firmware_section(tree)
    mappings = [(mask, name) for chip, mask, name in firmware_entries(source)
                if chip == 'BRCM_CC_4388_CHIP_ID']
    assert mappings, '4388 firmware table missing'
    # Selection uses the first matching revision bit. Check every defined bit,
    # including the existing populations on each side of revision six.
    for revision in range(32):
        selected = next(
            (name for mask, name in mappings if int(mask, 16) & (1 << revision)),
            None,
        )
        expected = '4388B0' if revision < 4 else '4388C2' if revision == 6 else '4388C0'
        assert selected == expected, (revision, selected, expected)
    assert 'BRCMF_FW_CLM_DEF(4388C2, "brcmfmac4388c2-pcie");' in source
    if original is not None:
        base = firmware_section(original)
        other = lambda text: [row for row in firmware_entries(text) if row[0] != 'BRCM_CC_4388_CHIP_ID']
        assert other(source) == other(base), 'another chip firmware mapping changed'
        definition = r'BRCMF_FW_CLM_DEF\((\w+),\s*"([^"\n]+)"\)'
        existing = lambda text: [row for row in re.findall(definition, text) if row[0] != '4388C2']
        assert existing(source) == existing(base), 'an existing firmware filename changed'
    header = uncomment((tree / DRIVER / 'fwil_types.h').read_text())
    for name, expected in (('MIN', 109), ('MAX', 116)):
        definitions = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+BRCMF_BSS_INFO_' + name
            + r'_VERSION[ \t]+([^\n]*)$', header, re.MULTILINE,
        )
        assert len(definitions) == 1, (name, 'expected one definition', definitions)
        # The pinned patch uses a literal with an optional block comment.
        # Reject expressions and redefinitions instead of reading a digit prefix.
        value = re.sub(r'/\*.*?\*/', '', definitions[0]).strip()
        assert re.fullmatch(str(expected), value), (name, value)
    print('PASS: revision 6 selects C2; other 31 mappings unchanged; scan range 109..116')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--patch', type=Path)
    args = parser.parse_args()
    if args.patch is None:
        check(args.kernel)
        return
    with tempfile.TemporaryDirectory(prefix='j713-radio-patch-') as temp:
        tree = Path(temp)
        target = tree / DRIVER
        target.mkdir(parents=True)
        for name in ('pcie.c', 'fwil_types.h'):
            shutil.copyfile(args.kernel / DRIVER / name, target / name)
        subprocess.run(['git', 'apply', '--check', '--whitespace=error-all', str(args.patch.resolve())], cwd=tree, check=True)
        subprocess.run(['git', 'apply', '--whitespace=error-all', str(args.patch.resolve())], cwd=tree, check=True)
        check(tree, args.kernel)


if __name__ == '__main__':
    main()
