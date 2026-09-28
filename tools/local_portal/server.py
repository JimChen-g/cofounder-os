"""Local owner-bound UI for the existing Spark runtime. No model/backend deployment."""
from __future__ import annotations
import argparse, datetime, http.client, http.server, importlib.util, json, mimetypes, os, re, secrets, shlex, threading
import time
from email.utils import parsedate_to_datetime
from collections import Counter
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
UI = BASE / 'app/ui/static'
spec = importlib.util.spec_from_file_location('existing_spark', BASE/'scripts/engineering_local_proxy.py')
existing = importlib.util.module_from_spec(spec); spec.loader.exec_module(existing)
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
PROTECTED = existing.PROTECTED_RUN
ASSETS = {'/ui/assets/app.js','/ui/assets/app.css','/ui/assets/engineering.js','/ui/assets/engineering.css','/ui/static/display-state.js','/ui/assets/display-state.js'}

def owner_summary(report, owner, inventory=None):
    # Join persisted facts; the shared browser helper derives display state.
    facts = {r['id']: r for r in (inventory or {}).get('runs', []) if r.get('owner') == owner}
    visible = []
    for original in report.get('recent_runs', []):
        if original.get('owner') != owner:
            continue
        row = dict(original)
        fact = facts.get(str(row.get('run_id')), {})
        for key in ('engineering', 'delivery_state', 'expires_at', 'revision', 'termination_reason'):
            if key in fact:
                row[key] = fact[key]
        if fact:
            row['status'] = fact['status']
        if row.get('engineering'):
            row['overall_score'] = None
            row['grade'] = 'not_applicable'
        visible.append(row)
    rows = [r for r in visible if not r.get('engineering')]
    total=lambda key:sum(r[key] for r in rows)
    pct=lambda a,b:round(100*a/b,1) if b else 0.0
    agents={}
    for r in rows:
        for a in r.get('agent_performance',[]):
            dst=agents.setdefault(a['agent_id'],dict(agent_id=a['agent_id'],tasks=0,completed=0,failed=0,retries=0,attempts=0))
            for k in ('tasks','completed','failed','retries'):dst[k]+=a[k]
            dst['attempts']+=a['average_attempts']*a['tasks']
    for a in agents.values():
        a['success_rate']=pct(a['completed'],a['tasks'])
        a['average_attempts']=round(a.pop('attempts')/a['tasks'],2) if a['tasks'] else 0
    return dict(schema_version='1.0',generated_at=report['generated_at'],run_count=len(rows),
        completion_rate=pct(sum(r['status']=='completed' for r in rows),len(rows)),
        average_score=round(total('overall_score')/len(rows),1) if rows else 0,
        task_success_rate=pct(total('completed_tasks'),total('task_count')),
        artifact_integrity_rate=pct(total('verified_artifact_count'),total('artifact_count')),
        total_retries=total('retry_count'),status_distribution=dict(Counter(r['status'] for r in rows)),
        grade_distribution=dict(Counter(r['grade'] for r in rows)),
        provider_distribution=dict(Counter(p for r in rows for p in r['providers'])),
        agent_performance=list(agents.values()),recent_runs=visible,engineering_run_count=len(visible)-len(rows))

def permitted(method, target):
    u = urlsplit(target)
    if u.scheme or u.netloc or u.fragment or '%' in target or '..' in u.path or '\\' in target:
        return False
    q = parse_qs(u.query, keep_blank_values=True)
    p = u.path
    if method == 'GET':
        if p in {'/','/ui','/ui/','/ui/engineering'}:
            return set(q) <= {'run'} and all(len(v)==1 and re.fullmatch(UUID,v[0]) for v in q.values())
        if p in ASSETS:
            return set(q) <= {'v'} and all(len(v)==1 and re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}',v[0]) for v in q.values())
        if p in {'/portal.css','/portal.js','/local/status','/local/runs','/api/health','/api/insurance-poc/fixture','/api/insurance-poc/evaluation'}:
            return not q
        if re.fullmatch(r'/api/engineering/runs/'+UUID+r'(/(candidate|delivery|export))?',p):
            return not q
        if re.fullmatch(r'/api/runs/'+UUID+r'(/(events|artifacts))?',p):
            allowed = {'limit'} if p.endswith('/events') else {'include_content'} if p.endswith('/artifacts') else {'event_limit'}
            return set(q)<=allowed and all(len(v)==1 and ((v[0] in ('true','false')) if k=='include_content' else v[0].isdigit() and 0<=int(v[0])<=1000) for k,v in q.items())
        if re.fullmatch(r'/api/insurance-poc/run-jobs/'+UUID,p) or re.fullmatch(r'/api/evaluation/runs/'+UUID,p):
            return not q
        if p=='/api/evaluation/summary':
            return set(q)<={'limit'} and all(len(v)==1 and v[0].isdigit() and 1<=int(v[0])<=200 for v in q.values())
    if method=='POST' and not q:
        return p in {'/api/engineering/runs','/api/runs','/api/insurance-poc/evidence','/api/insurance-poc/routing','/api/insurance-poc/run-jobs'} or bool(re.fullmatch(r'/api/engineering/runs/'+UUID+r'/(feedback|approve|reject|cancel|interrupt)',p)) or bool(re.fullmatch(r'/api/runs/'+UUID+r'/(retry|approvals/'+UUID+r')',p))
    return False

class Spark(existing.Spark):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.cookies = {}
        self.cookie_lock = threading.Lock()
        self.remote_root = '/home/Developer/cofounder-t07-t10'
        self.inventory_lock = threading.Lock()
        self.inventory_cache = {}
    def request(self, method, path, body=b''):
        client=self.connection()
        channel=client.get_transport().open_channel('direct-tcpip',('127.0.0.1',9000),('127.0.0.1',0),timeout=15)
        channel.settimeout(180 if method=='POST' else 30)
        try:
            with self.cookie_lock:
                self.cookies={k:v for k,v in self.cookies.items() if v[1]>time.time()}
                cookies='; '.join(k+'='+v[0] for k,v in self.cookies.items())
            headers=(f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:9000\r\nAuthorization: Bearer {self.token}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n')
            if cookies: headers += 'Cookie: '+cookies+'\r\n'
            channel.sendall((headers+'\r\n').encode()+body)
            response=http.client.HTTPResponse(channel);response.begin();payload=response.read(32*1024*1024+1)
            if len(payload)>32*1024*1024:raise ValueError('response_too_large')
            for k,v in response.getheaders():
                if k.lower()=='set-cookie':
                    cookie=SimpleCookie();cookie.load(v)
                    with self.cookie_lock:
                        for name,m in cookie.items():
                            if re.fullmatch(r'cofounder_approval_[0-9a-f]{32}',name):
                                expiry=time.time()+3600
                                if m['expires']:expiry=parsedate_to_datetime(m['expires']).timestamp()
                                if m['max-age']:expiry=time.time()+int(m['max-age'])
                                if expiry<=time.time():self.cookies.pop(name,None)
                                else:self.cookies[name]=(m.value,expiry)
            return response.status,response.getheader('Content-Type','application/json'),payload
        finally:channel.close()
    def inventory(self, owner):
        # Coalesce concurrent status/list/summary reads; writes invalidate immediately.
        with self.inventory_lock:
            cached = self.inventory_cache.get(owner)
            if cached and time.monotonic() - cached[0] < 2:
                return cached[1]
            result = self._inventory(owner)
            self.inventory_cache[owner] = (time.monotonic(), result)
            return result

    def invalidate_inventory(self):
        with self.inventory_lock:
            self.inventory_cache.clear()

    def _inventory(self, owner):
        script = """from pathlib import Path
import json,datetime
from app.state import FileStateRepository
root=Path(REMOTE_ROOT)
repo=FileStateRepository(root/'data/runs')
rows=[]
for r in repo.list_runs():
 if r.owner!=OWNER:continue
 d=r.metadata.get('delivery') or {}
 rows.append({'id':str(r.id),'objective':r.objective,'status':str(r.status),'owner':r.owner,'created_at':r.created_at.isoformat(),'updated_at':r.updated_at.isoformat(),'engineering':bool(r.metadata.get('engineering')),'request_id':r.metadata.get('engineering_request_id'),'delivery_state':d.get('state'),'revision':d.get('revision'),'expires_at':d.get('expires_at'),'termination_reason':r.metadata.get('termination_reason')})
print(json.dumps({'runs':rows[:200],'commit':(root/'DEPLOYED_COMMIT').read_text().strip(),'bridge':json.loads((root/'bridge/status.json').read_text()).get('connection'),'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
""".replace('REMOTE_ROOT',repr(self.remote_root)).replace('OWNER',repr(owner))
        client=self.connection()
        command = 'cd ' + shlex.quote(self.remote_root + '/src') + ' && ../venv/bin/python -'
        stdin,stdout,stderr=client.exec_command(command,timeout=25)
        stdin.write(script);stdin.channel.shutdown_write();data=stdout.read().decode();stderr.read()
        if stdout.channel.recv_exit_status()!=0:raise RuntimeError('inventory_unavailable')
        return json.loads(data)

class Portal(http.server.ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,port,spark):
        self.spark=spark;self.session=secrets.token_urlsafe(32);self.write_lock=threading.Lock();self.jobs=set()
        status,_,raw=spark.request('GET','/api/engineering/runs/'+PROTECTED)
        if status!=200:raise RuntimeError('Cannot establish authenticated founder identity')
        self.owner=json.loads(raw)['snapshot']['run']['owner']
        super().__init__(('127.0.0.1',port),Handler)
        self.origin=f'http://127.0.0.1:{self.server_port}'
    def run(self,rid):
        status,_,raw=self.spark.request('GET','/api/runs/'+rid+'?event_limit=0')
        if status!=200:raise LookupError('Run不存在或不属于当前账号')
        run=json.loads(raw)['run']
        if run['owner']!=self.owner:raise LookupError('Run不属于当前账号')
        return run

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,data,content_type='application/json',session=False):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
        if session:self.send_header('Set-Cookie',f'spark_ui={self.server.session}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def error(self,code,message):self.reply(code,{'error':message})
    def do_GET(self):self.dispatch()
    def do_POST(self):self.dispatch()
    def dispatch(self):
        u=urlsplit(self.path);p=u.path;origin=self.server.origin;landing=self.command=='GET' and p in {'/','/ui','/ui/','/ui/engineering'}
        if self.headers.get('Host')!=urlsplit(origin).netloc or self.headers.get('Origin') not in (None,origin) or self.headers.get('Sec-Fetch-Site') in ('cross-site','same-site'):
            return self.error(403,'请从本机正式地址打开界面')
        if not permitted(self.command,self.path):return self.error(403,'此入口未开放该路径或方法')
        c=SimpleCookie()
        try:c.load(self.headers.get('Cookie',''))
        except Exception:return self.error(403,'本机会话无效')
        if not landing and ('spark_ui' not in c or not secrets.compare_digest(c['spark_ui'].value,self.server.session)):
            return self.error(401,'请先打开本机首页')
        if self.command=='POST' and self.headers.get('Origin')!=origin:return self.error(403,'写操作只能来自本机页面')
        if self.headers.get('Transfer-Encoding'):return self.error(400,'不支持流式请求')
        locked=False
        try:
            raw=b''
            if self.command=='POST':
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.error(415,'仅接受JSON')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=12*1024*1024:return self.error(413,'请求为空或超过12MiB')
                raw=self.rfile.read(length);body=json.loads(raw)
                if not isinstance(body,dict):return self.error(400,'需要JSON对象')
                locked=self.server.write_lock.acquire(blocking=False)
                if not locked:return self.error(409,'已有操作正在处理，请先核对结果')
                for key in ('owner','decided_by'):
                    if key in body and body[key]!=self.server.owner:return self.error(403,'身份必须与当前账号一致')
                if p in ('/api/insurance-poc/run-jobs','/api/runs'):body['owner']=self.server.owner
                raw=json.dumps(body,ensure_ascii=False).encode()
            m=re.match(r'/api/(?:engineering/)?runs/('+UUID+')',p) or re.match(r'/api/evaluation/runs/('+UUID+')',p)
            if m:
                run=self.server.run(m.group(1))
                if self.command=='POST':
                    if m.group(1)==PROTECTED:return self.error(409,'已批准的T27历史产物仅供查看')
                    if p.startswith('/api/engineering/') and not existing.version_matches(body,run,'/'+p.rsplit('/',1)[-1]):return self.error(409,'当前版本已变化，请刷新后核对')
            if self.command=='GET' and p.startswith('/api/insurance-poc/run-jobs/') and p.rsplit('/',1)[-1] not in self.server.jobs:
                return self.error(404,'本机会话未创建该工作流；历史任务请从任务列表打开')
            if p in ('/','/ui/engineering','/portal.js','/portal.css'):
                filename={'/':'index.html','/ui/engineering':'index.html','/portal.js':'portal.js','/portal.css':'portal.css'}[p]
                return self.reply(200,(ROOT/'static'/filename).read_bytes(),mimetypes.guess_type(filename)[0]+'; charset=utf-8',session=landing)
            if p in ('/local/status','/local/runs'):
                info=self.server.spark.inventory(self.server.owner)
                if p=='/local/status':
                    status,_,health=self.server.spark.request('GET','/api/health')
                    return self.reply(status,{'connected':status==200,'owner':self.server.owner,'commit':info['commit'],'bridge':info['bridge'],'checked_at':info['checked_at'],'health':json.loads(health),'model':'Qwen · 实际调用身份见任务执行记录'})
                return self.reply(200,info)
            if p in ('/ui','/ui/') or p in ASSETS:
                filename = 'index.html' if p in ('/ui','/ui/') else p.rsplit('/',1)[-1]
                return self.reply(200,(UI/filename).read_bytes(),mimetypes.guess_type(filename)[0]+'; charset=utf-8',session=landing)
            try:
                status,ctype,data=self.server.spark.request(self.command,self.path,raw)
            finally:
                if self.command == 'POST':
                    self.server.spark.invalidate_inventory()
            if 200<=status<300 and self.command=='POST' and p=='/api/insurance-poc/run-jobs':
                job=json.loads(data).get('job_id')
                if job:self.server.jobs.add(str(job))
            if status==200 and p=='/api/evaluation/summary':
                data=json.dumps(owner_summary(json.loads(data),self.server.owner,self.server.spark.inventory(self.server.owner))).encode()
            return self.reply(status,data,ctype,session=landing and status==200)
        except (ValueError,KeyError):self.error(400,'请求或远程返回格式无效，请刷新核对')
        except LookupError as exc:self.error(404,str(exc))
        except Exception:self.error(502,'Spark连接失败；写入结果可能未知，未自动重试。请刷新真实状态后再操作。')
        finally:
            if locked:self.server.write_lock.release()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=19000)
    parser.add_argument('--ssh-host',required=True)
    parser.add_argument('--ssh-port',type=int,default=22)
    parser.add_argument('--ssh-user',required=True)
    parser.add_argument('--ssh-key',type=Path,required=True)
    parser.add_argument('--known-hosts',type=Path,required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--remote-root',default='/home/Developer/cofounder-t07-t10')
    parser.add_argument('--state',type=Path,required=True,help='Runtime state outside the repository')
    args=parser.parse_args()
    spark=Spark(args.ssh_key,args.known_hosts,args.ssh_host,args.ssh_port,args.ssh_user,token_file=args.token_file)
    spark.remote_root=args.remote_root
    server=Portal(args.port,spark)
    args.state.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(args.state,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump({'pid':os.getpid(),'url':server.origin,'owner':server.owner},stream)
    os.chmod(args.state,0o600)
    print(json.dumps({'url':server.origin,'ready':True}),flush=True)
    try:server.serve_forever()
    finally:
        server.server_close()
        if spark.client:spark.client.close()
if __name__=='__main__':main()
