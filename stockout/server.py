"""Start a local app independently of the terminal that launched it."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime'
PID_FILE=RUNTIME/'server.pid'
LOG_FILE=RUNTIME/'server.log'
URL='http://127.0.0.1:8501'


def healthy():
    try:
        with urlopen(URL+'/_stcore/health',timeout=.6) as response:
            return response.status==200 and response.read().strip()==b'ok'
    except (URLError,OSError):
        return False


def saved_process():
    try:
        pid=int(PID_FILE.read_text().strip())
        cwd=Path(os.readlink(f'/proc/{pid}/cwd')).resolve()
        args=Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        return pid if cwd==ROOT and b'streamlit' in args and b'app.py' in args else None
    except (OSError,ValueError):
        return None


def main():
    parser=argparse.ArgumentParser(description='Run Stockout Shield locally.')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--foreground',action='store_true',help='Run attached to the terminal.')
    mode.add_argument('--stop',action='store_true',help='Stop only the server started by this launcher.')
    args=parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    pid=saved_process()
    if args.stop:
        if pid:
            os.kill(pid,signal.SIGTERM)
            for _ in range(30):
                if saved_process() is None:break
                time.sleep(.1)
            if saved_process():
                raise SystemExit('Server is still shutting down. Try again in a few seconds.')
            PID_FILE.unlink(missing_ok=True)
            print('Stockout Shield stopped.')
        else:print('No launcher-managed Stockout Shield server is running.')
        return
    if healthy():
        print(f'Stockout Shield is available at {URL}' if pid else f'Port 8501 is already serving an app. Open {URL} to check it; no second process was started.')
        return
    if pid:raise SystemExit('The server is starting or unresponsive. Check runtime/server.log before restarting it.')
    command=[sys.executable,'-m','streamlit','run','app.py','--server.address','127.0.0.1','--server.port','8501','--server.fileWatcherType','none']
    if args.foreground:
        os.chdir(ROOT)
        os.execv(sys.executable,command)
    with LOG_FILE.open('ab') as log:
        child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True)
    PID_FILE.write_text(str(child.pid))
    for _ in range(40):
        if healthy():
            print(f'Stockout Shield is running at {URL}\nLog: {LOG_FILE}\nStop: ./start-stockout-shield.sh --stop')
            return
        if child.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise SystemExit(f'The server could not start. See {LOG_FILE}')
        time.sleep(.2)
    raise SystemExit(f'Server startup is taking longer than expected. See {LOG_FILE}')


if __name__=='__main__':
    main()
