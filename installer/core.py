"""Conservative, standard-library-only native CCC deployment.

Fixture tests inject Policy/platform_probe/table_builder; CLI never exposes hash
or platform-success overrides. Package checksums are integrity, not signatures:
obtain the installer and manifest from a trusted publisher.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import uuid

from .mapping import build_mapping, MappingError


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    table_sha256: str = '542b775831a1876c37d46eb8a825e7027562f6bab648f4ee2e6b20db483353a8'
    script_sha256: str = 'f20205c66d564b6bbc88f6b0ee7635f0e2fd6c9256b6255d6fa2065899618f7e'
    max_unpacked_bytes: int = 4 * 1024**3
    max_members: int = 100000


MARKER = '.ccc-install.json'
OWNER = 'ccc-native-installer-v1'
PRESERVE = {'config', 'saves', 'runs'}
GAME_DIRS = {'assets', 'sounds', 'dmd', 'dots', 'dots_sm_square', 'dots_square',
             'images', 'lampshows', 'procgame', 'ep', 'cc_modes', 'highscore'}
CONFIG_FILES = {'cc_machine.yaml', 'game_data_template.yaml', 'settings_template.yaml'}
SHARED_DIRS = {'dmd', 'config'}
SAVE_FILES = {'game_data.yaml', 'user_settings.yaml'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise InstallError('Invalid relative path: %r' % name)
    parts = name.split('/')
    if any(part in ('', '.', '..') for part in parts) or PurePosixPath(name).is_absolute():
        raise InstallError('Unsafe relative path: %r' % name)
    return Path(*parts)


def no_links(path):
    """Reject links/junctions in every existing ancestor, without resolving them away."""
    path = Path(os.path.abspath(path))
    for item in [path, *path.parents]:
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise InstallError('Symlink/junction is not allowed: %s' % item)
        if item.exists():
            st = item.lstat()
            if getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
                raise InstallError('Reparse point is not allowed: %s' % item)
    return path


def tree_files(root):
    root = no_links(root)
    if not root.is_dir():
        raise InstallError('Directory is missing: %s' % root)
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            item = no_links(Path(directory) / name)
            if item.is_file():
                if not stat.S_ISREG(item.lstat().st_mode):
                    raise InstallError('Not a regular file: %s' % item)
                files.append(item)
            elif not item.is_dir():
                raise InstallError('Special file is not allowed: %s' % item)
    return sorted(files)


def paths(userdata, root=None):
    ud = no_links(userdata)
    if not ud.is_dir():
        raise InstallError('userdata directory is missing: %s' % ud)
    root = no_links(root if root is not None else ud / 'system/ccc-native')
    try:
        rel = root.relative_to(ud)
    except ValueError:
        raise InstallError('Installation root must be inside userdata')
    if not rel.parts or len(rel.parts) < 2 or rel.parts[0] in {'roms', 'bios', 'saves', 'screenshots', 'music'}:
        raise InstallError('Protected userdata directory: %s' % root)
    launcher = no_links(ud / 'roms/ports/Cactus Canyon Continued.sh')
    return ud, root, launcher


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InstallError('Cannot read JSON: %s (%s)' % (path, exc)) from exc


def owned(root, active=True):
    if not root.is_dir():
        raise InstallError('No existing installation: %s' % root)
    tree_files(root)
    record = read_json(root / MARKER)
    if (not isinstance(record, dict) or record.get('owner') != OWNER or record.get('root') != str(root)
            or record.get('schema') != 1 or not isinstance(record.get('files'), dict)):
        raise InstallError('Invalid ownership marker; foreign directory will not be changed')
    if active and not record.get('active'):
        raise InstallError('Review/move residual uninstall data before reinstalling')
    for name, digest in record['files'].items():
        safe_relative(name)
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise InstallError('Invalid ownership manifest')
    for name in record.get('dirs', []):
        safe_relative(name)
    return record


def package_files(package):
    package = no_links(package)
    archive = no_links(package / 'payload/runtime.tar.gz')
    manifest = no_links(package / 'payload/SHA256SUMS')
    runtime_manifest = no_links(package / 'payload/runtime-manifest.json')
    expected = {'payload/runtime.tar.gz': archive}
    for file in tree_files(package / 'app'):
        # Private working copies may retain a map; never trust/copy it.
        if file.name == 'ccc_wpc_mapping.py' or file.suffix in {'.pyc', '.pyo'} or '__pycache__' in file.parts:
            continue
        expected[file.relative_to(package).as_posix()] = file
    if not (package / 'app/launch.py').is_file():
        raise InstallError('Package is missing app/launch.py')
    if not archive.is_file() or not runtime_manifest.is_file():
        raise InstallError('Package is missing runtime.tar.gz or payload/runtime-manifest.json')
    runtime = read_json(runtime_manifest)
    if (not isinstance(runtime, dict) or runtime.get('format') != 'tar.gz'
            or runtime.get('architecture') != 'x86_64'
            or type(runtime.get('bytes')) is not int or runtime['bytes'] != archive.stat().st_size
            or not re.fullmatch('[0-9a-f]{64}', str(runtime.get('sha256', '')))):
        raise InstallError('Invalid runtime-manifest.json format/architecture/length/SHA256')
    if sha256(archive) != runtime['sha256']:
        raise InstallError('Runtime SHA256 mismatch')
    if not manifest.exists():
        # Runtime manifest is mandatory. App source is trusted as part of the
        # installer release; additional SHA256SUMS hardens release completeness.
        return archive, {name: runtime['sha256'] if name == 'payload/runtime.tar.gz' else sha256(file)
                         for name, file in expected.items()}
    listed = {}
    for line in manifest.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *](.+)', line)
        if not match:
            raise InstallError('Invalid SHA256SUMS line')
        digest, name = match.groups()
        safe_relative(name)
        if name in listed or name not in expected:
            raise InstallError('Duplicate/unexpected manifest path: %s' % name)
        listed[name] = digest.lower()
    if set(listed) != set(expected):
        raise InstallError('SHA256SUMS does not cover every package file')
    for name, file in expected.items():
        if sha256(file) != listed[name]:
            raise InstallError('SHA256 mismatch: %s' % name)
    return archive, listed


def archive_members(archive, policy):
    members = []
    seen = set()
    total = 0
    try:
        with tarfile.open(archive, 'r:gz') as tf:
            for member in tf:
                name = member.name.rstrip('/') if member.isdir() else member.name
                rel = safe_relative(name)
                folded = name.casefold()
                if rel.parts[0] not in {'python', 'lib'} or folded in seen:
                    raise InstallError('Unexpected/duplicate runtime path: %s' % name)
                if not member.isfile() and not member.isdir():
                    raise InstallError('Runtime accepts only regular files/directories: %s' % name)
                if member.size < 0 or member.sparse is not None:
                    raise InstallError('Sparse/invalid runtime file')
                seen.add(folded)
                total += member.size
                if total > policy.max_unpacked_bytes or len(seen) > policy.max_members:
                    raise InstallError('Runtime exceeds extraction limit')
                members.append((name, member.isdir(), member.size, member.mode))
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise InstallError('Cannot read runtime archive: %s' % exc) from exc
    file_names = {n for n, directory, _, _ in members if not directory}
    for name, _, _, _ in members:
        if any(parent.as_posix() in file_names for parent in PurePosixPath(name).parents if parent.as_posix() != '.'):
            raise InstallError('Runtime file used as a directory: %s' % name)
    if 'python/bin/python2.7' not in file_names:
        raise InstallError('Runtime is missing python/bin/python2.7')
    return members, total


def extract_runtime(archive, destination, policy):
    archive_members(archive, policy)
    destination.mkdir()
    with tarfile.open(archive, 'r:gz') as tf:
        for member in tf:
            name = member.name.rstrip('/') if member.isdir() else member.name
            target = destination / safe_relative(name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(member) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                # No setuid/setgid/sticky bits; owner writable, others read/execute only.
                target.chmod(0o755 if member.mode & 0o111 or name == 'python/bin/python2.7' else 0o644)


def resolve_source(source, shared=None):
    source = no_links(source)
    if not (source / 'game.py').is_file() and (source / 'cactuscanyon/game.py').is_file():
        source = no_links(source / 'cactuscanyon')
    files = tree_files(source)
    required = ['game.py', 'assets.py', 'config.yaml', 'config/cc_machine.yaml',
                'config/game_data_template.yaml', 'config/settings_template.yaml',
                'procgame/__init__.py', 'ep/__init__.py', 'cc_modes/__init__.py']
    missing = [name for name in required if not (source / name).is_file()]
    for category, choices in [('sound', ('assets/sound', 'sounds')), ('dmd', ('assets/dmd', 'dmd', 'dots'))]:
        if not any(any(p.is_file() and p.stat().st_size for p in (source / choice).rglob('*')) for choice in choices):
            missing.append(category + ' assets (empty or missing)')
    if missing:
        raise InstallError('Missing source files: ' + ', '.join(missing))
    if shared is None:
        candidates = [source / 'shared', source.parent / 'shared', source.parent.parent / 'shared']
        shared = next((candidate for candidate in candidates if candidate.is_dir()), None)
    if shared is not None:
        shared = no_links(shared)
        tree_files(shared)
    return source, shared, files


def game_copy_files(source, files):
    selected = []
    for file in files:
        rel = file.relative_to(source)
        lower = [p.lower() for p in rel.parts]
        if any(p.startswith('.') or p in {'__pycache__', 'backups', 'backup', 'logs'} for p in lower):
            continue
        if any('game_data' in p and p != 'game_data_template.yaml' or 'user_settings' in p or 'backup' in p for p in lower):
            continue
        if file.suffix.lower() in {'.pyc', '.pyo', '.log', '.bak', '.exe', '.dll', '.so'}:
            continue
        if len(rel.parts) == 1:
            include = file.suffix == '.py' or rel.name in {'config.yaml', 'cc_machine.yaml'}
        elif rel.parts[0] == 'config':
            include = len(rel.parts) == 2 and rel.name in CONFIG_FILES
        else:
            include = rel.parts[0] in GAME_DIRS
        if include:
            selected.append((file, rel))
    return selected


def copy_selected(files, destination):
    for source, rel in files:
        no_links(source)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)


def copy_shared(shared, destination):
    destination.mkdir(parents=True, exist_ok=True)
    if shared is None:
        return
    selected = []
    for file in tree_files(shared):
        rel = file.relative_to(shared)
        if any(p.startswith('.') or 'backup' in p.lower() or 'user_settings' in p.lower() or 'game_data' in p.lower() for p in rel.parts):
            continue
        if (rel.parts[0] in SHARED_DIRS or rel.as_posix() == 'vp_game_map.yaml') and file.suffix.lower() not in {'.log', '.pyc', '.pyo'}:
            selected.append((file, rel))
    copy_selected(selected, destination)


def normalize_config(path):
    """Replace only the copied machine config, never private source settings.

    No source YAML is evaluated; original defaults/templates remain untouched.
    Standard CCC has sounds/dmd; some source distributions use assets/sound.
    """
    game = path.parent
    sound = './assets/sound/' if (game / 'assets/sound').is_dir() else './sounds/'
    dmd = './assets/dmd/' if (game / 'assets/dmd').is_dir() else './dmd/'
    text = ('# Clean native CCC machine policy; original source is untouched.\n'
            'font_path:\n  - ../shared/dmd/\n  - ./shared/dmd/\n  - ' + dmd + '\n'
            'config_path:\n  - ./config/\n  - ../shared/config/\n'
            'dmd_path: ' + dmd + '\nsound_path: ' + sound + '\n'
            'voice_dir: voice/\nsfx_dir: sfx/\nmusic_dir: music/\n'
            'hdfont_path: ./assets/fonts/\n'
            'pinproc_class: procgame.fakepinproc.FakePinPROC\n'
            'use_desktop: true\ncolor_desktop: true\nwindowless: false\n'
            'usb_update: false\nshutdown_flag: false\npower_button_combo: false\n'
            'vp_game_map_file: ../shared/vp_game_map.yaml\n')
    path.write_text(text, encoding='utf-8', newline='\n')


def probe_platform():
    report = {'supported': False, 'system': platform.system(), 'arch': platform.machine()}
    if report['system'] != 'Linux' or report['arch'] not in {'x86_64', 'amd64'}:
        report['reason'] = 'Only native Batocera Linux x86_64 is qualified'
        return report
    version = ''
    for path in [Path('/usr/share/batocera/batocera.version'), Path('/etc/batocera.version')]:
        if path.is_file():
            version = path.read_text().strip()
            break
    report['version'] = version
    if not re.match(r'^43(?:\.1)?(?:[-\s]|$)', version):
        report['reason'] = 'Only Batocera 43/43.1 is qualified; found: ' + version
        return report
    libc, version_glibc = platform.libc_ver()
    report['glibc'] = version_glibc
    try:
        glibc_ok = libc == 'glibc' and tuple(int(v) for v in version_glibc.split('.')[:2]) >= (2, 35)
    except ValueError:
        glibc_ok = False
    if not glibc_ok:
        report['reason'] = 'glibc >= 2.35 required; runtime support is version-limited'
        return report
    candidates = ['/usr/bin/vpinball/VPinballX_GL', '/usr/bin/vpinball/VPinballX',
                  '/usr/bin/vpinball/VPinballX_BGFX', '/usr/bin/VPinballX_GL',
                  '/usr/bin/vpinball', '/usr/bin/VPinballX']
    vpx = next((p for p in candidates if Path(p).is_file() and os.access(p, os.X_OK)), None)
    if not vpx:
        report['reason'] = 'Native VPX executable is missing'
        return report
    report.update(supported=True, native_vpx=vpx, reason='Version-limited preflight; not a live game/hardware test')
    return report


def dmd_address(value):
    if value is None or value == 'off':
        return None
    if not isinstance(value, str):
        raise InstallError('DMD endpoint must be HOST:PORT')
    match = re.fullmatch(r'(\[[0-9a-fA-F:]+\]|[A-Za-z0-9_.-]+):([0-9]{1,5})', value)
    if not match or not 1 <= int(match.group(2)) <= 65535:
        raise InstallError('Invalid DMD endpoint; expected HOST:PORT (1..65535)')
    host, port = match.groups()
    if host.startswith('['):
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError as exc:
            raise InstallError('Invalid IPv6 DMD address') from exc
        host = host[1:-1]
    elif host.startswith('.') or host.endswith('.') or '..' in host:
        raise InstallError('Invalid DMD host')
    return host, int(port)


def settings_default(root, platform_report, dmd_server=None):
    address = dmd_address(dmd_server)
    return {'schema': 1, 'profile': 'desktop', 'audio': True, 'sound_enabled': True,
            'audio_backend': 'auto', 'software_dmd': True,
            'physical_dmd': bool(address), 'dmd_host': address[0] if address else '127.0.0.1',
            'dmd_port': address[1] if address else 6789, 'volume': 50, 'display': {},
            'native_vpx': platform_report.get('native_vpx'), 'install_root': str(root),
            'dmd_server': dmd_server if address else None}


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


def atomic_write(path, data, mode=0o644):
    temp = path.with_name('.' + path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def install_lock(root):
    path = root.parent / ('.' + root.name + '.install-lock')
    try:
        path.mkdir()
    except FileExistsError as exc:
        raise InstallError('Installation is locked; check running process: %s' % path) from exc
    try:
        if (root / 'launch.lock').exists():
            raise InstallError('Game is running or has a stale launch.lock; stop/review it before changing the installation')
        yield
    finally:
        path.rmdir()


def launcher_bytes(root):
    return ('#!/bin/sh\n# CCC native; scoped launcher, no global video changes.\n'
            'export CCC_INSTALL_ROOT=' + shlex.quote(str(root)) + '\n'
            'exec python3 -B "$CCC_INSTALL_ROOT/app/launch.py" --root "$CCC_INSTALL_ROOT" "$@"\n').encode('utf-8')


def validate_launcher(launcher, record):
    if launcher.exists():
        if not record or record.get('launcher_sha256') != sha256(launcher):
            raise InstallError('Existing Ports launcher is not unchanged installer-owned content; refusing overwrite')


def default_table_builder(app, table, script, destination, final_root):
    builder = app / 'build_table.py'
    if not builder.is_file():
        raise InstallError('Package is missing app/build_table.py; native table build is required')
    command = [sys.executable, '-B', str(builder), str(table), str(script), str(destination),
               '--install-root', str(final_root)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise InstallError('Native table build failed: ' + (result.stderr or result.stdout)[-4000:])


def install(*, package, source, table, script, mapping_source=None, userdata='/userdata', root=None, shared=None,
            dry_run=False, skip_platform_check=False, dmd_server=None, import_saves=None,
            policy=None, platform_probe=None, table_builder=None, commit_hook=None):
    policy = policy or Policy()
    ud, root, launcher = paths(userdata, root)
    if skip_platform_check and not dry_run:
        raise InstallError('--skip-platform-check is only allowed for check/dry-run; installation is refused')
    report = (platform_probe or probe_platform)()
    if not report.get('supported') and not skip_platform_check:
        raise InstallError('Unsupported platform: ' + report.get('reason', 'unknown'))
    address = dmd_address(dmd_server)
    package = no_links(package)
    table, script = no_links(table), no_links(script)
    for file, expected, label in [(table, policy.table_sha256, 'VPX table'), (script, policy.script_sha256, 'original VBS script')]:
        if not file.is_file() or sha256(file) != expected:
            raise InstallError('Unknown/modified %s; only the exact qualified version is allowed' % label)
    if mapping_source is None:
        raise InstallError('--mapping-source is required (your exact register_vpcom.py)')
    mapping_source = no_links(mapping_source)
    if not mapping_source.is_file() or not stat.S_ISREG(mapping_source.stat().st_mode):
        raise InstallError('Mapping source must be a regular file')
    try:
        mapping_bytes, mapping_manifest = build_mapping(mapping_source)
    except MappingError as exc:
        raise InstallError(str(exc)) from exc
    source, shared, source_files = resolve_source(source, shared)
    for input_path in [package, source, table, script, mapping_source, *([shared] if shared else [])]:
        if root == input_path or root in input_path.parents or input_path in root.parents:
            raise InstallError('Source/package and installation root must not overlap')
    archive, hashes = package_files(package)
    _, expanded = archive_members(archive, policy)
    record = owned(root) if root.exists() else None
    validate_launcher(launcher, record)
    if import_saves is not None:
        import_saves = no_links(import_saves)
        tree_files(import_saves)
        if not any((import_saves / name).is_file() for name in SAVE_FILES):
            raise InstallError('--import-saves contains no game_data.yaml/user_settings.yaml')
    result = {'status': 'dry-run', 'root': str(root), 'platform': report, 'runtime_bytes': expanded,
              'supported_install': bool(report.get('supported') and not skip_platform_check),
              'warnings': ['Game assets remain user-supplied; no universal hardware compatibility guarantee.'],
              'source': str(source), 'shared': str(shared) if shared else None,
              'mapping': mapping_manifest}
    if skip_platform_check:
        result['warnings'].append('DEVELOPER CHECK: platform verification skipped; NOT a supported installation.')
    if not shared:
        result['warnings'].append('No shared directory found; required shared fonts/config must exist in the game source.')
    if dry_run:
        return result
    root.parent.mkdir(parents=True, exist_ok=True)
    no_links(root.parent)
    stage = root.parent / ('.' + root.name + '.stage-' + uuid.uuid4().hex)
    backup = root.parent / ('.' + root.name + '.backup-' + uuid.uuid4().hex)
    with install_lock(root):
        # Recheck external targets after lock acquisition.
        record = owned(root) if root.exists() else None
        validate_launcher(launcher, record)
        stage.mkdir()
        root_committed = False
        previous_moved = False
        launcher_written = False
        previous_launcher = launcher.read_bytes() if launcher.exists() else None
        try:
            copy_selected([(package / name, Path(name).relative_to('app'))
                           for name in hashes if name.startswith('app/')], stage / 'app')
            # Check copied app bytes against the verified package before executing its builder.
            for name, digest in hashes.items():
                if name.startswith('app/') and sha256(stage / name) != digest:
                    raise InstallError('Package changed during installation: ' + name)
            if sha256(archive) != hashes['payload/runtime.tar.gz']:
                raise InstallError('Runtime changed during installation')
            # Use exactly the verified/generated bytes from preflight, not a second input read.
            (stage / 'app/ccc_wpc_mapping.py').write_bytes(mapping_bytes)
            (stage / 'mapping-manifest.json').write_bytes(json_bytes(mapping_manifest))
            extract_runtime(archive, stage / 'runtime', policy)
            copy_selected(game_copy_files(source, source_files), stage / 'game')
            normalize_config(stage / 'game/config.yaml')
            copy_shared(shared, stage / 'shared')
            # assets.py from standard CCC also addresses game/shared/dmd directly.
            copy_shared(shared, stage / 'game/shared')
            (stage / 'tables').mkdir()
            (table_builder or default_table_builder)(stage / 'app', table, script, stage / 'tables', root)
            for name in ['CCC-native.vpx', 'CCC-native.vbs']:
                file = stage / 'tables' / name
                if not file.is_file() or file.stat().st_size == 0:
                    raise InstallError('Table builder did not produce ' + name)
            for directory in PRESERVE:
                (stage / directory).mkdir()
            if record:
                for directory in PRESERVE:
                    original = root / directory
                    if original.is_dir():
                        copy_selected([(p, p.relative_to(original)) for p in tree_files(original)], stage / directory)
            settings_file = stage / 'config/settings.json'
            if not settings_file.exists():
                settings_file.write_bytes(json_bytes(settings_default(root, report, dmd_server)))
            elif dmd_server is not None:
                settings = read_json(settings_file)
                settings.update(physical_dmd=bool(address), dmd_host=address[0] if address else '127.0.0.1',
                                dmd_port=address[1] if address else 6789, dmd_server=dmd_server if address else None)
                settings_file.write_bytes(json_bytes(settings))
            ini = stage / 'config/VPX.ini'
            if not ini.exists():
                ini.write_text('; Native VPX defaults; display is detected by the application.\n', encoding='utf-8')
            if import_saves:
                for name in SAVE_FILES:
                    original = import_saves / name
                    if original.is_file():
                        shutil.copyfile(original, stage / 'saves' / name)
            # Include only installed immutable files. Config/saves/runs are never uninstall targets.
            installed = {p.relative_to(stage).as_posix(): sha256(p) for p in tree_files(stage)
                         if p.relative_to(stage).parts[0] not in PRESERVE}
            directories = sorted(p.relative_to(stage).as_posix() for p in stage.rglob('*')
                                 if p.is_dir() and p.relative_to(stage).parts[0] not in PRESERVE)
            launch_data = launcher_bytes(root)
            marker = {'schema': 1, 'owner': OWNER, 'root': str(root), 'active': True,
                      'files': installed, 'dirs': directories, 'launcher_sha256': hashlib.sha256(launch_data).hexdigest(),
                      'source_table_sha256': policy.table_sha256, 'source_script_sha256': policy.script_sha256,
                      'package_sha256': hashes, 'platform': report,
                      'mapping': mapping_manifest}
            (stage / MARKER).write_bytes(json_bytes(marker))
            tree_files(stage)
            if commit_hook:
                commit_hook('staged')
            if root.exists():
                os.replace(root, backup)
                previous_moved = True
            os.replace(stage, root)
            root_committed = True
            if commit_hook:
                commit_hook('root-committed')
            launcher.parent.mkdir(parents=True, exist_ok=True)
            no_links(launcher.parent)
            atomic_write(launcher, launch_data, 0o755)
            launcher_written = True
            check(ud, root)
            result.update(status='installed', backup=str(backup) if previous_moved else None)
            return result
        except BaseException:
            if launcher_written:
                if previous_launcher is not None:
                    atomic_write(launcher, previous_launcher, 0o755)
                else:
                    launcher.unlink()
            if root_committed:
                # Return this attempt's owned tree to staging before restoring the old root.
                os.replace(root, stage)
            if previous_moved:
                os.replace(backup, root)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def check(userdata='/userdata', root=None):
    _, root, launcher = paths(userdata, root)
    record = owned(root)
    damaged = []
    for name, digest in record['files'].items():
        file = root / safe_relative(name)
        if not file.is_file() or sha256(file) != digest:
            damaged.append(name)
    if not launcher.is_file() or sha256(launcher) != record.get('launcher_sha256'):
        damaged.append('Ports launcher')
    if damaged:
        raise InstallError('Installation modified/incomplete: ' + ', '.join(damaged))
    settings = read_json(root / 'config/settings.json')
    return {'status': 'ok', 'root': str(root), 'verified_files': len(record['files']),
            'profile': settings.get('profile'), 'note': 'Integrity check; not a live VPX/hardware test.'}


def configure(userdata='/userdata', root=None, *, sound=None, dmd_server=None, dry_run=False):
    _, root, _ = paths(userdata, root)
    owned(root)
    path = root / 'config/settings.json'
    settings = read_json(path)
    if not isinstance(settings, dict):
        raise InstallError('settings.json must contain an object')
    if sound is not None:
        if not isinstance(sound, bool):
            raise InstallError('sound must be boolean')
        settings['audio'] = sound
        settings['sound_enabled'] = sound
    if dmd_server is not None:
        address = dmd_address(dmd_server)
        settings.update(physical_dmd=bool(address), dmd_host=address[0] if address else '127.0.0.1',
                        dmd_port=address[1] if address else 6789, dmd_server=dmd_server if address else None)
    if not dry_run:
        with install_lock(root):
            owned(root)
            atomic_write(path, json_bytes(settings))
    return {'status': 'dry-run' if dry_run else 'configured', 'root': str(root), 'settings': settings}


def uninstall(userdata='/userdata', root=None, *, dry_run=False):
    _, root, launcher = paths(userdata, root)
    record = owned(root)
    removed, retained = [], []
    for name, digest in record['files'].items():
        file = root / safe_relative(name)
        if file.is_file() and sha256(file) == digest:
            removed.append(name)
        elif file.exists():
            retained.append(name)
    launcher_remove = launcher.is_file() and sha256(launcher) == record.get('launcher_sha256')
    if not dry_run:
        with install_lock(root):
            owned(root)
            for name in removed:
                file = root / safe_relative(name)
                if file.is_file() and sha256(file) == record['files'][name]:
                    file.unlink()
            if launcher_remove and launcher.is_file() and sha256(launcher) == record['launcher_sha256']:
                launcher.unlink()
            for name in sorted(record.get('dirs', []), key=lambda p: (p.count('/'), p), reverse=True):
                directory = root / safe_relative(name)
                if directory.is_dir():
                    try:
                        directory.rmdir()
                    except OSError:
                        pass  # Foreign or changed files always win over cleanup.
            record.update(active=False, files={n: record['files'][n] for n in retained})
            atomic_write(root / MARKER, json_bytes(record))
    return {'status': 'dry-run' if dry_run else 'uninstalled', 'root': str(root),
            'removed_files': len(removed), 'retained_changed': retained,
            'preserved': ['config/', 'saves/', 'runs/', 'backups and foreign/changed files'],
            'launcher_removed': launcher_remove}
