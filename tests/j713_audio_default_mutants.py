#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bounded policy mutations; invoke only on a leased development test host."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LUA_PATH = Path('audio/j713/policy/usr/share/wireplumber/scripts/default-nodes/j713-protected.lua')
CONF_PATH = Path('audio/j713/policy/etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lua', default='lua')
    args = parser.parse_args()
    lua = shutil.which(args.lua)
    if not lua:
        raise SystemExit('Lua 5.4 executable required')
    cases = [
        ('omit filter application', LUA_PATH,
         'event:set_data ("available-nodes", Json.Array (selectable))',
         '-- omitted filter application', 'FAIL clean owner gets protected sink'),
        ('drop sink-category guard', LUA_PATH,
         'if event:get_properties () ["default-node.type"] ~= "audio.sink" then',
         'if false then', 'FAIL audio source selection untouched'),
        ('broaden name to every raw speaker', LUA_PATH,
         'props ["node.name"] ~= "alsa_output.j713.RawSpeakers"',
         'not props ["node.name"]:find("RawSpeakers", 1, true)',
         'FAIL other board raw name preserved'),
        ('drop media-class guard', LUA_PATH,
         ' or\n          props ["media.class"] ~= "Audio/Sink"', '',
         'FAIL other media class untouched'),
        ('run after selection', LUA_PATH,
         '  before = {\n    "default-nodes/find-selected-default-node",\n'
         '    "default-nodes/find-stored-default-node",\n'
         '    "default-nodes/find-best-default-node",\n'
         '    "default-nodes/apply-default-node",\n  },',
         '  before = {},', 'FAIL legacy raw selection has no bypass'),
        ('allow raw when graph missing', LUA_PATH,
         '    local selectable = {}',
         '    if #available:parse () == 1 then return end\n    local selectable = {}',
         'FAIL raw alone never becomes default'),
        ('drop all candidates', LUA_PATH,
         'table.insert (selectable, Json.Object (props))', '-- dropped candidate',
         'FAIL configured external output preserved'),
        ('remove component requirement', CONF_PATH,
         'hooks.default-nodes.j713-protected = required',
         'hooks.default-nodes.j713-protected = disabled',
         'FAIL: test_component_required_and_loads_shipped_hook'),
        ('quote component dependency token', CONF_PATH,
         'requires = [ policy.default-nodes ]',
         'requires = [ "policy.default-nodes" ]',
         'FAIL: test_component_required_and_loads_shipped_hook'),
        ('load wrong hook path', CONF_PATH,
         'default-nodes/j713-protected.lua', 'default-nodes/absent.lua',
         'FAIL: test_component_required_and_loads_shipped_hook'),
    ]
    with tempfile.TemporaryDirectory(prefix='j713-default-mutants-') as tmp:
        copy = Path(tmp)
        for rel in (LUA_PATH, CONF_PATH, Path('tests/test_j713_audio_default.py'),
                    Path('tests/j713_audio_default_policy.lua')):
            dest = copy / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, dest)
        shutil.copytree(ROOT / 'tests/fixtures/wireplumber-0.5.18',
                        copy / 'tests/fixtures/wireplumber-0.5.18')
        env = dict(os.environ, LUA=lua, PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                   '-p', 'test_j713_audio_default.py', '-v']
        for name, rel, old, new, evidence in cases:
            target = copy / rel
            original = target.read_text()
            assert original.count(old) == 1, name
            target.write_text(original.replace(old, new))
            result = subprocess.run(command, cwd=copy, env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            target.write_text(original)
            assert result.returncode != 0 and evidence in result.stdout, (
                name, result.returncode, result.stdout)
            print('KILLED ' + name + ': ' + evidence, flush=True)
        subprocess.run(command, cwd=copy, env=env, check=True)
        print(f'{len(cases)} intended mutants rejected; restored suite passed')


if __name__ == '__main__':
    main()
