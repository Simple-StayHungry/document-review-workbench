"""Runtime and provenance boundaries; these are not document-content acceptance tests."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
import urllib.error
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import server
import start
from workbench.ingest import Importer,validate_source_record
from workbench.version import BUILD,SCHEMA
from workbench.docxio import w,R,A,PR
from workbench.figures import figure_proposals
from workbench.model import Document


def docx_bytes(title='募集说明书',detail='测试发行有限公司'):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml',f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{title}</w:t></w:r></w:p><w:p><w:r><w:t>{detail}</w:t></w:r></w:p></w:body></w:document>')
    return data.getvalue()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='workbench-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=server.Store(self.root/'projects')

    def project(self):
        p=self.store.create('隔离运行测试')
        material=self.store.directory(p['id'])/'materials'
        im=Importer(material)
        im.add('当前募集说明书.docx',docx_bytes())
        p['files']=im.files
        self.store.save(p)
        return p

    def freeze(self,p):
        p['result']={'schema':SCHEMA,'engine_version':BUILD,'proposals':[]}
        p['input_signature']=server.input_signature(p['files'],p['params'])
        p['undo']=[{'example':'preserve'}]
        self.store.save(p)

    def http(self,p):
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        httpd.store=self.store
        thread=threading.Thread(target=httpd.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f'http://127.0.0.1:{httpd.server_port}/api/projects/{p["id"]}'

    def post(self,url,body):
        request=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request,timeout=3) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)

    def figure_project(self):
        p=self.store.create('真实来源图片接口测试');docs=[]
        for source,payload in ((False,b'original-image-bytes'),(True,b'current-prospectus-image-bytes')):
            root=ET.Element(w('document'));body=ET.SubElement(root,w('body'))
            values=['测试城市发展有限公司','2026年公司债券','募集说明书' if source else '核查记录及分析文件']
            if not source:values+=['1-4-3 行业情况核查记录','二、核查情况']
            values+=['4、屠宰行业','价格方面，市场供需变化影响生猪销售价格。过去十年全国市场价格变动情况如下。','图：价格变化情况（单位：元/公斤）','','数据来源：农业农村部','5、其他行业']
            for value in values:
                para=ET.SubElement(body,w('p'));run=ET.SubElement(para,w('r'));ET.SubElement(run,w('t')).text=value
                if value=='':
                    drawing=ET.SubElement(run,w('drawing'))
                    ET.SubElement(drawing,'{'+A+'}blip',{'{'+R+'}embed':'rIdShared'})
            path=self.root/('source.docx' if source else 'target.docx')
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('word/document.xml',ET.tostring(root))
                z.writestr('word/_rels/document.xml.rels',f'<Relationships xmlns="{PR}"><Relationship Id="rIdShared" Type="{R}/image" Target="media/shared.png"/></Relationships>')
                z.writestr('word/media/shared.png',payload)
            doc=Document(path);docs.append(doc)
            p['files'].append({'id':doc.hash,'name':path.name,'path':str(path),'role':'source' if source else 'target','profile':doc.profile})
        proposal=figure_proposals(docs[0],[docs[1]])[0]
        p['result']={'proposals':[proposal]}
        return p,proposal,docs

    def test_http_replacement_image_uses_actual_source_package_and_block(self):
        p,proposal,docs=self.figure_project();url=self.http(p)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plan=json.load(opener.open(url+'/plan?proposal='+proposal['id']))
        self.assertEqual('',plan['error']);self.assertEqual(3,len(plan['replacement']))
        image=next(b for b in plan['replacement'] if b['images'])
        source_block=next(b for b in docs[1].blocks if docs[1].images(b)).index
        self.assertEqual(source_block,image['index'])
        self.assertIn('file='+docs[1].hash,image['images'][0]);self.assertNotIn('block=-1',image['images'][0])
        raw=opener.open(url.split('/api/')[0]+image['images'][0]).read()
        self.assertEqual(hashlib.sha256(b'current-prospectus-image-bytes').hexdigest(),hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(hashlib.sha256(b'original-image-bytes').hexdigest(),hashlib.sha256(raw).hexdigest())
        self.assertIn('图：价格变化情况',plan['replacement'][0]['text'])
        self.assertEqual('数据来源：农业农村部',plan['replacement'][-1]['text'])

    def test_http_retained_replacement_image_uses_original_package(self):
        p,proposal,docs=self.figure_project();proposal['decision']='keep';url=self.http(p)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plan=json.load(opener.open(url+'/plan?proposal='+proposal['id']))
        image=next(b for b in plan['replacement'] if b['images'])
        target_block=next(b for b in docs[0].blocks if docs[0].images(b)).index
        self.assertEqual(target_block,image['index']);self.assertIn('file='+docs[0].hash,image['images'][0])
        self.assertEqual(b'original-image-bytes',opener.open(url.split('/api/')[0]+image['images'][0]).read())

    def test_http_replacement_span_has_real_source_block_index(self):
        p,proposal,docs=self.figure_project();candidate=proposal['candidates'][0]
        block=next(b for b in docs[1].blocks if b.text.startswith('价格方面'))
        candidate={k:v for k,v in candidate.items() if k not in ('source_fingerprint','replacement_digest')}
        candidate['span']={'block':block.index,'start':0,'end':12}
        proposal['candidates']=[candidate];proposal.pop('target_fingerprint',None)
        url=self.http(p);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plan=json.load(opener.open(url+'/plan?proposal='+proposal['id']))
        self.assertEqual('',plan['error']);self.assertEqual(1,len(plan['replacement']))
        self.assertEqual(block.index,plan['replacement'][0]['index'])
        self.assertEqual(block.text[:12],plan['replacement'][0]['text'])

    def test_http_field_preview_keeps_target_media_despite_source_metadata(self):
        p,proposal,docs=self.figure_project()
        block=next(b for b in docs[0].blocks if docs[0].images(b))
        proposal.update(kind='field',start=block.index,end=block.index+1,new_text='',replacements=[])
        url=self.http(p);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        plan=json.load(opener.open(url+'/plan?proposal='+proposal['id']))
        self.assertEqual('',plan['error']);self.assertEqual(1,len(plan['replacement']))
        image=plan['replacement'][0]
        self.assertEqual(block.index,image['index']);self.assertIn('file='+docs[0].hash,image['images'][0])
        self.assertEqual(b'original-image-bytes',opener.open(url.split('/api/')[0]+image['images'][0]).read())

    def test_launcher_reuses_same_version_without_office_cleanup(self):
        health={'app':'local-verification-workbench','build':BUILD,'instance':hashlib.sha256(str(start.ROOT).encode()).hexdigest()[:16]}
        response=io.BytesIO(json.dumps(health).encode())
        opener=Mock();opener.open.return_value=response
        with patch.object(start,'cleanup_legacy_wps_addin',side_effect=AssertionError('Office must not be touched')) as cleanup,patch.object(start.urllib.request,'build_opener',return_value=opener),patch.object(start.webbrowser,'open') as browser,patch.object(start.os,'chdir'),patch.object(server,'serve') as serve:
            start.main()
        cleanup.assert_not_called();serve.assert_not_called();browser.assert_called_once()
        self.assertEqual(start.BUILD,server.BUILD)

    def test_source_gate_checks_folders_aliases_and_workpaper_kind(self):
        base={'name':'当前文件.docx','origin':'正式来源/当前文件.docx','profile':{'kind':'prospectus'}}
        validate_source_record(base)
        for update in ({'name':'示例城投_教师对照.docx'},{'origin':'03_教师对照/当前文件.docx'},{'aliases':['历史问题输出_v18.8/当前文件.docx']},{'profile':{'kind':'workpaper'}}):
            with self.subTest(update=update),self.assertRaises(ValueError):validate_source_record(dict(base,**update))

    def test_historical_prospectus_is_excluded_before_cover_detection(self):
        im=Importer(self.root/'inputs')
        im.add('历史问题输出_v18.8/募集说明书.docx',docx_bytes())
        self.assertEqual(im.files[0]['role'],'reference')

    def test_deduplicated_teacher_alias_revokes_source_without_mutating_original(self):
        im=Importer(self.root/'inputs');raw=docx_bytes()
        im.add('当前募集说明书.docx',raw)
        original=copy.deepcopy(im.files)
        next_upload=Importer(self.root/'inputs',im.files)
        next_upload.add('老师对照/同一份文件.docx',raw)
        self.assertEqual(next_upload.files[0]['role'],'reference')
        self.assertEqual(im.files,original)

    def test_explicit_role_survives_recommendation_after_later_upload(self):
        im=Importer(self.root/'inputs')
        profile={'issuer':'测试公司','chapter':'第一章','historical_revisions':0}
        im.files=[{'role':'source','profile':dict(profile,kind='prospectus')},
                  {'role':'target','name':'核查意见.docx','role_locked':True,'profile':dict(profile,kind='opinion')},
                  {'role':'candidate','name':'底稿.docx','origin':'底稿.docx','profile':dict(profile,kind='workpaper')}]
        im.recommend()
        self.assertEqual(im.files[1]['role'],'target')

    def test_restart_preserves_current_result_and_decisions(self):
        p=self.project();self.freeze(p)
        recovered=server.Store(self.store.root).get(p['id'])
        self.assertEqual(recovered['result'],p['result'])
        self.assertEqual(recovered['undo'],p['undo'])

    def test_restart_invalidates_changed_roles_and_bytes(self):
        for change in ('role','bytes','missing','version'):
            with self.subTest(change=change):
                p=self.project();self.freeze(p)
                if change=='role':p['files'][0]['role']='reference';self.store.save(p)
                elif change=='bytes':Path(p['files'][0]['path']).write_bytes(docx_bytes(detail='新版内容'))
                elif change=='missing':Path(p['files'][0]['path']).unlink()
                else:p['result']['engine_version']='old';self.store.save(p)
                recovered=server.Store(self.store.root).get(p['id'])
                self.assertIsNone(recovered['result']);self.assertEqual(recovered['undo'],[])
                self.assertTrue(recovered['result_invalidated']['reason'])

    def test_http_role_settings_are_atomic_and_invalidate_cache(self):
        p=self.project();self.freeze(p);url=self.http(p)
        old_params=dict(p['params'])
        code,_=self.post(url+'/settings',{'params':{'issuer':'changed'},'roles':{'missing':'source'}})
        self.assertEqual(code,400);self.assertEqual(p['params'],old_params)
        self.store.engines[p['id']]=object()
        code,_=self.post(url+'/settings',{'roles':{p['files'][0]['id']:'reference'}})
        self.assertEqual(code,200);self.assertIsNone(p['result'])
        self.assertNotIn(p['id'],self.store.engines);self.assertTrue(p['files'][0]['role_locked'])

    def test_http_teacher_source_is_rejected_even_without_filename_prefix(self):
        p=self.project();p['files'][0]['role']='reference';p['files'][0]['origin']='材料/老师对照/当前募集.docx'
        code,data=self.post(self.http(p)+'/settings',{'roles':{p['files'][0]['id']:'source'}})
        self.assertEqual(code,400);self.assertIn('老师',data['error'])
        self.assertEqual(p['files'][0]['role'],'reference')

    def test_renaming_project_preserves_analysis_and_decisions(self):
        p=self.project();self.freeze(p)
        code,_=self.post(self.http(p)+'/settings',{'name':'重新命名'})
        self.assertEqual(code,200);self.assertIsNotNone(p['result'])
        self.assertEqual(p['undo'],[{'example':'preserve'}])

    def test_http_export_rejects_changed_file_before_engine_writes(self):
        p=self.project();self.freeze(p)
        Path(p['files'][0]['path']).write_bytes(docx_bytes(detail='同名不同内容'))
        with patch.object(self.store,'engine',side_effect=AssertionError('Export must not start')):
            code,data=self.post(self.http(p)+'/export',{})
        self.assertEqual(code,400);self.assertIn('变化',data['error']);self.assertIsNone(p['result'])

    def test_job_error_preserves_real_stage_timing_and_error(self):
        p=self.project();done=threading.Event()
        def fail(progress):
            progress(37,'正在核实真实输入')
            raise ValueError('测试来源缺失')
        original_save=self.store.save
        def save(project):
            original_save(project)
            if project.get('job',{}).get('finished'):done.set()
        with patch.object(self.store,'save',side_effect=save),patch.object(server.traceback,'print_exc'):
            self.store.run(p,'analyze',fail)
            self.assertTrue(done.wait(3))
        job=p['job']
        self.assertEqual(job['state'],'error');self.assertEqual(job['stage'],'正在核实真实输入')
        self.assertEqual(job['progress'],37);self.assertEqual(job['error'],'测试来源缺失')
        self.assertEqual(job['error_type'],'ValueError');self.assertGreaterEqual(job['finished'],job['stage_started'])
        self.assertGreaterEqual(server.public_job(job)['elapsed_seconds'],0)
        self.assertEqual(server.Store(self.store.root).get(p['id'])['job']['error'],'测试来源缺失')

    def test_restart_marks_running_job_interrupted(self):
        p=self.project();p['job']={'kind':'analyze','state':'running','progress':22,'stage':'读取来源','started':time.time()};self.store.save(p)
        recovered=server.Store(self.store.root).get(p['id'])
        self.assertEqual(recovered['job']['state'],'interrupted')
        self.assertIn('未完成',recovered['job']['message'])


if __name__=='__main__':unittest.main()
