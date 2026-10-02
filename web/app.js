'use strict';
const $=s=>document.querySelector(s), $$=s=>Array.from(document.querySelectorAll(s));
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={project:null,projects:[],step:'import',filter:'pending',selected:null,poll:null,uploading:false,previewToken:0,detailTab:'after',fullContext:false,detailId:null};
const roles={target:'待更新',source:'核查来源',reference:'不参与核查',candidate:'备选',ignored:'忽略'};
const noCorrespondence=x=>x.content_class==='no_correspondence_retained';
const sourceInsert=p=>p.action==='source_insert';
const needsReview=p=>p.decision==='pending'&&!noCorrespondence(p);
const statusLabel=p=>noCorrespondence(p)||p.decision==='keep'?'已确认不修改':['accept','same'].includes(p.decision)?'已应用修改':p.decision==='protected'?'保护保留':'待核对';
const objectLabel=x=>noCorrespondence(x)?'保留原文':x.status==='protected'?'保护保留':'待核对';
function chapterFromTargetName(name){
 const m=String(name||'').match(/第[一二三四五六七八九十百0-9０-９]+章/);
 return m?m[0]:'';
}
function setReviewChapter(targetName){
 const el=$('#reviewChapter');if(!el)return;
 const chapter=chapterFromTargetName(targetName);
 el.textContent=chapter?`修改位置 · ${chapter}`:'';
 el.title=chapter?String(targetName||''):'';
 el.classList.toggle('hidden',!chapter);
}
function reviewCounts(result){
 const proposals=result.proposals||[],objects=(result.documents||[]).flatMap(d=>d.object_inventory||[]),pending=proposals.filter(needsReview);
 return {pending:pending.length,pendingTransactions:new Set(pending.map(p=>p.transaction_id||p.id)).size,
  copied:proposals.filter(p=>p.kind==='material'&&!noCorrespondence(p)&&p.action!=='source_merge'&&['accept','same'].includes(p.decision)&&(p.candidates||[])[p.selected??0]).length,
  noCorrespondence:objects.filter(noCorrespondence).length,
  pendingObjects:objects.filter(x=>x.status==='pending'&&!noCorrespondence(x)).length,
  protectedObjects:objects.filter(x=>x.status==='protected'&&!noCorrespondence(x)).length};
}
const fmtSize=n=>n>=1048576?(n/1048576).toFixed(1)+' MB':Math.ceil(n/1024)+' KB';
async function api(path,body){
 const opt=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
 let r;try{r=await fetch(path,opt);}catch(e){throw new Error('未连接到本机程序。请双击“启动核查工作台”后，从自动打开的网页进入。');}
 const type=r.headers.get('content-type')||'';if(!type.includes('application/json'))throw new Error('程序返回异常，请重新启动本机服务。');
 const data=await r.json();if(!r.ok)throw new Error(data.error||'请求未完成。');return data;
}
function error(e){$('#errorText').textContent=e.message||e;$('#errorBox').classList.remove('hidden');$('#errorBox').scrollIntoView({block:'nearest',behavior:'smooth'});}
function clearError(){$('#errorBox').classList.add('hidden');}
function toast(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.add('hidden'),2400);}
function busy(){return state.saving||state.uploading||state.project?.job?.state==='running';}
function setBusyUI(){
 const running=busy();['#newProject','#analyzeBtn','#pickFiles','#pickFolder'].forEach(s=>{const e=$(s);if(e)e.disabled=running;});
 $$('.file-row select').forEach(e=>e.disabled=running);
 $$('.decision-card button,.review-actionbar button,#generateExport').forEach(e=>e.disabled=running||e.dataset.unavailable==='true');
 if($('#undoBtn'))$('#undoBtn').disabled=running||!state.project?.history?.can_undo;if($('#redoBtn'))$('#redoBtn').disabled=running||!state.project?.history?.can_redo;
 const historyActions=$('.review-head-actions');if(historyActions)historyActions.classList.toggle('hidden',running||!(state.project?.history?.can_undo||state.project?.history?.can_redo));
 $('#cancelJob').classList.toggle('hidden',!['analyze','export'].includes(state.project?.job?.kind)||state.project?.job?.state!=='running');
}
function showStep(step){
 if(!['import','review','export'].includes(step))step='review';
 state.step=step;document.body.dataset.step=step;window.scrollTo({top:0,left:0,behavior:'auto'});['import','review','export'].forEach(n=>$('#'+n+'Panel').classList.toggle('hidden',n!==step));
 $$('.step').forEach(e=>e.classList.toggle('active',e.dataset.step===step));
 if(step==='review')renderReview();if(step==='export')renderExport();setBusyUI();
}
async function refreshProjects(){state.projects=await api('/api/projects');renderProjects();}
function renderProjects(){
 $('#projects').innerHTML=state.projects.map(p=>`<button class="project-item ${p.id===state.project?.id?'active':''}" data-id="${p.id}" title="${esc(p.name)}">${esc(p.name)}</button>`).join('');
 $$('#projects button').forEach(b=>b.onclick=()=>loadProject(b.dataset.id));
}
async function createProject(){
 try{clearError();const p=await api('/api/projects',{name:'核查项目 '+new Date().toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})});await refreshProjects();await loadProject(p.id);}catch(e){error(e);}
}
async function loadProject(pid){
 try{clearError();clearTimeout(state.poll);state.project=await api('/api/projects/'+pid);localStorage.setItem('workbenchProject',pid);state.selected=null;state.filter='pending';renderProject();showStep(state.project.result?'review':'import');if(state.project.job?.state==='running')pollJob();else if(['error','interrupted'].includes(state.project.job?.state))error(new Error(state.project.job.message));}catch(e){error(e);}
}
function renderProject(){
 const p=state.project;$('#projectName').textContent=p.name;renderProjects();renderMaterials();renderParams();renderJob();
 if(state.step==='review')renderReview();if(state.step==='export')renderExport();setBusyUI();
}
function renderParams(){
 for(const k of ['issuer','author','date','bond','period'])$('#'+k).value=state.project.params[k]||'';
 $('#allow_unknown_source').checked=!!state.project.params.allow_unknown_source;
}
function getParams(){const p={};for(const k of ['issuer','author','date','bond','period'])p[k]=$('#'+k).value.trim();p.allow_unknown_source=$('#allow_unknown_source').checked;return p;}
function renderMaterials(){
 const files=state.project.files;const has=files.length>0;document.body.classList.toggle('has-materials',has);$('#dropzone h2').textContent=has?'继续添加':'把材料放在这里';$('#materialsSection').classList.toggle('hidden',!has);
 $('#materialSummary').textContent=`${files.length} 份材料 · ${files.filter(f=>f.role==='target').length} 份待更新 · ${files.filter(f=>f.role==='source').length} 份核查来源`;
 const q=$('#fileSearch').value.trim().toLowerCase(),all=$('#showOtherFiles').checked;
 const filtered=files.filter(f=>(all||['target','source'].includes(f.role))&&(!q||(f.name+f.profile.issuer+f.profile.chapter).toLowerCase().includes(q)));
 const order={target:0,source:1,reference:2,candidate:3,ignored:4};filtered.sort((a,b)=>order[a.role]-order[b.role]||a.name.localeCompare(b.name,'zh-CN'));
 $('#filesList').innerHTML=filtered.length?filtered.map(f=>`<div class="file-row"><div class="file-type">DOCX</div><div><div class="file-name">${esc(f.name)}</div><div class="file-meta">${esc(f.profile.issuer||'未识别发行人')} · ${esc(f.profile.chapter||'未识别章节')} · ${fmtSize(f.size)}${f.profile.historical_revisions?' · <span class="warn">含历史修订</span>':''}${f.source_exclusion?' · '+esc(f.source_exclusion):''}</div></div><select data-file="${f.id}" aria-label="${esc(f.name)}的用途">${Object.entries(roles).map(([k,v])=>`<option value="${k}" ${k===f.role?'selected':''} ${f.source_exclusion&&k==='source'?'disabled':''}>${v}</option>`).join('')}</select></div>`).join(''):'<div class="files-empty">没有匹配的材料。</div>';
 $$('.file-row select').forEach(s=>s.onchange=async()=>{const pid=state.project.id;try{const updated=await api(`/api/projects/${pid}/settings`,{roles:{[s.dataset.file]:s.value}});if(state.project.id!==pid)return;state.project=updated;renderProject();toast('已保存，请重新分析');}catch(e){error(e);renderMaterials();}});
 setBusyUI();
}
function uploadFiles(files){
 if(!files.length||busy())return;clearError();let bytes=0;const form=new FormData();for(const f of files){bytes+=f.size;form.append('files',f,f.webkitRelativePath||f.name);}
 if(bytes>320*1024*1024){error(new Error('单次上传请控制在 320 MB 以内，可分批加入同一项目。'));return;}
 state.uploading=true;setBusyUI();$('#jobBox').classList.remove('hidden');$('#jobText').textContent='正在导入本机材料';$('#jobBar').style.width='1%';$('#jobPercent').textContent='0%';
 const xhr=new XMLHttpRequest();xhr.open('POST',`/api/projects/${state.project.id}/upload`);
 xhr.upload.onprogress=e=>{if(e.lengthComputable){const n=Math.round(e.loaded/e.total*100);$('#jobText').textContent=n===100?'文件已送入本机，正在读取压缩包':'正在导入本机材料';$('#jobPercent').textContent=n+'%';$('#jobBar').style.width=n+'%';}};
 xhr.onload=()=>{state.uploading=false;try{const r=JSON.parse(xhr.responseText);if(xhr.status>=400)throw new Error(r.error);state.project.job=r.job;renderJob();pollJob();}catch(e){error(e);setBusyUI();}};
 xhr.onerror=()=>{state.uploading=false;error(new Error('与服务器的连接中断了。已收到的材料会保留；请刷新页面查看当前状态，再补传缺失的部分。'));setBusyUI();};xhr.send(form);
 $('#fileInput').value='';$('#folderInput').value='';
}
function renderJob(){
 const j=state.project?.job;const active=j?.state==='running',reviewOwnsAnalyze=active&&j?.kind==='analyze'&&state.step==='review';$('#jobBox').classList.toggle('hidden',(!active&&!state.uploading)||reviewOwnsAnalyze);
 if(active){$('#jobText').textContent=j.message+' · 已用时 '+Math.floor(j.elapsed_seconds??Math.max(0,Date.now()/1000-j.started))+' 秒';$('#jobPercent').textContent=Math.round(j.progress)+'%';$('#jobBar').style.width=Math.max(2,j.progress)+'%';}
 setBusyUI();
}
async function pollJob(){
 clearTimeout(state.poll);const pid=state.project.id;
 try{const data=await api(`/api/projects/${pid}/status`);if(pid!==state.project.id)return;state.project.job=data.job;renderJob();
 if(data.job?.state==='running'){state.poll=setTimeout(pollJob,900);if(state.step==='review'&&data.job?.kind==='analyze')renderAnalyzing(data.job);return;}
 state.project=await api(`/api/projects/${pid}`);renderProject();await refreshProjects();
 if(data.job?.state==='error'){if(data.job?.kind==='analyze'){state.analyzeError=data.job.message||'核查失败';showStep('review');renderReview();}else error(new Error(data.job.message));return;}
 if(data.job?.state==='cancelled'){toast(data.job.message);return;}
 if(data.job?.kind==='analyze'){state.selected=null;state.filter='pending';showStep('review');state.analyzeError=null;toast('核查完成');}
 else if(data.job?.kind==='export'){showStep('export');toast('文件已生成');}
 else if(data.job?.kind==='upload'){showStep('import');toast('材料已导入');}
 }catch(e){error(e);setBusyUI();}
}
async function analyze(){
 clearError();state.analyzeError=null;
 const starting={kind:'analyze',state:'running',message:'正在准备核查',progress:1,started:Date.now()/1000};
 state.project.job=starting;showStep('review');renderJob();renderReview();
 try{
  const params=getParams();
  if(JSON.stringify(params)!==JSON.stringify(state.project.params)){const updated=await api(`/api/projects/${state.project.id}/settings`,{params});state.project=updated;state.project.job=starting;}
  const r=await api(`/api/projects/${state.project.id}/analyze`,{});state.project.job=r.job;renderJob();renderReview();pollJob();
 }catch(e){state.project.job=null;state.analyzeError=e.message||String(e);renderJob();renderReview();}
}
function renderAnalyzing(job,failure){
 $('#reviewEmpty').classList.add('hidden');$('#reviewContent').classList.remove('hidden');
 setReviewChapter('');
 $('#reviewWarnings').innerHTML='';$('#indexCaption').textContent='';$('#changeList').innerHTML='';
 $('#reviewProgress').textContent=failure?'核查未完成':'正在分析材料并生成修改对照';
 renderSteps();
 $('#changeDetail').innerHTML=failure
  ?'<div class="empty-selection analyzing"><h2>核查失败</h2><p class="reason">'+esc(failure)+'</p><button class="primary" id="retryAnalyze">重新核查</button></div>'
  :'<div class="empty-selection analyzing"><h2>正在核查材料</h2><p>'+esc(job?.message||'正在比对募集说明书与核查稿')+'</p><div class="progress-track"><div id="reviewJobBar" style="width:'+Math.max(2,job?.progress??2)+'%"></div></div><p class="quiet-note">完成后会直接进入第一项需要你决定的修改。</p></div>';
 if(failure)$('#retryAnalyze').onclick=()=>analyze();
}
function renderSteps(){
 const r=state.project?.result;
 const pending=r?reviewUnits().pending.length:0;
 const labels={import:'材料',review:r?(pending?('核对 '+pending):'核对 完成'):'核对',export:'导出'};
 $$('.step').forEach(b=>{b.textContent=labels[b.dataset.step]||b.dataset.step;b.classList.toggle('done',b.dataset.step==='import'?!!r:b.dataset.step==='review'?!!r&&!pending:false);});
}
function renderReview(){
 const j=state.project?.job;
 if(j&&j.kind==='analyze'&&j.state==='running'){renderAnalyzing(j);return;}
 if(state.analyzeError){renderAnalyzing(null,state.analyzeError);return;}
 const r=state.project?.result;$('#reviewEmpty').classList.toggle('hidden',!!r);$('#reviewContent').classList.toggle('hidden',!r);renderSteps();if(!r){setReviewChapter('');$('#reviewEmpty p').textContent=state.project?.result_invalidated?.reason||'先在“材料”中导入并开始核查。';return;}
 const q=reviewUnits();
 $('#reviewProgress').textContent=q.pending.length?`剩余 ${q.pending.length} 项需要你决定`:(Number(r.initial_manual||0)>0?'所有需要人工确认的事项均已处理，可以导出。':'未发现需要人工确认的修改，可以导出。');
 $('#reviewWarnings').innerHTML=q.pending.length?'':`<div class="done-banner">${Number(r.initial_manual||0)>0?'核对完成 · 所有需要人工确认的事项均已处理，可以导出。':'核查完成 · 未发现需要人工确认的修改，可以导出。'}</div>`;
 renderChangeList();
}
function missingCandidates(){const out=[];for(const d of state.project?.result?.documents||[])for(const c of d.missing_candidates||[])out.push({...c,_doc:d.name,_docid:d.id});return out;}
function missingFingerprintsMatch(decision,candidate){
 if(!decision)return false;
 const keys=['source_hash','source_body_hash','parent_fingerprint','previous_sibling_fingerprint','next_boundary_fingerprint'];
 return keys.every(k=>(decision[k]||'')===(candidate[k]||''));
}
function primaryMissingEvidence(rows){
 // Keep the browser on the exact same representative-evidence rule as the
 // server/export path: one logical missing item may be reported by several
 // formal sources, but its persisted decision is frozen to one representative.
 return rows.find(r=>r.source_kind==='prospectus')||rows[0]||null;
}
function missingItemGroups(){
 const map={},order=[];
 for(const c of missingCandidates()){
  // candidate_id is the logical identity (target location + item). Multiple
  // formal sources are evidence rows for that one decision, not extra tasks.
  const key=c.candidate_id;
  if(!map[key]){map[key]={key,item:c,rows:[],sources:[]};order.push(key);}
  const g=map[key];g.rows.push(c);
  const name=c.source_kind==='prospectus'?'募集说明书':c.source_kind==='opinion'?'核查意见':(c.source_kind||'正式来源');
  if(!g.sources.includes(name))g.sources.push(name);
 }
 const decisions=state.project?.missing_decisions||{};
 const out={primary:[],secondary:[],processed:[]};
 for(const key of order){
  const g=map[key],representative=primaryMissingEvidence(g.rows),decision=decisions[key];
  if(representative)g.item=representative;
  // The server stores one decision per logical candidate and binds its frozen
  // fingerprints to representative evidence. Requiring that same decision to
  // match every evidence row makes multi-source items reappear forever after
  // clicking “应用修改/保持原文”. Validate only the representative here too.
  if(decision&&representative&&missingFingerprintsMatch(decision,representative)){
   g.decision=decision;out.processed.push(g);continue;
  }
  // If a prior decision exists but its representative evidence changed, it is
  // genuinely stale and must return for confirmation.
  g.stale=!!decision;
  (g.item.evidence?.body_exists_elsewhere_in_target?out.secondary:out.primary).push(g);
 }
 return out;
}
function suggestMissingTitle(c){
 const prev=c.previous_sibling_heading||'',src=c.source_item_heading||'';
 const m=prev.match(/^\s*(?:[（(]([^）)]*)[）)]|(\d+(?:[.．]\d+)*)\s*[、.．)）])/);
 if(!m)return '';const cur=(m[1]||m[2]||'').trim();if(!/^\d+$/.test(cur))return '';
 const body=src.replace(/^\s*(?:第[一二三四五六七八九十百\d]+[章节部分]|[（(][一二三四五六七八九十百\d]+[）)]|[一二三四五六七八九十百\d]+[、.．]|\d+(?:[.．]\d+)*[、.．)）])\s*/,'');
 if(!body)return '';const n=String(Number(cur)+1);
 return m[1]?'（'+n+'）'+body:n+'、'+body;
}
function nextPendingAfter(id){
 const pending=reviewUnits().pending,idx=pending.findIndex(u=>u.id===id);
 if(idx<0)return pending[0]?.id||null;
 return pending[idx+1]?.id||pending[idx-1]?.id||null;
}
async function decideMissing(group,act){
 const consumed='m:'+group.key,nextId=nextPendingAfter(consumed);
 try{state.saving=true;setBusyUI();
  // One logical candidate = one persisted decision. Do not POST once per
  // evidence row: the server already resolves all rows to the representative
  // evidence and export uses that same identity.
  const c=primaryMissingEvidence(group.rows)||group.item;
  if(!c)throw new Error('缺项候选不存在，请重新核查。');
  const body={candidate_id:c.candidate_id,decision:act};
  if(act==='add'){const t=suggestMissingTitle(c);if(t)body.approved_title=t;}
  const last=await api(`/api/projects/${state.project.id}/missing`,body);
  state.project.missing_decisions=last.missing_decisions;
  state.selected=nextId;
  renderReview();
  $('.change-item.active')?.scrollIntoView({block:'nearest'});
  toast(act==='add'?'已应用修改，已从待处理列表移除':'已确认保持原文，已从待处理列表移除');
 }catch(e){error(e);}finally{state.saving=false;setBusyUI();}
}
// One decision per logical candidate — never per evidence row and never per
// title string. Two formal sources proving the same item is still one decision,
// while the same title in two chapters is two.
function missingOneLine(g){const who=g.sources.join('、');return who+'中存在「'+g.item.source_item_heading+'」，当前核查稿没有对应内容。';}
function missingDoneLine(g){const d=g.decision||{};return d.decision==='add'?'已确认应用修改：将新增「'+(d.approved_title||g.item.source_item_heading)+'」。':'已确认保持原文，文档不作新增。';}
function dedupeTransactions(list,all){
 const seen=new Set(),out=[];for(const p of list){const t=p.transaction_id||p.id;if(seen.has(t))continue;seen.add(t);const members=all.filter(x=>(x.transaction_id||x.id)===t);out.push({...p,transaction_member_count:members.length,has_table:members.some(x=>x.has_table),_members:members});}return out;
}
function proposalReasonLabel(){return '需要判断';}
function proposalOneLine(p){
 const chosen=(p.candidates||[])[p.selected??0]||{},txt=[p.reason,...(chosen.source_warnings||[])].filter(Boolean).join(' ');
 if(/版本|一致性提示|适用版本|不同位置或版本|多个不同来源/.test(txt))return '当前正式来源之间存在实质差异，程序无法安全判断应采用哪一版，需要你决定。';
 if((p.candidates||[]).length>1)return '存在多个会产生不同最终内容的正式来源，程序无法安全替你选择。';
 return '这一项没有可靠的安全默认，需要你决定是否修改。';
}
function reviewUnits(){
 const r=state.project?.result;if(!r)return {pending:[]};
 const ps=r.proposals||[],mg=missingItemGroups(),pending=[];
 for(const g of [...mg.primary,...mg.secondary]){
  if(g.item.evidence?.auto_excluded)continue;
  pending.push({kind:'missing',id:'m:'+g.key,title:g.item.source_item_heading,label:'是否新增'});
 }
 for(const p of dedupeTransactions(ps.filter(needsReview),ps))pending.push({kind:'proposal',id:p.id,title:p.transaction_title||p.heading||p.matter,label:'需要判断'});
 return {pending};
}
function currentReviewUnit(){return reviewUnits().pending.find(x=>x.id===state.selected)||null;}
function filteredChanges(){
 const q=reviewUnits();return q.pending.filter(u=>u.kind==='proposal').map(u=>state.project.result.proposals.find(p=>p.id===u.id)).filter(Boolean);
}
function chosenProposal(){return state.project?.result?.proposals.find(p=>p.id===state.selected);}
function missingGroupByKey(key){const mg=missingItemGroups();for(const g of [...mg.primary,...mg.secondary,...mg.processed])if(g.key===key)return g;return null;}
function renderChangeList(){
 if(!state.project?.result)return;
 const q=reviewUnits(),pending=q.pending;
 if(state.selected&&!pending.some(u=>u.id===state.selected))state.selected=pending[0]?.id||null;
 if(!state.selected)state.selected=pending[0]?.id||null;
 $('#indexCaption').textContent=pending.length?`剩余 ${pending.length} 项`:'核对完成';state.listEmpty=pending.length===0;
 const item=u=>`<button class="change-item ${state.selected===u.id?'active':''}" data-id="${esc(u.id)}"><h3>${esc(u.title)}</h3><span class="reason-chip">${esc(u.label)}</span></button>`;
 $('#changeList').innerHTML=pending.length?pending.map(u=>item(u)).join(''):'<div class="empty-selection compact">没有待处理事项。</div>';
 $$('.change-item').forEach(b=>b.onclick=()=>{state.selected=b.dataset.id;$$('.change-item').forEach(x=>x.classList.toggle('active',x.dataset.id===state.selected));renderDetail();});
 renderDetail();
}
function renderMissingDetail(key){
 const g=missingGroupByKey(key);
 if(!g){setReviewChapter('');$('#changeDetail').innerHTML='<div class="empty-selection">该事项不在当前候选中，请重新核查。</div>';return;}
 const c=g.item,d=g.decision||{},decided=!!d.decision,elsewhere=!!c.evidence?.body_exists_elsewhere_in_target;
 setReviewChapter(c._doc||'');
 const body=c.source_text||'',anchor=c.next_boundary_heading||c.target_parent_heading||'对应事项组';
 const positionText=c.previous_sibling_heading?`在「${c.previous_sibling_heading}」之后新增`:c.next_boundary_heading?`在「${c.next_boundary_heading}」之前新增`:`在「${c.target_parent_heading||'对应事项组'}」中新增`;
 const reason=elsewhere?'核查稿其他位置已有相关内容，但这个位置是否需要单独披露仍需确认。':'正式来源中存在这一事项，而当前核查稿目标位置没有对应内容。';
 $('#changeDetail').innerHTML=`<div class="detail-head simple"><div class="detail-labels"><span class="reason-chip">${elsewhere?'位置判断':'新增'}</span></div><h2>${esc(c.source_item_heading)}</h2><p class="reason plain">${esc(reason)}</p><p class="position-line">${esc(positionText)}</p></div>
 <div class="decision-compare"><section class="compare-pane before"><div class="compare-title">处理前</div><div class="document-preview compact-preview" id="ctxBefore"><p class="preview-placeholder">正在读取插入位置附近内容…</p></div></section><section class="compare-pane after"><div class="compare-title">处理后</div><div class="document-preview compact-preview" id="ctxAfter"><div class="inserted-block"><p>${esc(body.slice(0,260))}${body.length>260?'……':''}</p></div></div></section></div>
 <details class="evidence-card review-evidence" id="missingEvidence"><summary>判断依据</summary><p>来源：${esc(g.sources.join('、'))}</p><p>目标位置：${esc(c._doc)} → ${esc(c.target_parent_heading||'')}</p><p>同级事项覆盖：${c.matched_sibling_count}/${c.sibling_total}</p>${elsewhere?`<p>全文已有位置：${esc(c.evidence.body_elsewhere_at||'已找到相关正文')} · 内容覆盖 ${Math.round((c.evidence.body_coverage||0)*100)}%</p>`:''}</details>
 <div class="review-actionbar"><div class="action-state">${decided?(d.decision==='add'?'已应用修改':'已保持原文'):'确认这一项后会自动进入下一项'}</div><div class="action-buttons">${decided?'':`<button id="applyMissing" class="primary">应用修改</button><button id="skipMissing" class="secondary">保持原文</button>`}<button id="toggleMissingEvidence" class="text-button">查看依据</button></div></div>`;
 if($('#applyMissing'))$('#applyMissing').onclick=()=>decideMissing(g,'add');
 if($('#skipMissing'))$('#skipMissing').onclick=()=>decideMissing(g,'skip');
 $('#toggleMissingEvidence').onclick=()=>{const e=$('#missingEvidence');e.open=!e.open;if(e.open)e.scrollIntoView({block:'nearest'});};
 if(c._docid&&Number.isInteger(c.suggested_insert_position)){
  const pos=c.suggested_insert_position,ids=[pos-2,pos-1,pos,pos+1].filter(i=>i>=0).join(',');
  api(`/api/projects/${state.project.id}/preview?file=${c._docid}&indices=${ids}`).then(pv=>{
   const blocks=pv.blocks||[],idList=ids.split(',').filter(Boolean).map(Number),spot=Math.max(0,idList.indexOf(pos));
   const before=$('#ctxBefore'),after=$('#ctxAfter');if(!before||!after)return;
   before.replaceChildren();after.replaceChildren();
   const head=blocks.slice(0,spot),tail=blocks.slice(spot);
   renderBlocks(before,head);const slot=document.createElement('div');slot.className='missing-slot';slot.textContent='这里目前没有「'+c.source_item_heading+'」';before.append(slot);if(tail.length){const t=document.createElement('div');t.className='context-tail';renderBlocks(t,tail);before.append(t);}
   renderBlocks(after,head);const ins=document.createElement('div');ins.className='inserted-block';const title=document.createElement('p');title.className='heading';title.textContent=d.approved_title||suggestMissingTitle(c)||c.source_item_heading;const para=document.createElement('p');para.textContent=body;ins.append(title,para);after.append(ins);if(tail.length){const t=document.createElement('div');t.className='context-tail';renderBlocks(t,tail);after.append(t);}
  }).catch(()=>{});
 }
 setBusyUI();
}
async function renderDetail(){
 if(state.selected&&state.selected.startsWith('m:')){renderMissingDetail(state.selected.slice(2));return;}
 const token=++state.previewToken,p=chosenProposal();if(!p){setReviewChapter('');$('#changeDetail').innerHTML='<div class="empty-selection">'+(state.listEmpty?'核对完成，可以进入“导出”生成修订稿。':'选择一项，查看处理前和处理后。')+'</div>';return;}
 setReviewChapter(p.target_name||'');
 const retained=noCorrespondence(p),ci=p.selected??0,c=retained?null:p.candidates[ci],field=['metadata','field','voice'].includes(p.kind),locked=p.status==='protected'||retained,blockReason=c?.blocked_reason||c?.unsupported||'';
 const decided=p.decision!=='pending',tag=proposalReasonLabel(p);
 $('#changeDetail').innerHTML=`<div class="detail-head simple"><div class="detail-labels"><span class="reason-chip">${esc(tag)}</span></div><h2>${esc(p.transaction_title||p.heading||p.matter)}</h2><p class="reason plain">${esc(proposalOneLine(p))}</p></div>
 <div class="decision-compare"><section class="compare-pane before"><div class="compare-title">处理前</div><div class="document-preview compact-preview" id="beforePreview"><p class="preview-placeholder">正在读取当前内容…</p></div></section><section class="compare-pane after"><div class="compare-title">处理后</div><div class="document-preview compact-preview" id="afterPreview"><p class="preview-placeholder">正在生成修改结果…</p></div></section></div>
 <details class="evidence-card review-evidence" id="proposalEvidence"><summary>判断依据</summary>${c?`<p>来源：${esc(c.source_name)}</p><p>${esc(c.locator)}</p>${p.candidates.length>1&&Number(p.transaction_member_count||1)===1?`<label class="candidate-label">候选来源<select id="candidateSelect" aria-label="选择候选来源">${p.candidates.map((x,i)=>`<option value="${i}" ${i===ci?'selected':''}>${i+1} · ${esc(x.source_name.slice(0,24))} · ${esc(x.locator.slice(-80))}</option>`).join('')}</select></label>`:''}`:''}<p>目标位置：${esc(p.target_name)} · ${esc(p.locator)}</p>${blockReason?`<p class="evidence-warning">自动应用被阻止：${esc(blockReason)}</p>`:''}<details class="note-details"><summary>备注</summary><textarea id="decisionNote" aria-label="核对说明" placeholder="需要说明时填写">${esc(p.note||'')}</textarea></details></details>
 <div class="review-actionbar"><div class="action-state">${decided?(['accept','same'].includes(p.decision)?'已应用修改':p.decision==='keep'?'已保持原文':'当前按保护规则保留'):(blockReason?'这项不能自动应用，请保持原文或查看依据。':'确认这一项后会自动进入下一项')}</div><div class="action-buttons">${((!locked&&c)||field)?`<button id="acceptChange" class="primary" ${blockReason||['same','accept'].includes(p.decision)?'disabled data-unavailable="true"':''}>${blockReason?'暂不能应用':['same','accept'].includes(p.decision)?'已应用修改':'应用修改'}</button>`:''}${!locked?`<button id="keepChange" class="secondary">保持原文</button>`:''}<button id="toggleProposalEvidence" class="text-button">查看依据</button>${p.decision!=='pending'&&!p.auto&&!locked?'<button id="resetChange" class="text-button">改回待核对</button>':''}</div></div>`;
 if($('#candidateSelect'))$('#candidateSelect').onchange=async e=>{await saveDecision({...p,selected:Number(e.target.value)},'pending');};
 for(const [id,decision] of [['acceptChange','accept'],['keepChange','keep'],['resetChange','pending']])if($('#'+id))$('#'+id).onclick=()=>saveDecision(p,decision);
 $('#toggleProposalEvidence').onclick=()=>{const e=$('#proposalEvidence');e.open=!e.open;if(e.open)e.scrollIntoView({block:'nearest'});};setBusyUI();
 try{
  const plan=await api(`/api/projects/${state.project.id}/plan?proposal=${encodeURIComponent(p.id)}&selected=${ci}&full=0`);if(token!==state.previewToken)return;state.activePlan=plan;
  const before=$('#beforePreview'),after=$('#afterPreview');if(!before||!after)return;
  before.replaceChildren();after.replaceChildren();
  if(sourceInsert(p)){
   const pre=(plan.before||[]).slice(-2),post=(plan.after||[]).slice(0,2);renderBlocks(before,pre);const slot=document.createElement('div');slot.className='missing-slot';slot.textContent='这里当前没有拟新增内容';before.append(slot);if(post.length){const t=document.createElement('div');t.className='context-tail';renderBlocks(t,post);before.append(t);}
   renderBlocks(after,pre);const ins=document.createElement('div');ins.className='inserted-block';renderBlocks(ins,plan.replacement||[]);after.append(ins);if(post.length){const t=document.createElement('div');t.className='context-tail';renderBlocks(t,post);after.append(t);}
  }else{
   renderBlocks(before,plan.original||[]);
   if(plan.deletion_only){const gone=document.createElement('div');gone.className='removed-result';gone.textContent='应用后，这一段内容将删除。';after.append(gone);}
   else{
    const actual=p.decision==='keep'?(plan.original||[]):((plan.replacement&&plan.replacement.length)?plan.replacement:(plan.retained_after||plan.original||[]));renderBlocks(after,actual);if(p.decision!=='keep'&&!retained)markChangedAgainst(after,plan.original||[]);
   }
  }
  if(plan.error){const warn=document.createElement('p');warn.className='preview-warning';warn.textContent=plan.error;after.prepend(warn);}
 }catch(e){if(token===state.previewToken){$('#beforePreview').innerHTML='<p class="preview-warning">预览读取失败。</p>';$('#afterPreview').innerHTML='<p class="preview-warning">'+esc(e.message)+'</p>';const btn=$('#acceptChange');if(btn){btn.disabled=true;btn.dataset.unavailable='true';btn.textContent='预览未完成';}}}
}
function splitSentences(t){return String(t).split(/(?<=[。；！？])/).filter(x=>x.trim());}
function markChangedAgainst(container,oldBlocks){
 if(!container)return;const olds=new Set();
 (oldBlocks||[]).forEach(b=>{if(b.text)splitSentences(b.text).forEach(x=>olds.add(x));});
 container.querySelectorAll('p').forEach(p=>{const t=p.textContent;if(!t.trim()||p.className==='heading')return;
  p.innerHTML=splitSentences(t).map(x=>olds.has(x)?esc(x):'<mark>'+esc(x)+'</mark>').join('');});
}
function renderBlocks(node,blocks){
 node.replaceChildren();for(const b of blocks){
  if(b.kind==='table'&&b.rows){
   const wrap=document.createElement('div');wrap.className='table-scroll';wrap.tabIndex=0;wrap.setAttribute('aria-label','完整表格，可横向滚动');const table=document.createElement('table');const active=new Map();
   for(const row of b.rows){const tr=document.createElement('tr');let col=0;
    for(const cell of row){const span=Math.max(1,Number(cell.colspan)||1);if(cell.vertical==='continue'&&active.has(col)){active.get(col).rowSpan++;col+=span;continue;}
     const td=document.createElement('td');td.textContent=cell.text;td.colSpan=span;
     for(let j=col;j<col+span;j++)active.delete(j);
     if(cell.vertical==='restart')active.set(col,td);tr.append(td);col+=span;
    }table.append(tr);
   }wrap.append(table);node.append(wrap);
  }else if(b.text){const p=document.createElement('p');p.textContent=b.text;if(b.heading)p.className='heading';node.append(p);}
  for(const url of b.images||[]){const img=document.createElement('img');img.src=url;img.alt='当前查看范围中的图片';img.loading='lazy';node.append(img);}
 }
 if(!node.children.length)node.textContent='此范围没有可展示的文字。';
}
async function saveDecision(p,decision){
 if(busy())return;const nextId=decision==='pending'?p.id:nextPendingAfter(p.id);state.saving=true;setBusyUI();
 try{clearError();const note=$('#decisionNote')?.value||'',selected=p.selected??0;const r=await api(`/api/projects/${state.project.id}/decision`,{id:p.id,decision,selected,note});for(const updated of (r.proposals||[])){const i=state.project.result.proposals.findIndex(x=>x.id===updated.id);if(i>=0)state.project.result.proposals[i]=updated;}state.project.result.summary=r.summary;if(r.documents)state.project.result.documents=r.documents;state.project.history=r.history;state.project.export=null;state.selected=nextId;renderReview();$('.change-item.active')?.scrollIntoView({block:'nearest'});toast(decision==='accept'?'已应用修改，导出时写入文档':decision==='keep'?'已确认保持原文，文档不作修改':'已改回待核对');}catch(e){error(e);}finally{state.saving=false;setBusyUI();}
}
function renderExport(){
 const r=state.project?.result;$('#exportEmpty').classList.toggle('hidden',!!r);$('#exportContent').classList.toggle('hidden',!r);if(!r){$('#exportEmpty p').textContent=state.project?.result_invalidated?.reason||'完成分析后即可生成修订稿。';return;}const s=r.summary,x=state.project.export,counts=reviewCounts(r);
 const manualPending=reviewUnits().pending.length;const warnings=[];if(manualPending)warnings.push(`仍有 ${manualPending} 项需要确认；继续导出时这些位置保持当前核查稿内容不变。`);if(s.cross_project)warnings.push(`${s.cross_project} 份涉及跨发行人更新，需重新核验。`);
 // Export is the user's final action screen, not an internal diagnostics dashboard.
 // Non-blocking quality/source notes are kept in project data for audit/debugging but
 // must not become extra work for the user after the manual queue is already clear.
 const failed=x?.checks?.filter(q=>!q.roundtrip||!q.unrelated_parts_preserved||q.structure_errors.length)||[];
 $('#exportContent').innerHTML=`<div class="export-hero"><h2>${x?'文件已生成':'导出修订稿'}</h2><p>保留原标题和编号；对应内容从募集说明书或核查意见实际复制，记录删除与插入修订，相同内容也复制。同科目新增正文保留插入修订；无对应内容原样保留。</p><p>${s.documents} 份底稿 · ${counts.copied} 处来源复制 · ${s.tables_sync} 张表格 · ${counts.noCorrespondence} 个无对应对象保留 · ${manualPending} 项待处理</p>${warnings.map(t=>'<div class="notice warning">'+esc(t)+'</div>').join('')}${x&&failed.length===0?'<div class="export-check-ok">导出结构检查通过</div>':''}${failed.map(q=>`<div class="notice warning">${esc(q.file)} 导出检查存在异常，请查看核查报告。</div>`).join('')}<div class="export-actions">${x?`<a class="primary" id="downloadZip" href="${x.url}" download>下载文件</a>`:'<button id="generateExport" class="primary">生成文件</button>'}</div></div>`;
 if($('#generateExport'))$('#generateExport').onclick=async()=>{
  const manualPending=reviewUnits().pending.length;
  const doExport=async()=>{try{clearError();const out=await api(`/api/projects/${state.project.id}/export`,{});state.project.job=out.job;renderJob();pollJob();}catch(e){error(e);}};
  if(manualPending>0){openModal('还有未处理的事项','<p>还有 <strong>'+manualPending+'</strong> 项未处理。继续导出将保持这些位置的当前核查稿内容不变。</p><div style="margin-top:14px;display:flex;gap:10px"><button class="primary" id="forceExport">继续导出</button><button class="secondary" id="backReview" type="button">返回核对</button></div>');$('#forceExport').onclick=doExport;$('#backReview').onclick=()=>{$('#modal').close();showStep('review');};return;}
  doExport();};setBusyUI();
}
function openModal(title,body){$('#modalTitle').textContent=title;$('#modalBody').innerHTML=body;if(!$('#modal').open)$('#modal').showModal();requestAnimationFrame(()=>$('#modalBody input,#modalBody button,#modalClose')?.focus());}
function openText(title,text){openModal(title,'<pre class="preview-text">'+esc(text)+'</pre>');}
function bind(){
 bindPolish();
 $('#newProject').onclick=createProject;$('#projectName').onclick=()=>{if(busy())return;openModal('项目名称','<input id="renameInput" maxlength="100" aria-label="项目名称" value="'+esc(state.project.name)+'"><div style="margin-top:16px"><button id="saveName" class="primary">保存</button></div>');$('#saveName').onclick=async()=>{try{state.project=await api(`/api/projects/${state.project.id}/settings`,{name:$('#renameInput').value});$('#modal').close();$('#projectName').textContent=state.project.name;await refreshProjects();}catch(e){error(e);}};};
 $$('.step').forEach(b=>b.onclick=()=>showStep(b.dataset.step));
 $('#pickFiles').onclick=e=>{e.stopPropagation();$('#fileInput').click();};$('#pickFolder').onclick=e=>{e.stopPropagation();$('#folderInput').click();};
 $('#fileInput').onchange=e=>uploadFiles(Array.from(e.target.files));$('#folderInput').onchange=e=>uploadFiles(Array.from(e.target.files));
 const zone=$('#dropzone');zone.onclick=e=>{if(!e.target.closest('button,input')&&!busy())$('#fileInput').click();};zone.onkeydown=e=>{if(e.target===zone&&['Enter',' '].includes(e.key)){e.preventDefault();$('#fileInput').click();}};
 ['dragenter','dragover'].forEach(t=>zone.addEventListener(t,e=>{e.preventDefault();if(!busy())zone.classList.add('dragging');}));['dragleave','drop'].forEach(t=>zone.addEventListener(t,e=>{e.preventDefault();zone.classList.remove('dragging');}));zone.addEventListener('drop',e=>uploadFiles(Array.from(e.dataTransfer.files)));
 document.addEventListener('dragover',e=>e.preventDefault());document.addEventListener('drop',e=>e.preventDefault());
 $('#fileSearch').oninput=renderMaterials;$('#showOtherFiles').onchange=renderMaterials;$('#analyzeBtn').onclick=analyze;
 $('#modalClose').onclick=()=>$('#modal').close();$('#dismissError').onclick=clearError;
 window.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#modal').open)$('#modal').close();});
}

function allIssues(){
 const r=state.project?.result;if(!r)return [];
 const objects=r.documents.flatMap(d=>(d.object_inventory||[]).filter(x=>['protected','pending'].includes(x.status)).map(x=>({origin:'inventory',object_status:x.status,content_class:x.content_class,title:objectLabel(x)+' · '+(x.text||'空段或结构对象').slice(0,65),detail:x.reason,file:d.name,file_id:d.id,block:x.block,locator:x.locator,text:x.text,evidence:[{text:x.text||'空段或结构对象',file:d.name,file_id:d.id,block:x.block,locator:x.locator}]})));
 return [...(r.source_issues||[]).map(i=>({...i,origin:'source'})),...r.documents.flatMap(d=>(d.quality_issues||[]).map(i=>({...i,file:i.file||d.name,file_id:i.file_id||d.id,origin:'target'}))),...objects].map((i,n)=>({...i,key:'issue-'+n}));
}
const issueLabel=i=>i.origin==='source'?'来源待核实':i.origin==='inventory'?objectLabel({status:i.object_status,content_class:i.content_class}):'底稿提示';
function issueItems(){const q=($('#reviewSearch')?.value||'').trim().toLowerCase(),doc=$('#docFilter')?.value||'';return allIssues().filter(i=>(!doc||i.file_id===doc||i.origin==='source')&&(!q||(i.title+i.detail+i.locator+i.file+(i.text||'')).toLowerCase().includes(q)));}
function renderIssueList(){
 const items=issueItems();$('#indexCaption').textContent=`${items.length} 项提示与对象记录 · 点击查看原因和原文`;
 if(!items.some(i=>i.key===state.selected))state.selected=items[0]?.key||null;
 $('#changeList').innerHTML=items.map(i=>`<button class="change-item ${state.selected===i.key?'active':''}" data-issue="${i.key}"><h3>${esc(i.title)}</h3><p><span class="badge ${i.origin==='source'?'source':i.object_status==='protected'?'protected':'pending'}">${issueLabel(i)}</span></p><small title="${esc(i.file)}">${esc(i.file)}</small></button>`).join('')||'<div class="empty-selection">当前筛选下没有提示。<br>不代表所有事实已经核实。</div>';
 $$('[data-issue]').forEach(b=>b.onclick=()=>{state.selected=b.dataset.issue;$$('[data-issue]').forEach(x=>x.classList.toggle('active',x.dataset.issue===state.selected));renderIssueDetail();});renderIssueDetail();
}
function renderIssueDetail(){
 ++state.previewToken;const i=allIssues().find(i=>i.key===state.selected);if(!i){$('#changeDetail').innerHTML='<div class="empty-selection">选择一项提示，查看原始依据。</div>';return;}
 const seen=new Set(),evidence=(i.evidence||[]).filter(e=>{const k=(e.value||e.text)+e.file_id;if(seen.has(k))return false;seen.add(k);return true;});
 $('#changeDetail').innerHTML=`<div class="issue-detail"><div class="detail-head"><span class="badge source">${issueLabel(i)}</span><h2>${esc(i.title)}</h2><div class="path">${esc(i.file)}<br>${esc(i.locator||'全文检查')}</div></div><div class="notice ${i.object_status==='protected'?'':'warning'}">${esc(i.detail)}</div>${evidence.map((e,n)=>`<div class="issue-evidence"><h3>${esc(e.period||'原文')} ${esc(e.metric||'')} ${esc(e.scope||'')} ${e.value?' · '+esc(e.value):''}</h3><p>${esc(e.text||'')}</p><small>${esc(e.file||i.file)} · ${esc(e.locator||'')}</small>${Number.isInteger(e.block)?`<button class="secondary small" data-evidence="${n}">查看原文位置</button>`:''}</div>`).join('')}${!evidence.length&&Number.isInteger(i.block)?'<button class="secondary small" id="issueOrigin">查看原文位置</button>':''}<div class="detail-nav"><button id="previousIssue">上一项</button><button id="nextIssue">下一项</button></div></div>`;
 $$('[data-evidence]').forEach(b=>b.onclick=()=>{const e=evidence[Number(b.dataset.evidence)];openEvidence(e.file_id||i.file_id,e.block,e.file||i.file);});
 if($('#issueOrigin'))$('#issueOrigin').onclick=()=>openEvidence(i.file_id,i.block,i.file);
 $('#previousIssue').onclick=()=>navigateChange(-1);$('#nextIssue').onclick=()=>navigateChange(1);
}
async function openEvidence(fid,block,name){
 try{const r=await api(`/api/projects/${state.project.id}/preview?file=${fid}&start=${block}&end=${block+1}`);openModal('原文位置','<p>'+esc(name)+' · 结构块 '+(block+1)+'（非 WPS 页码）</p><div id="evidencePreview" class="document-preview"></div>');renderBlocks($('#evidencePreview'),r.blocks);}catch(e){error(e);}
}
function patchSummary(a,b){let l=0,r=0;while(l<Math.min(a.length,b.length)&&a[l]===b[l])l++;while(r<Math.min(a.length-l,b.length-l)&&a[a.length-r-1]===b[b.length-r-1])r++;return `<div class="difference-summary"><strong>本次只调整这段文字</strong>${esc(a.slice(Math.max(0,l-24),l))}<del>${esc(a.slice(l,a.length-r))}</del> <ins>${esc(b.slice(l,b.length-r))}</ins>${esc(b.slice(b.length-r,b.length-r+24))}</div>`;}
function navigateChange(direction){
 const list=reviewUnits().pending,i=list.findIndex(x=>x.id===state.selected),next=list[i+direction];if(!next){toast(direction>0?'已经是最后一项。':'已经是第一项。');return;}state.selected=next.id;renderChangeList();$('.change-item.active')?.scrollIntoView({block:'nearest',inline:'nearest'});
}
async function historyAction(action){
 if(busy()||!state.project?.history?.['can_'+action])return;state.saving=true;setBusyUI();
 try{state.project=await api(`/api/projects/${state.project.id}/${action}`,{});if(state.step==='review')renderReview();if(state.step==='export')renderExport();toast(action==='undo'?'已撤销上一次核对决定。':'已重做核对决定。');}catch(e){error(e);}finally{state.saving=false;setBusyUI();}
}
function bindPolish(){
 $('#toggleSidebar').onclick=()=>{document.body.classList.toggle('sidebar-open');$('#toggleSidebar').setAttribute('aria-expanded',String(document.body.classList.contains('sidebar-open')));};
 document.addEventListener('pointerdown',e=>{if(innerWidth<=900&&!e.target.closest('.sidebar,#toggleSidebar'))document.body.classList.remove('sidebar-open');});
 $('#undoBtn').onclick=()=>historyAction('undo');$('#redoBtn').onclick=()=>historyAction('redo');
 $('#cancelJob').onclick=async()=>{try{await api(`/api/projects/${state.project.id}/cancel`,{});$('#cancelJob').disabled=true;toast('正在停止');}catch(e){error(e);}finally{$('#cancelJob').disabled=false;}};
 document.addEventListener('keydown',e=>{const input=e.target.closest('input,textarea,select,[contenteditable=true]'),mod=e.metaKey||e.ctrlKey;if(mod&&e.key.toLowerCase()==='z'&&!input&&!$('#modal').open){e.preventDefault();historyAction(e.shiftKey?'redo':'undo');}else if(!mod&&!input&&!$('#modal').open&&state.step==='review'&&['j','k'].includes(e.key)){e.preventDefault();navigateChange(e.key==='j'?1:-1);}});
}
(async()=>{bind();try{await api('/api/health');await refreshProjects();const saved=localStorage.getItem('workbenchProject');if(state.projects.some(p=>p.id===saved))await loadProject(saved);else if(state.projects.length)await loadProject(state.projects[0].id);else await createProject();}catch(e){error(e);}})();
