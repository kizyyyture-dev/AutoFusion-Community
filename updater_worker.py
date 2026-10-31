"""Runs OUTSIDE the AutoFusion install directory during a verified update."""
from __future__ import annotations
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _wait_pid_windows(pid: int, timeout_ms=60000):
    if sys.platform != 'win32':
        end=time.monotonic()+timeout_ms/1000
        while time.monotonic() < end:
            try: os.kill(pid,0)
            except ProcessLookupError: return
            time.sleep(.2)
        raise TimeoutError('原程序未能及时退出')
    SYNCHRONIZE = 0x00100000
    k=ctypes.windll.kernel32
    handle=k.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return
    try:
        result=k.WaitForSingleObject(handle,timeout_ms)
        if result!=0:
            raise TimeoutError('等待 AutoFusion 退出超时')
    finally:
        k.CloseHandle(handle)


def _rename_retry(old:Path, new:Path, attempts=20):
    last=None
    for _ in range(attempts):
        try:
            old.rename(new)
            return
        except OSError as e:
            last=e; time.sleep(.5)
    raise last


def apply_update(plan: dict, *, wait_for_parent=True, health_timeout=25):
    target=Path(plan['target']).resolve()
    stage=Path(plan['stage']).resolve()
    workdir=Path(plan['workdir']).resolve()
    health=Path(plan['health_file']).resolve()
    python=Path(plan['python'])
    version=plan['version']
    if workdir != stage.parent or workdir not in health.parents or target == stage or target.name.lower()=='fusion':
        raise ValueError('更新计划中的目录不安全')
    if any((target/x).exists() for x in ('fusion','sra','ok-ww','_downloads')):
        raise ValueError('目标目录包含用户数据，拒绝覆盖')
    if not stage.is_dir() or not (stage/'app.py').is_file() or not (target/'app.py').is_file():
        raise ValueError('更新所需文件不存在')
    if wait_for_parent:
        _wait_pid_windows(int(plan['parent_pid']))
    backup=target.with_name(target.name + '.af-backup-' + str(os.getpid()))
    if backup.exists():
        raise ValueError('备份目录已存在，拒绝覆盖')
    newproc=None
    switched=False
    try:
        _rename_retry(target,backup)
        try:
            _rename_retry(stage,target)
            switched=True
        except Exception:
            _rename_retry(backup,target)
            raise
        newproc=subprocess.Popen([str(python),str(target/'app.py'),'--af-update-health',str(health)],
                                cwd=str(target), creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        stop=time.monotonic()+health_timeout
        while time.monotonic()<stop:
            if health.is_file():
                try:
                    marker=json.loads(health.read_text(encoding='utf-8'))
                    if marker.get('version')==version and marker.get('ready') is True:
                        time.sleep(0.7)
                        if newproc.poll() is not None:
                            raise RuntimeError('新版窗口刚启动就退出，尝试恢复旧版')
                        shutil.rmtree(backup)  # once health passes, delete OLD version
                        print('UPDATE_SUCCESS',version,flush=True)
                        return True
                except (OSError,ValueError):
                    pass
            if newproc.poll() is not None:
                raise RuntimeError('新版本启动失败，正在恢复旧版')
            time.sleep(.4)
        raise TimeoutError('新版本启动验证超时，尝试恢复旧版')
    except Exception:
        if switched and backup.exists():
            if newproc and newproc.poll() is None:
                newproc.terminate()
                try: newproc.wait(timeout=8)
                except subprocess.TimeoutExpired: newproc.kill()
            failed=target.with_name(target.name+'.af-failed-'+str(os.getpid()))
            try:
                _rename_retry(target,failed)
                _rename_retry(backup,target)
                shutil.rmtree(failed,ignore_errors=True)
                subprocess.Popen([str(python),str(target/'app.py')],cwd=str(target),
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                print('UPDATE_ROLLED_BACK',flush=True)
            except Exception as e:
                print('ROLLBACK_FAILED',repr(e),'BACKUP:',backup,flush=True)
        raise


def main():
    planfile=Path(sys.argv[1]).resolve()
    plan=json.loads(planfile.read_text(encoding='utf-8'))
    workdir=Path(plan['workdir']).resolve()
    os.chdir(workdir)
    try:
        apply_update(plan)
    except Exception as e:
        print('UPDATE_FAILED',repr(e),flush=True)
        raise
    finally:
        try: shutil.rmtree(workdir)
        except OSError: pass

if __name__=='__main__':
    main()
