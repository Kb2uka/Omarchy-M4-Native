#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Offline checks; no serial device or network is opened."""
import importlib.util
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('air_trustcache', Path(__file__).resolve().parents[1] / 'tools/air_trustcache.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def fixture(compatible=b'apple,j713\0apple,t8132\0', entries=()):
    structure = struct.pack('>II', 1, 0)
    structure += struct.pack('>III', 3, len(compatible), 0) + compatible
    structure += b'\0' * (-len(compatible) % 4)
    structure += struct.pack('>II', 2, 9)
    table = b''.join(struct.pack('>QQ', *e) for e in entries) + b'\0' * 16
    strings = b'compatible\0'
    tree = 40 + len(table)
    offset_strings = tree + len(structure)
    return struct.pack('>10I', 0xd00dfeed, offset_strings + len(strings), tree,
                       offset_strings, 40, 17, 16, 0, len(strings), len(structure)) + table + structure + strings


def hardware():
    adt = 0x10005ab0000
    start, size = adt + 0x74000, 0x60000
    memory = {'DeviceTree': (adt, 0x74000), 'TrustCache': (start, size), 'BootKC-rw': (start + size, 0x110000)}
    proxy = SimpleNamespace(get_chipid=lambda: 0x8132, read32=lambda _: 0)
    utils = SimpleNamespace(adt={'/product': SimpleNamespace(unique_model='J713AP'), '/chosen/memory-map': SimpleNamespace(_properties=memory)},
                            ba=SimpleNamespace(phys_base=0x10002770000, mem_size=0x3daa74000),
                            mrs=lambda reg: adt if reg == M.D_LOWER else start + size - 0x1000)
    return proxy, utils, start, size


class Reservations(unittest.TestCase):
    def test_regression_trustcache_is_added(self):
        source = fixture()
        output = M.reserve(source, 0x10005b24000, 0x60000)
        self.assertEqual(struct.unpack_from('>QQ', output, 40), (0x10005b24000, 0x60000))
        self.assertEqual(output[56:72], b'\0' * 16)
        old, new = M.dtb_layout(source), M.dtb_layout(output)
        self.assertEqual(output[new[2]:], source[old[2]:])
        self.assertEqual(len(output), len(source) + 16)

    def test_existing_reservation_preserved_and_idempotent(self):
        source = fixture(entries=[(0x10004000000, 0x4000)])
        output = M.reserve(source, 0x10005b24000, 0x60000)
        self.assertEqual(struct.unpack_from('>QQ', output, 40), (0x10004000000, 0x4000))
        self.assertEqual(M.reserve(output, 0x10005b24000, 0x60000), output)

    def test_containing_reservation_needs_no_addition(self):
        source = fixture(entries=[(0x10005ab0000, 0xd4000)])
        self.assertEqual(M.reserve(source, 0x10005b24000, 0x60000), source)

    def test_partial_overlap_rejected(self):
        for entry in [(0x10005b20000, 0x8000), (0x10005b80000, 0x8000)]:
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                M.reserve(fixture(entries=[entry]), 0x10005b24000, 0x60000)

    def test_wrong_board_and_soc_rejected(self):
        for value in [b'apple,j713\0apple,t8122\0', b'apple,j714\0apple,t8132\0', b'apple,j713\0apple,t8132']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                M.reserve(fixture(value), 0x10005b24000, 0x60000)

    def test_malformed_headers_rejected(self):
        for word, value in [(0, 0), (1, 40), (2, 41), (3, 40), (4, 41), (5, 16), (6, 18), (8, 0xffffffff), (9, 0xffffffff)]:
            source = bytearray(fixture());struct.pack_into('>I', source, word * 4, value)
            with self.subTest(word=word), self.assertRaises(ValueError):
                M.reserve(source, 0x10005b24000, 0x60000)

    def test_missing_terminator_rejected(self):
        source = bytearray(fixture());struct.pack_into('>QQ', source, 40, 0x10004000000, 0x4000)
        with self.assertRaises(ValueError):M.reserve(source, 0x10005b24000, 0x60000)

    def test_invalid_ranges_rejected(self):
        for start,size in [(1,0x4000),(0x4000,1),(0,0),(-0x4000,0x4000),(1<<64,0x4000)]:
            with self.subTest(start=start,size=size),self.assertRaises(ValueError):M.reserve(fixture(),start,size)

    def test_parser_integer_subclasses_are_valid(self):
        class DisplayedInteger(int):
            pass
        proxy,utils,start,size=hardware()
        memory=utils.adt['/chosen/memory-map']._properties
        for key,value in memory.items():
            memory[key]=[DisplayedInteger(x) for x in value]
        utils.ba.phys_base=DisplayedInteger(utils.ba.phys_base)
        utils.ba.mem_size=DisplayedInteger(utils.ba.mem_size)
        self.assertEqual(M.live_range(proxy,utils),(start,size))

    def test_boolean_is_not_an_address_or_size(self):
        for value in ((True,0x4000),(0x4000,True)):
            with self.subTest(value=value),self.assertRaises(ValueError):
                M.checked_range(value,'invalid')

    def test_live_hardware_bounds(self):
        proxy,utils,start,size=hardware()
        self.assertEqual(M.live_range(proxy,utils),(start,size))

    def test_live_allocation_alignment_and_size(self):
        for shift, size in [(0x1000, 0x60000), (0, 16 * 1024 * 1024 + M.PAGE)]:
            proxy,utils,start,_=hardware()
            memory=utils.adt['/chosen/memory-map']._properties
            adt,adt_size=memory['DeviceTree']
            memory['DeviceTree']=(adt+shift,adt_size)
            memory['TrustCache']=(start+shift,size)
            memory['BootKC-rw']=(start+shift+size,0x110000)
            utils.mrs=lambda reg: adt+shift if reg==M.D_LOWER else start+shift+size-0x1000
            with self.subTest(shift=shift,size=size),self.assertRaisesRegex(ValueError,'alignment or TrustCache size'):
                M.live_range(proxy,utils)

    def test_forwarded_cli_forms_and_proxyclient_guard(self):
        class Proxy:
            kboot_prepare_dt=object()
        original=Proxy.kboot_prepare_dt
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);client=root/'client';(client/'m1n1').mkdir(parents=True)
            (client/'m1n1/proxy.py').touch();runtime=root/'runtime.py';runtime.touch()
            prefix=['air_trustcache.py','--proxyclient',str(client),'--watchdog-runtime',str(runtime),'--output-dtb',str(root/'output.dtb'),'--']
            cases=[(['--arm','--kernel','Image.gz','--dtb','board.dtb'],True),
                   (['--arm','--kernel=Image.gz','--dtb=board.dtb'],True),
                   (['--arm','--kernel','Image.gz','--dtb','board.dtb','--proxyclient=elsewhere'],False),
                   (['--arm','--kernel','Image.gz','--dtb','board.dtb','--proxyclient','elsewhere'],False),
                   (['--kernel=Image.gz','--dtb=board.dtb'],False),
                   (['--arm','--dtb=board.dtb'],False),
                   (['--arm','--kernel=Image.gz'],False)]
            for forwarded,accepted in cases:
                with self.subTest(forwarded=forwarded),patch.object(M.sys,'argv',prefix+forwarded),patch.object(M.sys,'path',list(M.sys.path)),patch.dict(M.sys.modules,{'m1n1.proxy':SimpleNamespace(M1N1Proxy=Proxy)}),patch.object(M.runpy,'run_path') as run:
                    if accepted:
                        seen=[]
                        run.side_effect=lambda *a,**kw:seen.append(list(M.sys.argv))
                        M.main();run.assert_called_once_with(str(runtime),run_name='__main__')
                        self.assertEqual(seen,[[str(runtime),'--proxyclient',str(client),*forwarded]])
                    else:
                        with self.assertRaises(SystemExit):M.main()
                        run.assert_not_called()
                    self.assertIs(Proxy.kboot_prepare_dt,original)

    def test_stale_or_wrong_hardware_rejected(self):
        changes = [lambda p,u:setattr(p,'get_chipid',lambda:0x8122),
                   lambda p,u:setattr(p,'read32',lambda _:4),
                   lambda p,u:setattr(u.adt['/product'],'unique_model','J714AP'),
                   lambda p,u:setattr(u,'mrs',lambda _:0),
                   lambda p,u:u.adt['/chosen/memory-map']._properties.update(TrustCache=(0x10005b20000,0x60000))]
        for change in changes:
            proxy,utils,_,_=hardware();change(proxy,utils)
            with self.subTest(change=change),self.assertRaises(ValueError):M.live_range(proxy,utils)

    def test_handoff_uploads_and_verifies_modified_dtb(self):
        proxy,utils,start,size=hardware();source=fixture();memory={0x800000:source};calls=[]
        utils.iface=SimpleNamespace(readmem=lambda address,length:memory[address][:length],writemem=lambda address,data:memory.update({address:data}))
        utils.malloc=lambda length:0x900000
        with tempfile.TemporaryDirectory() as folder:
            evidence=Path(folder)/'passed.dtb'
            self.assertEqual(M.prepare(proxy,utils,0x800000,lambda p,a:calls.append(a) or 7,evidence),7)
            self.assertEqual(calls,[0x900000]);self.assertEqual(memory[0x900000],evidence.read_bytes())
            self.assertEqual(struct.unpack_from('>QQ',evidence.read_bytes(),40),(start,size))
            with self.assertRaises(FileExistsError):M.prepare(proxy,utils,0x800000,lambda p,a:None,evidence)

    def test_upload_mismatch_never_calls_handoff(self):
        proxy,utils,_,_=hardware();source=fixture();called=[]
        utils.iface=SimpleNamespace(readmem=lambda address,length:source[:length],writemem=lambda address,data:None)
        utils.malloc=lambda length:0x900000
        with tempfile.TemporaryDirectory() as folder,self.assertRaises(RuntimeError):
            M.prepare(proxy,utils,0x800000,lambda p,a:called.append(a),Path(folder)/'failed.dtb')
        self.assertFalse(called)


if __name__=='__main__':unittest.main()
