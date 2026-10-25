#!/usr/bin/env python3
"""Native CCC installer: no Wine, untrusted downloads or bundled game assets."""
import argparse
import json
from pathlib import Path
import sys

# check/dry-run must not even create Python bytecode caches.
sys.dont_write_bytecode = True
if __package__:
    from . import core
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from installer import core


def parser():
    p = argparse.ArgumentParser(description='CCC native for Batocera 43/43.1 x86_64 (version-limited).')
    commands = p.add_subparsers(dest='command', required=True)
    for name in ['install', 'check', 'configure', 'uninstall']:
        child = commands.add_parser(name)
        child.add_argument('--userdata', default='/userdata', help='Existing userdata directory')
        child.add_argument('--root', help='Installation root inside userdata (default system/ccc-native)')
        child.add_argument('--dry-run', action='store_true', help='Validate only; do not write anything')
        if name in {'install', 'check', 'configure'}:
            child.add_argument('--profile', choices=['desktop', 'cabinet'], help='Persist desktop view or portrait cabinet view')
            child.add_argument('--dmd-mode', choices=['software', 'hardware', 'both'],
                               help='DMD display: software (default), hardware via local server, or both')
        if name in {'install', 'check'}:
            child.add_argument('--source', required=name == 'install', help='Your CCC game directory or its parent')
            child.add_argument('--table', required=name == 'install', help='Exact supported original Bigus VPX table')
            child.add_argument('--script', required=name == 'install', help='Matching original VBS sidecar')
            child.add_argument('--mapping-source', required=name == 'install', help='Your exact original register_vpcom.py; locally extracted, never executed')
            child.add_argument('--shared', help='Your shared fonts/config directory (otherwise local autodetection)')
            child.add_argument('--skip-platform-check', action='store_true', help='Offline check/dry-run ONLY; never installs')
            child.add_argument('--import-saves', help='Explicit opt-in: import only game_data.yaml/user_settings.yaml')
            child.add_argument('--dmd-server', help='Compatible local DMD server 127.0.0.1:PORT or off; hardware/both default to port 6789')
            child.add_argument('--package', default=str(Path(__file__).resolve().parents[1]), help=argparse.SUPPRESS)
        if name == 'install':
            child.add_argument('--check', action='store_true', help='Alias for --dry-run')
        if name == 'configure':
            child.add_argument('--sound', choices=['on', 'off'])
            child.add_argument('--dmd-server', help='127.0.0.1:PORT or off')
    return p


def prepare_options(args):
    """Validate before bootstrap; prompt only for a new, real, supported TTY install."""
    if args.command not in ('install', 'check', 'configure'):
        return False
    core.dmd_settings({}, args.dmd_mode, args.dmd_server)
    if args.command == 'check' and not args.source and (args.dmd_mode is not None or args.dmd_server is not None):
        raise core.InstallError('DMD options require a pre-install check with --source')
    if (args.command != 'install' or args.dry_run or args.check or args.skip_platform_check
            or args.dmd_mode is not None or args.dmd_server is not None
            or not sys.stdin.isatty() or not sys.stdout.isatty()):
        return False
    root = Path(args.root) if args.root else Path(args.userdata) / 'system/ccc-native'
    if root.exists() or not core.probe_platform().get('supported'):
        return False  # Ordinary updates preserve the saved choice, including legacy configs.
    print('Choose your DMD display:\n'
          '  1) Software DMD (on-screen, recommended default)\n'
          '  2) Hardware DMD only\n'
          '  3) Both software and hardware DMD', file=sys.stderr)
    try:
        while True:
            choice = input('DMD choice [1]: ').strip() or '1'
            if choice in ('1', '2', '3'):
                break
            print('Please enter 1, 2 or 3.', file=sys.stderr)
        mode = {'1': 'software', '2': 'hardware', '3': 'both'}[choice]
        endpoint = None
        if mode != 'software':
            print('Hardware output requires an already-running compatible local DMD server.\n'
                  'The installer does not install, start, stop or change any service.', file=sys.stderr)
            while True:
                endpoint = input('Local DMD server [127.0.0.1:6789]: ').strip() or '127.0.0.1:6789'
                try:
                    core.dmd_settings({}, mode, endpoint)
                    break
                except core.InstallError as exc:
                    print(str(exc), file=sys.stderr)
    except (EOFError, KeyboardInterrupt) as exc:
        raise core.InstallError('DMD selection cancelled; nothing was installed. Use --dmd-mode for unattended installs.') from exc
    args.dmd_mode, args.dmd_server = mode, endpoint
    return True


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        prepare_options(args)
        if args.command == 'install' or args.command == 'check' and args.source:
            if not args.table or not args.script or not args.mapping_source:
                p.error('Pre-install check requires --source, --table, --script and --mapping-source together')
            result = core.install(package=args.package, source=args.source, table=args.table, script=args.script,
                                  shared=args.shared, userdata=args.userdata, root=args.root, mapping_source=args.mapping_source,
                                  dry_run=args.command == 'check' or args.dry_run or getattr(args, 'check', False),
                                  skip_platform_check=args.skip_platform_check,
                                  dmd_server=args.dmd_server, dmd_mode=args.dmd_mode,
                                  import_saves=args.import_saves, profile=args.profile)
        elif args.command == 'check':
            if args.mapping_source or args.table or args.script or args.shared or args.import_saves or args.skip_platform_check:
                p.error('Source check requires all --source/--table/--script/--mapping-source arguments')
            result = core.check(args.userdata, args.root)
        elif args.command == 'configure':
            result = core.configure(args.userdata, args.root,
                                    sound=None if args.sound is None else args.sound == 'on',
                                    dmd_server=args.dmd_server, dmd_mode=args.dmd_mode,
                                    profile=args.profile, dry_run=args.dry_run)
        else:
            result = core.uninstall(args.userdata, args.root, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (core.InstallError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
