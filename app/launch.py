#!/usr/bin/env python3
"""Relocatable CCC native supervisor. Default: interactive local desktop play."""
import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from configuration import (install_root, inside, settings, dmd_endpoint, detect_displays,
                           dmd_geometry, player_environment, prepare_ini, preflight)


def validate_result(result, session):
    errors = []
    if result.get('session') != session:
        errors.append('Runtime result session mismatch')
    if result.get('status') != 'STOPPED' or result.get('error'):
        errors.append('Runtime did not stop cleanly')
    if result.get('stop_reason') != 'protocol-STOP':
        errors.append('Runtime exit was not protocol STOP')
    if not any(r[1:] == ['STOP'] for r in result.get('requests', [])):
        errors.append('Matching runtime has no received STOP')
    outputs = result.get('final_outputs')
    if (not isinstance(outputs, list) or len(outputs) != 3 or
            any(not isinstance(a, list) or len(a) != n for a, n in zip(outputs, (64, 90, 5)))):
        errors.append('Malformed final outputs')
    elif any(type(v) is not int or v != 0 for a in outputs for v in a):
        errors.append('Outputs are not all off')
    return errors


def read_state(run, session=None):
    lines = (run / 'ipc/state.txt').read_text().splitlines()
    header = lines[0].split()
    if (len(header) != 5 or header[0] != 'CCC2' or
            (session is not None and header[1] != session)):
        raise ValueError('Invalid runtime session/header')
    if header[3] == 'ERROR':
        raise RuntimeError('Backend reported ERROR')
    return header, lines


def backend_health(runtime, run, session):
    if runtime.poll() is not None:
        try:
            result = json.loads((run / 'result.json').read_text())
            if runtime.returncode == 0 and not validate_result(result, session):
                return 'protocol-stopped'
        except (OSError, ValueError):
            pass
        return 'failed'
    try:
        read_state(run, session)
    except (OSError, ValueError, IndexError, RuntimeError):
        return 'failed'
    return 'active'


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(3)


def check_idle():
    if not Path('/proc').is_dir():
        raise RuntimeError('Interactive/backend execution requires Linux')
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if args and args[0].rsplit(b'/', 1)[-1].startswith(b'VPinballX'):
            raise RuntimeError('Another VPX is active; leave it untouched and exit first')


class HotkeyContext:
    """Restore only the context we installed, never replace a newer owner's state."""
    def __init__(self):
        self.path = Path('/var/run/hotkeygen.context')
        self.previous = None
        self.name = 'ccc-native-' + uuid.uuid4().hex[:10]
        self.active = False

    def start(self):
        if not shutil.which('hotkeygen'):
            return
        self.previous = json.loads(self.path.read_text()) if self.path.exists() else None
        keys = {'exit': 'KEY_F4', 'coin': 'KEY_5', 'menu': 'KEY_ESC', 'pause': 'KEY_P', 'reset': 'KEY_F3'}
        subprocess.run(['hotkeygen', '--new-context', self.name, json.dumps(keys)], check=True, timeout=4)
        self.active = True
        if json.loads(self.path.read_text()).get('name') != self.name:
            raise RuntimeError('Hotkey context did not become active')

    def close(self):
        if not self.active:
            return
        if not self.path.exists() or json.loads(self.path.read_text()).get('name') != self.name:
            return
        if self.previous is None:
            command = ['hotkeygen', '--default-context']
        else:
            command = ['hotkeygen', '--new-context', self.previous['name'], json.dumps(self.previous['keys'])]
        subprocess.run(command, check=True, timeout=4)
        current = json.loads(self.path.read_text()) if self.path.exists() else None
        if self.previous is not None and current != self.previous:
            raise RuntimeError('Prior hotkey context was not restored')


class IPCClient:
    """Actual backend selftest client; no game emulation or synthesized game results."""
    def __init__(self, run, session):
        self.run, self.session, self.seq = run, session, 0

    def send(self, command):
        self.seq += 1
        stem = self.run / 'ipc' / str(self.seq)
        stem.with_suffix('.req').write_text('%s %d %s\n' % (self.session, self.seq, command), encoding='ascii')
        stem.with_suffix('.ready').write_text('1\n', encoding='ascii')

    def heartbeat(self):
        _, lines = read_state(self.run, self.session)
        for channel, row in zip('CLG', lines[4:7]):
            if row:
                self.send('ACK %s %s' % (channel, row.split(';')[-1].split(',')[0]))
        self.send('PING')


def owned_window(process, env):
    if process is None or process.poll() is not None or not shutil.which('xdotool'):
        return None
    query = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(process.pid)],
                           capture_output=True, text=True, timeout=2, env=env)
    return next(iter(query.stdout.split()), None)


def focus_windows(player, runtime, env, software):
    play = owned_window(player, env)
    display = owned_window(runtime, env) if software else None
    if display:
        subprocess.run(['xdotool', 'windowraise', display], env=env, check=True, timeout=2)
    if play:
        subprocess.run(['xdotool', 'windowfocus', '--sync', play], env=env, check=True, timeout=2)
    return bool(play and (display or not software))


def request_player_exit(player, env):
    window = owned_window(player, env)
    if not window:
        raise RuntimeError('Cannot target normal F4 exit: owned VPX window/xdotool missing')
    subprocess.run(['xdotool', 'windowfocus', '--sync', window, 'key', '--clearmodifiers', 'F4'],
                   env=env, check=True, timeout=3)


def runtime_environment(root, run, cfg, env, geometry, headless=False):
    result = dict(env)
    result.update(CCC_INSTALL_ROOT=str(root), CCC_SAVE_DIR=str(root / 'saves'),
                  PYTHONHOME=str(root / 'runtime/python'),
                  PYTHONPATH=os.pathsep.join(str(p) for p in (root / 'app', root / 'game', root / 'runtime/python/lib/python2.7/site-packages')),
                  LD_LIBRARY_PATH=str(root / 'runtime/lib'), PYTHONDONTWRITEBYTECODE='1',
                  HOME=str(run / 'backend-home'), XDG_CONFIG_HOME=str(run / 'backend-home/configs'),
                  XDG_DATA_HOME=str(run / 'backend-home/data'), XDG_CACHE_HOME=str(run / 'backend-home/cache'),
                  CCC_DMD_GEOMETRY=','.join(map(str, geometry)), CCC_MAX_SECONDS='0',
                  CCC_DMD_OUTPUT='1' if cfg['dmd_server'] and not headless else '0',
                  CCC_DMD_PORT=str(dmd_endpoint(cfg['dmd_server'])[1] if cfg['dmd_server'] else 6789),
                  CCC_SELFTEST='1' if headless else '0')
    if headless or not cfg['software_dmd']:
        result['SDL_VIDEODRIVER'] = 'dummy'
    else:
        result['SDL_VIDEODRIVER'] = 'x11'
    backend = cfg['audio_backend']
    if headless or not cfg['sound_enabled']:
        driver = 'dummy'
    elif backend == 'auto':
        driver = 'pulse' if result.get('PULSE_SERVER') else 'alsa'
    else:
        driver = backend
    result['SDL_AUDIODRIVER'] = driver
    result['CCC_AUDIO'] = driver
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Explicit installation root (otherwise CCC_INSTALL_ROOT or app parent)')
    parser.add_argument('--check', action='store_true', help='Read-only files/machine check; no game/audio/network started')
    parser.add_argument('--headless-test', action='store_true', help='Bounded real CCC/IPC/frame test, no VPX, physical output, or save writes')
    parser.add_argument('--seconds', type=float, help='Bounded run; default interactive (headless default 8 seconds)')
    parser.add_argument('--mute', action='store_true')
    parser.add_argument('--no-software-dmd', action='store_true')
    parser.add_argument('--profile', choices=('desktop', 'cabinet'), default='desktop')
    args = parser.parse_args(argv)
    if args.seconds is not None and (not 2 <= args.seconds <= 86400):
        parser.error('--seconds must be between 2 and 86400')
    if args.check and args.headless_test:
        parser.error('--check and --headless-test are mutually exclusive')
    root = args.root.resolve() if args.root else install_root()
    cfg = settings(root)
    if args.mute:
        cfg['sound_enabled'] = False
    if args.no_software_dmd:
        cfg['software_dmd'] = False
    python, native, table = preflight(root, cfg)
    base_env = player_environment(root, mute=args.mute)
    displays = detect_displays(base_env)
    geometry = dmd_geometry(displays, cfg.get('dmd_position'))
    if args.check:
        print(json.dumps(dict(status='PREFLIGHT_ONLY', root=str(root), software_dmd=cfg['software_dmd'],
                              dmd_server=cfg['dmd_server'], displays=displays, dmd_geometry=geometry,
                              outputs_started=False, qualification='File checks only; run headless test and visible VPX separately'), indent=2))
        return 0
    check_idle()
    lock = inside(root, 'launch.lock')
    lock.mkdir()  # A stale lock is reported, never silently stolen.
    run = inside(root, 'runs') / ('run-' + uuid.uuid4().hex)
    runtime = player = None
    logs, errors = [], []
    report = {'run': str(run), 'headless_test': args.headless_test, 'profile': args.profile}
    reason = 'initializing'
    interrupted = [False]
    previous_signals = {}
    context = HotkeyContext()
    def interrupt(signum, frame):
        interrupted[0] = True
    def attempt(label, action):
        try:
            return action()
        except Exception as exc:
            errors.append(label + ': ' + repr(exc))
            print(errors[-1], file=sys.stderr)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_signals[sig] = signal.signal(sig, interrupt)
        run.mkdir()
        for directory in ('prefs', 'player-home/configs', 'backend-home/configs'):
            (run / directory).mkdir(parents=True)
        env = player_environment(run / 'player-home', mute=not cfg['sound_enabled'] or args.headless_test)
        prepare_ini(inside(root, 'config/VPX.ini', True), run / 'prefs/VPinballX.ini', cfg['sound_enabled'], displays, args.profile)
        if cfg['dmd_server'] and not args.headless_test:
            with socket.create_connection(dmd_endpoint(cfg['dmd_server']), timeout=.5):
                pass
        if not args.headless_test:
            context.start()
        renv = runtime_environment(root, run, cfg, env, geometry, args.headless_test)
        logs.append(open(run / 'runtime.log', 'wb'))
        runtime = subprocess.Popen([str(python), '-u', str(root / 'app/runtime_main.py'), str(run)],
                                   cwd=root / 'game', env=renv, stdout=logs[-1], stderr=subprocess.STDOUT, start_new_session=True)
        ready_by = time.monotonic() + 60
        while not (run / 'ipc/state.txt').exists():
            if interrupted[0]:
                raise RuntimeError('Interrupted during initialization')
            if runtime.poll() is not None:
                raise RuntimeError('CCC initialization failed; see runtime.log')
            if time.monotonic() >= ready_by:
                raise RuntimeError('CCC initialization timed out')
            time.sleep(.05)
        header, _ = read_state(run)
        if header[3] != 'READY':
            raise RuntimeError('Backend was not READY')
        session = header[1]
        report['session'] = session
        # Require a progressing heartbeat before handing this session to a client.
        ready_beat = int(header[2])
        challenge_by = time.monotonic() + 3
        while int(read_state(run, session)[0][2]) <= ready_beat:
            if runtime.poll() is not None or time.monotonic() > challenge_by:
                raise RuntimeError('Backend readiness heartbeat stalled')
            time.sleep(.05)
        client = None
        if args.headless_test:
            client = IPCClient(run, session)
            client.send('RUN cc_13')
            # Genuine WPC mapping, tested with the real game's non-debounced inputs.
            client.send('SW 15 1')
            client.send('SW 15 0')
        else:
            temporary = inside(root, 'active-ipc.tmp')
            temporary.write_text(str(run / 'ipc') + '\n', encoding='utf-8')
            os.replace(temporary, inside(root, 'active-ipc.txt'))
            logs.append(open(run / 'player.log', 'wb'))
            player = subprocess.Popen([str(native), '-PrefPath', str(run / 'prefs') + '/',
                                       '-Ini', str(run / 'prefs/VPinballX.ini'), '-Play', str(table)],
                                      cwd=run, env=env, stdout=logs[-1], stderr=subprocess.STDOUT, start_new_session=True)
        print('CCC_RUN ' + str(run), flush=True)
        duration = args.seconds if args.seconds is not None else (8 if client else None)
        deadline = time.monotonic() + duration if duration else None
        last_beat, beat_at, last_ping = None, time.monotonic(), 0
        focus_at, focused = time.monotonic() + 4, False
        reason = 'normal-player-exit'
        while client or player.poll() is None:
            now = time.monotonic()
            health = backend_health(runtime, run, session)
            if health == 'failed':
                raise RuntimeError('Backend died/reported an invalid state during play')
            if health == 'protocol-stopped':
                if player:
                    player.wait(6)
                break
            header, _ = read_state(run, session)
            if header[2] != last_beat:
                last_beat, beat_at = header[2], now
            elif now - beat_at > 3:
                raise RuntimeError('Backend heartbeat stalled')
            if interrupted[0]:
                reason = 'operator-interrupt'
                break
            if deadline and now >= deadline:
                reason = 'bounded-selftest' if client else 'bounded-F4'
                break
            if client and now - last_ping >= .2:
                client.heartbeat()
                last_ping = now
            if player and not focused and now >= focus_at:
                focused = focus_windows(player, runtime, env, cfg['software_dmd'])
                focus_at = now + .5
            time.sleep(.05)
        if client and runtime.poll() is None:
            client.heartbeat()
            client.send('STOP')
        if player and player.poll() is None:
            request_player_exit(player, env)
            player.wait(8)
        if player and player.returncode:
            errors.append('VPX exited with status ' + str(player.returncode))
        runtime.wait(12)
        if runtime.returncode:
            errors.append('CCC exited with status ' + str(runtime.returncode))
        result = json.loads((run / 'result.json').read_text())
        errors.extend(validate_result(result, session))
        if client:
            if result.get('backend') != 'FakePinPROC' or result.get('ticks', 0) <= 0 or result.get('frames', 0) <= 0:
                errors.append('Real CCC processing/frame gate failed')
            if result.get('cabinet_output', {}).get('frames', 0) <= 0:
                errors.append('Post-draw RGB frame capture missing')
            raw = (run / 'dmd-rgb-final.rgb').read_bytes()
            if len(raw) != 256 * 64 * 3 or not any(raw):
                errors.append('RGB frame blank or malformed')
            actual = [r[1:] for r in result.get('requests', [])]
            if ['SW', '15', '1'] not in actual or ['SW', '15', '0'] not in actual:
                errors.append('Selftest input did not traverse real mapping')
        if player:
            logs[-1].flush()
            text = (run / 'player.log').read_text(errors='replace')
            if 'Script Error' in text or ('ScriptError' in text and '0 ScriptErrors' not in text):
                errors.append('VPX reported script errors')
            if cfg['sound_enabled'] and any('BASS' in line and ('ERROR' in line or '(error ' in line) for line in text.splitlines()):
                errors.append('VPX reported BASS audio errors')
    except Exception as exc:
        errors.append(repr(exc))
        reason = 'failure'
    finally:
        attempt('stop owned VPX', lambda: stop_owned(player))
        attempt('stop owned CCC', lambda: stop_owned(runtime))
        for log in logs:
            attempt('close log', log.close)
        def remove_pointer(name):
            path = inside(root, name)
            if path.exists() and path.read_text().strip() == str(run / 'ipc'):
                path.unlink()
        for name in ('active-ipc.txt', 'active-ipc.tmp'):
            attempt('remove own pointer', lambda name=name: remove_pointer(name))
        attempt('restore hotkeys', context.close)
        attempt('release lock', lock.rmdir)
        for sig, previous in previous_signals.items():
            attempt('restore signal handler', lambda sig=sig, previous=previous: signal.signal(sig, previous))
        report.update(status='FAIL' if errors else 'PASS', reason=reason, errors=errors,
                      runtime_rc=runtime.returncode if runtime else None, player_rc=player.returncode if player else None)
        attempt('persist launch result', lambda: (run / 'launch-result.json').write_text(json.dumps(report, indent=2)))
        report.update(status='FAIL' if errors else 'PASS', errors=errors)
    print(json.dumps(report, indent=2))
    return 1 if errors else (130 if interrupted[0] else 0)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('CCC_REFUSED: ' + repr(exc), file=sys.stderr)
        sys.exit(2)
