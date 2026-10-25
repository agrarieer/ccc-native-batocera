"""Portable installation policy and machine discovery (stdlib only, no game imports)."""
import configparser
import json
import os
import re
import shutil
import socket
import stat
import subprocess
from pathlib import Path
from build_table import TABLE_SHA256, SCRIPT_SHA256, sha256, vbs_literal


def install_root():
    return Path(os.environ.get('CCC_INSTALL_ROOT', str(Path(__file__).resolve().parent.parent))).resolve()


def inside(root, relative, must_exist=False):
    root = Path(root).resolve()
    path = root / relative
    # Reject even in-root symlinks: private mutable state has one canonical owner.
    current = root
    for part in Path(relative).parts:
        if part in ('..', '.') or Path(relative).is_absolute():
            raise ValueError('Invalid private path')
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink is forbidden: ' + str(current))
    path = path.resolve(strict=must_exist)
    path.relative_to(root)
    return path


def settings(root):
    value = {'schema': 1, 'sound_enabled': True, 'audio_backend': 'auto',
             'software_dmd': True, 'dmd_server': None, 'display': 'current'}
    source = inside(root, 'config/settings.json', True)
    user = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(user, dict) or user.get('schema', 1) != 1:
        raise ValueError('Unsupported settings schema')
    value.update(user)
    for name in ('sound_enabled', 'software_dmd'):
        if type(value[name]) is not bool:
            raise ValueError(name + ' must be boolean')
    if value['audio_backend'] not in ('auto', 'pulse', 'alsa', 'dummy'):
        raise ValueError('audio_backend must be auto, pulse, alsa or dummy')
    dmd_endpoint(value['dmd_server'])
    return value


def dmd_endpoint(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', value):
        raise ValueError('Physical DMD requires explicit compatibility selection 127.0.0.1:PORT')
    port = int(value.split(':')[1])
    if not 1 <= port <= 65535:
        raise ValueError('Invalid DMD port')
    return '127.0.0.1', port


def detect_displays(env=None):
    env = dict(os.environ if env is None else env)
    if not env.get('DISPLAY') or not shutil.which('xrandr'):
        return []
    try:
        result = subprocess.run(['xrandr', '--query'], env=env, capture_output=True, text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return []
    displays = []
    for line in result.stdout.splitlines():
        match = re.match(r'^(\S+) connected (?:primary )?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', line)
        if match:
            name, w, h, x, y = match.groups()
            displays.append(dict(name=name, width=int(w), height=int(h), x=int(x), y=int(y), primary=' primary ' in line))
    displays.sort(key=lambda d: not d['primary'])
    return displays


def dmd_geometry(displays, configured=None):
    if configured is not None:
        if (not isinstance(configured, list) or len(configured) != 3 or
                any(type(i) is not int for i in configured) or not 1 <= configured[2] <= 14):
            raise ValueError('dmd_position must be [x,y,pixel_size], pixel_size 1..14')
        return tuple(configured)
    target = displays[1] if len(displays) > 1 else (displays[0] if displays else None)
    if target:
        scale = min(5, max(1, target['width'] // 128), max(1, target['height'] // 32))
        return target['x'] + 16, target['y'] + 16, scale
    return 16, 16, 5  # 640x160 fallback; no imported cabinet geometry.


def player_environment(home, env=None, mute=False):
    source = dict(os.environ if env is None else env)
    result = dict(source)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'LD_LIBRARY_PATH', 'SDL_VIDEODRIVER', 'SDL_AUDIODRIVER',
                 'CCC_TEST_FAULT', 'ALSA_CONFIG_PATH', 'ALSA_CONFIG_DIR'):
        result.pop(name, None)
    result.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'configs'),
                  XDG_DATA_HOME=str(home / 'data'), XDG_CACHE_HOME=str(home / 'cache'))
    if not result.get('DISPLAY') and Path('/tmp/.X11-unix/X0').exists():
        result['DISPLAY'] = ':0'
    runtime_candidates = [source.get('XDG_RUNTIME_DIR'), '/run/user/' + str(getattr(os, 'getuid', lambda: 0)()), '/var/run']
    if not result.get('PULSE_SERVER'):
        for candidate in runtime_candidates:
            if candidate and Path(candidate, 'pulse/native').exists():
                result['PULSE_SERVER'] = 'unix:' + str(Path(candidate, 'pulse/native'))
                result['XDG_RUNTIME_DIR'] = candidate
                break
    if not result.get('PULSE_COOKIE'):
        candidates = [Path(source.get('XDG_CONFIG_HOME', str(Path(source.get('HOME', '~')) / '.config'))) / 'pulse/cookie']
        # Batocera's regular user cookie is read-only input, never copied into release.
        candidates.append(Path('/userdata/system/.config/pulse/cookie'))
        for candidate in candidates:
            if candidate.is_file():
                result['PULSE_COOKIE'] = str(candidate)
                break
    if Path('/usr/share/alsa/alsa.conf').is_file():
        result['ALSA_CONFIG_PATH'] = '/usr/share/alsa/alsa.conf'
    if mute:
        result['SDL_AUDIODRIVER'] = 'dummy'
    return result


def prepare_ini(source, target, sound, displays, profile='desktop'):
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.optionxform = str
    # Begin with this machine's native settings, then its private overrides.
    global_ini = Path('/userdata/system/configs/vpinball/VPinballX.ini')
    if global_ini.is_file():
        cfg.read(global_ini)
    # Global per-table/cabinet camera must not replace the supplied table view.
    cfg.remove_section('TableOverride')
    cfg.read(source)
    if not cfg.has_section('Player'):
        cfg.add_section('Player')
    for section in cfg.sections():
        for key in list(cfg[section]):
            if key.lower() in ('dmdserver', 'b2splugins', 'pupplugin', 'dofplugin', 'b2senabled'):
                cfg[section][key] = '0'
    for key in ('DMDServer', 'B2SPlugins', 'PUPPlugin', 'DOFPlugin', 'B2SEnabled'):
        cfg['Player'][key] = '0'
    cfg['Player']['MusicVolume'] = cfg['Player'].get('MusicVolume', '80') if sound else '0'
    cfg['Player']['SoundVolume'] = cfg['Player'].get('SoundVolume', '80') if sound else '0'
    cfg['Player']['SoundDevice'] = ''
    cfg['Player']['SoundDeviceBG'] = ''
    if profile == 'desktop':
        # A software DMD must remain above the playfield, not exclusive fullscreen.
        cfg['Player']['FullScreen'] = '0'
    if displays:
        primary = displays[0]
        cfg['Player']['Width'] = str(primary['width'])
        cfg['Player']['Height'] = str(primary['height'])
        cfg['Player']['WindowPosX'] = str(primary.get('x', 0))
        cfg['Player']['WindowPosY'] = str(primary.get('y', 0))
    # Never inherit the working cabinet camera. Only explicit portrait mode opts in.
    if profile == 'cabinet':
        if not displays or displays[0]['height'] <= displays[0]['width']:
            raise ValueError('Cabinet profile requires a detected portrait playfield')
        cfg['TableOverride'] = dict(ViewCabMode='0', ViewCabScaleX='2.133588', ViewCabScaleY='1.641140',
                                   ViewCabScaleZ='1.005232', ViewCabRotation='0.000000', ViewCabPlayerX='8.480000',
                                   ViewCabPlayerY='134.780380', ViewCabPlayerZ='1438.534180', ViewCabLookAt='15.400000',
                                   ViewCabFOV='38.599995', ViewCabLayback='34.199993')
    with open(target, 'w', encoding='utf-8', newline='\n') as output:
        cfg.write(output)


def preflight(root, cfg):
    for name in ('app', 'runtime', 'runtime/python', 'runtime/lib', 'game', 'shared', 'tables', 'config', 'saves', 'runs'):
        inside(root, name, True)
    python = inside(root, 'runtime/python/bin/python2.7', True)
    table = inside(root, 'tables/CCC-native.vpx', True)
    script = inside(root, 'tables/CCC-native.vbs', True)
    manifest = json.loads(inside(root, 'tables/build-manifest.json', True).read_text())
    if manifest.get('source_table_sha256') != TABLE_SHA256 or manifest.get('source_script_sha256') != SCRIPT_SHA256:
        raise ValueError('Build manifest is not for supported source pair')
    if sha256(table) != TABLE_SHA256 or sha256(script) != manifest.get('script_sha256'):
        raise ValueError('Installed table/script integrity mismatch')
    pointer = (Path(root) / 'active-ipc.txt').as_posix()
    if manifest.get('pointer') != pointer or vbs_literal(pointer) not in script.read_text(encoding='utf-8'):
        raise ValueError('Table patch targets another installation; rebuild for this root')
    for name in ('game/game.py', 'game/config.yaml', 'game/config/cc_machine.yaml',
                 'game/config/game_data_template.yaml', 'game/config/settings_template.yaml', 'config/VPX.ini'):
        inside(root, name, True)
    native = cfg.get('native_vpx') or next((str(p) for p in (Path('/usr/bin/vpinball/VPinballX_GL'), Path('/usr/bin/vpinball/VPinballX')) if p.is_file()), None)
    if not native or not Path(native).is_file() or not os.access(native, os.X_OK):
        raise ValueError('Native VPX executable unavailable')
    if not os.access(python, os.X_OK):
        raise ValueError('Bundled Python2 is not executable')
    return python, Path(native), table
