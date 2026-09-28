"""Launch the repository portal using explicit external credential and state paths."""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--port', type=int, default=19000)
    args, server_args = parser.parse_known_args()
    url = f'http://127.0.0.1:{args.port}/'
    def ready():
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status == 200 and b'portal.js' in response.read()
        except Exception:
            return False
    if ready():
        print(json.dumps({'url': url, 'already_running': True}), flush=True)
        return
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open('ab') as log:
        process = subprocess.Popen([sys.executable, str(ROOT/'server.py'), '--port', str(args.port), *server_args],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(45):
        if ready():
            print(json.dumps({'url': url, 'pid': process.pid}), flush=True)
            return
        if process.poll() is not None:
            raise SystemExit('启动失败，请查看指定的日志文件')
        time.sleep(1)
    raise SystemExit('连接仍未就绪，请检查 Spark 网络；进程可能仍在连接，请先检查状态，不要重复启动')

if __name__ == '__main__':
    main()
