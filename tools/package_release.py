#!/usr/bin/env python3
"""Build an allowlisted source installer archive; never archive the workspace."""
import argparse
import hashlib
import io
import json
import re
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXACT = ('README.md', 'ccc.py', 'install.sh', 'docs/INSTALLATION.md', 'docs/VERIFICATION.md',
         'payload/runtime-downloads.lock.json', 'payload/native-overlay.tar.gz',
         'payload/native-overlay-sources.tar.gz', 'payload/native-overlay-manifest.json',
         'payload/native-overlay-allowlist.json', '.github/workflows/tests.yml')
TOOLS = ('tools/prepare_runtime.py', 'tools/build_overlay.py', 'tools/package_release.py')
PRIVATE_NAMES = {'ccc_wpc_mapping.py', 'register_vpcom.py', 'user_settings.yaml',
                 'game_data.yaml', 'runtime.tar.gz', 'runtime-manifest.json'}


def sha(blob):
    return hashlib.sha256(blob).hexdigest()


def selected_files(root):
    chosen = list(EXACT) + list(TOOLS)
    for folder, suffixes in [('app', {'.py', '.sh', '.vbs', '.md', '.txt'}),
                             ('installer', {'.py', '.md'}), ('licenses', {'.txt', '.md'}),
                             ('tests', {'.py'}), ('app-tests', {'.py'}), ('entry-tests', {'.py'})]:
        for path in sorted((root / folder).iterdir()):
            if path.is_file() and path.suffix in suffixes and path.name not in PRIVATE_NAMES:
                chosen.append(path.relative_to(root).as_posix())
    for name in chosen:
        path = root / name
        if not path.is_file() or path.is_symlink() or any(p in PRIVATE_NAMES or p == '__pycache__' for p in Path(name).parts):
            raise ValueError('Missing or unsafe selected release input: ' + name)
    return {name: (root / name).read_bytes() for name in sorted(set(chosen))}


def public_export(files):
    """Redact host account paths in evidence only; preserve compiled/source bytes."""
    lock = json.loads(files['payload/runtime-downloads.lock.json'])
    source_key = 'payload/' + lock['overlay']['sources_filename']
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(files[source_key]), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile():
                raise ValueError('Unexpected source export member')
            entries[member.name] = tar.extractfile(member).read()
    changes = []
    for name in ('FRESH-BUILD.json', 'build-evidence/commands.json'):
        original = entries[name]
        redacted = re.sub(rb'/mnt/[a-z]/Users/[^/\s"\\]+', b'/SOURCE-WORKSPACE', original)
        if original != redacted:
            entries[name] = redacted
            changes.append({'path': name, 'original_sha256': sha(original),
                            'public_sha256': sha(redacted), 'scope': 'Host account path prefix only'})
    if changes:
        entries['PUBLIC-REDACTIONS.json'] = (json.dumps({'note': 'Private originals retained by maintainer. No source, Setup, compile flags or binaries changed.', 'changes': changes}, indent=2) + '\n').encode()
        manifest = json.loads(entries['SOURCE-MANIFEST.json'])
        for row in manifest['files']:
            blob = entries[row['path']]
            row.update(bytes=len(blob), sha256=sha(blob))
        blob = entries['PUBLIC-REDACTIONS.json']
        manifest['files'].append({'path': 'PUBLIC-REDACTIONS.json', 'bytes': len(blob), 'sha256': sha(blob)})
        entries['SOURCE-MANIFEST.json'] = (json.dumps(manifest, indent=2) + '\n').encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz', format=tarfile.PAX_FORMAT) as tar:
            for name, blob in sorted(entries.items()):
                info = tarfile.TarInfo(name)
                info.mode, info.mtime, info.size = 0o644, 0, len(blob)
                tar.addfile(info, io.BytesIO(blob))
        files[source_key] = stream.getvalue()
        lock['overlay']['sources_sha256'] = sha(files[source_key])
        files['payload/runtime-downloads.lock.json'] = (json.dumps(lock, indent=2) + '\n').encode()
    return files


def verify_payload(files):
    lock = json.loads(files['payload/runtime-downloads.lock.json'])
    overlay = lock['overlay']
    for filename, digest_key in [(overlay['filename'], 'sha256'), (overlay['sources_filename'], 'sources_sha256')]:
        if sha(files['payload/' + filename]) != overlay[digest_key]:
            raise ValueError('Overlay/lock checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(files['payload/' + overlay['filename']]), mode='r:gz') as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith('python/lib/python2.7/site-packages/') or Path(member.name).suffix not in {'.py', '.so'}:
                raise ValueError('Unexpected binary overlay member: ' + member.name)
    with tarfile.open(fileobj=io.BytesIO(files['payload/' + overlay['sources_filename']]), mode='r:gz') as tar:
        for member in tar:
            name = member.name
            if not member.isfile() or name.startswith('/') or '..' in Path(name).parts or '\\' in name:
                raise ValueError('Unsafe source export member: ' + name)
            blob = tar.extractfile(member).read()
            if b'\0' in blob or blob.startswith((b'MZ', b'\x7fELF', b'\x1f\x8b', b'PK\x03\x04')) or Path(name).suffix.lower() in {'.ttf', '.otf', '.png', '.jpg', '.wav', '.exe', '.dll', '.pyc', '.zip', '.gz'}:
                raise ValueError('Binary/resource/nested archive in source export: ' + name)


def build(root, output, development=False):
    files = public_export(selected_files(root))
    verify_payload(files)
    if not development:
        gate_path = root / 'release-evidence/gates.json'
        gates = json.loads(gate_path.read_text())
        if gates.get('download_lock_sha256') != sha(files['payload/runtime-downloads.lock.json']):
            raise ValueError('Release evidence is stale for this dependency/overlay lock')
        for gate in ('source_correspondence', 'batocera_installed_headless', 'batocera_visible_player'):
            record = gates.get(gate, {})
            if record.get('status') != 'PASS':
                raise ValueError('Release gate not passed: ' + gate)
            report = root / record['report']
            if report.resolve().parent != (root / 'release-evidence').resolve() or sha(report.read_bytes()) != record['sha256']:
                raise ValueError('Missing/changed gate evidence: ' + gate)
    metadata = {'schema': 1, 'status': 'DEVELOPMENT-NOT-QUALIFIED' if development else 'RELEASE',
                'files': {name: sha(blob) for name, blob in files.items()},
                'exclusions': sorted(PRIVATE_NAMES), 'runtime': 'recipient-side pinned downloads only'}
    files['PACKAGE-MANIFEST.json'] = (json.dumps(metadata, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for name, blob in files.items():
            info = zipfile.ZipInfo('ccc-native-batocera/' + name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = (0o100755 if name.endswith('.sh') else 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, blob)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise ValueError('Release archive verification failed')
        for name, blob in files.items():
            if archive.read('ccc-native-batocera/' + name) != blob:
                raise ValueError('Release member readback mismatch')
    return {'status': metadata['status'], 'archive': str(output), 'sha256': sha(output.read_bytes()), 'members': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--development', action='store_true', help='Explicitly label package unqualified; never claim a tested public release')
    args = parser.parse_args()
    print(json.dumps(build(ROOT, args.output, args.development), indent=2))


if __name__ == '__main__':
    main()
