"""Patch only a hash-qualified, user-provided VPX/sidecar pair. No table code ships here."""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

TABLE_SHA256 = '542b775831a1876c37d46eb8a825e7027562f6bab648f4ee2e6b20db483353a8'
SCRIPT_SHA256 = 'f20205c66d564b6bbc88f6b0ee7635f0e2fd6c9256b6255d6fa2065899618f7e'
APP = Path(__file__).resolve().parent


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def vbs_literal(value):
    value = str(value)
    if any(c in value for c in '\r\n\0'):
        raise ValueError('Invalid path in VBScript literal')
    return '"' + value.replace('"', '""') + '"'


def patch_script(text, install_root):
    pointer = Path(install_root) / 'active-ipc.txt'
    replacements = [
        ('  LoadProc "02000000", "WPC.VBS", 3.50',
         '  LoadVBSFiles "WPC.VBS"\n'
         '  Set Controller = New CCCNativeController\n'
         '  Dim CCCfs, CCCpathfile, CCCipc\n'
         '  Set CCCfs = CreateObject("Scripting.FileSystemObject")\n'
         '  Set CCCpathfile = CCCfs.OpenTextFile(' + vbs_literal(pointer.as_posix()) + ',1)\n'
         '  CCCipc = Trim(CCCpathfile.ReadLine): CCCpathfile.Close\n'
         '  Controller.RootPath = CCCipc\n'
         '  B2SOn=False: B2SOnALT=False'),
        ('Const UseVPMModSol = True', 'Const UseVPMModSol = False'),
        ('  Controller.Stop', '  Controller.Shutdown'),
        ('    On Error Resume Next\n    .Run\n    If Err Then MsgBox Err.Description',
         '    On Error Goto 0\n    .Run')]
    text = text.replace('\r\n', '\n')
    for old, new in replacements:
        if text.count(old) != 1:
            raise ValueError('Expected exactly one supported table patch anchor: ' + repr(old))
        text = text.replace(old, new)
    return text + '\n\n' + (APP / 'ccc_native_controller.vbs').read_text(encoding='utf-8')


def build_table(source_vpx, source_vbs, destination, install_root=None, *, _hashes=None):
    """Private _hashes is fixture-only; CLI never permits unsupported source hashes."""
    source_vpx, source_vbs, destination = map(Path, (source_vpx, source_vbs, destination))
    expected = _hashes or (TABLE_SHA256, SCRIPT_SHA256)
    actual = (sha256(source_vpx), sha256(source_vbs))
    if actual != tuple(expected):
        raise ValueError('Unsupported table/script pair; exact known Bigusmod hashes required')
    destination = destination.resolve()
    install_root = Path(install_root).resolve() if install_root else destination.parent
    if not install_root.is_absolute():
        raise ValueError('Installation root must be absolute')
    # Installer builds in a staging tree but embeds the final installation path.
    if destination in (source_vpx.resolve().parent, source_vbs.resolve().parent):
        raise ValueError('Never patch in the user source directory')
    script = patch_script(source_vbs.read_text(encoding='utf-8-sig'), install_root)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('CCC-native.vpx', 'CCC-native.vbs', 'build-manifest.json'):
        if (destination / name).exists() or (destination / name).is_symlink():
            raise FileExistsError('Candidate already exists: ' + name)
    with tempfile.TemporaryDirectory(prefix='.build-', dir=destination) as temp:
        temp = Path(temp)
        shutil.copyfile(source_vpx, temp / 'CCC-native.vpx')
        (temp / 'CCC-native.vbs').write_text(script, encoding='utf-8', newline='\n')
        # Recheck the copied bytes, not just the pre-copy source hash.
        if sha256(temp / 'CCC-native.vpx') != expected[0]:
            raise ValueError('Table source changed while copying')
        manifest = {'schema': 1, 'source_table_sha256': actual[0], 'source_script_sha256': actual[1],
                    'table_sha256': sha256(temp / 'CCC-native.vpx'),
                    'script_sha256': sha256(temp / 'CCC-native.vbs'),
                    'pointer': (install_root / 'active-ipc.txt').as_posix()}
        (temp / 'build-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        for name in ('CCC-native.vpx', 'CCC-native.vbs', 'build-manifest.json'):
            os.replace(temp / name, destination / name)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source_vpx', type=Path, nargs='?')
    p.add_argument('source_vbs', type=Path, nargs='?')
    p.add_argument('destination', type=Path, nargs='?')
    p.add_argument('--table', type=Path)
    p.add_argument('--script', type=Path)
    p.add_argument('--output-table', type=Path)
    p.add_argument('--output-script', type=Path)
    p.add_argument('--install-root', type=Path)
    a = p.parse_args()
    if any((a.table, a.script, a.output_table, a.output_script)):
        if any((a.source_vpx, a.source_vbs, a.destination)) or not all((a.table, a.script, a.output_table, a.output_script)):
            p.error('Use either three positional paths or all four named table/script paths')
        if (a.output_table.name != 'CCC-native.vpx' or a.output_script.name != 'CCC-native.vbs' or
                a.output_table.parent != a.output_script.parent):
            p.error('Output names must be CCC-native.vpx and CCC-native.vbs in one directory')
        a.source_vpx, a.source_vbs, a.destination = a.table, a.script, a.output_table.parent
    if not all((a.source_vpx, a.source_vbs, a.destination)):
        p.error('Three source/destination paths required')
    print(json.dumps(build_table(a.source_vpx, a.source_vbs, a.destination, a.install_root), indent=2))


if __name__ == '__main__':
    main()
