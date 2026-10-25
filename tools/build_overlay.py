#!/usr/bin/env python3
"""Maintainer-only overlay builder; never copies a game, save or cabinet config."""
import argparse,gzip,hashlib,io,json,pathlib,tarfile
import os,platform,shutil,subprocess,sys,datetime

from prepare_runtime import download,tar_index,member_bytes
PREFIX='python/lib/python2.7/site-packages/'
SOURCE_SUFFIXES={'.m','.c','.h','.cpp','.hpp','.py','.pyi','.pyx','.pxd','.pxi','.in','.cmake','.asm','.s','.S','.rst','.txt','.md','.markdown','.def','.sh'}

def source_allowed(name):
    p=pathlib.PurePosixPath(name)
    if any(x in {'.git','__pycache__','installers','obj','build','dist','bin','libpinproc.xcodeproj'} for x in p.parts):return False
    if p.parts[0]=='pygame-1.9.6':
        if name.startswith('pygame-1.9.6/docs/reST/') and p.suffix in {'.rst','.py','.h','.txt'}:return True
        if len(p.parts)>2 and p.parts[1] not in {'src_c','src_py','buildconfig'}:
            return p.name in {'LGPL','GPL','LICENSE','COPYING','LICENSE.txt','COPYING.txt'}
    elif p.parts[0] not in {'libpinproc','pypinproc'}:return False
    return p.suffix in SOURCE_SUFFIXES or p.name in {'LGPL','GPL','LICENSE','COPYING','Makefile','CMakeLists.txt','Doxyfile','PKG-INFO','MANIFEST.in'}

def export_sources(tar):
    kept={};omitted=[]
    for name,info in sorted(tar_index(tar).items()):
        if info.isdir():continue
        if not info.isfile():raise ValueError('Source link/special file requires review: '+name)
        if source_allowed(name):
            blob=tar.extractfile(info).read()
            if b'\0' in blob:raise ValueError('Binary disguised as source: '+name)
            kept[name]=blob
        else:omitted.append(name)
    return kept,omitted

def write_tar(path,files):
    with path.open('wb') as output,gzip.GzipFile(filename='',mode='wb',fileobj=output,mtime=0) as gz,tarfile.open(fileobj=gz,mode='w',format=tarfile.PAX_FORMAT) as tar:
        for name,blob in sorted(files.items()):add(tar,name,blob)

DRIVER=b'''# Python2-compatible packaging driver; upstream setup.py remains unchanged.
import os,sys,runpy
sys.path.insert(0,os.getcwd())
import setuptools
original=setuptools.setup
def code_setup(**kw):
    kw['packages']=['pygame','pygame.threads']
    kw['package_dir']={'pygame':'src_py','pygame.threads':'src_py/threads'}
    kw['data_files']=[]
    return original(**kw)
setuptools.setup=code_setup
runpy.run_path('setup.py',run_name='__main__')
'''
BUILD=b'''CODE-ONLY SOURCE EXPORT, not the complete upstream sdist.
Retained upstream bytes are unchanged. SOURCE-MANIFEST.json records hashes and
every omitted path. All C/Python code, generated C/doc headers, SDL_gfx, rotozoom,
queue notices and build/configuration inputs are retained. Fonts/icons, optional
examples/tests/docs resources, bytecode, Windows .obj and native installers
(including DPInst) are excluded. No nested archives are distributed.
Do NOT publish the recipient-generated runtime or download cache.

Rebuild guidance: x86_64 Ubuntu 22.04, Python2.7.18 with development headers,
gcc/g++, cmake and Python2 setuptools. Install development headers/libraries for
SDL1.2, SDL_image, SDL_mixer, SDL_ttf, FreeType, JPEG, PNG, PortMidi, libftdi1,
libusb-1.0 and libusb-0.1. Use an isolated prefix/venv, not the cabinet/system.

pygame (do not set PYGAME_SDL2):
  cd pygame-1.9.6
  python2.7 ../BUILD-PYGAME-CODE.py -auto build
The driver changes packaging metadata only, limiting packages to pygame/threads
and omitting data/tests/docs/examples. Generated C avoids requiring Cython.
Copy resulting pygame .py/.so into your replacement runtime. For OPTIONAL local
font/icon data use tools/prepare_runtime.py: it verifies the exact upstream
pygame_source URL/bytes/SHA256 in runtime-downloads.lock.json and copies only
six src_py resources locally; those resources are NOT in this sidecar.

pinproc (from directory containing libpinproc and pypinproc):
  cmake -S libpinproc -B libpinproc/build -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DBUILD_SHARED_LIBS=OFF -DPINPROC_BUILD_TOOLS=OFF
  cmake --build libpinproc/build --verbose
  cd pypinproc
  LDFLAGS="-L$(pwd)/../libpinproc/build" python2.7 setup.py build_ext --inplace --verbose
Upstream setup.py uses -O0 -g -Wno-write-strings; pypinproc.cpp,dmdutil.cpp,dmd.c;
and libraries usb,ftdi1,pinproc. Intended static input is
libpinproc/build/libpinproc.a. Ensure no unrelated /usr/local/lib/libpinproc
wins resolution. Capture actual verbose compile/link lines, static archive hash,
compiler versions and pygame generated Setup for any NEW distributed build.

RELEASE BLOCKER: the historical build log, exact pygame generated Setup, complete
compiler flags and static libpinproc archive hash were not recovered from the
old sidecar. Overlay bytes match the audited 59-file allowlist; byte-for-byte
source/build correspondence is NOT verified. These commands are rebuild guidance,
not a historical log. Obtain original build records/source-match evidence or
make/audit a new build and retain complete records before release. No claim that
the historical native binary was unmodified is made.

Third-party licenses remain in force: pygame LGPL-2.1, companion GPL text,
Python license, native MIT README grants and all embedded notices are retained.
Wrapper MIT terms do not relicense these. Users may replace and debug modified
LGPL components. SDL/system dependencies remain dynamic, recipient-fetched.
'''
ROOT=pathlib.Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def add(tar,name,blob):
    info=tarfile.TarInfo(name);info.mode=0o644;info.size=len(blob);info.mtime=0;tar.addfile(info,io.BytesIO(blob))
PINPROC_DRIVER=b'''# Packaging-only adjustment: exact fresh static archive; no /usr/local search.
import os,runpy
import distutils.core
original=distutils.core.setup
def code_setup(**kw):
    for ext in kw['ext_modules']:
        ext.libraries=[n for n in ext.libraries if n!='pinproc']
        ext.library_dirs=[]
        ext.extra_objects=[os.environ['OVERLAY_STATIC_ARCHIVE']]
        ext.extra_link_args=['-Wl,-t','-Wl,-Map,'+os.environ['OVERLAY_LINK_MAP']]
    return original(**kw)
distutils.core.setup=code_setup
runpy.run_path('setup.py',run_name='__main__')
'''
FRESH_BUILD=b'''AUDITABLE FRESH CODE-ONLY OVERLAY BUILD
The historical audit is retained unchanged and does not authenticate these new
binary hashes. FRESH-BUILD.json and FRESH-ALLOWLIST.json record new correspondence.
All retained upstream source bytes are unchanged; the two BUILD-*.py drivers
adjust packaging/link selection only. BUILD-PINPROC-CODE.py supplies the exact
fresh static archive explicitly, removing ambiguous /usr/local library searches.
No historical binary-source correspondence is claimed or repaired retroactively.

Reproduce in an isolated x86_64 Ubuntu 22.04 development environment with Python
2.7 headers/setuptools, gcc/g++, CMake, SDL1.2/image/mixer/ttf, FreeType, PortMidi,
JPEG, PNG, libftdi1, libusb1.0 and libusb0.1 development packages. Tool versions,
installed package inventory, header hashes and the actual environment/commands
are in build-evidence/. The Python runtime and dynamic libraries are NOT shipped
in this code-only source export; recipient dependency locators remain in the
runtime lock. Run the commands in commands.json with equivalent absolute paths.
Rebuild pygame with BUILD-PYGAME-CODE.py -auto build, libpinproc using verbose
CMake build, then pypinproc with BUILD-PINPROC-CODE.py build_ext --inplace --verbose.
Set OVERLAY_STATIC_ARCHIVE to the fresh libpinproc.a and OVERLAY_LINK_MAP to a
private output filename. Full logs, generated pygame Setup, CMakeCache, link
map and static archive fingerprint are retained. No source C/C++ patches applied.
The static archive and intermediates remain in the private build directory, not
inside this flat code-only source archive. Do not ship the private runtime/cache.

SOURCE-MANIFEST.json records all retained hashes and all omissions. Optional
upstream media, fonts/icons, tests/examples and Windows installers are omitted.
Acquire optional runtime resources directly from the pinned pygame_source URL
using tools/prepare_runtime.py; the six reviewed resources are recipient-only.
Retained reST/doc templates are preferred form for generated documentation headers.

pygame LGPL-2.1 and GPL companion, Python notices, native MIT grants and embedded
SDL_gfx/rotozoom notices remain in force. Wrapper terms do not relicense them.
Users may replace/rebuild/debug modified LGPL components and dynamic dependencies.
This record proves this fresh build's inputs/outputs and isolated runtime smoke;
it is not cabinet/game/hardware acceptance or a blanket legal release approval.
'''

def fresh_build(args):
    """Build from the exact flat export; verify before optionally publishing."""
    if sys.platform!='linux' or platform.machine()!='x86_64':
        raise ValueError('Run --fresh-build inside isolated x86_64 WSL Ubuntu-22.04')
    os_release=pathlib.Path('/etc/os-release').read_text()
    if 'VERSION_ID="22.04"' not in os_release:raise ValueError('Ubuntu 22.04 required')
    work=args.build_dir
    if work is None or not work.is_absolute() or work.exists():
        raise ValueError('--build-dir must be a NEW absolute private directory')
    if args.native_sources is None:raise ValueError('--native-sources exact snapshot is required')
    work.mkdir(parents=True)
    evidence=work/'evidence';evidence.mkdir()
    out=work/'artifacts';out.mkdir()
    payload=ROOT/'payload';lock_path=payload/'runtime-downloads.lock.json'
    original_lock=lock_path.read_bytes();lock=json.loads(original_lock)
    audit_path=ROOT/'audit/lean-overlay-allowlist.json'
    audit_blob=audit_path.read_bytes();audit=json.loads(audit_blob)
    expected=set(audit['files'])
    if len(expected)!=59:raise ValueError('Expected exact historical 59-name allowlist')
    # Preserve all historical records/bytes before any publication.
    historical=work/'historical';historical.mkdir()
    for name in ['native-overlay.tar.gz','native-overlay-sources.tar.gz','native-overlay-manifest.json','native-overlay-source-manifest.json','runtime-downloads.lock.json']:
        shutil.copyfile(payload/name,historical/name)
    (historical/audit_path.name).write_bytes(audit_blob)
    pg=args.pygame_source or download(lock['pypi']['pygame_source'],payload/'download-cache')
    if sha(pg)!=lock['pypi']['pygame_source']['sha256'] or pg.stat().st_size!=lock['pypi']['pygame_source']['bytes']:
        raise ValueError('Invalid pygame sdist')
    with tarfile.open(pg) as tar:sources,omitted_pg=export_sources(tar)
    with tarfile.open(args.native_sources) as tar:native,omitted_native=export_sources(tar)
    sources.update(native)
    # The previously reviewed notices are carried byte-for-byte, not regenerated.
    with tarfile.open(historical/'native-overlay-sources.tar.gz') as tar:
        for m in tar:
            if m.isfile() and m.name.startswith('notices/'):
                sources[m.name]=tar.extractfile(m).read()
    sources.update({'BUILD-PYGAME-CODE.py':DRIVER,'BUILD-PINPROC-CODE.py':PINPROC_DRIVER,'BUILD-INSTRUCTIONS.txt':FRESH_BUILD})
    src=work/'source';src.mkdir()
    for name,blob in sources.items():
        target=src/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
    env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(work/'home'),
         'LANG':'C.UTF-8','LC_ALL':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYGAME_HIDE_SUPPORT_PROMPT':'1'}
    pathlib.Path(env['HOME']).mkdir()
    commands=[]
    def run(label,argv,cwd=src,extra=None):
        runenv=dict(env);runenv.update(extra or {})
        record={'label':label,'argv':[str(a) for a in argv],'cwd':str(cwd),'environment':runenv}
        commands.append(record)
        (evidence/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        with (evidence/(label+'.log')).open('wb') as f:
            result=subprocess.run(record['argv'],cwd=str(cwd),env=runenv,stdout=f,stderr=subprocess.STDOUT)
        record['exit_code']=result.returncode
        (evidence/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        print(label+': exit '+str(result.returncode),flush=True)
        if result.returncode:raise RuntimeError('Failed '+label+'; inspect '+str(evidence/(label+'.log')))
    (evidence/'os-release.txt').write_text(os_release)
    for label,cmd in [('gcc-version',['gcc','-v']),('gxx-version',['g++','-v']),('ld-version',['ld','--version']),('ar-version',['ar','--version']),('cmake-version',['cmake','--version']),('packages',['dpkg-query','-W']),('sdl-config',['sdl-config','--cflags','--libs']),('freetype-config',['pkg-config','--cflags','--libs','freetype2'])]:run(label,cmd)
    run('python-toolchain',['python2.7','-c','import sys,json,distutils.sysconfig as c,setuptools; print(sys.version); print(setuptools.__version__); print(json.dumps(c.get_config_vars(),sort_keys=True,default=str))'])
    toolchain={str(p):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in pathlib.Path('/usr/include/python2.7').rglob('*') if p.is_file()}
    for name in ['gcc','g++','ld','ar','cmake','python2.7']:
        p=pathlib.Path(shutil.which(name)).resolve();toolchain[str(p)]={'bytes':p.stat().st_size,'sha256':sha(p)}
    (evidence/'toolchain-input-hashes.json').write_text(json.dumps(toolchain,indent=2)+'\n')
    pgdir=src/'pygame-1.9.6'
    run('pygame-build',['python2.7','../BUILD-PYGAME-CODE.py','-auto','build'],pgdir)
    shutil.copyfile(pgdir/'Setup',evidence/'pygame-Setup.txt')
    libdir=src/'libpinproc';libbuild=work/'libpinproc-build'
    run('libpinproc-configure',['cmake','-S',libdir,'-B',libbuild,'-DCMAKE_POSITION_INDEPENDENT_CODE=ON','-DBUILD_SHARED_LIBS=OFF','-DPINPROC_BUILD_TOOLS=OFF','-DCMAKE_EXPORT_COMPILE_COMMANDS=ON'])
    run('libpinproc-build',['cmake','--build',libbuild,'--verbose'])
    for name in ['CMakeCache.txt','compile_commands.json']:
        shutil.copyfile(libbuild/name,evidence/name)
    static=libbuild/'libpinproc.a'
    static_record={'path':str(static),'bytes':static.stat().st_size,'sha256':sha(static)}
    (evidence/'static-archive.json').write_text(json.dumps(static_record,indent=2)+'\n')
    run('static-archive-members',['ar','tv',static])
    pindir=src/'pypinproc'
    run('pypinproc-build',['python2.7','../BUILD-PINPROC-CODE.py','build_ext','--inplace','--verbose'],pindir,
        {'OVERLAY_STATIC_ARCHIVE':str(static),'OVERLAY_LINK_MAP':str(evidence/'pinproc-link-map.txt')})
    linkmap=(evidence/'pinproc-link-map.txt').read_text()
    if str(static) not in linkmap:raise ValueError('Fresh static archive absent from actual linker map')
    pgoutputs=list((pgdir/'build').glob('lib.*'))
    if len(pgoutputs)!=1:raise ValueError('Ambiguous pygame build output')
    pgout=pgoutputs[0]
    actual={str(p.relative_to(pgout)).replace(os.sep,'/') for p in pgout.rglob('*') if p.is_file()}
    if actual!=expected-{'pinproc.so'}:
        raise ValueError('59-file name alignment failed; missing='+repr(expected-{'pinproc.so'}-actual)+' extra='+repr(actual-expected))
    binary={PREFIX+n:(pindir/n if n=='pinproc.so' else pgout/n).read_bytes() for n in sorted(expected)}
    # Every original exported input must remain byte-for-byte unchanged by build.
    for n,b in sources.items():
        if (src/n).read_bytes()!=b:raise ValueError('Build altered exported source: '+n)
    # Isolated recipient runtime: only Python/libs/approved resources, never game.
    runtime=work/'smoke-runtime';runtime.mkdir()
    baseline=payload/'runtime.tar.gz'
    baseline_record=json.loads((payload/'runtime-manifest.json').read_text())
    if sha(baseline)!=baseline_record['sha256']:raise ValueError('Baseline runtime hash mismatch')
    with tarfile.open(baseline) as tar:
        for n,m in tar_index(tar).items():
            if not m.isfile():raise ValueError('Unexpected runtime special entry: '+n)
            if not n.startswith(('python/','lib/')):raise ValueError('Unexpected baseline content: '+n)
            target=runtime/n;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(tar.extractfile(m).read());target.chmod(m.mode)
    for n,b in binary.items():(runtime/n).write_bytes(b)
    sp=runtime/PREFIX
    observed={str(p.relative_to(sp)).replace(os.sep,'/') for p in (sp/'pygame').rglob('*') if p.is_file() and p.suffix in {'.py','.so'}}|{'pinproc.so'}
    if observed!=expected:raise ValueError('Smoke overlay includes unexpected code')
    smoke_env={'PYTHONHOME':str(runtime/'python'),'PYTHONPATH':str(sp),'LD_LIBRARY_PATH':str(runtime/'lib'),'SDL_VIDEODRIVER':'dummy','SDL_AUDIODRIVER':'dummy'}
    run('runtime-smoke',[runtime/'python/bin/python2.7','-B',ROOT/'runtime-tests/smoke_py27.py'],runtime,smoke_env)
    smoke=json.loads((evidence/'runtime-smoke.log').read_text())
    if smoke['status']!='PASS' or len(smoke['pygame_native_extensions_imported'])!=34:raise ValueError('Incomplete smoke')
    if list(runtime.rglob('*.pyc')) or list(runtime.rglob('*.pyo')):raise ValueError('Stale/generated bytecode')
    for n in sorted(expected):
        if n.endswith('.so'):run('elf-'+n.replace('/','-'),['readelf','-d',runtime/(PREFIX+n)])
    files=[{'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in sorted(binary.items())]
    new_allowlist={'format':1,'status':'Fresh rebuild exact allowlist; historical audit remains unchanged','historical_allowlist_sha256':hashlib.sha256(audit_blob).hexdigest(),'files':{r['path'][len(PREFIX):]:{'size':r['bytes'],'sha256':r['sha256']} for r in files}}
    changed=[n for n in sorted(expected) if new_allowlist['files'][n]!=audit['files'][n]]
    # Evidence is flat text/JSON, no nested archive or toolchain binaries.
    sources['FRESH-ALLOWLIST.json']=(json.dumps(new_allowlist,indent=2)+'\n').encode()
    sources['HISTORICAL-ALLOWLIST.json']=audit_blob
    for p in evidence.iterdir():
        if p.is_file():sources['build-evidence/'+p.name]=p.read_bytes()
    sources['build-evidence/build_overlay.py']=pathlib.Path(__file__).read_bytes()
    sources['build-evidence/prepare_runtime.py']=(ROOT/'tools/prepare_runtime.py').read_bytes()
    sources['build-evidence/smoke_py27.py']=(ROOT/'runtime-tests/smoke_py27.py').read_bytes()
    build_record={'format':1,'built_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'builder_invocation':sys.argv,
        'historical_build_verified':False,'fresh_build_verified':True,'source_changes':[],
        'packaging_drivers':['BUILD-PYGAME-CODE.py','BUILD-PINPROC-CODE.py'],
        'native_snapshot':{'sha256':sha(args.native_sources),'bytes':args.native_sources.stat().st_size},
        'pygame_upstream':lock['pypi']['pygame_source'],'static_archive':static_record,
        'baseline_runtime_sha256':sha(baseline),'binary_files':files,'changed_from_historical':changed,
        'checks':{'exact_59_names':True,'exported_source_unchanged':True,'runtime_smoke':smoke,'no_bytecode':True,'static_link_map_verified':True},
        'scope':'Isolated WSL Ubuntu-22.04; no cabinet, hardware or game access'}
    sources['FRESH-BUILD.json']=(json.dumps(build_record,indent=2)+'\n').encode()
    manifest={'format':2,'pygame_upstream':lock['pypi']['pygame_source'],
        'files':[{'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in sorted(sources.items())],
        'omitted_pygame':omitted_pg,'omitted_native':omitted_native,'historical_build_verified':False,
        'fresh_build_verified':True,'blockers':[],'build_record':'FRESH-BUILD.json'}
    sources['SOURCE-MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    for n,b in sources.items():
        if b'\0' in b or b.startswith((b'\x7fELF',b'MZ',b'\x1f\x8b',b'PK\x03\x04')):raise ValueError('Non-text source/evidence: '+n)
    write_tar(out/'native-overlay.tar.gz',binary)
    write_tar(out/'native-overlay-sources.tar.gz',sources)
    overlay={'filename':'native-overlay.tar.gz','sha256':sha(out/'native-overlay.tar.gz'),'bytes':(out/'native-overlay.tar.gz').stat().st_size,
        'sources_filename':'native-overlay-sources.tar.gz','sources_sha256':sha(out/'native-overlay-sources.tar.gz')}
    bin_manifest={'files':files,'sources':overlay['sources_filename'],'build':'Fresh isolated build; see FRESH-BUILD.json and build-evidence/ in source sidecar',
        'fresh_build_verified':True,'historical_build_verified':False,'allowlist':'native-overlay-allowlist.json'}
    for n,data in [('native-overlay-allowlist.json',new_allowlist),('native-overlay-manifest.json',bin_manifest),('native-overlay-source-manifest.json',manifest),('native-overlay-build.json',build_record)]:
        (out/n).write_text(json.dumps(data,indent=2)+'\n')
    # Re-open the exact candidate archives: count, bytes and hashes, no special paths.
    for name,wanted in [('native-overlay.tar.gz',binary),('native-overlay-sources.tar.gz',sources)]:
        with tarfile.open(out/name) as tar:
            idx=tar_index(tar)
            if set(idx)!=set(wanted):raise ValueError('Candidate archive membership mismatch')
            for n,b in wanted.items():
                if not idx[n].isfile() or tar.extractfile(idx[n]).read()!=b:raise ValueError('Candidate readback mismatch: '+n)
    report={'overlay':overlay,'binary_file_count':len(binary),'source_file_count':len(sources),
            'changed_binary_hashes':changed,'private_build_directory':str(work),'verification_stage':'Candidate verified before optional publication'}
    (out/'native-overlay-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    archive_dir=ROOT/'overlay-build-artifacts'/work.name
    if archive_dir.exists():raise ValueError('Evidence destination already exists')
    archive_dir.mkdir(parents=True)
    shutil.copytree(evidence,archive_dir/'evidence')
    shutil.copytree(historical,archive_dir/'historical')
    shutil.copytree(out,archive_dir/'candidate')
    if args.publish:
        if lock_path.read_bytes()!=original_lock or audit_path.read_bytes()!=audit_blob:raise ValueError('Concurrent input change; do not publish')
        # Publish only after every actual build/source/allowlist/runtime check passed.
        for p in out.iterdir():
            if p.name.startswith('native-overlay'):
                temporary=payload/(p.name+'.new');shutil.copyfile(p,temporary);temporary.replace(payload/p.name)
        lock['overlay']=overlay
        temporary=payload/'runtime-downloads.lock.json.new';temporary.write_text(json.dumps(lock,indent=2)+'\n');temporary.replace(lock_path)
        for p in out.iterdir():
            if p.name.startswith('native-overlay') and sha(payload/p.name)!=sha(p):raise ValueError('Published artifact readback failed')
        if json.loads(lock_path.read_text())['overlay']!=overlay:raise ValueError('Published lock readback failed')
        report['published']=True
    (archive_dir/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime-source',type=pathlib.Path);p.add_argument('--native-sources',type=pathlib.Path);p.add_argument('--pygame-source',type=pathlib.Path)
    p.add_argument('--fresh-build',action='store_true');p.add_argument('--build-dir',type=pathlib.Path);p.add_argument('--publish',action='store_true');args=p.parse_args()
    if args.fresh_build:return fresh_build(args)
    if args.publish:raise ValueError('--publish requires --fresh-build')
    payload=ROOT/'payload';lock_path=payload/'runtime-downloads.lock.json';lock=json.loads(lock_path.read_text());audit=json.loads((ROOT/'audit/lean-overlay-allowlist.json').read_text())
    if len(audit['files'])!=59:raise ValueError('Expected reviewed 59-file allowlist')
    with tarfile.open(payload/'native-overlay.tar.gz') as tar:
        index=tar_index(tar)
        binary={PREFIX+n:((args.runtime_source/(PREFIX+n)).read_bytes() if args.runtime_source else member_bytes(tar,index,PREFIX+n)) for n in audit['files']}
    for n,r in audit['files'].items():
        b=binary[PREFIX+n]
        if len(b)!=r['size'] or hashlib.sha256(b).hexdigest()!=r['sha256']:raise ValueError('Audited code mismatch: '+n)
    old_path=payload/'native-overlay-sources.tar.gz'
    with tarfile.open(old_path) as old:
        if args.native_sources:native=args.native_sources.read_bytes()
        elif 'libpinproc-and-pypinproc-sources.tar.gz' in old.getnames():native=old.extractfile('libpinproc-and-pypinproc-sources.tar.gz').read()
        else:
            native=None;native_files={m.name:old.extractfile(m).read() for m in old if m.isfile() and m.name.startswith(('libpinproc/','pypinproc/'))}
            native_omitted=json.loads(old.extractfile('SOURCE-MANIFEST.json').read())['omitted_native']
    cache=payload/'download-cache';cache.mkdir(exist_ok=True)
    pg=args.pygame_source or download(lock['pypi']['pygame_source'],cache)
    if sha(pg)!=lock['pypi']['pygame_source']['sha256'] or pg.stat().st_size!=lock['pypi']['pygame_source']['bytes']:raise ValueError('Invalid pygame sdist')
    with tarfile.open(pg) as tar:sources,omitted=export_sources(tar)
    if native is not None:
        with tarfile.open(fileobj=io.BytesIO(native)) as tar:native_files,native_omitted=export_sources(tar)
    sources.update(native_files)
    licenses=ROOT/'licenses';licenses.mkdir(exist_ok=True)
    for n in ['LGPL-2.1','GPL-2']:(licenses/(n+'.txt')).write_bytes((ROOT/'audit/notices'/n).read_bytes())
    for n in ['libpinproc','pypinproc']:(licenses/(n+'-README-and-license.md')).write_bytes(sources[n+'/README.markdown'])
    for f in sorted(licenses.iterdir()):
        if f.is_file():sources['notices/'+f.name]=f.read_bytes()
    sources['BUILD-INSTRUCTIONS.txt']=BUILD;sources['BUILD-PYGAME-CODE.py']=DRIVER
    manifest={'format':1,'pygame_upstream':lock['pypi']['pygame_source'],'files':[{'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in sorted(sources.items())],'omitted_pygame':omitted,'omitted_native':native_omitted,'historical_build_verified':False,'blockers':['Historical build records/Setup/static archive hash missing; see BUILD-INSTRUCTIONS.txt']}
    sources['SOURCE-MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    write_tar(payload/'native-overlay.tar.gz',binary);write_tar(old_path,sources)
    lock['overlay']={'filename':'native-overlay.tar.gz','sha256':sha(payload/'native-overlay.tar.gz'),'bytes':(payload/'native-overlay.tar.gz').stat().st_size,'sources_filename':old_path.name,'sources_sha256':sha(old_path)}
    lock_path.write_text(json.dumps(lock,indent=2)+'\n')
    (payload/'native-overlay-source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (payload/'native-overlay-manifest.json').write_text(json.dumps({'files':[{'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for n,b in sorted(binary.items())],'sources':old_path.name,'build':'Audited binary snapshot; historical build correspondence unverified'},indent=2)+'\n')
    print(json.dumps({'overlay':lock['overlay'],'binary_file_count':len(binary),'source_files':len(sources),'omitted_pygame':len(omitted),'omitted_native':native_omitted},indent=2))
if __name__=='__main__':main()
