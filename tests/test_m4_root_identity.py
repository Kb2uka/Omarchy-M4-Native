# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from pathlib import Path
import subprocess
source=(Path(__file__).resolve().parents[1] / 'tools' / 'm4-root-identity.sh').read_text()
part='71b71300-2026-4000-8000-000000000008'
fs='19b71300-2026-4000-8000-000000000008'
gpt='0fc63daf-8483-4772-8e79-3d69d8477de4'
base=[part,fs,'nvme0n1',part,fs,'ext4','nvme0n1',gpt]
cases=[('valid',base,0)]
for name,index in [('matching_malformed_part',0),('matching_malformed_fs',1)]:
 args=base.copy();args[index]='invalid';args[index+3]='invalid';cases.append((name,args,1))
for name,index,value in [('malformed_part',0,'invalid'),('malformed_fs',1,'-bad'),('wrong_partition',3,fs),('wrong_filesystem',4,part),('apfs',5,'apfs'),('wrong_namespace',6,'nvme0n2'),('wrong_gpt_type',7,'c12a7328-f81f-11d2-ba4b-00a0c93ec93b')]:
 args=base.copy();args[index]=value;cases.append((name,args,1))
uuid_cases = [
 ('valid', part, True),
 ('newline_suffix', part + '\ninvalid', False),
 ('newline_prefix', 'invalid\n' + part, False),
 ('trailing_newline', part + '\n', False),
 ('non_hex', 'zzzzzzzz-2026-4000-8000-000000000008', False),
]
for index, character in enumerate(part):
 for label, replacement in [('foreign', 'x' if character == '-' else 'z'), ('wrong_class', '0' if character == '-' else '-')]:
  invalid = part[:index] + replacement + part[index+1:]
  uuid_cases.append((f'{label}_position_{index}', invalid, False))
def check(text):
 for name,value,expected in uuid_cases:
  result=subprocess.run(['sh','-c',text+'\nvalid_uuid "$1"','uuid',value],capture_output=True)
  if (result.returncode==0)!=expected:raise AssertionError(name)

 for name,args,expected in cases:
  result=subprocess.run(['sh','-c',text+'\nvalidate_root_identity "$@"','identity',*args],capture_output=True)
  if (result.returncode==0)!=(expected==0):raise AssertionError(name)
check(source)
print('ROOT_IDENTITY_PASS',len(cases),'identity cases;',len(uuid_cases),'UUID cases')
for guard in ['test "${#1}" -eq 36 || return 1', 'test "$actual_part" = "$expected_part" || return 1','test "$actual_fs" = "$expected_fs" || return 1','test "$actual_type" = ext4 || return 1','test "$actual_parent" = "$expected_parent" || return 1','test "$actual_gpt_type" = '+gpt+' || return 1']:
 assert source.count(guard)==1
 try:check(source.replace(guard,':'))
 except AssertionError as e:print('MUTANT_KILLED',str(e))
 else:raise AssertionError('guard deletion survived')
