"""Launcher WM/profile regressions without requiring a local X server."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]/'app'
sys.path.insert(0,str(APP))
import launch
import software_dmd

class LauncherDmdTests(unittest.TestCase):
    def test_scoped_wm_state_geometry_and_passive_focus(self):
        with mock.patch.object(launch,'owned_window',side_effect=['101','202']), mock.patch.object(launch.shutil,'which',return_value='/usr/bin/wmctrl'), mock.patch.object(launch.subprocess,'run') as run:
            self.assertTrue(launch.focus_windows(object(),object(),{'DISPLAY':':0'},True,(10,20,5)))
        commands=[call.args[0] for call in run.call_args_list]
        self.assertEqual(commands,[
            ['wmctrl','-i','-r','101','-b','remove,fullscreen'],
            ['wmctrl','-i','-r','202','-b','remove,fullscreen'],
            ['wmctrl','-i','-r','202','-b','add,above'],
            ['xdotool','windowsize','--sync','202','640','160','windowmove','202','10','20'],
            ['xdotool','windowfocus','--sync','101'],
            ['xdotool','windowraise','202']])
        self.assertTrue(all(call.kwargs['check'] and call.kwargs['timeout']==2 for call in run.call_args_list))

    def test_no_software_leaves_window_states_untouched(self):
        with mock.patch.object(launch,'owned_window',return_value='101'),mock.patch.object(launch.subprocess,'run') as run:
            self.assertTrue(launch.focus_windows(object(),None,{},False))
        self.assertEqual(run.call_args.args[0],['xdotool','windowfocus','--sync','101'])
        self.assertEqual(run.call_count,1)

    def test_wait_for_both_owned_windows(self):
        with mock.patch.object(launch,'owned_window',side_effect=['101',None]),mock.patch.object(launch.subprocess,'run') as run:
            self.assertFalse(launch.focus_windows(object(),object(),{},True,(0,0,5)))
        self.assertFalse(any(c.args[0][0]=='wmctrl' for c in run.call_args_list))

    def test_missing_wmctrl_fails_explicitly(self):
        with mock.patch.object(launch,'owned_window',side_effect=['101','202']),mock.patch.object(launch.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'wmctrl'):launch.focus_windows(object(),object(),{},True)

    def profile_to_ini(self,config,flags=()):
        import tempfile
        cfg={'software_dmd':True,'dmd_server':None,'sound_enabled':False,**config}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'runs').mkdir()
            (root/'config').mkdir()
            (root/'config/VPX.ini').write_text('')
            with mock.patch.object(launch,'settings',return_value=cfg),mock.patch.object(launch,'preflight',return_value=(None,None,None)),mock.patch.object(launch,'player_environment',return_value={}),mock.patch.object(launch,'detect_displays',return_value=[]),mock.patch.object(launch,'dmd_geometry',return_value=(0,0,5)),mock.patch.object(launch,'check_idle'),mock.patch.object(launch,'prepare_ini',side_effect=RuntimeError('stop before runtime')) as ini,contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch.main(['--root',str(root),*flags]),1)
            return ini.call_args.args[-1]

    def test_profile_saved_and_override_and_legacy_default(self):
        self.assertEqual(self.profile_to_ini({}),'desktop')
        self.assertEqual(self.profile_to_ini({'profile':'cabinet'}),'cabinet')
        self.assertEqual(self.profile_to_ini({'profile':'cabinet'},['--profile','desktop']),'desktop')

    def test_invalid_saved_profile_refused_before_preflight(self):
        with mock.patch.object(launch,'settings',return_value={'profile':'sideways'}),mock.patch.object(launch,'preflight') as preflight,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:launch.main(['--root',str(APP.parent)])
        self.assertEqual(exc.exception.code,2);preflight.assert_not_called()

    def test_renderer_backing_stays_hd_and_only_visible_surface_scales(self):
        pygame=mock.Mock();pygame.NOFRAME=32
        desktop=type('Desktop',(),{'draw_window':lambda self,*args:None})
        software_dmd.configure_desktop(pygame,desktop,(0,0,5))
        instance=desktop();instance.setup_window()
        pygame.display.set_mode.assert_called_once_with((640,160),32)
        pygame.Surface.assert_called_once_with((1280,320),depth=24)
        self.assertIs(instance.screen,instance.dmd_display)
        pygame.display.update()
        pygame.transform.scale.assert_called_once_with(instance.screen,instance.visible_window.get_size())
        self.assertEqual(instance.get_keyboard_events(),[])

if __name__=='__main__':unittest.main()
