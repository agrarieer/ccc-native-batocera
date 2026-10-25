"""Real filesystem fixtures; never claim these run native VPX/CCC."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        from installer import core
        self.c = core
        # Transaction fixtures do not embed or require the private bridge source.
        from unittest.mock import patch
        self.mapping_patch = patch('installer.core.build_mapping', return_value=(
            b'# mapping fixture only; never executed\n', {'fixture_only': True}))
        self.mapping_patch.start()
        self.addCleanup(self.mapping_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.pkg = self.base / 'package'
        self.pkg.mkdir()
        (self.pkg / 'app').mkdir()
        (self.pkg / 'app/launch.sh').write_text('#!/bin/sh\nexit 0\n')
        (self.pkg / 'app/launch.py').write_text('# fixture only; never executed\n')
        (self.pkg / 'payload').mkdir()
        self.archive = self.pkg / 'payload/runtime.tar.gz'
        self.tar([('python/bin/python2.7', b'fixture-not-an-executable'), ('lib/fixture.so', b'fixture')])
        self.source = self.base / 'source'
        for name in ['game.py', 'assets.py', 'config.yaml', 'config/cc_machine.yaml',
                     'config/game_data_template.yaml', 'config/settings_template.yaml',
                     'procgame/__init__.py', 'ep/__init__.py', 'cc_modes/__init__.py',
                     'assets/sound/voice/test.wav', 'assets/dmd/test.dmd']:
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('fixture\n')
        (self.source / 'config.yaml').write_text('font_path:\n  - ../shared/dmd/\nconfig_path:\n  - ../shared/config/\nsound_path: ./assets/sound/\n')
        for name in ['config/game_data.yaml', 'config/user_settings.yaml', 'config/game_data_backup.yaml', 'secret.log', '.secret']:
            (self.source / name).write_text('PRIVATE_SCORE_123')
        self.table = self.base / 'table.vpx'
        self.table.write_bytes(b'fixture-table')
        self.script = self.base / 'table.vbs'
        self.script.write_bytes(b'fixture-script')
        self.userdata = self.base / 'userdata'
        self.userdata.mkdir()
        self.root = self.userdata / 'system/ccc-native'
        self.policy = core.Policy(table_sha256=self.sha(self.table), script_sha256=self.sha(self.script))
        self.platform = lambda: {'supported': True, 'native_vpx': '/usr/bin/vpinball', 'version': '43.1', 'arch': 'x86_64', 'glibc': '2.38'}

    @staticmethod
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def tar(self, members, kind=None):
        with tarfile.open(self.archive, 'w:gz') as tf:
            for name, content in members:
                ti = tarfile.TarInfo(name)
                ti.mode = 0o755 if name.endswith('python2.7') else 0o644
                if kind:
                    ti.type = kind
                    ti.linkname = '../../escape'
                else:
                    ti.size = len(content)
                tf.addfile(ti, io.BytesIO(content) if not kind else None)
        self.manifest()

    def manifest(self):
        paths = [self.archive] + sorted((self.pkg / 'app').rglob('*'))
        text = ''.join(f'{self.sha(p)}  {p.relative_to(self.pkg).as_posix()}\n' for p in paths if p.is_file())
        (self.pkg / 'payload/SHA256SUMS').write_text(text)
        (self.pkg / 'payload/runtime-manifest.json').write_text(json.dumps({
            'sha256': self.sha(self.archive), 'bytes': self.archive.stat().st_size,
            'format': 'tar.gz', 'architecture': 'x86_64'}))

    def install(self, **kw):
        args = dict(package=self.pkg, source=self.source, table=self.table, script=self.script,
                    mapping_source=self.script,
                    userdata=self.userdata, policy=self.policy, platform_probe=self.platform,
                    table_builder=self.fixture_table_builder)
        args.update(kw)
        return self.c.install(**args)

    @staticmethod
    def fixture_table_builder(app, table, script, destination, final_root):
        # Filesystem transaction fixture only; real native VPX conversion is a separate gate.
        import shutil
        shutil.copyfile(table, destination / 'CCC-native.vpx')
        shutil.copyfile(script, destination / 'CCC-native.vbs')

    def test_all_dmd_modes_install_configure_and_update(self):
        path = self.root / 'config/settings.json'
        for mode, software, physical in [('software', True, False), ('hardware', False, True), ('both', True, True)]:
            with self.subTest(mode=mode):
                self.install(dmd_mode=mode)
                settings = json.loads(path.read_text())
                self.assertEqual(settings['dmd_mode'], mode)
                self.assertEqual(settings['software_dmd'], software)
                self.assertEqual(settings['physical_dmd'], physical)
                self.assertEqual(settings['dmd_server'], '127.0.0.1:6789' if physical else None)
                before = path.read_bytes()
                self.install()
                self.assertEqual(path.read_bytes(), before)
                self.c.configure(self.userdata, dmd_mode=mode, dry_run=True)
                self.assertEqual(path.read_bytes(), before)
                self.c.configure(self.userdata, dmd_mode=mode)
                self.assertEqual(json.loads(path.read_text()), settings)

    def test_dmd_custom_endpoint_and_legacy_off_restore_software(self):
        self.install(dmd_mode='hardware', dmd_server='127.0.0.1:12345')
        path = self.root / 'config/settings.json'
        settings = json.loads(path.read_text())
        self.assertEqual(settings['dmd_port'], 12345)
        self.assertFalse(settings['software_dmd'])
        self.c.configure(self.userdata, dmd_server='off')
        settings = json.loads(path.read_text())
        self.assertTrue(settings['software_dmd'])
        self.assertFalse(settings['physical_dmd'])
        self.assertIsNone(settings['dmd_server'])
        self.c.configure(self.userdata, dmd_server='127.0.0.1:12346')
        settings = json.loads(path.read_text())
        self.assertTrue(settings['software_dmd'])
        self.assertTrue(settings['physical_dmd'])
        self.assertEqual(settings['dmd_port'], 12346)

    def test_invalid_dmd_choices_atomic_for_install_and_configure(self):
        self.install(dmd_mode='hardware')
        before = {str(p): p.read_bytes() for p in self.userdata.rglob('*') if p.is_file()}
        for kwargs in [dict(dmd_mode='invalid'), dict(dmd_mode='software', dmd_server='127.0.0.1:6789'),
                       dict(dmd_mode='hardware', dmd_server='off'), dict(dmd_mode='both', dmd_server='off'),
                       dict(dmd_mode='both', dmd_server='localhost:6789')]:
            for operation in (lambda: self.install(**kwargs), lambda: self.c.configure(self.userdata, **kwargs)):
                with self.subTest(kwargs=kwargs), self.assertRaises(self.c.InstallError):
                    operation()
                self.assertEqual(before, {str(p): p.read_bytes() for p in self.userdata.rglob('*') if p.is_file()})
        self.assertFalse((self.root.parent / '.ccc-native.install-lock').exists())

    def test_invalid_settings_object_rejected_before_staging(self):
        self.install()
        path = self.root / 'config/settings.json'
        path.write_text('[]')
        for operation in (lambda: self.install(dmd_mode='hardware'),
                          lambda: self.c.configure(self.userdata, dmd_mode='both')):
            with self.assertRaisesRegex(self.c.InstallError, 'object'):
                operation()
            self.assertEqual(path.read_text(), '[]')
            self.assertEqual(list(self.root.parent.glob('.*.stage-*')), [])

    def cli_args(self, command='install'):
        return [command, '--package', str(self.pkg), '--source', str(self.source), '--table', str(self.table),
                '--script', str(self.script), '--mapping-source', str(self.script), '--userdata', str(self.userdata)]

    def test_dmd_cli_modes_all_commands(self):
        from installer.main import main
        self.install()
        before = {str(p): p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        for mode in ('software', 'hardware', 'both'):
            for command in ('install', 'check', 'configure'):
                argv = (['configure', '--userdata', str(self.userdata)] if command == 'configure'
                        else self.cli_args(command)) + ['--dmd-mode', mode, '--dry-run']
                with self.subTest(mode=mode, command=command), patch('installer.core.Policy', return_value=self.policy), \
                        patch('installer.core.probe_platform', self.platform), \
                        patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                        patch('installer.core.subprocess.run', side_effect=AssertionError('must not spawn')), \
                        redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(argv), 0)
                self.assertEqual(json.loads(out.getvalue())['settings']['dmd_mode'], mode)
                self.assertEqual(before, {str(p): p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_legacy_dmd_settings_remain_byte_identical_on_update(self):
        self.install(dmd_mode='hardware', dmd_server='127.0.0.1:8765')
        path = self.root / 'config/settings.json'
        settings = json.loads(path.read_text())
        del settings['dmd_mode']
        path.write_text(json.dumps(settings))
        before = path.read_bytes()
        self.install()
        self.assertEqual(path.read_bytes(), before)

    def test_interactive_dmd_choice_real_transaction_fixture(self):
        from installer.main import main
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err, \
                patch('installer.core.Policy', return_value=self.policy), patch('installer.core.probe_platform', self.platform), \
                patch('installer.core.default_table_builder', self.fixture_table_builder), \
                patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), \
                patch('builtins.input', side_effect=['wrong', '2', 'bad', 'off', '127.0.0.1:4321']) as prompt:
            self.assertEqual(main(self.cli_args()), 0)
        settings = json.loads((self.root / 'config/settings.json').read_text())
        self.assertFalse(settings['software_dmd'])
        self.assertTrue(settings['physical_dmd'])
        self.assertEqual(settings['dmd_server'], '127.0.0.1:4321')
        self.assertEqual(prompt.call_count, 5)
        self.assertIn('compatible local DMD server', err.getvalue())
        self.assertIn('does not install, start, stop or change', err.getvalue())

    def test_interactive_default_both_and_cancellation(self):
        from installer.main import parser, prepare_options
        for answers, expected in [([''], ('software', None)), (['3', ''], ('both', '127.0.0.1:6789'))]:
            args = parser().parse_args(self.cli_args())
            with patch('installer.core.probe_platform', self.platform), patch('sys.stdin.isatty', return_value=True), \
                    patch('sys.stdout.isatty', return_value=True), patch('builtins.input', side_effect=answers), redirect_stderr(io.StringIO()):
                self.assertTrue(prepare_options(args))
            self.assertEqual((args.dmd_mode, args.dmd_server), expected)
        for answers in ([EOFError()], ['2', EOFError()], [KeyboardInterrupt()]):
            args = parser().parse_args(self.cli_args())
            with patch('installer.core.probe_platform', self.platform), patch('sys.stdin.isatty', return_value=True), \
                    patch('sys.stdout.isatty', return_value=True), patch('builtins.input', side_effect=answers), \
                    redirect_stderr(io.StringIO()), self.assertRaisesRegex(self.c.InstallError, 'cancelled'):
                prepare_options(args)
            self.assertFalse(self.root.exists())

    def test_readonly_explicit_nontty_unsupported_update_never_prompt(self):
        from installer.main import parser, prepare_options
        for argv in [self.cli_args('check'), self.cli_args() + ['--check'], self.cli_args() + ['--dry-run'],
                     self.cli_args() + ['--skip-platform-check'], self.cli_args() + ['--dmd-mode', 'software'],
                     self.cli_args() + ['--dmd-server', '127.0.0.1:6789'], ['configure']]:
            with patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                    patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
                self.assertFalse(prepare_options(parser().parse_args(argv)))
        for tty, supported in [(False, True), (True, False)]:
            with patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                    patch('sys.stdin.isatty', return_value=tty), patch('sys.stdout.isatty', return_value=tty), \
                    patch('installer.core.probe_platform', return_value={'supported': supported}):
                self.assertFalse(prepare_options(parser().parse_args(self.cli_args())))
        self.install(dmd_mode='hardware')
        with patch('builtins.input', side_effect=AssertionError('must not prompt')), \
                patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True):
            self.assertFalse(prepare_options(parser().parse_args(self.cli_args())))

    def test_profile_install_configure_update_preserved(self):
        self.install(profile='cabinet')
        path = self.root / 'config/settings.json'
        self.assertEqual(json.loads(path.read_text())['profile'], 'cabinet')
        self.install()
        self.assertEqual(json.loads(path.read_text())['profile'], 'cabinet')
        before = path.read_bytes()
        self.c.configure(self.userdata, profile='desktop', dry_run=True)
        self.assertEqual(path.read_bytes(), before)
        self.c.configure(self.userdata, profile='desktop')
        self.assertEqual(json.loads(path.read_text())['profile'], 'desktop')
        with self.assertRaises(self.c.InstallError):
            self.c.configure(self.userdata, profile='rotated')
        self.assertEqual(json.loads(path.read_text())['profile'], 'desktop')

    def test_invalid_profile_never_installs(self):
        with self.assertRaises(self.c.InstallError):
            self.install(profile='rotated')
        self.assertFalse(self.root.exists())

    def test_dmd_configuration_matches_launcher_policy(self):
        self.install()
        path = self.root / 'config/settings.json'
        before = path.read_bytes()
        for endpoint in ('192.0.2.10:6789', 'localhost:6789', '[::1]:6789', '127.0.0.1:0'):
            with self.subTest(endpoint=endpoint), self.assertRaises(self.c.InstallError):
                self.c.configure(self.userdata, dmd_server=endpoint)
            self.assertEqual(path.read_bytes(), before)
        self.c.configure(self.userdata, dmd_server='127.0.0.1:6789')
        sys.path.insert(0, str(PROJECT / 'app'))
        import configuration
        self.assertEqual(configuration.settings(self.root)['dmd_server'], '127.0.0.1:6789')
        self.c.configure(self.userdata, dmd_server='off')
        self.assertIsNone(configuration.settings(self.root)['dmd_server'])

    def test_dots_only_layout_rejected_before_writes(self):
        import shutil
        shutil.rmtree(self.source / 'assets/dmd')
        (self.source / 'dots').mkdir()
        (self.source / 'dots/animation.dmd').write_bytes(b'inert asset fixture')
        with self.assertRaises(self.c.InstallError):
            self.install()
        self.assertFalse(self.root.exists())

    def test_mapping_cli_requires_source(self):
        from installer.main import main
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()):
            for args in [['install', '--source', 'game', '--table', 'table', '--script', 'script'],
                         ['check', '--mapping-source', 'bridge.py'],
                         ['check', '--source', 'game', '--table', 'table', '--script', 'script']]:
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    main(args)

    def test_private_mapping_in_release_manifest_rejected(self):
        (self.pkg / 'app/ccc_wpc_mapping.py').write_text('PRIVATE')
        self.manifest()
        with self.assertRaisesRegex(self.c.InstallError, 'manifest path'):
            self.install()

    def test_mapping_source_directory_rejected(self):
        with self.assertRaisesRegex(self.c.InstallError, 'regular file'):
            self.install(mapping_source=self.source)

    def test_mapping_source_required(self):
        with self.assertRaisesRegex(self.c.InstallError, 'mapping-source'):
            self.install(mapping_source=None)
        self.assertFalse(self.root.exists())

    def test_unknown_mapping_source_rejected(self):
        self.mapping_patch.stop()
        with self.assertRaisesRegex(self.c.InstallError, 'register_vpcom'):
            self.install(dry_run=True)
        self.assertFalse((self.userdata / 'system').exists())

    def test_private_mapping_ignored_generated_mapping_owned(self):
        (self.pkg / 'app/ccc_wpc_mapping.py').write_text('raise RuntimeError("PRIVATE")')
        self.install()
        self.assertEqual((self.root / 'app/ccc_wpc_mapping.py').read_bytes(), b'# mapping fixture only; never executed\n')
        marker = self.c.read_json(self.root / self.c.MARKER)
        self.assertIn('app/ccc_wpc_mapping.py', marker['files'])
        self.assertIn('mapping-manifest.json', marker['files'])
        self.assertNotIn('app/ccc_wpc_mapping.py', marker['package_sha256'])
        (self.root / 'app/ccc_wpc_mapping.py').write_text('changed')
        with self.assertRaises(self.c.InstallError):
            self.c.check(self.userdata, self.root)

    def test_install_clean_and_launcher(self):
        result = self.install()
        self.assertEqual(result['status'], 'installed')
        self.assertTrue((self.root / 'runtime/python/bin/python2.7').is_file())
        self.assertTrue((self.root / 'game/config/game_data_template.yaml').is_file())
        for name in ['game_data.yaml', 'user_settings.yaml', 'game_data_backup.yaml']:
            self.assertFalse((self.root / 'game/config' / name).exists())
        self.assertFalse((self.root / 'game/secret.log').exists())
        self.assertFalse((self.root / 'game/.secret').exists())
        settings = json.loads((self.root / 'config/settings.json').read_text())
        self.assertTrue(settings['audio'])
        self.assertFalse(settings['physical_dmd'])
        self.assertEqual(settings['display'], {})
        self.assertEqual(settings['profile'], 'desktop')
        launcher = self.userdata / 'roms/ports/Cactus Canyon Continued.sh'
        self.assertIn('CCC_INSTALL_ROOT', launcher.read_text())
        self.assertIn('app/launch.py', launcher.read_text())
        self.assertEqual(self.c.check(self.userdata, self.root)['status'], 'ok')

    def test_dry_run_no_writes(self):
        before = sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*'))
        self.assertEqual(self.install(dry_run=True)['status'], 'dry-run')
        self.assertEqual(before, sorted(str(p.relative_to(self.base)) for p in self.base.rglob('*')))

    def test_bad_table_is_rejected_without_writes(self):
        self.table.write_bytes(b'unknown')
        with self.assertRaises(self.c.InstallError):
            self.install()
        self.assertFalse((self.userdata / 'system').exists())

    def test_bad_script_is_rejected(self):
        self.script.write_bytes(b'unknown')
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_modified_payload_rejected(self):
        with self.archive.open('ab') as f:
            f.write(b'tamper')
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_unlisted_app_file_rejected(self):
        (self.pkg / 'app/injected.py').write_text('not covered')
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_missing_assets_reported(self):
        (self.source / 'assets/sound/voice/test.wav').unlink()
        with self.assertRaisesRegex(self.c.InstallError, 'sound'):
            self.install()

    def test_archive_attacks_rejected(self):
        for name, kind in [('../escape', None), ('/absolute', None), ('python/../../escape', None),
                           ('C:/escape', None), ('python/link', tarfile.SYMTYPE),
                           ('python/hard', tarfile.LNKTYPE), ('python/dev', tarfile.CHRTYPE)]:
            with self.subTest(name=name, kind=kind):
                self.tar([(name, b'evil')], kind)
                with self.assertRaises(self.c.InstallError):
                    self.install()
        self.assertFalse((self.base / 'escape').exists())

    def test_archive_duplicates_rejected(self):
        self.tar([('python/bin/python2.7', b'a'), ('python/bin/python2.7', b'b')])
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_archive_size_limit(self):
        policy = self.c.Policy(table_sha256=self.sha(self.table), script_sha256=self.sha(self.script), max_unpacked_bytes=1)
        with self.assertRaises(self.c.InstallError):
            self.install(policy=policy)

    def test_platform_unsupported_blocks_install(self):
        with self.assertRaises(self.c.InstallError):
            self.install(platform_probe=lambda: {'supported': False, 'reason': 'not Batocera'})
        self.assertFalse(self.root.exists())

    def test_foreign_root_never_replaced(self):
        self.root.mkdir(parents=True)
        (self.root / 'foreign').write_text('keep')
        with self.assertRaises(self.c.InstallError):
            self.install()
        self.assertEqual((self.root / 'foreign').read_text(), 'keep')

    def test_foreign_launcher_never_replaced(self):
        launcher = self.userdata / 'roms/ports/Cactus Canyon Continued.sh'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('foreign')
        with self.assertRaises(self.c.InstallError):
            self.install()
        self.assertEqual(launcher.read_text(), 'foreign')

    def test_root_escape_rejected(self):
        for root in [self.base / 'outside', self.userdata, self.userdata / 'system', self.userdata / 'roms/ports']:
            with self.subTest(root=root), self.assertRaises(self.c.InstallError):
                self.install(root=root)

    def test_update_preserves_config_saves_and_backups(self):
        self.install()
        (self.root / 'saves/scores.yaml').write_text('score-local')
        self.c.configure(self.userdata, self.root, sound=False, dmd_server='127.0.0.1:6789')
        (self.pkg / 'app/launch.sh').write_text('#!/bin/sh\n# new\nexit 0\n')
        self.manifest()
        result = self.install()
        self.assertEqual((self.root / 'saves/scores.yaml').read_text(), 'score-local')
        self.assertFalse(json.loads((self.root / 'config/settings.json').read_text())['audio'])
        backup = Path(result['backup'])
        self.assertTrue((backup / '.ccc-install.json').is_file())
        self.assertNotIn('# new', (backup / 'app/launch.sh').read_text())
        self.assertEqual(self.c.check(self.userdata, self.root)['status'], 'ok')

    def test_uninstall_preserves_changed_and_foreign_files(self):
        self.install()
        (self.root / 'saves/scores').write_text('local')
        (self.root / 'app/foreign.txt').write_text('keep')
        (self.root / 'app/launch.sh').write_text('changed')
        result = self.c.uninstall(self.userdata, self.root)
        self.assertEqual(result['status'], 'uninstalled')
        self.assertTrue((self.root / 'saves/scores').exists())
        self.assertTrue((self.root / 'config/settings.json').exists())
        self.assertEqual((self.root / 'app/foreign.txt').read_text(), 'keep')
        self.assertEqual((self.root / 'app/launch.sh').read_text(), 'changed')
        self.assertFalse((self.root / 'runtime/python/bin/python2.7').exists())
        self.assertFalse((self.userdata / 'roms/ports/Cactus Canyon Continued.sh').exists())
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_check_detects_tampering(self):
        self.install()
        (self.root / 'app/launch.sh').write_text('bad')
        with self.assertRaises(self.c.InstallError):
            self.c.check(self.userdata, self.root)

    def test_configure_validation_and_atomic_write(self):
        self.install()
        settings = self.root / 'config/settings.json'
        original = settings.read_bytes()
        with self.assertRaises(self.c.InstallError):
            self.c.configure(self.userdata, self.root, dmd_server='bad;command')
        self.assertEqual(settings.read_bytes(), original)
        self.c.configure(self.userdata, self.root, sound=False, dmd_server='127.0.0.1:6789')
        self.assertEqual(json.loads(settings.read_text())['dmd_server'], '127.0.0.1:6789')
        self.c.configure(self.userdata, self.root, dmd_server='off')
        self.assertIsNone(json.loads(settings.read_text())['dmd_server'])

    def test_explicit_import_saves_only(self):
        saved = self.base / 'explicit-saves'
        saved.mkdir()
        (saved / 'game_data.yaml').write_text('explicit-score')
        (saved / 'unrelated').write_text('do-not-import')
        self.install(import_saves=saved)
        self.assertEqual((self.root / 'saves/game_data.yaml').read_text(), 'explicit-score')
        self.assertFalse((self.root / 'saves/unrelated').exists())

    def test_commit_failure_rolls_back(self):
        self.install()
        original = (self.root / 'app/launch.sh').read_bytes()
        def fail(stage):
            if stage == 'root-committed':
                raise OSError('injected fixture failure')
        with self.assertRaises(OSError):
            self.install(commit_hook=fail)
        self.assertEqual((self.root / 'app/launch.sh').read_bytes(), original)
        self.assertEqual(self.c.check(self.userdata, self.root)['status'], 'ok')
        self.assertEqual(list(self.root.parent.glob('.*.stage-*')), [])
        self.assertFalse((self.root.parent / '.ccc-native.install-lock').exists())

    def test_cli_help_runs(self):
        result = subprocess.run([sys.executable, str(PROJECT / 'installer/main.py'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('uninstall', result.stdout)

    def test_runtime_json_manifest_supported_without_sums(self):
        (self.pkg / 'payload/SHA256SUMS').unlink()
        self.assertEqual(self.install()['status'], 'installed')

    def test_runtime_json_manifest_wrong_architecture_rejected(self):
        path = self.pkg / 'payload/runtime-manifest.json'
        record = json.loads(path.read_text())
        record['architecture'] = 'aarch64'
        path.write_text(json.dumps(record))
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_runtime_json_manifest_wrong_length_rejected(self):
        path = self.pkg / 'payload/runtime-manifest.json'
        record = json.loads(path.read_text())
        record['bytes'] += 1
        path.write_text(json.dumps(record))
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_running_game_blocks_mutation(self):
        self.install()
        (self.root / 'launch.lock').mkdir()
        for operation in [lambda: self.install(), lambda: self.c.configure(self.userdata, self.root, sound=False),
                          lambda: self.c.uninstall(self.userdata, self.root)]:
            with self.subTest(operation=operation), self.assertRaises(self.c.InstallError):
                operation()

    def test_private_machine_config_is_not_copied(self):
        (self.source / 'config.yaml').write_text('usb_update: true\nshutdown_flag: true\nfont_path: C:/PERSONAL/shared/dmd\ndmd_x: 3840\n')
        self.install()
        result = (self.root / 'game/config.yaml').read_text()
        self.assertNotIn('PERSONAL', result)
        self.assertNotIn('3840', result)
        self.assertIn('usb_update: false', result)

    def test_skip_platform_never_installs(self):
        with self.assertRaises(self.c.InstallError):
            self.install(skip_platform_check=True)
        result = self.install(skip_platform_check=True, dry_run=True, platform_probe=lambda: {'supported': False})
        self.assertFalse(result['supported_install'])
        self.assertFalse(self.root.exists())

    def test_dry_run_configure_and_uninstall_no_writes(self):
        self.install()
        before = {str(p.relative_to(self.userdata)): p.read_bytes() for p in self.userdata.rglob('*') if p.is_file()}
        self.c.configure(self.userdata, self.root, sound=False, dry_run=True)
        self.c.uninstall(self.userdata, self.root, dry_run=True)
        after = {str(p.relative_to(self.userdata)): p.read_bytes() for p in self.userdata.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_source_sound_directory_layout_supported(self):
        import shutil
        shutil.move(str(self.source / 'assets/sound'), self.source / 'sounds')
        shutil.move(str(self.source / 'assets/dmd'), self.source / 'dmd')
        self.install()
        self.assertTrue((self.root / 'game/sounds/voice/test.wav').is_file())

    def test_shared_auto_discovery_and_sanitization(self):
        shared = self.source.parent.parent / ('shared-' + self.base.name)
        # Keep fixture scope inside the temporary tree, not the user's parent.
        shared = self.base / 'shared'
        (shared / 'dmd').mkdir(parents=True)
        (shared / 'dmd/font.dmd').write_text('font')
        (shared / 'config').mkdir()
        (shared / 'config/game_data.yaml').write_text('PRIVATE')
        (shared / 'config/controller.yaml').write_text('generic')
        (shared / 'secret.txt').write_text('SECRET')
        self.install()
        self.assertTrue((self.root / 'shared/dmd/font.dmd').is_file())
        self.assertTrue((self.root / 'game/shared/dmd/font.dmd').is_file())
        self.assertFalse((self.root / 'shared/config/game_data.yaml').exists())
        self.assertFalse((self.root / 'shared/secret.txt').exists())

    def test_tar_file_parent_collision_rejected(self):
        self.tar([('python/bin', b'not directory'), ('python/bin/python2.7', b'file')])
        with self.assertRaises(self.c.InstallError):
            self.install()

    def test_builder_cli_receives_final_root(self):
        script = self.pkg / 'app/build_table.py'
        script.write_text('import sys,json\nfrom pathlib import Path\n'
                          'table,vbs,destination,flag,root=sys.argv[1:]\n'
                          'assert flag=="--install-root"\n'
                          'p=Path(destination); p.mkdir(exist_ok=True)\n'
                          '(p/"CCC-native.vpx").write_bytes(Path(table).read_bytes())\n'
                          '(p/"CCC-native.vbs").write_text(root)\n')
        self.manifest()
        self.install(table_builder=None)
        self.assertEqual((self.root / 'tables/CCC-native.vbs').read_text(), str(self.root))

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlink unsupported')
    def test_source_and_target_symlinks_rejected(self):
        link = self.source / 'external'
        try:
            link.symlink_to(self.table)
        except OSError:
            self.skipTest('Windows symlink privilege unavailable')
        with self.assertRaises(self.c.InstallError):
            self.install()
        link.unlink()
        self.install()
        (self.root / 'saves/outside').symlink_to(self.table)
        with self.assertRaises(self.c.InstallError):
            self.c.uninstall(self.userdata, self.root)
        self.assertTrue(self.table.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
