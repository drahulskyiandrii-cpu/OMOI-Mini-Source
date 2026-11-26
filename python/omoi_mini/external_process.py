"""Startet externe Worker ohne das DLL-Suchverzeichnis der Frozen-GUI."""
import os
import json
import queue
import subprocess
import sys
import threading

_spawn_lock=threading.Lock()


def spawn_external(*args, **kwargs):
    if os.name!="nt" or not getattr(sys,"frozen",False) or not getattr(sys,"_MEIPASS",None):
        return subprocess.Popen(*args,**kwargs)
    import ctypes
    kernel=ctypes.windll.kernel32
    with _spawn_lock:
        buffer=ctypes.create_unicode_buffer(32768)
        length=kernel.GetDllDirectoryW(len(buffer),buffer)
        if length>=len(buffer):
            raise OSError("DLL_DIRECTORY_TOO_LONG")
        previous=buffer.value if length else None
        if not kernel.SetDllDirectoryW(None):
            raise OSError("DLL_DIRECTORY_RESET_FAILED")
        try:
            return subprocess.Popen(*args,**kwargs)
        finally:
            kernel.SetDllDirectoryW(previous)


def stop_owned_process(process,*,tree=False,timeout=4):
    """Nur einen gespeicherten eigenen Prozess beenden und auf sein Ende warten."""
    if process is None or process.poll() is not None:return
    if tree and os.name=="nt":
        try:
            subprocess.run(["taskkill","/F","/T","/PID",str(process.pid)],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
                check=True,timeout=8)
        except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
            if process.poll() is None:process.terminate()
    else:
        try:process.terminate()
        except OSError:
            if process.poll() is None:raise
    try:process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


def signal_pipe_failure(messages):
    """Ein EOF/Protokollfehler darf niemals an einer vollen Queue haengen."""
    while True:
        try:
            messages.put_nowait({"event":"fatal"})
            return
        except queue.Full:
            try:messages.get_nowait()
            except queue.Empty:pass



def read_worker_events(process,messages,max_line):
    """JSONL begrenzen; bei EOF oder Protokollflut den Worker invalidieren."""
    try:
        if process.stdout is None:return
        while True:
            line=process.stdout.readline(max_line+1)
            if not line:break
            if len(line)>max_line:break
            try:event=json.loads(line)
            except (ValueError,TypeError):continue
            if not isinstance(event,dict):continue
            try:messages.put_nowait(event)
            except queue.Full:break
    except (OSError,ValueError):pass
    finally:signal_pipe_failure(messages)

def finish_worker_pipes(process,reader=None,writer=None):
    """Nach Prozessende Pipes schliessen; niemals auf einer aktiven Pipe blockieren."""
    if process is None:return
    for name,thread in (("stdin",writer),("stdout",reader)):
        if thread is not None:
            if thread is threading.current_thread():continue
            if thread.ident is not None:thread.join(timeout=1)
            if thread.is_alive():continue
        pipe=getattr(process,name,None)
        if pipe is not None:
            try:pipe.close()
            except (OSError,ValueError):pass
