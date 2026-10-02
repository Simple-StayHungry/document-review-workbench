"""Engine boundary integration with complete, isolated OOXML fixtures.

These fixtures verify safety gates and object accounting, not the nine-chapter
business acceptance corpus or Word/WPS visual behavior.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from xml.etree import ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.docxio import W,R,PR,w,sha
from workbench.engine import Engine
from workbench.ingest import Importer
from workbench.model import Document
from workbench.version import BUILD,SCHEMA
import server

COMPANY='测试城市发展有限公司'
FACT='发行人主要从事城市基础设施建设、公共事业运营和城市资产管理，业务覆盖多个地区，经营组织结构清晰。'
JUDGMENT='经项目组核查，发行人的业务经营符合相关要求，项目组未发现重大异常情况。'


def make_docx(path,source=False,historical=False,extra='',cover_date=''):
    root=ET.Element(w('document'));body=ET.SubElement(root,w('body'))
    def para(value,tracked=False):
        p=ET.SubElement(body,w('p'))
        parent=ET.SubElement(p,w('ins'),{w('id'):'7',w('author'):'旧作者',w('date'):'2020-01-01T00:00:00Z'}) if tracked else p
        r=ET.SubElement(parent,w('r'));ET.SubElement(r,w('t')).text=value
        return p
    para(COMPANY);para('2026年公司债券');para('募集说明书' if source else '核查记录及分析文件')
    if cover_date:para(cover_date)
    if source:para('第一章 发行人基本情况')
    else:
        para('1-1-1 发行人基本情况核查记录');para('一、核查程序');para('项目组查阅发行人提供的材料并实施核对。');para('二、核查情况')
    para('（一）主要业务情况');para(FACT,tracked=historical)
    para(JUDGMENT)
    para('（二）主要资产情况')
    table=ET.SubElement(body,w('tbl'))
    pr=ET.SubElement(table,w('tblPr'));ET.SubElement(pr,w('tblW'),{w('w'):'0',w('type'):'auto'})
    grid=ET.SubElement(table,w('tblGrid'))
    ET.SubElement(grid,w('gridCol'),{w('w'):'3500'});ET.SubElement(grid,w('gridCol'),{w('w'):'3500'})
    for values in (('资产类别','资产说明'),('城市道路','依法管理城市道路相关设施'),('市政设施','提供城市公共事业配套服务')):
        tr=ET.SubElement(table,w('tr'))
        for value in values:
            tc=ET.SubElement(tr,w('tc'));p=ET.SubElement(tc,w('p'));r=ET.SubElement(p,w('r'));ET.SubElement(r,w('t')).text=value
    if extra:para(extra)
    if not source:para('三、核查结论');para('项目组认为发行人的资料与已实施的核查程序相符。')
    para('')
    sect=ET.SubElement(body,w('sectPr'));ET.SubElement(sect,w('pgSz'),{w('w'):'11906',w('h'):'16838'})
    content_types=('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '</Types>')
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('[Content_Types].xml',content_types)
        z.writestr('word/document.xml',ET.tostring(root,encoding='utf-8',xml_declaration=True))
        z.writestr('_rels/.rels',f'<Relationships xmlns="{PR}"><Relationship Id="rId1" Type="{R}/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/_rels/document.xml.rels',f'<Relationships xmlns="{PR}"/>')
    d=Document(path)
    return {'id':d.hash,'name':path.name,'path':str(path),'role':'source' if source else 'target','profile':d.profile,'origin':path.name,'aliases':[]}


class EngineBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='workbench-engine-boundaries-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.engine=Engine()
        self.files=[make_docx(self.base/'原始底稿.docx'),make_docx(self.base/'当前募集说明书.docx',source=True)]
        self.params={'author':'柒','issuer':COMPANY,'date':'','bond':'','period':''}

    def analyze(self):return self.engine.analyze(self.files,self.params)

    def merge_analysis(self):
        tail='发行人进一步完善了对子公司的日常经营管理体系，通过明确岗位责任、规范业务审批流程、完善财务报告机制加强对子公司的统一管理。'
        for record in self.files:
            path=Path(record['path'])
            with zipfile.ZipFile(path) as z:entries={name:z.read(name) for name in z.namelist()}
            root=ET.fromstring(entries['word/document.xml']);body=root.find(w('body'))
            paragraph=next(p for p in body.findall(w('p')) if ''.join(p.itertext())==FACT)
            paragraph.find('.//'+w('t')).text=FACT+(tail if record['role']=='source' else '原有子公司管理安排由旧版材料中的若干规定确定。')
            if record['role']=='target':
                another=ET.Element(w('p'));r=ET.SubElement(another,w('r'));ET.SubElement(r,w('t')).text=tail
                body.insert(list(body).index(paragraph)+1,another)
            entries['word/document.xml']=ET.tostring(root,encoding='utf-8',xml_declaration=True)
            with zipfile.ZipFile(path,'w') as z:
                for name,data in entries.items():z.writestr(name,data)
            doc=Document(path);record['id']=doc.hash;record['profile']=doc.profile
        return self.analyze()


    def test_revision_date_is_not_a_visible_cover_edit(self):
        cover='调查日期：【2026】年【7】月【17】日'
        files=[make_docx(self.base/'有日期底稿.docx',cover_date=cover),make_docx(self.base/'日期来源.docx',source=True)]
        params=dict(self.params,date='2026-09-26')
        result=self.engine.analyze(files,params)
        self.assertEqual(result['parameter_authorities']['revision_date'],'2026-09-26')
        self.assertFalse(any(any(c.get('field')=='date' for c in p.get('parameter_changes',[])) for p in result['proposals']))
        self.assertFalse(any(p.get('old_text','').startswith('调查日期') and p.get('old_text')!=p.get('new_text',p.get('old_text')) for p in result['proposals']))

    def test_json_roundtrip_roles_accepts_current_plan_and_exports(self):
        result=self.analyze()
        self.assertEqual((BUILD,SCHEMA),(result['engine_version'],result['schema']))
        decoded=json.loads(json.dumps(result,ensure_ascii=False))
        self.assertTrue(all(isinstance(x,list) for x in decoded['input_roles']))
        exported=Engine().export(decoded,self.files,self.base/'json-restart')
        self.assertTrue(Path(exported['zip']).is_file())
        self.assertTrue(all(x['independent_output_audit']['passed'] for x in exported['checks']))

    def test_removed_source_is_rejected_before_output(self):
        result=self.analyze();out=self.base/'removed'
        with self.assertRaisesRegex(ValueError,'材料集合|来源用途|来源文件'):
            self.engine.export(result,[self.files[0]],out)
        self.assertFalse(out.exists())

    def test_changed_source_role_is_rejected_before_output(self):
        result=self.analyze();changed=copy.deepcopy(self.files);changed[1]['role']='reference';out=self.base/'changed-role'
        with self.assertRaisesRegex(ValueError,'材料集合|来源用途'):
            self.engine.export(result,changed,out)
        self.assertFalse(out.exists())

    def test_same_name_with_new_bytes_rejects_old_plan_and_cached_document(self):
        result=self.analyze();make_docx(Path(self.files[1]['path']),source=True,extra='这是同名文件后来新增的正式来源内容。')
        with self.assertRaisesRegex(ValueError,'材料内容已经变化'):
            self.engine.export(result,self.files,self.base/'changed-bytes')
        with self.assertRaisesRegex(ValueError,'材料内容已经变化'):
            self.engine.analyze(self.files,self.params)

    def test_missing_source_file_rejects_cached_document(self):
        result=self.analyze();Path(self.files[1]['path']).unlink()
        with self.assertRaisesRegex(ValueError,'材料内容已经变化|丢失'):
            self.engine.export(result,self.files,self.base/'missing-file')

    def test_mutated_frozen_parameters_cannot_export_as_original_analysis(self):
        result=self.analyze();result['params']['author']='未经分析确认的新作者'
        with self.assertRaisesRegex(ValueError,'参数|计划|分析'):
            self.engine.export(result,self.files,self.base/'changed-params')

    def test_historical_target_revisions_are_not_silently_accepted(self):
        self.files[0]=make_docx(Path(self.files[0]['path']),historical=True)
        before=Path(self.files[0]['path']).read_bytes()
        with self.assertRaisesRegex(ValueError,'历史修订|历史审阅'):
            result=self.analyze();self.engine.export(result,self.files,self.base/'target-history')
        self.assertEqual(before,Path(self.files[0]['path']).read_bytes())

    def test_historical_source_revisions_require_an_explicit_baseline(self):
        self.files[1]=make_docx(Path(self.files[1]['path']),source=True,historical=True)
        with self.assertRaisesRegex(ValueError,'历史修订|历史审阅'):
            result=self.analyze();self.engine.export(result,self.files,self.base/'source-history')

    def test_same_bytes_teacher_alias_revokes_source_at_direct_export(self):
        result=self.analyze();im=Importer(self.base/'imported',self.files)
        im.add('老师对照/同一字节稿.docx',Path(self.files[1]['path']).read_bytes())
        source=next(f for f in im.files if f['id']==self.files[1]['id'])
        self.assertEqual(source['role'],'reference')
        # Even a bypass caller restoring role=source must not bypass aliases.
        source['role']='source'
        with self.assertRaisesRegex(ValueError,'老师|教师'):
            self.engine.export(result,im.files,self.base/'teacher-alias')

    def test_every_target_block_has_exactly_one_inventory_disposition(self):
        result=self.analyze();document=Document(self.files[0]['path']);report=result['documents'][0]
        items=report['object_inventory']
        self.assertEqual(list(range(len(document.blocks))),[x['block'] for x in items])
        self.assertEqual(len(document.blocks),sum(report['coverage']['dispositions'].values()))
        self.assertEqual([b.text for b in document.blocks],[x['text'] for x in items])
        self.assertTrue(any(x['kind']=='table' for x in items))
        self.assertEqual('protected',next(x for x in items if x['text']==JUDGMENT)['status'])
        by_id={p['id']:p for p in result['proposals']}
        for item in items:
            if item['proposal_id']:
                proposal=by_id[item['proposal_id']]
                self.assertTrue(proposal['start']<=item['block']<proposal['end'])

    def test_exported_inventory_reflects_final_keep_decision(self):
        result=self.analyze()
        chosen=next(p for p in result['proposals'] if p['decision'] in ('accept','same'))
        for proposal in result['proposals']:
            if proposal.get('transaction_id')==chosen.get('transaction_id'):proposal['decision']='keep'
        self.engine.export(result,self.files,self.base/'kept')
        trace=json.loads(next((self.base/'kept').glob('*_来源追溯.json')).read_text())
        relevant=[x for x in trace['object_inventory'] if chosen['start']<=x['block']<chosen['end']]
        self.assertTrue(relevant)
        self.assertTrue(all(x['status']=='protected' for x in relevant),relevant)

    def test_http_merge_preview_and_decisions_remain_linked_through_undo_redo(self):
        result=self.merge_analysis()
        merged=next(p for p in result['proposals'] if p.get('action')=='source_merge')
        store=server.Store(self.base/'server-projects');project=store.create('合段联动测试')
        project['files']=self.files;project['params']=self.params;project['result']=result
        project['input_signature']=server.input_signature(self.files,self.params)
        store.engines[project['id']]=self.engine;store.save(project)
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);httpd.store=store
        threading.Thread(target=httpd.serve_forever,kwargs={'poll_interval':.01},daemon=True).start()
        self.addCleanup(httpd.server_close);self.addCleanup(httpd.shutdown)
        base=f'http://127.0.0.1:{httpd.server_port}/api/projects/{project["id"]}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(action,body=None):
            data=None if body is None else json.dumps(body).encode()
            req=urllib.request.Request(base+'/'+action,data=data,headers={'Content-Type':'application/json'})
            with opener.open(req,timeout=3) as response:return json.load(response)
        plan=request('plan?proposal='+merged['id'])
        self.assertTrue(plan['deletion_only']);self.assertEqual([],plan['replacement'])
        self.assertIn('删除修订',plan['action_message']);self.assertIn(merged['merge_into'],plan['linked_decisions'])
        def linked():return server.decision_members(project['result'],merged)
        kept=request('decision',{'id':merged['id'],'decision':'keep','selected':0,'note':'整组合段保留'})
        self.assertTrue(all(p['decision']=='keep' for p in kept['proposals']))
        self.assertTrue(all(p['decision']=='keep' for p in linked()))
        kept_plan=request('plan?proposal='+merged['id']);self.assertFalse(kept_plan['deletion_only']);self.assertTrue(kept_plan['replacement'])
        request('undo',{});self.assertTrue(all(p['decision'] in ('same','accept') for p in linked()))
        request('redo',{});self.assertTrue(all(p['decision']=='keep' for p in linked()))
        request('decision',{'id':merged['id'],'decision':'accept','selected':0,'note':'按同一完整来源合段'})
        self.assertTrue(all(p['decision']=='accept' for p in linked()))
        request('decision',{'id':merged['id'],'decision':'pending','selected':0,'note':''})
        self.assertTrue(all(p['decision']=='pending' for p in linked()))
        inventory=project['result']['documents'][0]['object_inventory']
        self.assertTrue(all(x['status']=='pending' for x in inventory if x['proposal_id'] in {p['id'] for p in linked()}))


if __name__=='__main__':unittest.main()
