"""Bootstrap regressions using a tiny import-cycle fixture, not CCC game assets."""
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'


class BootstrapTests(unittest.TestCase):
    def fixture(self, commands):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            root = Path(tmp)
            (root/'ep').mkdir()
            (root/'ep/__init__.py').write_text('from . import lamps\nWHITE = 0\nclass EP_Desktop: pass\n')
            (root/'ep/lamps.py').write_text('import game\n')
            (root/'game.py').write_text('import ep\nDEFAULT_COLOR = ep.WHITE\n')
            return subprocess.run([sys.executable, '-B', '-c', '\n'.join(commands)], cwd=root, capture_output=True, text=True)

    def test_fixture_detects_ep_first_cycle(self):
        result = self.fixture(['from ep import EP_Desktop', 'import game as game_module'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('WHITE', result.stderr)

    def test_runtime_import_sequence_initializes_game_first(self):
        tree = ast.parse((APP/'runtime_main.py').read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(a.name == 'game' for a in node.names):
                imports.append(node)
            elif isinstance(node, ast.ImportFrom) and node.module == 'ep':
                imports.append(node)
        commands = [ast.unparse(n) for n in sorted(imports, key=lambda n:n.lineno)]
        self.assertEqual(len(commands), 2)
        result = self.fixture(commands)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hardware_guard_precedes_game_import(self):
        text = (APP/'runtime_main.py').read_text()
        self.assertLess(text.index('pinproc.PinPROC=hardware_forbidden'), text.index('import game as game_module'))
        self.assertLess(text.index('GameController.create_pinproc='), text.index('import game as game_module'))


if __name__ == '__main__':
    unittest.main()
