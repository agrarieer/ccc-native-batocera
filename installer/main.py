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
        if name in {'install', 'check'}:
            child.add_argument('--source', required=name == 'install', help='Your CCC game directory or its parent')
            child.add_argument('--table', required=name == 'install', help='Exact supported original Bigus VPX table')
            child.add_argument('--script', required=name == 'install', help='Matching original VBS sidecar')
            child.add_argument('--mapping-source', required=name == 'install', help='Your exact original register_vpcom.py; locally extracted, never executed')
            child.add_argument('--shared', help='Your shared fonts/config directory (otherwise local autodetection)')
            child.add_argument('--skip-platform-check', action='store_true', help='Offline check/dry-run ONLY; never installs')
            child.add_argument('--import-saves', help='Explicit opt-in: import only game_data.yaml/user_settings.yaml')
            child.add_argument('--dmd-server', help='Optional physical DMD server HOST:PORT (disabled by default)')
            child.add_argument('--package', default=str(Path(__file__).resolve().parents[1]), help=argparse.SUPPRESS)
        if name == 'install':
            child.add_argument('--check', action='store_true', help='Alias for --dry-run')
        if name == 'configure':
            child.add_argument('--sound', choices=['on', 'off'])
            child.add_argument('--dmd-server', help='HOST:PORT or off')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.command == 'install' or args.command == 'check' and args.source:
            if not args.table or not args.script or not args.mapping_source:
                p.error('Pre-install check requires --source, --table, --script and --mapping-source together')
            result = core.install(package=args.package, source=args.source, table=args.table, script=args.script,
                                  shared=args.shared, userdata=args.userdata, root=args.root, mapping_source=args.mapping_source,
                                  dry_run=args.command == 'check' or args.dry_run or getattr(args, 'check', False),
                                  skip_platform_check=args.skip_platform_check,
                                  dmd_server=args.dmd_server, import_saves=args.import_saves)
        elif args.command == 'check':
            if args.mapping_source or args.table or args.script or args.shared or args.import_saves or args.skip_platform_check:
                p.error('Source check requires all --source/--table/--script/--mapping-source arguments')
            result = core.check(args.userdata, args.root)
        elif args.command == 'configure':
            result = core.configure(args.userdata, args.root,
                                    sound=None if args.sound is None else args.sound == 'on',
                                    dmd_server=args.dmd_server, dry_run=args.dry_run)
        else:
            result = core.uninstall(args.userdata, args.root, dry_run=args.dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (core.InstallError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
