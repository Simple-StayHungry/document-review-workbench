"""Local-only HTTP service. Uploaded document code is never executed."""
from __future__ import annotations
import collections,copy,hashlib,argparse,concurrent.futures,email,email.policy,html,json,mimetypes,os,re,secrets,sys,threading,time,traceback,urllib.parse,uuid,webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
_ROOT_BOOTSTRAP=Path(__file__).resolve().parent
if str(_ROOT_BOOTSTRAP) not in sys.path:sys.path.insert(0,str(_ROOT_BOOTSTRAP))
from workbench.engine import Engine,summarize
from workbench.ingest import Importer,source_exclusion_reason,validate_source_record,validate_material_hash
from workbench.version import BUILD,SCHEMA
from workbench.docxio import w,text
from workbench.plans import build,retained_plan,effective_context
from workbench.model import Block
from workbench.source_policy import inventory,prepare_target

ROOT=Path(__file__).resolve().parent
INSTANCE=hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
class Cancelled(Exception):pass
MAX_REQUEST=320*1024*1024
JOBS=concurrent.futures.ThreadPoolExecutor(max_workers=2,thread_name_prefix='workbench')

def input_signature(files,params):
    records=[{k:f.get(k) for k in ('id','name','role','origin','aliases')} for f in files]
    value={'files':sorted(records,key=lambda f:f['id']),'params':params}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def validate_inputs(files):
    for f in files:
        if f.get('role')=='source':validate_source_record(f)
        if f.get('role') in ('source','target'):validate_material_hash(f)

def public_job(job):
    if not job:return None
    result=dict(job)
    result['elapsed_seconds']=round(max(0,(job.get('finished') or time.time())-job['started']),1)
    return result

def decision_members(result,proposal):
    """One reversible decision includes every copy/deletion linked by a merge."""
    proposals=result['proposals'];ids={proposal['id']}
    while True:
        transactions={x.get('transaction_id',x['id']) for x in proposals if x['id'] in ids}
        parents={x.get('merge_into') for x in proposals if x['id'] in ids and x.get('merge_into')}
        updated=ids|parents|{x['id'] for x in proposals if x.get('transaction_id',x['id']) in transactions or x.get('merge_into') in ids}
        if updated==ids:break
        ids=updated
    return sorted([x for x in proposals if x['id'] in ids],key=lambda x:(x['target_id'],x['start']))

class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.projects={};self.engines={};self.busy=set()
        for f in self.root.glob('*/project.json'):
            try:
                saved=f.read_text('utf-8');p=json.loads(saved)
                if (p.get('job') or {}).get('state')=='running':
                    p['job'].update(state='interrupted',finished=time.time(),message='服务已重启，上一次任务未完成，请重新执行。',error='服务中断')
                for record in p.get('files',[]):
                    local=f.parent/'materials'/(record['id']+'.docx')
                    if local.is_file():record['path']=str(local.resolve())
                if p.get('export'):
                    old=Path(p['export']['zip']);local=f.parent/'exports'/old.name
                    if local.is_file():p['export']['zip']=str(local.resolve())
                    else:p['export']=None
                p.setdefault('undo',[]);p.setdefault('redo',[]);p.setdefault('events',[])
                if p.get('result'):
                    try:
                        if p['result'].get('schema')!=SCHEMA or p['result'].get('engine_version')!=BUILD:
                            raise ValueError('程序版本已变化，请重新分析。')
                        if p.get('input_signature')!=input_signature(p['files'],p['params']):
                            raise ValueError('材料角色或项目参数已变化，请重新分析。')
                        validate_inputs(p['files'])
                    except (OSError,ValueError) as exc:self.invalidate(p,str(exc))
                self.projects[p['id']]=p
                if json.loads(saved)!=p:self.save(p)
            except Exception:continue
    def create(self,name='未命名项目'):
        with self.lock:
            pid=uuid.uuid4().hex
            p={'id':pid,'name':name.strip()[:100] or '未命名项目','created':time.time(),'updated':time.time(),'files':[],'inventory':[],'params':{'author':'柒','issuer':'','bond':'','period':'','date':''},'result':None,'export':None,'comparison':None,'job':None,'undo':[],'redo':[],'events':[]}
            self.projects[pid]=p;self.save(p);return p
    def get(self,pid):
        if not re.fullmatch('[a-f0-9]{32}',pid) or pid not in self.projects:raise ValueError('项目不存在。')
        return self.projects[pid]
    def directory(self,pid):return self.root/pid
    def save(self,p):
        p['updated']=time.time();d=self.directory(p['id']);d.mkdir(parents=True,exist_ok=True)
        tmp=d/'project.tmp';tmp.write_text(json.dumps(p,ensure_ascii=False),encoding='utf-8');tmp.replace(d/'project.json')
    def engine(self,pid):
        if pid not in self.engines:self.engines[pid]=Engine()
        return self.engines[pid]
    def invalidate(self,p,reason):
        p['result']=None;p['export']=None;p['comparison']=None;p['undo']=[];p['redo']=[];p['input_signature']=None
        p['result_invalidated']={'reason':reason,'time':time.time()}
        self.engines.pop(p['id'],None)
    def check_inputs(self,p,require_result=False):
        try:
            validate_inputs(p['files'])
            if require_result and p.get('input_signature')!=input_signature(p['files'],p['params']):
                raise ValueError('分析后材料角色或项目参数已变化，请重新分析。')
        except (OSError,ValueError) as exc:
            self.invalidate(p,str(exc));self.save(p);raise ValueError(str(exc)) from exc
    def refresh_inventory(self,p):
        if not p.get('result'):return
        for record in p['result']['documents']:
            file=next(f for f in p['files'] if f['id']==record['id'])
            doc=self.engine(p['id']).load(file);prepare_target(doc,[])
            record['object_inventory']=inventory(doc,[x for x in p['result']['proposals'] if x['target_id']==record['id']])
            record.setdefault('coverage',{})['dispositions']=dict(collections.Counter(x['status'] for x in record['object_inventory']))
    def public(self,p):
        q={k:v for k,v in p.items() if k not in ('files','export','inventory','undo','redo','events')}
        q['history']={'can_undo':bool(p.get('undo')),'can_redo':bool(p.get('redo')),'count':len(p.get('events',[]))}
        q['files']=[{k:v for k,v in f.items() if k!='path'} for f in p['files']]
        for f in q['files']:f['source_exclusion']=source_exclusion_reason(f)
        q['job']=public_job(p.get('job'))
        q['inventory']=p['inventory'][-500:];q['inventory_total']=len(p['inventory'])
        if p.get('export'):q['export']={k:v for k,v in p['export'].items() if k!='zip'};q['export']['url']='/api/projects/'+p['id']+'/download'
        else:q['export']=None
        return q
    def run(self,p,kind,fn):
        with self.lock:
            if p['id'] in self.busy:raise RuntimeError('本项目正在处理，请在完成后操作。')
            now=time.time()
            self.busy.add(p['id']);p['job']={'kind':kind,'state':'running','stage':'准备处理','progress':0,'message':'准备处理','started':now,'stage_started':now,'updated':now,'error':None};self.save(p)
        def progress(value,msg):
            with self.lock:
                if p['job'].get('cancel_requested'):raise Cancelled('已停止，保留上一次完成的结果。')
                if p['job'].get('stage')!=msg:p['job']['stage_started']=time.time()
                p['job'].update(progress=value,message=msg,stage=msg,updated=time.time())
        def work():
            try:
                result=fn(progress)
                with self.lock:
                    if p['job'].get('cancel_requested'):raise Cancelled('已停止，保留上一次完成的结果。')
                    if kind=='analyze':
                        self.check_inputs(p)
                        p['result']=result;p['export']=None;p['undo']=[];p['redo']=[];p['input_signature']=input_signature(p['files'],p['params']);p.pop('result_invalidated',None)
                    elif kind=='export':self.check_inputs(p,require_result=True);p['export']=result
                    p['job'].update(state='done',progress=100,message={'analyze':'分析完成','export':'导出完成','upload':'材料已整理'}.get(kind,'完成'))
            except Cancelled as ex:
                with self.lock:p['job'].update(state='cancelled',message=str(ex))
            except Exception as ex:
                traceback.print_exc()
                with self.lock:p['job'].update(state='error',message=str(ex) or '处理失败，请检查材料格式。',error=str(ex),error_type=type(ex).__name__)
            finally:
                with self.lock:
                    p['job']['finished']=time.time();p['job']['updated']=p['job']['finished'];p['job']['elapsed_seconds']=round(p['job']['finished']-p['job']['started'],1)
                    self.busy.discard(p['id']);self.save(p)
        JOBS.submit(work)

class Handler(BaseHTTPRequestHandler):
    server_version='Workbench/1.0'
    def log_message(self,fmt,*args):
        if '/status' not in args[0] if args else True:super().log_message(fmt,*args)
    @property
    def store(self):return self.server.store
    def reply(self,data,status=200,kind='application/json; charset=utf-8'):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        elif isinstance(data,str):data=data.encode('utf-8')
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def jsonbody(self):
        n=int(self.headers.get('Content-Length','0'))
        if n>2*1024*1024:raise ValueError('请求过大。')
        return json.loads(self.rfile.read(n) or '{}')
    def authorized(self):
        host=self.headers.get('Host','')
        if host not in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}',f'[::1]:{self.server.server_port}'}:return False
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+host}:return False
        fetch=self.headers.get('Sec-Fetch-Site')
        if fetch=='cross-site':return False
        return True
    def do_GET(self):
        try:self.get()
        except (ValueError,KeyError) as e:self.reply({'error':str(e)},400)
        except Exception as e:traceback.print_exc();self.reply({'error':'处理请求失败：'+str(e)},500)
    def get(self):
        if not self.authorized():return self.reply({'error':'仅允许本机页面访问。'},403)
        url=urllib.parse.urlsplit(self.path);path=url.path;query=urllib.parse.parse_qs(url.query)
        if path=='/api/health':return self.reply({'ok':True,'app':'local-verification-workbench','offline_processing':True,'build':BUILD,'instance':INSTANCE})
        if path=='/api/projects':
            with self.store.lock:ps=[{'id':p['id'],'name':p['name'],'updated':p['updated'],'files':len(p['files'])} for p in self.store.projects.values()]
            return self.reply(sorted(ps,key=lambda p:p['updated'],reverse=True))
        m=re.fullmatch(r'/api/projects/([a-f0-9]{32})(?:/(\w+))?',path)
        if m:
            pid,action=m.groups();p=self.store.get(pid)
            if not action:return self.reply(self.store.public(p))
            if action=='status':return self.reply({'job':public_job(p['job'])})
            if action=='download':
                if not p.get('export'):raise ValueError('尚未生成导出包。')
                file=Path(p['export']['zip']).resolve()
                if not file.is_relative_to(self.store.directory(pid).resolve()) or not file.is_file():raise ValueError('导出文件不存在，请重新导出。')
                raw=file.read_bytes();self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Disposition',"attachment; filename=verification-results.zip; filename*=UTF-8''"+urllib.parse.quote(p['name']+'_核查修订包.zip'));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
            if action=='plan':
                if not p.get('result'):raise ValueError('请先分析材料。')
                proposal=next((x for x in p['result']['proposals'] if x['id']==query.get('proposal',[''])[0]),None)
                if not proposal:raise ValueError('替换计划不存在。')
                selected=int(query.get('selected',[str(proposal.get('selected') or 0)])[0])
                candidates=proposal.get('candidates',[])
                if candidates and not 0<=selected<len(candidates):raise ValueError('候选来源不存在。')
                f=next(x for x in p['files'] if x['id']==proposal['target_id']);engine=self.store.engine(pid);doc=engine.load(f)
                txid=proposal.get('transaction_id',proposal['id'])
                txmembers=sorted([x for x in p['result']['proposals'] if x.get('transaction_id',x['id'])==txid],key=lambda x:x['start'])
                if not txmembers:txmembers=[proposal]
                lo,hi=min(x['start'] for x in txmembers),max(x['end'] for x in txmembers)
                matter=next((m for m in doc.matters if m['start']<=lo<m['end']),None)
                full=query.get('full',['0'])[0]=='1'
                before=max(matter['start'] if matter else 0,lo-3);after=min(matter['end'] if matter else len(doc.blocks),hi+3)
                if full:before=matter['start'] if matter else max(0,lo-30);after=matter['end'] if matter else min(len(doc.blocks),hi+30)
                show=lambda d,file,bs:[self.block_preview(pid,file,d,b) for b in bs]
                payload={'target_name':doc.name,'target_locator':proposal['locator'],'target_range':[lo+1,hi],'transaction_id':txid,'transaction_members':len(txmembers),
                         'before':show(doc,f,doc.blocks[before:lo]),'original':show(doc,f,doc.blocks[lo:hi]),'after':show(doc,f,doc.blocks[hi:after]),
                         'target_full':full,'protected':[], 'replacement':[], 'source':None,'error':'','digest':'',
                         'action':proposal.get('action','source_copy'),'merge_into':proposal.get('merge_into'),
                         'linked_decisions':[x['id'] for x in decision_members(p['result'],proposal)]}
                if proposal.get('action')=='source_merge':
                    payload['deletion_only']=proposal.get('decision') not in ('keep','protected')
                    payload['action_message']='此旧段已合并到前段，导出删除修订，无重复插入。'
                try:
                    payload['effective_before']=[self.block_preview(pid,f,doc,Block(-1,e,'table' if e.tag==w('tbl') else 'paragraph',text(e))) for e in effective_context(engine,proposal,p['result'],p['files'],before,lo)]
                    payload['effective_after']=[self.block_preview(pid,f,doc,Block(-1,e,'table' if e.tag==w('tbl') else 'paragraph',text(e))) for e in effective_context(engine,proposal,p['result'],p['files'],hi,after)]
                except ValueError as ex:payload['context_error']=str(ex)
                if matter:
                    retained=[b for b in doc.blocks[matter['start']:matter['end']] if b.protected and not b.heading and b.text and b.section in ('proc','concl')]
                    payload['protected']=show(doc,f,retained)
                if candidates:
                    c=candidates[selected];sf=next(x for x in p['files'] if x['id']==c['doc_hash']);sd=engine.load(sf)
                    txsources=[]
                    same_source=True
                    for member in txmembers:
                        mi=selected if member['id']==proposal['id'] else member.get('selected')
                        if not isinstance(mi,int) or not member.get('candidates') or not 0<=mi<len(member['candidates']):continue
                        mc=member['candidates'][mi]
                        if mc.get('doc_hash')!=c.get('doc_hash'):same_source=False;break
                        txsources.append(mc)
                    if same_source and txsources:
                        sl=min(x['start'] for x in txsources);sh=max(x['end'] for x in txsources)
                        source_blocks=[]
                        for x in txsources:source_blocks.extend(sd.blocks[i] for i in x.get('indices',range(x['start'],x['end'])))
                    else:
                        sl,sh=c['start'],c['end'];source_blocks=[sd.blocks[i] for i in c.get('indices',range(sl,sh))]
                    sb=max(0,sl-3);sa=min(len(sd.blocks),sh+3)
                    if full:
                        sb=next((i for i in range(sl-1,max(-1,sl-100),-1) if sd.blocks[i].heading),max(0,sl-20))
                        sa=next((i for i in range(sh,min(len(sd.blocks),sh+100)) if sd.blocks[i].heading),min(len(sd.blocks),sh+30))
                    payload['source']={'name':sf['name'],'locator':c['locator'],'range':[sl+1,sh],'span':c.get('span'),'identity':c.get('identity'),
                        'before':show(sd,sf,sd.blocks[sb:sl]),'original':show(sd,sf,source_blocks),'after':show(sd,sf,sd.blocks[sh:sa])}
                if candidates or proposal['kind'] in ('metadata','field','voice'):
                    try:
                        chosen=(proposal.get('candidates') or [{}])[selected or 0]
                        if chosen.get('blocked_reason'):
                            retained=retained_plan(engine,proposal,p['files'])
                            payload['retained_after']=self.planned_preview(pid,engine,proposal,p['files'],retained,selected)
                            raise ValueError(chosen['blocked_reason'])
                        planned_parts=[]
                        for member in txmembers:
                            msel=member.get('selected')
                            if member['id']==proposal['id']:msel=selected
                            if member.get('decision') in ('keep','protected'):
                                plan=retained_plan(engine,member,p['files'])
                            elif member.get('candidates') or member.get('kind') in ('metadata','field','voice'):
                                plan=build(engine,member,p['files'],msel)
                            else:plan=retained_plan(engine,member,p['files'])
                            planned_parts.extend(self.planned_preview(pid,engine,member,p['files'],plan,msel))
                        payload['digest']='transaction:'+txid
                        payload['replacement']=planned_parts
                    except ValueError as ex:payload['error']=str(ex)
                return self.reply(payload)
            if action=='preview':
                fid=query.get('file',[''])[0];f=next((x for x in p['files'] if x['id']==fid),None)
                if not f:raise ValueError('材料不存在。')
                d=self.store.engine(pid).load(f);lo=max(0,int(query.get('start',['0'])[0]));hi=min(len(d.blocks),int(query.get('end',[str(lo+8)])[0]),lo+100)
                indices=query.get('indices',[''])[0]
                requested=[int(i) for i in indices.split(',')] if indices else list(range(lo,hi))
                if len(requested)>150 or any(i<0 or i>=len(d.blocks) for i in requested):raise ValueError('预览范围无效。')
                return self.reply({'name':d.name,'profile':d.profile,'blocks':[self.block_preview(pid,f,d,d.blocks[i]) for i in requested]})
            if action=='image':
                fid=query.get('file',[''])[0];bi=int(query.get('block',['-1'])[0]);ii=int(query.get('index',['0'])[0]);f=next((x for x in p['files'] if x['id']==fid),None)
                if not f:raise ValueError('材料不存在。')
                d=self.store.engine(pid).load(f)
                if not 0<=bi<len(d.blocks):raise ValueError('图像位置无效。')
                ims=d.images(d.blocks[bi])
                if not 0<=ii<len(ims):raise ValueError('未找到图像。')
                part,data=ims[ii];kind=mimetypes.guess_type(part)[0] or 'application/octet-stream'
                return self.reply(data,kind=kind)
            if action=='inventory':return self.reply(p['inventory'])
            if action=='history':return self.reply(p.get('events',[]))
            if action=='sourcesearch':
                q=query.get('q',[''])[0].strip()
                if not q:return self.reply([])
                out=[]
                for f in p['files']:
                    if f['role']!='source':continue
                    d=self.store.engine(pid).load(f)
                    for b in d.blocks:
                        if q in b.text:
                            out.append({'file':f['id'],'name':f['name'],'start':b.index,'end':min(b.index+4,len(d.blocks)),'locator':' > '.join(b.path[-4:]),'text':b.text[:1400]})
                            if len(out)>=50:return self.reply(out)
                return self.reply(out)
            raise ValueError('接口不存在。')
        if path in ('/','/index.html','/app.js','/style.css'):
            name='index.html' if path=='/' else path[1:];fp=ROOT/'web'/name
            return self.reply(fp.read_bytes(),kind=mimetypes.guess_type(name)[0]+('; charset=utf-8' if name.endswith(('.html','.css','.js')) else ''))
        self.reply({'error':'页面不存在。'},404)
    def planned_preview(self,pid,engine,proposal,files,plan,selected=None):
        """Resolve copied media against its real package and physical block."""
        if not plan['new']:return []
        if plan.get('source') is not None:
            f=next(x for x in files if x['id']==plan['source_hash'])
            ci=proposal.get('selected') if selected is None else selected
            candidate=proposal['candidates'][0 if ci is None else ci]
            indices=([candidate['span']['block']] if candidate.get('span') else
                     list(candidate.get('indices',range(candidate['start'],candidate['end']))))
        else:
            f=next(x for x in files if x['id']==proposal['target_id'])
            indices=list(range(proposal['start'],proposal['end']))[:len(plan['new'])]
        if len(indices)!=len(plan['new']):raise ValueError('预览对象与真实来源位置数量不一致。')
        doc=engine.load(f)
        return [self.block_preview(pid,f,doc,Block(i,e,'table' if e.tag==w('tbl') else 'paragraph',text(e)))
                for i,e in zip(indices,plan['new'])]

    def block_preview(self,pid,f,d,b):
        result={'index':b.index,'kind':b.kind,'text':b.text,'heading':b.heading,'protected':b.protected,'reason':b.reason,'images':[]}
        if b.kind=='table':
            rows=[]
            for tr in b.el.findall(w('tr')):
                row=[]
                for tc in tr.findall(w('tc')):
                    gs=tc.find('./'+w('tcPr')+'/'+w('gridSpan'));vm=tc.find('./'+w('tcPr')+'/'+w('vMerge'))
                    row.append({'text':text(tc),'colspan':int(gs.get(w('val'),'1')) if gs is not None else 1,'vertical':vm.get(w('val'),'continue') if vm is not None else ''})
                rows.append(row)
            result['rows']=rows
        for i,(part,data) in enumerate(d.images(b)):
            result['images'].append('/api/projects/'+pid+'/image?file='+f['id']+'&block='+str(b.index)+'&index='+str(i))
        return result
    def do_POST(self):
        try:self.post()
        except RuntimeError as e:self.reply({'error':str(e)},409)
        except (ValueError,KeyError,json.JSONDecodeError) as e:self.reply({'error':str(e)},400)
        except Exception as e:traceback.print_exc();self.reply({'error':'处理请求失败：'+str(e)},500)
    def post(self):
        if not self.authorized():return self.reply({'error':'仅允许本机页面操作，已阻止跨站请求。'},403)
        path=urllib.parse.urlsplit(self.path).path
        if path=='/api/projects':
            p=self.store.create(self.jsonbody().get('name','新项目'));return self.reply(self.store.public(p),201)
        m=re.fullmatch(r'/api/projects/([a-f0-9]{32})/(\w+)',path)
        if not m:return self.reply({'error':'接口不存在。'},404)
        pid,action=m.groups();p=self.store.get(pid)
        if action=='cancel':
            if (p.get('job') or {}).get('state')=='running' and p['job']['kind'] in ('analyze','export','compare'):
                with self.store.lock:p['job']['cancel_requested']=True;p['job']['message']='正在安全停止，原始材料不受影响';self.store.save(p)
                return self.reply({'job':p['job']})
            raise ValueError('当前没有可停止的分析任务。')
        # Upload is exempt here on purpose: it must read the whole request body
        # first, and it handles a busy project itself by waiting. Rejecting it
        # before the body is read resets the connection while the browser is
        # still sending megabytes, which is what turned an append into 上传中断
        # and silently dropped the whole batch.
        if pid in self.store.busy and action!='upload':raise RuntimeError('本项目正在处理，请在完成后操作。')
        if action=='upload':
            n=int(self.headers.get('Content-Length','0'))
            if n<=0 or n>MAX_REQUEST:raise ValueError('单次上传请控制在 320 MB 以内。')
            ctype=self.headers.get('Content-Type','')
            if 'multipart/form-data' not in ctype:raise ValueError('上传格式错误。')
            raw=self.rfile.read(n)
            msg=email.message_from_bytes(('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw,policy=email.policy.default)
            inputs=[];role=None
            for part in msg.iter_parts():
                field=part.get_param('name',header='content-disposition');name=part.get_filename()
                if name:inputs.append((name,part.get_payload(decode=True)))
                elif field=='role':role=part.get_content().strip()
            if not inputs:raise ValueError('没有收到文件。')
            # An append can land while the previous import is still running. The
            # body is already fully read, so wait here instead of resetting the
            # connection and silently dropping the whole batch.
            deadline=time.time()+120
            while pid in self.store.busy and time.time()<deadline:time.sleep(0.4)
            if pid in self.store.busy:raise RuntimeError('上一次导入还在进行，请等它完成后再追加材料。')
            def upload(progress):
                im=Importer(self.store.directory(pid)/'materials',p['files'])
                for i,(name,data) in enumerate(inputs):
                    progress(5+int(i/max(1,len(inputs))*80),'正在整理 '+name)
                    im.add(name,data,role)
                progress(91,'正在去重并整理底稿和来源');im.recommend()
                with self.store.lock:
                    p['files']=im.files;p['inventory'].extend(im.inventory);self.store.invalidate(p,'材料已导入，请重新分析。')
                    sources=[f for f in p['files'] if f['role']=='source']
                    issuers={f['profile']['issuer'] for f in sources if f['profile']['issuer']}
                    if len(issuers)==1 and not p['params'].get('issuer'):p['params']['issuer']=next(iter(issuers))
                return None
            self.store.run(p,'upload',upload);return self.reply({'job':p['job']},202)
        body=self.jsonbody()
        if action=='settings':
            with self.store.lock:
                if pid in self.store.busy:raise RuntimeError('本项目正在处理，请在完成后操作。')
                before_config=input_signature(p['files'],p['params'])
                # Validate every role before applying any change in this request.
                for fid,role in body.get('roles',{}).items():
                    if role not in ('target','source','reference','candidate','ignored'):raise ValueError('材料用途无效。')
                    f=next((f for f in p['files'] if f['id']==fid),None)
                    if not f:raise ValueError('材料不存在。')
                    if role=='source':validate_source_record(f)
                if 'name' in body:p['name']=str(body['name']).strip()[:100] or '新项目'
                if 'params' in body:
                    for k in ('issuer','author','bond','period','date'):
                        if k in body['params']:p['params'][k]=str(body['params'][k]).strip()[:240]
                    if 'allow_unknown_source' in body['params']:p['params']['allow_unknown_source']=bool(body['params']['allow_unknown_source'])
                for fid,role in body.get('roles',{}).items():
                    if role not in ('target','source','reference','candidate','ignored'):raise ValueError('材料用途无效。')
                    f=next((f for f in p['files'] if f['id']==fid),None)
                    if not f:raise ValueError('材料不存在。')
                    f['role']=role;f['role_locked']=True
                if before_config!=input_signature(p['files'],p['params']):self.store.invalidate(p,'材料用途或项目参数已变化，请重新分析。')
                self.store.save(p)
            return self.reply(self.store.public(p))
        if action=='analyze':
            self.store.check_inputs(p)
            def converge(progress):
                result=self.store.engine(pid).analyze(p['files'],p['params'],progress)
                from workbench.missing_pipeline import converge_pending
                converge_pending(result,self.store.engine(pid),p['files'])
                result['summary']=summarize(result)
                return result
            self.store.run(p,'analyze',converge);return self.reply({'job':p['job']},202)
        if action in ('undo','redo'):
            with self.store.lock:
                stack=p.setdefault(action,[])
                if not p.get('result') or not stack:raise ValueError('没有可'+('撤销' if action=='undo' else '重做')+'的决定。')
                event=stack.pop();changes=event.get('changes')
                if changes is None:changes=[{'id':event['id'],'before':event['before'],'after':event['after']}]
                for change in changes:
                    proposal=next((x for x in p['result']['proposals'] if x['id']==change['id']),None)
                    if proposal is None:raise ValueError('分析已变更，请重新核对。')
                    proposal.update(change['before'] if action=='undo' else change['after'])
                p.setdefault('redo' if action=='undo' else 'undo',[]).append(event)
                p.setdefault('events',[]).append({'type':action,'transaction_id':event.get('transaction_id'),'count':len(changes),'time':time.time()})
                self.store.refresh_inventory(p);p['result']['summary']=summarize(p['result']);p['export']=None;self.store.save(p)
            return self.reply(self.store.public(p))
        if action=='decision':
            if not p['result']:raise ValueError('请先开始分析。')
            proposal=next((x for x in p['result']['proposals'] if x['id']==body.get('id')),None)
            if proposal is None:raise ValueError('改动项不存在。')
            decision=body.get('decision')
            if decision not in ('accept','keep','pending'):raise ValueError('决定无效。')
            txid=proposal.get('transaction_id',proposal['id'])
            members=decision_members(p['result'],proposal)
            if any(x['status']=='protected' for x in members):raise ValueError('该来源范围包含受保护判断，不能自动替换。')
            clicked_selected=body.get('selected',proposal.get('selected'))
            note=str(body.get('note',''))[:2000]
            # Validate the whole transaction before changing any member.
            for member in members:
                selected=clicked_selected if member['id']==proposal['id'] else member.get('selected')
                if selected is not None and member['candidates']:
                    if type(selected) is not int or not 0<=selected<len(member['candidates']):raise ValueError('候选来源无效，请重新选择。')
                elif selected not in (None,0):raise ValueError('此事项没有该候选来源。')
                if decision=='accept' and member['kind'] not in ('metadata','voice'):
                    if type(selected) is not int or not 0<=selected<len(member['candidates']):raise ValueError('该来源范围中存在未定位来源，不能只采用一部分。')
                    c=member['candidates'][selected]
                    if c.get('unsupported'):raise ValueError('该来源范围中含未支持引用关系，请保留原文或人工处理。')
                    if member['kind']=='material':
                        if c.get('blocked_reason'):raise ValueError(c['blocked_reason'])
                        build(self.store.engine(pid),member,p['files'],selected)
                    if c.get('source_warnings') and not note.strip():raise ValueError('该来源范围存在一致性提示，请先填写核实说明；没有查明时请保留原文。')
                    if member.get('action')=='source_merge':
                        parent=next((x for x in members if x['id']==member.get('merge_into')),None)
                        parent_selected=clicked_selected if parent and parent['id']==proposal['id'] else parent.get('selected') if parent else None
                        if not parent or type(parent_selected) is not int or not 0<=parent_selected<len(parent['candidates']):
                            raise ValueError('合段删除缺少一起采用的完整来源复制。')
                        parent_candidate=parent['candidates'][parent_selected]
                        if any(c.get(k)!=parent_candidate.get(k) for k in ('doc_hash','start','end','indices','span')):
                            raise ValueError('合段删除与前段选择了不同来源范围，请重新分析。')
            with self.store.lock:
                changes=[]
                for member in members:
                    selected=clicked_selected if member['id']==proposal['id'] else member.get('selected')
                    before={k:copy.deepcopy(member.get(k)) for k in ('decision','selected','note')}
                    after={'decision':decision,'selected':selected,'note':note}
                    changes.append({'id':member['id'],'before':before,'after':after})
                event={'transaction_id':txid,'changes':changes,'time':time.time()}
                if any(x['before']!=x['after'] for x in changes):
                    p.setdefault('undo',[]).append(event);p['redo']=[];p.setdefault('events',[]).append({'type':'transaction_decision','transaction_id':txid,'count':len(changes),'decision':decision,'time':time.time()})
                for change in changes:
                    member=next(x for x in members if x['id']==change['id']);member.update(change['after']);member.pop('auto',None)
                self.store.refresh_inventory(p);p['result']['summary']=summarize(p['result']);p['export']=None;self.store.save(p)
            return self.reply({'proposals':members,'transaction_id':txid,'summary':p['result']['summary'],'documents':p['result']['documents'],'history':self.store.public(p)['history']})
        if action=='missing':
            # Side channel only: a decision about a detected item. It never edits
            # the synchronisation proposals, and the insert itself happens later
            # through the ordinary source_insert path.
            from workbench.missing_pipeline import derive_new_item_title,decision_fields,primary_evidence
            # One identity can carry several source evidences; the stored
            # fingerprints belong to the representative one so that a later
            # re-detection compares like with like.
            candidate=None;owner=None
            for record in (p.get('result') or {}).get('documents',[]):
                matches=[c for c in record.get('missing_candidates') or []
                         if c.get('candidate_id')==body.get('candidate_id')]
                if matches:candidate=primary_evidence(matches);owner=record;break
            if candidate is None:raise ValueError('缺项候选不存在，请重新分析。')
            choice=body.get('decision')
            if choice not in ('add','skip'):raise ValueError('决定无效。')
            row={'company':p.get('params',{}).get('issuer',''),'target_name':owner.get('name',''),
                 'source_name':candidate.get('source_name',''),
                 'source_item_heading':candidate.get('source_item_heading','')}
            row.update(decision_fields(candidate));row['decision']=choice
            if choice=='add':
                title=(str(body.get('approved_title','')).strip()
                       or derive_new_item_title(candidate.get('previous_sibling_heading'),
                                                candidate.get('source_item_heading')))
                if not title:raise ValueError('无法按目标同级体例生成标题，请人工填写标题后再加入。')
                row['approved_title']=title
            with self.store.lock:
                p.setdefault('missing_decisions',{})[candidate['candidate_id']]=row
                p['export']=None;p['missing_review']=None;self.store.save(p)
            return self.reply({'missing_decisions':p['missing_decisions']})
        if action=='export':
            if not p['result']:raise ValueError('请先开始分析。')
            self.store.check_inputs(p,require_result=True)
            exportdir=self.store.directory(pid)/'exports'/('核查修订包_'+time.strftime('%Y%m%d_%H%M%S')+'_'+secrets.token_hex(2))
            def run_export(progress):
                # Approved missing items join the plan here and only here. The
                # stored result keeps its original proposals untouched.
                from workbench.missing_pipeline import approved_inserts
                plans,stale=approved_inserts(p['result'],p['files'],
                                              dict(p.get('missing_decisions') or {}),
                                              self.store.engine(pid))
                with self.store.lock:
                    p['missing_review']=stale;self.store.save(p)
                work=p['result']
                if plans:
                    work=dict(p['result']);work['proposals']=list(p['result']['proposals'])+plans
                    # The independent audit re-checks every extra heading against
                    # the persisted decision before accepting it.
                    work['missing_decisions']=dict(p.get('missing_decisions') or {})
                return self.store.engine(pid).export(work,p['files'],exportdir,progress)
            self.store.run(p,'export',run_export);return self.reply({'job':p['job']},202)
        if action=='delete':
            # Explicitly deletes this project's own local uploads/results only.
            import shutil
            with self.store.lock:
                shutil.rmtree(self.store.directory(pid));self.store.projects.pop(pid);self.store.engines.pop(pid,None)
            return self.reply({'deleted':True})
        raise ValueError('接口不存在。')

def serve(port=8766,data=None,open_browser=False):
    store=Store(data or ROOT/'data'/'projects');server=None
    for n in range(port,port+30):
        try:server=ThreadingHTTPServer(('127.0.0.1',n),Handler);break
        except OSError:continue
    if server is None:raise OSError('可用端口不足，请关闭重复启动的程序。')
    server.store=store;server.daemon_threads=True
    url='http://127.0.0.1:'+str(server.server_port)
    print('\n核查工作台已启动：'+url+'\n材料仅保存在本机。关闭此终端可停止服务。\n',flush=True)
    if open_browser:threading.Timer(.6,lambda:webbrowser.open(url)).start()
    try:server.serve_forever(poll_interval=.3)
    except KeyboardInterrupt:pass
    finally:server.server_close();JOBS.shutdown(wait=True,cancel_futures=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);parser.add_argument('--data');parser.add_argument('--open',action='store_true');args=parser.parse_args();serve(args.port,args.data,args.open)
