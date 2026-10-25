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
    def install_args(self):
        return ['install', '--source', 'input', '--table', 'table', '--script', 'script', '--mapping-source', 'mapping',
                '--root', str(ROOT / 'entry-tests/nonexistent-fixture-root')]

    def test_invalid_dmd_options_never_download_or_prompt(self):
        for flags in [['--dmd-mode', 'software', '--dmd-server', '127.0.0.1:6789'],
                      ['--dmd-mode', 'hardware', '--dmd-server', 'off'],
                      ['--dmd-mode', 'both', '--dmd-server', 'off'],
                      ['--dmd-server', 'localhost:6789'], ['--dmd-server', '127.0.0.1:0']]:
            with self.subTest(flags=flags), patch.object(entry.subprocess, 'run') as spawn, \
                    patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                    patch('installer.core.probe_platform', return_value={'supported': True}), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(entry.main(self.install_args() + flags), 1)
            spawn.assert_not_called()

    def test_readonly_and_help_never_prompt_or_download_on_tty(self):
        for argv in [['check'], self.install_args() + ['--dry-run'], self.install_args() + ['--check'],
                     self.install_args() + ['--skip-platform-check']]:
            with patch('installer.main.main', return_value=0), patch.object(entry.subprocess, 'run') as spawn, \
                    patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                    patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
                self.assertEqual(entry.main(argv), 0)
            spawn.assert_not_called()
        with patch.object(entry.subprocess, 'run') as spawn, patch('builtins.input') as prompt, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exc:
            entry.main(['install', '--help'])
        self.assertEqual(exc.exception.code, 0)
        prompt.assert_not_called()
        spawn.assert_not_called()

    def test_tty_selection_passed_once_before_bootstrap(self):
        for answers, flags in [([''], ['--dmd-mode', 'software']),
                               (['2', ''], ['--dmd-mode', 'hardware', '--dmd-server', '127.0.0.1:6789']),
                               (['3', '127.0.0.1:9999'], ['--dmd-mode', 'both', '--dmd-server', '127.0.0.1:9999'])]:
            with self.subTest(answers=answers), patch('installer.core.probe_platform', return_value={'supported': True}), \
                    patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
                    patch('builtins.input', side_effect=answers) as prompt, patch('installer.main.main', return_value=0) as run, \
                    patch.object(entry.subprocess, 'run') as spawn, contextlib.redirect_stderr(io.StringIO()):
                spawn.return_value.returncode = 0
                self.assertEqual(entry.main(self.install_args()), 0)
                run.assert_called_once_with(self.install_args() + flags)
                self.assertEqual(prompt.call_count, len(answers))
                spawn.assert_called_once()

    def test_tty_eof_cancels_before_bootstrap(self):
        with patch('installer.core.probe_platform', return_value={'supported': True}), \
                patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
                patch('builtins.input', side_effect=EOFError), patch.object(entry.subprocess, 'run') as spawn, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(entry.main(self.install_args()), 1)
        self.assertIn('cancelled', err.getvalue())
        spawn.assert_not_called()

    def test_nontty_defaults_and_explicit_modes(self):
        for flags in [[], ['--dmd-mode', 'software'], ['--dmd-mode', 'hardware'], ['--dmd-mode', 'both']]:
            argv = self.install_args() + flags
            with patch('installer.core.probe_platform', return_value={'supported': True}), \
                    patch('sys.stdin.isatty', return_value=False), patch('builtins.input') as prompt, \
                    patch('installer.main.main', return_value=0) as run, patch.object(entry.subprocess, 'run') as spawn:
                spawn.return_value.returncode = 0
                self.assertEqual(entry.main(argv), 0)
            prompt.assert_not_called()
            run.assert_called_once_with(argv)

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
        with patch('installer.core.probe_platform', return_value={'supported': False}), patch.object(entry.subprocess, 'run') as spawn, \
                patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
                patch('builtins.input', side_effect=AssertionError('must not prompt')), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(entry.main(argv), 1)
        spawn.assert_not_called()

    def test_unknown_argument_never_downloads(self):
        with patch.object(entry.subprocess, 'run') as spawn, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                entry.main(['install', '--unknown'])
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
