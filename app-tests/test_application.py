"""Offline fixtures only: no native game, VPX, devices, network or cabinet access."""
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import configuration as policy
import build_table as builder
import launch
from software_dmd import configure_desktop
from runtime_cleanup import finalize

# A tiny invented anchor fixture, not a copy of third-party table code.
ANCHORS = '\n'.join(['  LoadProc "02000000", "WPC.VBS", 3.50',
                     'Const UseVPMModSol = True', '  Controller.Stop',
                     '    On Error Resume Next\n    .Run\n    If Err Then MsgBox Err.Description'])


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ccc-app-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_default_root_and_environment_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(policy.install_root(), APP.parent)
        with patch.dict(os.environ, {'CCC_INSTALL_ROOT': str(self.root)}):
            self.assertEqual(policy.install_root(), self.root.resolve())

    def test_defaults_no_physical_dmd_no_imported_geometry(self):
        (self.root / 'config').mkdir()
        (self.root / 'config/settings.json').write_text('{"schema":1}')
        cfg = policy.settings(self.root)
        self.assertTrue(cfg['software_dmd'])
        self.assertTrue(cfg['sound_enabled'])
        self.assertIsNone(cfg['dmd_server'])
        self.assertEqual(policy.dmd_geometry([]), (16, 16, 5))
        self.assertEqual(policy.dmd_geometry([dict(x=0,y=0,width=1920,height=1080),dict(x=1920,y=0,width=800,height=600)]), (1936,16,5))
        for value in ('localhost:6789', '0.0.0.0:6789', '192.0.2.1:6789', '127.0.0.1:0', '127.0.0.1:65536', True):
            with self.assertRaises(ValueError):
                policy.dmd_endpoint(value)
        self.assertEqual(policy.dmd_endpoint('127.0.0.1:6789'), ('127.0.0.1',6789))

    def test_path_boundaries(self):
        with self.assertRaises(ValueError):
            policy.inside(self.root, '../outside')
        outside = self.root / 'outside'; outside.mkdir()
        try:
            (self.root / 'saves').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Host does not permit symlink creation')
        with self.assertRaises(ValueError):
            policy.inside(self.root, 'saves/test')

    def test_audio_environment_preserved_and_private(self):
        env = {'HOME': '/original', 'DISPLAY': ':7', 'PULSE_SERVER': 'unix:/custom/socket',
               'PULSE_COOKIE': '/custom/cookie', 'XDG_RUNTIME_DIR': '/run/user/42',
               'PYTHONHOME': '/wrong', 'LD_LIBRARY_PATH': '/wrong', 'SDL_VIDEODRIVER': 'dummy'}
        actual = policy.player_environment(self.root, env)
        self.assertEqual(actual['HOME'], str(self.root))
        for key in ('DISPLAY', 'PULSE_SERVER', 'PULSE_COOKIE', 'XDG_RUNTIME_DIR'):
            self.assertEqual(actual[key], env[key])
        self.assertNotIn('PYTHONHOME', actual)
        self.assertNotIn('LD_LIBRARY_PATH', actual)
        self.assertNotIn('SDL_VIDEODRIVER', actual)
        self.assertEqual(policy.player_environment(self.root, env, mute=True)['SDL_AUDIODRIVER'], 'dummy')

    def test_private_ini_comment_defaults_and_no_external_plugins(self):
        source, target = self.root / 'VPX.ini', self.root / 'output.ini'
        source.write_text('; Defaults only\n[Player]\nDMDServer=1\nPUPPlugin=1\n')
        displays = [dict(width=1600,height=900)]
        policy.prepare_ini(source,target,False,displays)
        text = target.read_text()
        self.assertIn('DMDServer = 0',text)
        self.assertIn('PUPPlugin = 0',text)
        self.assertIn('Width = 1600',text)
        self.assertIn('SoundVolume = 0',text)
        self.assertNotIn('TableOverride',text)
        with self.assertRaises(ValueError):
            policy.prepare_ini(source,target,True,displays,'cabinet')
        policy.prepare_ini(source,target,True,[dict(width=900,height=1600)],'cabinet')
        self.assertIn('ViewCabScaleX = 2.133588',target.read_text())

    def test_relocated_patch_embeds_final_pointer_with_quotes_escaped(self):
        final = self.root / 'quoted"installation'
        script = builder.patch_script(ANCHORS, final)
        self.assertIn('CCCpathfile.ReadLine', script)
        self.assertNotIn('CCCpathfile.ReadAll', script)
        self.assertIn(builder.vbs_literal((final / 'active-ipc.txt').as_posix()),script)
        self.assertIn('root=""',script)
        self.assertNotIn('LoadProc ',script)
        self.assertIn('Controller.Shutdown',script)
        self.assertIn('Const UseVPMModSol = False',script)
        with self.assertRaises(ValueError):
            builder.patch_script(ANCHORS + '\n' + ANCHORS, final)
        with self.assertRaises(ValueError):
            builder.vbs_literal('x\ny')

    def test_builder_hash_fail_closed_and_original_inputs_unchanged(self):
        source = self.root / 'source';source.mkdir()
        table, script = source / 'input.vpx', source / 'input.vbs'
        table.write_bytes(b'inert table fixture, NOT a VPX')
        script.write_text(ANCHORS)
        destination = self.root / 'stage/tables'
        final = self.root / 'final'
        with self.assertRaises(ValueError):
            builder.build_table(table,script,destination,final)
        self.assertFalse(destination.exists())
        original = (table.read_bytes(),script.read_bytes())
        hashes = (builder.sha256(table),builder.sha256(script))
        manifest = builder.build_table(table,script,destination,final,_hashes=hashes)
        self.assertEqual(manifest['pointer'],(final/'active-ipc.txt').as_posix())
        self.assertEqual(builder.sha256(destination/'CCC-native.vpx'),hashes[0])
        self.assertEqual(manifest['script_sha256'],builder.sha256(destination/'CCC-native.vbs'))
        self.assertEqual(original,(table.read_bytes(),script.read_bytes()))
        with self.assertRaises(FileExistsError):
            builder.build_table(table,script,destination,final,_hashes=hashes)

    def test_builder_cli_no_hash_override_and_help_works(self):
        result = subprocess.run([sys.executable,'-B',str(APP/'build_table.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--install-root',result.stdout)
        self.assertIn('--output-table',result.stdout)
        result = subprocess.run([sys.executable,'-B',str(APP/'build_table.py'),'--table-hash','ignored'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)

    def test_runtime_environment_defaults_and_selftest_outputs_off(self):
        cfg = dict(software_dmd=True,sound_enabled=True,audio_backend='auto',dmd_server=None)
        env = launch.runtime_environment(self.root,self.root/'runs/1',cfg,{'DISPLAY':':7'},(16,16,5))
        self.assertEqual(env['SDL_VIDEODRIVER'],'x11')
        self.assertEqual(env['SDL_AUDIODRIVER'],'alsa')
        self.assertEqual(env['CCC_DMD_OUTPUT'],'0')
        self.assertEqual(env['PYTHONHOME'],str(self.root/'runtime/python'))
        cfg['dmd_server']='127.0.0.1:6789'
        env = launch.runtime_environment(self.root,self.root/'runs/1',cfg,{'PULSE_SERVER':'custom'},(16,16,5),True)
        self.assertEqual(env['CCC_DMD_OUTPUT'],'0')
        self.assertEqual(env['SDL_VIDEODRIVER'],'dummy')
        self.assertEqual(env['SDL_AUDIODRIVER'],'dummy')
        self.assertEqual(env['CCC_SELFTEST'],'1')

    def test_fresh_result_validation_and_backend_failure(self):
        good = dict(session='fresh',status='STOPPED',error=None,stop_reason='protocol-STOP',requests=[[0,'STOP']],final_outputs=[[0]*64,[0]*90,[0]*5])
        self.assertEqual(launch.validate_result(good,'fresh'),[])
        self.assertTrue(launch.validate_result(good,'another'))
        (self.root/'result.json').write_text(json.dumps(good))
        process=SimpleNamespace(poll=lambda:0,returncode=0)
        self.assertEqual(launch.backend_health(process,self.root,'fresh'),'protocol-stopped')
        self.assertEqual(launch.backend_health(process,self.root,'other'),'failed')
        process.returncode=9
        self.assertEqual(launch.backend_health(process,self.root,'fresh'),'failed')

    def test_stop_reaps_disappearing_owned_child_and_does_not_broad_kill(self):
        process=SimpleNamespace(pid=12345,poll=lambda:None,wait=Mock(return_value=0))
        with patch.object(launch.os,'killpg',create=True,side_effect=ProcessLookupError) as kill:
            launch.stop_owned(process)
        kill.assert_called_once_with(12345,signal_number())
        process.wait.assert_called_once_with(4)

    def test_ipc_selftest_client_atomic_requests_are_session_bound(self):
        (self.root/'ipc').mkdir()
        client=launch.IPCClient(self.root,'session')
        client.send('RUN cc_13')
        self.assertEqual((self.root/'ipc/1.req').read_text(),'session 1 RUN cc_13\n')
        self.assertTrue((self.root/'ipc/1.ready').exists())
        client.send('STOP')
        self.assertEqual((self.root/'ipc/2.req').read_text(),'session 2 STOP\n')

    def test_sources_relocatable_and_no_personal_data(self):
        for path in APP.iterdir():
            if path.suffix not in ('.py','.vbs','.sh'):
                continue
            text=path.read_text(encoding='utf-8')
            for token in ('ccc-linux-lab','C:/Users/','C:' + chr(92) + 'Users' + chr(92),'1080x1920'):
                self.assertNotIn(token,text,str(path))
            if path.suffix=='.py':
                ast.parse(text)
        names={p.name for p in APP.iterdir()}
        for personal in ('user_settings.yaml','game_data.yaml','config.yaml','CCC-native.vbs','CCC-native.vpx'):
            self.assertNotIn(personal,names)

    def test_original_hd_backing_surface_is_not_cropped(self):
        class Surface:
            def __init__(self,size,**kwargs):self.size=size;self.blits=[]
            def fill(self,color):pass
            def blit(self,image,xy):self.blits.append((image,xy))
            def get_size(self):return self.size
        window=Surface((640,160));updates=[];scaled=[]
        pygame=SimpleNamespace(init=lambda:None,NOFRAME=1,Surface=Surface,
            display=SimpleNamespace(set_mode=lambda size,flags:window,set_caption=lambda text:None,update=lambda:updates.append(1)),
            transform=SimpleNamespace(scale=lambda src,size:(scaled.append((src,size)) or Surface(size))),
            event=SimpleNamespace(pump=lambda:None,clear=lambda:None))
        class Desktop:
            def draw_window(self,pixel,x,y):
                self.pixel_size=pixel;self.setup_window()
        configure_desktop(pygame,Desktop,(16,16,5))
        desktop=Desktop();desktop.draw_window(14,999,999)
        self.assertEqual(desktop.pixel_size,10)
        self.assertEqual(desktop.screen.size,(1280,320))
        pygame.display.update()
        self.assertIs(scaled[0][0],desktop.screen)
        self.assertEqual(scaled[0][1],(640,160))
        self.assertEqual(desktop.get_keyboard_events(),[])

    def test_supervisor_startup_failure_releases_lock_and_reports_failure(self):
        (self.root/'config').mkdir()
        (self.root/'config/settings.json').write_text('{"schema":1}')
        (self.root/'config/VPX.ini').write_text('; defaults')
        (self.root/'runs').mkdir()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launch,'install_root',return_value=self.root))
            stack.enter_context(patch.object(launch,'preflight',return_value=(self.root/'python',self.root/'vpx',self.root/'table')))
            stack.enter_context(patch.object(launch,'detect_displays',return_value=[]))
            stack.enter_context(patch.object(launch,'check_idle'))
            stack.enter_context(patch.object(launch.subprocess,'Popen',side_effect=OSError('injected start failure')))
            stack.enter_context(patch.object(launch.HotkeyContext,'start'))
            close=stack.enter_context(patch.object(launch.HotkeyContext,'close'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            rc=launch.main(['--mute','--no-software-dmd'])
        self.assertEqual(rc,1)
        self.assertFalse((self.root/'launch.lock').exists())
        close.assert_called_once()
        reports=list((self.root/'runs').glob('*/launch-result.json'))
        self.assertEqual(len(reports),1)
        result=json.loads(reports[0].read_text())
        self.assertEqual(result['status'],'FAIL')
        self.assertIn('injected start failure',' '.join(result['errors']))

    def test_supervisor_backend_death_stops_only_owned_children_and_removes_pointer(self):
        (self.root/'config').mkdir()
        (self.root/'config/settings.json').write_text('{"schema":1}')
        (self.root/'config/VPX.ini').write_text('; defaults')
        (self.root/'runs').mkdir()
        processes=[];stopped=[];beat=[0]
        class Process:
            def __init__(self,pid):self.pid=pid;self.returncode=None
            def poll(self):return self.returncode
        def spawn(command,**kwargs):
            process=Process(100+len(processes));processes.append(process)
            if len(processes)==1:
                run=Path(command[-1]);(run/'ipc').mkdir();(run/'ipc/state.txt').write_text('inert readiness fixture')
            return process
        def state(*args):
            beat[0]+=1
            return ['CCC2','fixture-session',str(beat[0]),'READY','0'],[]
        def stop(process):
            if process is not None:
                stopped.append(process.pid);process.returncode=-15
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launch,'install_root',return_value=self.root))
            stack.enter_context(patch.object(launch,'preflight',return_value=(self.root/'python',self.root/'vpx',self.root/'table')))
            stack.enter_context(patch.object(launch,'detect_displays',return_value=[]))
            stack.enter_context(patch.object(launch,'check_idle'))
            stack.enter_context(patch.object(launch,'read_state',side_effect=state))
            stack.enter_context(patch.object(launch,'backend_health',return_value='failed'))
            stack.enter_context(patch.object(launch,'stop_owned',side_effect=stop))
            stack.enter_context(patch.object(launch.subprocess,'Popen',side_effect=spawn))
            stack.enter_context(patch.object(launch.HotkeyContext,'start'))
            stack.enter_context(patch.object(launch.HotkeyContext,'close'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            rc=launch.main(['--seconds','2','--mute','--no-software-dmd'])
        self.assertEqual(rc,1)
        self.assertEqual(stopped,[101,100])
        self.assertFalse((self.root/'launch.lock').exists())
        self.assertFalse((self.root/'active-ipc.txt').exists())
        reports=list((self.root/'runs').glob('*/launch-result.json'))
        self.assertEqual(json.loads(reports[0].read_text())['status'],'FAIL')

    def test_check_is_read_only_and_never_constructs_processes_or_network(self):
        (self.root/'config').mkdir()
        (self.root/'config/settings.json').write_text('{"schema":1,"dmd_server":"127.0.0.1:6789"}')
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launch,'install_root',return_value=self.root))
            stack.enter_context(patch.object(launch,'preflight',return_value=(self.root/'python',self.root/'vpx',self.root/'table')))
            stack.enter_context(patch.object(launch,'detect_displays',return_value=[]))
            popen=stack.enter_context(patch.object(launch.subprocess,'Popen'))
            network=stack.enter_context(patch.object(launch.socket,'create_connection'))
            output=stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            rc=launch.main(['--check'])
        self.assertEqual(rc,0)
        popen.assert_not_called();network.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['outputs_started'])
        self.assertFalse((self.root/'launch.lock').exists())
        self.assertFalse((self.root/'runs').exists())

    def test_cleanup_faults_do_not_skip_disable_publish_quit(self):
        actions=[];errors=[]
        driver=SimpleNamespace(state_change_handler=lambda:None,disable=lambda:actions.append('disable'))
        game=SimpleNamespace(proc=SimpleNamespace(drivers=[driver]),end_run_loop=lambda:actions.append('end'),
                             save_game_data=lambda:actions.append('save'),save_settings=lambda:actions.append('settings'))
        transport=SimpleNamespace(capture=lambda a:actions.append('capture'),publish=lambda:actions.append('publish'))
        mapping=SimpleNamespace(outputs=lambda:([0]*64,[0]*90,[0]*5))
        pygame=SimpleNamespace(quit=lambda:actions.append('quit'))
        output=SimpleNamespace(close=lambda:(_ for _ in ()).throw(OSError('injected storage failure')))
        with contextlib.redirect_stderr(io.StringIO()):
            finalize(game,transport,mapping,pygame,output,errors,lambda:actions.append('artifacts'),persist=True)
        self.assertTrue(errors)
        self.assertEqual(actions,['disable','capture','end','artifacts','publish','quit'])
        self.assertIsNone(driver.state_change_handler)


def signal_number():
    return launch.signal.SIGTERM


if __name__=='__main__':
    unittest.main()
