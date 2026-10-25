from __future__ import print_function
import os,sys,time,json,shutil,traceback,logging,signal
ROOT=os.path.dirname(os.path.abspath(__file__))
BASE=os.path.realpath(os.environ.get('CCC_INSTALL_ROOT',os.path.dirname(ROOT)))
GAME=os.path.join(BASE,'game')
sys.path.insert(0,GAME);sys.path.insert(0,ROOT)
os.chdir(GAME)
from clock import monotonic
from transport import Transport,BoundedList
from runtime_cleanup import finalize
run=os.path.abspath(sys.argv[1]);start=monotonic()
game=transport=mapping=pygame=output=None
errors=[];state={'paused':False,'stop':False,'run':False,'stop_reason':None}
events=BoundedList(4096);samples=BoundedList(4096)
ticks=0;frames=[0];best=[''];best_nonzero=[0];output_result={};attempts=[]
video=None;mixer=None;saves=None;fault=os.environ.get('CCC_TEST_FAULT','')
def signal_stop(signum,frame):state.update(stop=True,stop_reason='signal-'+str(signum))
signal.signal(signal.SIGTERM,signal_stop);signal.signal(signal.SIGINT,signal_stop)
def snapshot(label):
    return {'time':time.time(),'label':label,'ball':game.ball,'players':len(game.players),
            'scores':[p.score for p in game.players],'switches':dict((s.name,bool(s.is_active())) for s in game.switches),
            'credits':getattr(game,'credits',None),'ticks':ticks,'frames':frames[0]}
def lifecycle(fields):
    if fields==['RUN','cc_13']:
        if state['run']:raise ValueError('Duplicate RUN')
        state['run']=True;transport.status='RUNNING'
    elif fields in (['PAUSE','0'],['PAUSE','1']):
        if not state['run']:raise ValueError('Pause before RUN')
        state['paused']=fields[1]=='1';transport.status='PAUSED' if state['paused'] else 'RUNNING'
    elif fields==['STOP']:state.update(stop=True,stop_reason='protocol-STOP')
    else:raise ValueError('Invalid lifecycle request: '+repr(fields))
def request(fields):
    if fields[0]=='SW':
        if not state['run']:raise ValueError('Switch before RUN')
        if len(fields)!=3 or fields[2] not in ('0','1') or not 0<=int(fields[1])<120:raise ValueError('Invalid switch')
    mapping.request(fields);events.append([time.time()]+fields);samples.append(snapshot('request '+' '.join(fields)))
def capture(frame):
    data=frame.get_data();frames[0]+=1;nonzero=sum(ord(v)!=0 for v in data)
    if nonzero>=best_nonzero[0]:best[0]=data;best_nonzero[0]=nonzero
def artifacts():
    if fault=='png':raise IOError('Injected PNG save failure')
    if best[0]:open(os.path.join(run,'dmd-best.raw'),'wb').write(best[0])
    if pygame and pygame.display.get_surface():pygame.image.save(pygame.display.get_surface(),os.path.join(run,'dmd-color-final.png'))
try:
    if not os.path.isdir(run):os.makedirs(run)
    import pinproc,pygame
    # Refuse direct USB PinPROC before importing or constructing user game code.
    def hardware_forbidden(*args,**kwargs):raise RuntimeError('Physical PinPROC is forbidden')
    pinproc.PinPROC=hardware_forbidden
    from procgame import config as game_config
    game_config.values.update(pinproc_class='procgame.fakepinproc.FakePinPROC',use_desktop=True,
                              color_desktop=True,windowless=False,keyboard_switch_map={},
                              usb_update=False,shutdown_flag=False,power_button_combo=False)
    from procgame.game import GameController
    from procgame.fakepinproc import FakePinPROC
    GameController.create_pinproc=lambda self:FakePinPROC(self.machine_type)
    from ep import EP_Desktop
    from software_dmd import configure_desktop
    geometry=[int(v) for v in os.environ.get('CCC_DMD_GEOMETRY','16,16,5').split(',')]
    configure_desktop(pygame,EP_Desktop,geometry)
    import game as game_module
    from ccc_wpc_mapping import CCCWPCMapping
    from cabinet_output import Output
    expected_saves=os.path.join(run,'selftest-saves') if os.environ.get('CCC_SELFTEST')=='1' else os.path.join(BASE,'saves')
    saves=os.path.realpath(expected_saves)
    if os.path.islink(expected_saves):raise ValueError('Save symlinks are forbidden')
    if not os.path.isdir(saves):os.makedirs(saves)
    if os.environ.get('CCC_SELFTEST')=='1':
        for filename in ('game_data.yaml','user_settings.yaml','game_data_backup.yaml','user_settings_backup.yaml'):
            source=os.path.join(BASE,'saves',filename)
            if os.path.islink(source):raise ValueError('Save symlinks are forbidden')
            if os.path.isfile(source):shutil.copyfile(source,os.path.join(saves,filename))
    for name,filename in [('user_game_data_path','game_data.yaml'),('user_settings_path','user_settings.yaml'),('user_game_data_backup','game_data_backup.yaml'),('user_settings_backup','user_settings_backup.yaml')]:
        target=os.path.join(saves,filename)
        if os.path.islink(target):raise ValueError('Save symlinks are forbidden')
        # Never import original personal data; game templates create fresh defaults.
        setattr(game_module,name,target)
    logging.basicConfig(level=logging.INFO)
    game=game_module.CCGame.__new__(game_module.CCGame)
    game_module.CCGame.__init__(game)
    game.desktop.clear_key_map()
    mapping=CCCWPCMapping(game,pinproc,lifecycle)
    if fault=='init':raise RuntimeError('Injected post-constructor initialization failure')
    assert type(game.proc).__name__=='FakePinPROC','Hardware backend forbidden'
    if not pygame.mixer.get_init():raise RuntimeError('CCC mixer initialization failed; refusing silent success')
    video=pygame.display.get_driver();mixer=pygame.mixer.get_init()
    output=Output(pygame,run,enabled=os.environ.get('CCC_DMD_OUTPUT','0')=='1',
                  port=int(os.environ.get('CCC_DMD_PORT','6789')))
    def bind(original):
        def rendered(*args,**kwargs):
            result=original(*args,**kwargs);output.capture(game.desktop.dmd_display);return result
        return rendered
    for method in ('draw','blit','blackout'):setattr(game.desktop,method,bind(getattr(game.desktop,method)))
    transport=Transport(os.path.join(run,'ipc'));game.dmd.frame_handlers.append(capture)
    for i in range(120):mapping.set_switch(i,False)
    transport.capture(mapping.outputs())
    for driver in game.proc.drivers:
        previous=driver.state_change_handler
        def observer(previous=previous):
            if previous:previous()
            transport.capture(mapping.outputs())
        driver.state_change_handler=observer
    transport.publish();print('READY %s %.3f'%(transport.session,monotonic()-start));sys.stdout.flush()
    duration=float(os.environ.get('CCC_MAX_SECONDS','90'));deadline=start+duration if duration>0 else None
    last_sample=start
    while not state['stop']:
        if deadline is not None and monotonic()>=deadline:
            state['stop_reason']='deadline';raise RuntimeError('Bounded runtime deadline reached without STOP')
        output.check();transport.consume(request)
        if state['stop']:break
        if deadline is not None and monotonic()>=deadline:
            state['stop_reason']='deadline';raise RuntimeError('Bounded runtime deadline reached without STOP')
        if not state['paused']:
            for event in game.get_events():game.process_event(event)
            game.tick();game.tick_virtual_drivers();game.modes.tick();ticks+=1
        game.proc.watchdog_tickle();game.proc.flush();transport.capture(mapping.outputs());transport.publish()
        if monotonic()-last_sample>=.25:samples.append(snapshot('tick'));last_sample=monotonic()
        time.sleep(.005)
    if state['stop_reason']!='protocol-STOP':raise RuntimeError('Runtime terminated without protocol STOP: '+str(state['stop_reason']))
except BaseException:
    if not state['stop_reason']:state['stop_reason']='backend-failure'
    errors.append(traceback.format_exc());sys.stderr.write(errors[-1]+'\n')
finally:
    try:output_result,attempts=finalize(game,transport,mapping,pygame,output,errors,artifacts,persist=state['stop_reason']=='protocol-STOP' and os.environ.get('CCC_SELFTEST')!='1',publish_fault=fault=='publish')
    except BaseException:
        errors.append('Unexpected cleanup failure:\n'+traceback.format_exc());sys.stderr.write(errors[-1]+'\n')
    result={'session':transport.session if transport else None,'status':'ERROR' if errors else 'STOPPED',
            'error':'\n'.join(errors) if errors else None,'errors':errors,'stop_reason':state['stop_reason'],
            'ticks':ticks,'frames':frames[0],'elapsed':monotonic()-start,'requests':events,'samples':samples,
            'edges':transport.history if transport else [],'acks':transport.acks if transport else [],
            'edge_ends':transport.ends if transport else [],'pending':[len(q) for q in transport.queues] if transport else [],
            'final_outputs':transport.arrays if transport else (mapping.outputs() if mapping else []),'transport_requests':transport.requests if transport else [],
            'backend':type(game.proc).__name__ if game and hasattr(game,'proc') else None,'sdl_video':video,'mixer':mixer,
            'cabinet_output':output_result,'audio_mode':os.environ.get('CCC_AUDIO','dummy'),'saves':saves,
            'cleanup_attempts':attempts,'diagnostics':transport.diagnostics() if transport else {}}
    result['diagnostics'].update(events=events.metadata(),samples=samples.metadata())
    try:
        tmp=os.path.join(run,'result.tmp');open(tmp,'w').write(json.dumps(result,indent=2));os.rename(tmp,os.path.join(run,'result.json'))
    except BaseException:
        errors.append('Result persistence failed:\n'+traceback.format_exc());sys.stderr.write(errors[-1]+'\n')
    print('RESULT',result['status'],'reason',state['stop_reason'],'ticks',ticks,'frames',frames[0]);sys.stdout.flush()
if errors:sys.exit(1)
