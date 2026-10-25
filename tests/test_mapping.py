"""No private source fixture is distributed; real-source tests use caller input."""
import ast
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from installer.mapping import build_mapping, MappingError, SOURCE_SHA256, SOURCE_BYTES
import test_installer as fixtures

REAL_SOURCE = Path(os.environ.get('CCC_MAPPING_SOURCE', 'C:/P-ROC/tools/register_vpcom.py'))


class MappingNegativeTests(unittest.TestCase):
    def test_unknown_input_never_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'register_vpcom.py'
            sentinel = Path(directory) / 'executed'
            payload = ('open(%r, "w").write("BAD")' % str(sentinel)).encode()
            for data in (b'', payload, payload.ljust(SOURCE_BYTES, b' '), b'x' * (SOURCE_BYTES + 1)):
                source.write_bytes(data)
                with self.assertRaises(MappingError):
                    build_mapping(source)
                self.assertFalse(sentinel.exists())

    def test_missing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                build_mapping(Path(directory) / 'missing.py')


@unittest.skipUnless(REAL_SOURCE.is_file(), 'Set CCC_MAPPING_SOURCE to the caller-owned exact register_vpcom.py')
class RealMappingTests(unittest.TestCase):
    def test_real_generation_safe_and_deterministic(self):
        self.assertEqual(hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest(), SOURCE_SHA256)
        # Generation must not execute/import the input, or the generated module.
        with patch('builtins.exec', side_effect=AssertionError('execution during generation')):
            module, manifest = build_mapping(REAL_SOURCE)
        self.assertEqual((module, manifest), build_mapping(REAL_SOURCE))
        self.assertEqual(manifest['generated_sha256'], hashlib.sha256(module).hexdigest())
        tree = ast.parse(module)
        self.assertFalse(any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)))
        self.assertNotIn(b'win32com', module)
        self.assertNotIn(b'logging.basicConfig', module)
        self.assertNotIn(b'def Run(', module)
        self.assertNotIn(b'def Stop(', module)

    def test_real_generated_mapping_matches_private_current_behavior(self):
        # Execution is explicitly a TEST gate, never check/install behavior.
        module, _ = build_mapping(REAL_SOURCE)
        current = (PROJECT / 'app/ccc_wpc_mapping.py')
        if not current.is_file():
            self.skipTest('Private comparison module is correctly absent from public package')
        generated, reference = {}, {}
        exec(module, generated)
        exec(current.read_bytes(), reference)
        p = SimpleNamespace(MachineTypeWPC95=95, MachineTypeSternSAM=96,
                            EventTypeSwitchClosedNondebounced=1, EventTypeSwitchClosedDebounced=2,
                            EventTypeSwitchOpenNondebounced=3, EventTypeSwitchOpenDebounced=4)
        addresses = {}
        def decode(machine, address):
            return addresses.setdefault(address, 500 + len(addresses))
        p.decode = decode
        def game():
            events = []
            drivers = {i: SimpleNamespace(curr_state=False) for i in range(2048)}
            switches = {i: SimpleNamespace(type='NC' if i % 2 else 'NO') for i in range(2048)}
            return SimpleNamespace(machine_type=95, switches=switches,
                                   proc=SimpleNamespace(drivers=drivers, add_switch_event=lambda *args: events.append(args))), events
        g1, e1 = game()
        g2, e2 = game()
        m1 = generated['CCCWPCMapping'](g1, p, lambda fields: fields)
        m2 = reference['CCCWPCMapping'](g2, p, lambda fields: fields)
        for number in range(128):
            for value in (False, True):
                self.assertEqual(m1.set_switch(number, value), m2.set_switch(number, value))
        self.assertEqual(e1, e2)
        for active in range(2048):
            g1.proc.drivers[active].curr_state = True
            g2.proc.drivers[active].curr_state = True
            self.assertEqual(m1.outputs(), m2.outputs(), 'driver %s' % active)
            g1.proc.drivers[active].curr_state = False
            g2.proc.drivers[active].curr_state = False
        self.assertEqual(m1.request(['RUN']), ['RUN'])
        with self.assertRaises(ValueError):
            generated['CCCWPCMapping'](SimpleNamespace(machine_type=0), p, None)

    def test_real_installer_generation_and_manifest(self):
        fixture = fixtures.InstallerTests('test_install_clean_and_launcher')
        fixture.setUp()
        try:
            fixture.mapping_patch.stop()
            before = REAL_SOURCE.read_bytes()
            result = fixture.install(mapping_source=REAL_SOURCE, dry_run=True)
            self.assertEqual(result['mapping']['source_sha256'], SOURCE_SHA256)
            self.assertFalse(fixture.root.exists())
            fixture.install(mapping_source=REAL_SOURCE)
            installed = (fixture.root / 'app/ccc_wpc_mapping.py').read_bytes()
            self.assertEqual(installed, build_mapping(REAL_SOURCE)[0])
            manifest = fixture.c.read_json(fixture.root / 'mapping-manifest.json')
            self.assertEqual(manifest['generated_sha256'], hashlib.sha256(installed).hexdigest())
            self.assertEqual(fixture.c.check(fixture.userdata, fixture.root)['status'], 'ok')
            self.assertEqual(REAL_SOURCE.read_bytes(), before)
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
