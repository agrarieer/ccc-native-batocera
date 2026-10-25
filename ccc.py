#!/usr/bin/env python3
"""CCC installer entry point; runtime downloads only on an actual install."""
import subprocess
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from installer.main import parser, main as installer_main, prepare_options
    from installer.core import InstallError
    args = parser().parse_args(argv)
    # Parse every argument before touching the network or package directory.
    try:
        chosen = prepare_options(args)
    except InstallError as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}), file=sys.stderr)
        return 1
    if chosen:
        argv += ['--dmd-mode', args.dmd_mode]
        if args.dmd_server is not None:
            argv += ['--dmd-server', args.dmd_server]
    if args.command == 'install' and not (args.dry_run or args.check):
        if args.skip_platform_check:
            return installer_main(argv)  # never bootstrap for an invalid override
        from installer.core import probe_platform
        report = probe_platform()
        if not report.get('supported'):
            print(json.dumps({'status': 'error', 'platform': report}), file=sys.stderr)
            return 1
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/prepare_runtime.py')])
        if result.returncode:
            return result.returncode
    return installer_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
