"""Python2/3 RGB sender. No hardware access until explicit enabled=True.
Wire format captured from installed dmd-play --hd --once to a fake sink.
Each frame uses its own bounded transaction, exactly like dmd-play --once.
"""
from __future__ import print_function
import socket,struct,threading,time,hashlib
from clock import monotonic

def rgb_packet(rgb):
    if len(rgb)!=256*64*3: raise ValueError('Expected 256x64 RGB24')
    values=bytearray(rgb)
    # Installed dmd-play type3 stores network-order RGB565 in a fixed
    # RGB24-sized buffer, zero-padding the last third. Proved byte-for-byte.
    packed=b''.join(struct.pack('!H',((values[i]>>3)<<11)|((values[i+1]>>2)<<5)|(values[i+2]>>3))
                    for i in range(0,len(values),3))
    payload=packed+b'\0'*(256*64)
    return struct.pack('!10sBIHHBBI',b'DMDStream\0',1,3,256,64,1,1,len(payload))+payload

def reset_packet():
    # Verified installed server protocol: empty-ROM grayscale Data clears Serum.
    return (struct.pack('!10sBIHHBBI',b'DMDStream\0',1,1,0,0,0,0,0)
            +b'Paths\0'+b'\0'*(16+256+256)
            +struct.pack('!III',1,0,2)+b'\0'*(256*64*3+256*64*2+128*2)
            +bytes(bytearray([1,0,0,0,0,0]))+struct.pack('!HH',256,64))

def transact(packet,host,port):
    s=socket.create_connection((host,port),timeout=0.35)
    try:
        s.settimeout(0.35); s.sendall(packet)
    finally: s.close()

def dense_grid(pygame, surface):
    """Sample actual source-dot centers, then fill exact 2x2 hardware blocks.
    Do not scale the desktop's simulated dot outlines onto the physical LEDs.
    """
    width,height=surface.get_size()
    if width<128 or height<32:
        raise ValueError('CCC source is smaller than the logical 128x32 grid')
    if width%128==0 and height%32==0:
        # C blit/scale path: nearest sampling starts at each logical dot center.
        centered=surface.copy()
        centered.blit(surface, (-(width//256), -(height//64)))
        logical=pygame.transform.scale(centered,(128,32))
    else:
        logical=pygame.Surface((128,32),depth=24)
        for y in range(32):
            for x in range(128):
                logical.set_at((x,y),surface.get_at((min(width-1,int((x+.5)*width/128)),
                                                   min(height-1,int((y+.5)*height/32)))))
    return pygame.transform.scale(logical,(256,64))

class Output(object):
    def __init__(self,pygame,run,enabled=False,host='127.0.0.1',port=6789):
        self.pygame=pygame; self.run=run; self.enabled=enabled
        if host!='127.0.0.1': raise ValueError('Only loopback is allowed')
        self.host=host; self.port=int(port); self.error=None
        self.frames=0; self.sent=0; self.dropped=0; self.last_rgb=None
        self.pending=None; self.stopping=False; self.lock=threading.Lock()
        self.wake=threading.Event(); self.stop=threading.Event(); self.thread=None; self.last_send=0
        if enabled:
            self.thread=threading.Thread(target=self.worker); self.thread.daemon=True; self.thread.start()
    def capture(self,surface):
        # Called AFTER EP_Desktop draws, including HD blits. No guessed palette.
        image=dense_grid(self.pygame,surface)
        rgb=self.pygame.image.tostring(image,'RGB')
        self.frames+=1; self.last_rgb=rgb
        if self.frames==1 or self.frames%60==0:
            self.pygame.image.save(image,self.run+'/dmd-rgb-latest.png')
            open(self.run+'/dmd-rgb-latest.rgb','wb').write(rgb)
        if self.enabled:
            with self.lock:
                if self.pending is not None: self.dropped+=1
                self.pending=rgb
            self.wake.set()
    def worker(self):
        try:
            transact(reset_packet(),self.host,self.port)
            if self.stop.wait(.25): return
            while not self.stop.is_set():
                self.wake.wait(.1); self.wake.clear()
                if self.stop.is_set(): break
                delay=1./15-(monotonic()-self.last_send)
                if delay>0 and self.stop.wait(delay): break
                with self.lock:
                    if self.stop.is_set(): break
                    rgb=self.pending; self.pending=None
                if rgb is None: continue
                packet=rgb_packet(rgb)
                if self.stop.is_set(): break
                transact(packet,self.host,self.port)
                self.sent+=1; self.last_send=monotonic()
        except Exception as exc:
            self.error='DMD forwarding failed: '+repr(exc)
    def check(self):
        if self.error: raise RuntimeError(self.error)
    def close(self):
        self.stopping=True; self.stop.set(); self.wake.set()
        if self.thread:
            self.thread.join(2)
            if self.thread.is_alive():
                self.error=(self.error or '')+'; DMD worker did not stop; black cleanup withheld'
            else:
                # Only a joined sender may be followed by cleanup black frames.
                # Never clear without authorization. Do not restart the server.
                try:
                    for _ in range(2):
                        transact(rgb_packet(b'\0'*(256*64*3)),self.host,self.port); time.sleep(.05)
                except Exception as exc: self.error=(self.error or '')+'; DMD cleanup failed: '+repr(exc)
        if self.last_rgb: open(self.run+'/dmd-rgb-final.rgb','wb').write(self.last_rgb)
        return {'enabled':self.enabled,'frames':self.frames,'sent':self.sent,'dropped':self.dropped,
                'error':self.error,'rgb_sha256':hashlib.sha256(self.last_rgb).hexdigest() if self.last_rgb else None,
                'provenance':'EP_Desktop actual dot centers128x32; solid nearest2x2 hardware grid256x64, no simulated dot gaps'}
