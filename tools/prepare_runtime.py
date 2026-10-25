#!/usr/bin/env python3
"""Assemble the legacy runtime locally from checksum-pinned official downloads.
The public project does not redistribute Ubuntu binary dependencies or game data.
"""
import argparse,concurrent.futures,hashlib,io,json,lzma,os,pathlib,posixpath,shutil,subprocess,tarfile,tempfile,urllib.request,zipfile
ROOT=pathlib.Path(__file__).resolve().parents[1]


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def download(item,cache):
    sha=item['sha256'];size=item['bytes']
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha) or not 0<size<200*1024*1024:raise ValueError('Invalid locked artifact')
    target=cache/sha
    if target.is_file() and target.stat().st_size==size and digest(target)==sha:return target
    urls=item.get('urls') or [item['url']];errors=[]
    for url in urls:
        if not url.startswith('https://'):raise ValueError('Only HTTPS downloads are accepted')
        temporary=target.with_suffix('.download')
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'ccc-native-batocera/0.1 runtime-bootstrap'})
            with urllib.request.urlopen(request,timeout=60) as response,open(temporary,'wb') as output:
                total=0
                for block in iter(lambda:response.read(1024*1024),b''):
                    total+=len(block)
                    if total>size:raise ValueError('Download exceeds locked size')
                    output.write(block)
            if total!=size or digest(temporary)!=sha:raise ValueError('Download size/SHA256 mismatch')
            os.replace(str(temporary),str(target));return target
        except Exception as exc:
            errors.append(str(exc))
            try:temporary.unlink()
            except FileNotFoundError:pass
    raise RuntimeError('Cannot fetch '+item.get('package',item.get('filename',sha))+': '+'; '.join(errors))


def decompress_zstd(blob):
    if shutil.which('zstd'):
        return subprocess.run(['zstd','-d','-q','-c'],input=blob,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,check=True).stdout
    import ctypes,ctypes.util
    name=ctypes.util.find_library('zstd')
    if not name:raise RuntimeError('Runtime preparation needs zstd or the system libzstd shared library; run this on Batocera/Linux')
    lib=ctypes.CDLL(name);source=ctypes.create_string_buffer(blob)
    lib.ZSTD_getFrameContentSize.argtypes=[ctypes.c_void_p,ctypes.c_size_t];lib.ZSTD_getFrameContentSize.restype=ctypes.c_ulonglong
    size=lib.ZSTD_getFrameContentSize(source,len(blob))
    if size>=2**64-2:
        lib.ZSTD_decompressBound.argtypes=[ctypes.c_void_p,ctypes.c_size_t];lib.ZSTD_decompressBound.restype=ctypes.c_ulonglong
        size=lib.ZSTD_decompressBound(source,len(blob))
    if not 0<size<200*1024*1024:raise ValueError('Invalid/oversize zstd dependency archive')
    out=ctypes.create_string_buffer(size);lib.ZSTD_decompress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t];lib.ZSTD_decompress.restype=ctypes.c_size_t
    result=lib.ZSTD_decompress(out,size,source,len(blob));lib.ZSTD_isError.argtypes=[ctypes.c_size_t];lib.ZSTD_isError.restype=ctypes.c_uint
    if lib.ZSTD_isError(result):raise ValueError('Dependency zstd decompression failed')
    return out.raw[:result]


def deb_tar(path):
    data=path.read_bytes()
    if not data.startswith(b'!<arch>\n'):raise ValueError('Invalid Debian archive')
    offset=8
    while offset<len(data):
        header=data[offset:offset+60]
        if len(header)!=60 or header[58:60]!=b'`\n':raise ValueError('Invalid ar header')
        name=header[:16].decode('ascii').strip().rstrip('/');size=int(header[48:58]);offset+=60
        blob=data[offset:offset+size];offset+=size+(size%2)
        if len(blob)!=size:raise ValueError('Truncated Debian archive')
        if name.startswith('data.tar'):
            if name.endswith('.zst'):
                blob=decompress_zstd(blob)
            return tarfile.open(fileobj=io.BytesIO(blob),mode='r:*')
    raise ValueError('No data archive in Debian package')


def normalized(name):
    if name.startswith('/'):raise ValueError('Absolute archive path')
    name=posixpath.normpath(name)
    if name=='..' or name.startswith('../'):raise ValueError('Archive traversal')
    return name.lstrip('./')


def tar_index(tar):
    result={}
    for info in tar.getmembers():
        name=normalized(info.name)
        if name in result:raise ValueError('Duplicate archive path '+name)
        if info.isdev() or info.isfifo():raise ValueError('Special file in dependency archive')
        result[name]=info
    return result


def member_bytes(tar,index,name,seen=None):
    seen=set() if seen is None else seen
    if name in seen:raise ValueError('Archive link cycle')
    seen.add(name);item=index[name]
    if item.isfile():
        if item.size>100*1024*1024:raise ValueError('Oversize dependency member')
        return tar.extractfile(item).read()
    if item.issym() or item.islnk():
        link=item.linkname
        target=link.lstrip('/') if link.startswith('/') else (posixpath.join(posixpath.dirname(name),link) if item.issym() else link)
        target=normalized(target)
        if target not in index:raise ValueError('Dependency symlink target missing: '+target)
        return member_bytes(tar,index,target,seen)
    raise ValueError('Expected regular dependency member '+name)


def write_file(root,name,data,executable=False):
    name=normalized(name);out=root/name;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data);out.chmod(0o755 if executable else 0o644)


def apply_overlay(path,root):
    with tarfile.open(path,'r:*') as tar:
        seen=set()
        for info in tar.getmembers():
            name=normalized(info.name)
            if name in seen:raise ValueError('Duplicate overlay path')
            seen.add(name)
            if info.isdir():continue
            if not info.isfile() or not name.startswith('python/lib/python2.7/site-packages/'):raise ValueError('Unsafe overlay member')
            write_file(root,name,tar.extractfile(info).read())


PYGAME_RESOURCES=('freesansbold.ttf','pygame.ico','pygame_icon.bmp','pygame_icon.icns','pygame_icon.svg','pygame_icon.tiff')


def pygame_resources(path,root):
    """Copy only six local resources from the checksum-verified official sdist."""
    records=[]
    with tarfile.open(path,'r:*') as tar:
        index=tar_index(tar)
        for name in PYGAME_RESOURCES:
            source='pygame-1.9.6/src_py/'+name
            if source not in index or not index[source].isfile():raise ValueError('Missing/nonregular pygame resource '+source)
            blob=member_bytes(tar,index,source);target='python/lib/python2.7/site-packages/pygame/'+name
            write_file(root,target,blob)
            records.append({'path':target,'source':source,'bytes':len(blob),'sha256':hashlib.sha256(blob).hexdigest()})
        write_file(root,'python/licenses/pygame-LGPL.txt',member_bytes(tar,index,'pygame-1.9.6/docs/LGPL'))
    write_file(root,'python/licenses/pygame-local-resources.json',(json.dumps({'upstream_sha256':digest(path),'files':records,'notice':'Recipient-fetched optional resources; FreeSans font metadata specifies GPL without an established exception. Do not redistribute this locally assembled runtime under wrapper MIT/LGPL claims.'},indent=2)+'\n').encode())
    return records


def assemble(lock,downloads,overlay,root):
    libs={}
    for lib in lock['libraries']:libs.setdefault(lib['package'],[]).append(lib['name'])
    for package in lock['packages']:
        name=package['package']
        with deb_tar(downloads[name]) as tar:
            index=tar_index(tar)
            for library in libs.get(name,[]):
                candidates=[p for p in index if posixpath.basename(p)==library]
                if len(candidates)!=1:raise RuntimeError('Dependency '+name+' lacks unique '+library)
                write_file(root,'lib/'+library,member_bytes(tar,index,candidates[0]))
            if name in lock['python_packages']:
                for path,info in index.items():
                    if path=='usr/bin/python2.7':write_file(root,'python/bin/python2.7',member_bytes(tar,index,path),True)
                    elif path.startswith('usr/lib/python2.7/') and not info.isdir():
                        rel=path[len('usr/lib/python2.7/'):]
                        if rel.endswith(('.pyc','.pyo')) or '__pycache__' in rel or 'sitecustomize' in rel:continue
                        if info.isfile():write_file(root,'python/lib/python2.7/'+rel,member_bytes(tar,index,path))
            copyright_name='usr/share/doc/'+name+'/copyright'
            if copyright_name in index:
                try:write_file(root,'python/licenses/'+name+'.txt',member_bytes(tar,index,copyright_name))
                except (KeyError,ValueError):pass
    pillow=lock['pypi']['pillow']
    with zipfile.ZipFile(downloads['pillow']) as wheel:
        for info in wheel.infolist():
            path=normalized(info.filename)
            if info.is_dir() or path.endswith(('.pyc','.pyo')):continue
            if not (path.startswith('PIL/') or path.startswith('Pillow-')):raise ValueError('Unexpected Pillow wheel member')
            write_file(root,'python/lib/python2.7/site-packages/'+path,wheel.read(info))
    with tarfile.open(downloads['pyyaml'],'r:*') as tar:
        index=tar_index(tar)
        for name,info in index.items():
            if '/lib/yaml/' in name and info.isfile() and name.endswith('.py'):
                write_file(root,'python/lib/python2.7/site-packages/yaml/'+name.split('/lib/yaml/',1)[1],tar.extractfile(info).read())
            if posixpath.basename(name)=='LICENSE' and info.isfile():write_file(root,'python/licenses/PyYAML.txt',tar.extractfile(info).read())
    pygame_resources(downloads['pygame_source'],root)
    apply_overlay(overlay,root)
    for notice in sorted((ROOT/'licenses').iterdir()):
        if notice.is_file() and not notice.is_symlink() and notice.suffix in ('.txt','.md'):
            write_file(root,'python/licenses/overlay-'+notice.name,notice.read_bytes())
    for name in ['python/bin/python2.7','python/lib/python2.7/site-packages/pygame/__init__.py','python/lib/python2.7/site-packages/pinproc.so','python/lib/python2.7/site-packages/PIL/__init__.py','python/lib/python2.7/site-packages/yaml/__init__.py']:
        if not (root/name).is_file():raise RuntimeError('Assembled runtime is incomplete: '+name)


def archive_runtime(root,target):
    total=0;count=0
    with tarfile.open(target,'w:gz',format=tarfile.PAX_FORMAT,compresslevel=6) as tar:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.suffix in ('.pyc','.pyo') or '__pycache__' in path.parts:continue
            blob=path.read_bytes();name=path.relative_to(root).as_posix();info=tarfile.TarInfo(name);info.size=len(blob);info.mode=0o755 if name=='python/bin/python2.7' else 0o644;info.mtime=0
            tar.addfile(info,io.BytesIO(blob));total+=len(blob);count+=1
    return total,count


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache',type=pathlib.Path);parser.add_argument('--force',action='store_true');args=parser.parse_args(argv)
    payload=ROOT/'payload';lock=json.loads((payload/'runtime-downloads.lock.json').read_text());overlay_record=lock['overlay'];overlay=payload/overlay_record['filename']
    if digest(overlay)!=overlay_record['sha256']:raise RuntimeError('Bundled overlay checksum mismatch')
    lock_sha=digest(payload/'runtime-downloads.lock.json');archive=payload/'runtime.tar.gz';manifest=payload/'runtime-manifest.json'
    if not args.force and archive.is_file() and manifest.is_file():
        previous=json.loads(manifest.read_text())
        if previous.get('format')=='tar.gz' and previous.get('preparer_sha256')==digest(pathlib.Path(__file__)) and previous.get('download_lock_sha256')==lock_sha and digest(archive)==previous.get('sha256'):
            print('Verified cached runtime:',archive);return 0
    cache=args.cache or payload/'download-cache';cache.mkdir(parents=True,exist_ok=True)
    items=[(x['package'],x) for x in lock['packages']]+[(k,lock['pypi'][k]) for k in ('pillow','pyyaml','pygame_source')]
    print('Downloading/checking %d pinned upstream artifacts (no game/table downloads).'%len(items),flush=True)
    downloads={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures={pool.submit(download,item,cache):name for name,item in items}
        for future in concurrent.futures.as_completed(futures):downloads[futures[future]]=future.result()
    with tempfile.TemporaryDirectory(prefix='.runtime-build-',dir=str(payload)) as temp:
        tree=pathlib.Path(temp)/'runtime';tree.mkdir();assemble(lock,downloads,overlay,tree)
        staged=pathlib.Path(temp)/'runtime.tar.gz';total,count=archive_runtime(tree,staged)
        record={'format':'tar.gz','preparer_sha256':digest(pathlib.Path(__file__)),'architecture':'x86_64','archive':'runtime.tar.gz','sha256':digest(staged),'bytes':staged.stat().st_size,'unpacked_bytes':total,'members':count,'download_lock_sha256':lock_sha,'provenance':'Locally assembled from pinned official Ubuntu/PyPI artifacts plus source-provided overlay'}
        os.replace(str(staged),str(archive));manifest.write_text(json.dumps(record,indent=2)+'\n')
    print('Runtime prepared:',archive,'SHA256',record['sha256']);return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as exc:raise SystemExit('RUNTIME_PREPARATION_FAILED: '+str(exc))
