"""Failure-isolated cleanup shared by the real runner and fault tests (Python2)."""
from __future__ import print_function
import traceback,sys

def finalize(game,transport,mapping,pygame,output,errors,artifacts,persist=False,publish_fault=False):
    attempts=[]; output_result={}
    def attempt(label,action):
        attempts.append(label)
        try: return action()
        except BaseException:
            message=label+':\n'+traceback.format_exc();errors.append(message)
            sys.stderr.write(message+'\n')
    try:
        drivers=getattr(getattr(game,'proc',None),'drivers',[]) if game else []
        for driver in drivers: attempt('detach-driver',lambda d=driver:setattr(d,'state_change_handler',None))
        for driver in drivers: attempt('disable-driver',driver.disable)
        if mapping and transport:
            attempt('capture-final-outputs',lambda:transport.capture(mapping.outputs()))
            # Mapping remains authoritative even when capture/publish has failed.
            arrays=attempt('read-final-outputs',mapping.outputs)
            if arrays is not None: transport.arrays=[list(a) for a in arrays]
        if output:
            output_result=attempt('close-dmd',output.close) or {}
            if output_result.get('error'): errors.append(output_result['error'])
        if game:
            attempt('end-run-loop',game.end_run_loop)
            if persist and not errors:
                attempt('save-installation-game-data',game.save_game_data)
                attempt('save-installation-settings',game.save_settings)
        attempt('artifacts',artifacts)
        if transport:
            transport.status='ERROR' if errors else 'STOPPED'
            def publish():
                if publish_fault: raise IOError('Injected final publish failure')
                transport.publish()
            attempt('publish-terminal',publish)
            if errors: transport.status='ERROR'
    finally:
        if pygame: attempt('pygame-quit',pygame.quit)
    return output_result,attempts
