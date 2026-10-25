"""Offline bootstrap entry-point policy tests; never run a real download."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('ccc_entry', ROOT / 'ccc.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class EntryTests(unittest.TestCase):
    def test_check_never_bootstraps(self):
        with patch('installer.main.main', return_value=0) as run, patch.object(entry.subprocess, 'run') as spawn:
            self.assertEqual(entry.main(['check']), 0)
        run.assert_called_once_with(['check'])
        spawn.assert_not_called()

    def test_dry_run_never_bootstraps(self):
        for flag in ('--dry-run', '--check'):
            argv = ['install', '--source', 'input', '--table', 'table', '--script', 'script', '--mapping-source', 'mapping', flag]
            with patch('installer.main.main', return_value=0), patch.object(entry.subprocess, 'run') as spawn:
                self.assertEqual(entry.main(argv), 0)
            spawn.assert_not_called()

    def test_unsupported_platform_never_downloads(self):
        argv = ['install', '--source', 'input', '--table', 'table', '--script', 'script', '--mapping-source', 'mapping']
        with patch('installer.core.probe_platform', return_value={'supported': False}), patch.object(entry.subprocess, 'run') as spawn, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(entry.main(argv), 1)
        spawn.assert_not_called()

    def test_unknown_argument_never_downloads(self):
        with patch.object(entry.subprocess, 'run') as spawn, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                entry.main(['install', '--unknown'])
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
