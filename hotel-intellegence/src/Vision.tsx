import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, ChevronRight, ClipboardList, FileImage, Plus, Upload, Video } from 'lucide-react';
import { EmptyState, WorkHeading, StateTag } from './Workspace';

type Summary = { id: string; filename: string; created: string; scene: string; state: string; review: string };
type Group = { frames: {file: string; seconds: number | null}[]; findings: {name: string; status: string; detail: string; level: string}[] };
type Report = Summary & {kind: string; error: string; groups: Group[]; model: string; reviewNote?: string};
type Setup = {installed: boolean; profiles: Record<string,string[]>; model: string};
const base = '/api/vision';
const stamp = (s: number | null) => s === null ? '输入图片' : `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5,'0')}`;
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(base + path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : '巡检服务请求失败');
  }
  return response.json();
}
function exportReport(report: Report) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = report.filename + '-巡检报告.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Vision({token,pending,setPending,canExport=true}: {token: string;pending:boolean;setPending:(value:boolean)=>void;canExport?:boolean}) {
  const [creating, setCreating] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [scene, setScene] = useState('公区卫生');
  const [checks, setChecks] = useState<string[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [interval, setIntervalValue] = useState(5);
  const [standard, setStandard] = useState('');
  const [jobs, setJobs] = useState<Summary[]>([]);
  const [offset, setOffset] = useState(0);
  const [id, setId] = useState(window.location.hash.match(/^#vision\/([a-f0-9]{32})$/)?.[1] || '');
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [note, setNote] = useState('');
  const options = setup?.profiles[scene] || [];
  const chosen = checks ?? options;
  async function importTemplate(input?: File) {
    if (!input || !setup) return;
    try {
      if (input.size > 32768) throw new Error();
      const template = JSON.parse(await input.text());
      const allowed = setup.profiles[template.scene];
      if (!allowed || !Array.isArray(template.checks) || !template.checks.length ||
          !template.checks.every((x: unknown) => typeof x === 'string' && allowed.includes(x)) ||
          typeof template.standard !== 'string' || template.standard.length > 1200) throw new Error();
      setScene(template.scene); setChecks(template.checks); setStandard(template.standard); setError('');
    } catch {setError('模板格式无效，请使用下方示例中的场景和检查项名称。');}
  }
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const [config, list, selected] = await Promise.all([
          request<Setup>('/status'), request<Summary[]>('/jobs?limit=41&offset='+offset+'&pending='+pending), id ? request<Report>('/jobs/' + id) : null]);
        if (active) {
          setSetup(config); setJobs(list); setReport(selected); setRefreshError('');
        }
      } catch (e) { if (active) setRefreshError((e as Error).message); }
      if (active) timer = setTimeout(refresh, 2500);
    }
    refresh();
    return () => {active = false; clearTimeout(timer);};
  }, [id,offset,pending]);
  function select(next: string) {
    window.history.replaceState(null, '', next ? '#vision/'+next : '#vision');
    setCreating(false); setReport(null); setId(next); setNote(''); setError('');}
  async function submit() {
    if (!file || !chosen.length) return;
    setBusy(true); setError('');
    try {
      const body = new FormData();
      body.set('file',file); body.set('scene',scene); body.set('standard',standard);
      body.set('checks',JSON.stringify(chosen)); body.set('interval',String(interval));
      const job = await request<{id:string}>('/jobs',{method:'POST', headers:{'x-local-token':token},body});
      select(job.id);
    } catch(e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  async function action(state?: string) {
    setBusy(true); setError('');
    try {
      if (state) {
        const updated = await request<Report>('/jobs/'+id+'/review',{method:'POST',headers:{'x-local-token':token,'Content-Type':'application/json'},body:JSON.stringify({state,note:note || report?.reviewNote || ''})});
        setReport(updated);
      } else {
        await request('/jobs/'+id+'/retry',{method:'POST',headers:{'x-local-token':token}});
        setReport(await request<Report>('/jobs/'+id));
      }
    } catch(e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  const visibleJobs = jobs.slice(0,40);
  return <section className="work-page vision">
    {(creating || id) && <button className="work-back" onClick={()=>select('')}><ArrowLeft size={16}/>返回巡检记录</button>}
    <WorkHeading title={creating ? '新建巡检' : id ? '巡检报告' : '影像巡检'}
      description={creating ? '选择现场素材与检查项，在本机分析。' : id ? undefined : '查看现场影像、巡检证据与整改进度。'}>
      {!creating && !id && <button className="primary" onClick={()=>{setError('');setCreating(true);}}><Plus size={16}/>新建巡检</button>}
    </WorkHeading>
    {(error || refreshError) && <p role="alert" className="work-notice">{error || refreshError}</p>}
    {creating && <section className="work-card vision-card vision-compose">
      <h2>巡检素材</h2>
      <input hidden ref={fileInput} type="file" accept=".jpg,.jpeg,.png,.bmp,.webp,.mp4,.mov,.mkv,.avi,.m4v,.webm" onChange={e=>setFile(e.target.files?.[0] || null)}/>
      <button className="vision-upload" onClick={()=>fileInput.current?.click()}>
        <span className="work-icon"><Upload size={22}/></span><strong>{file?.name || '选择图片或视频'}</strong>
        <small>{file ? '点击更换素材' : '单文件最多 300 MB，视频最长 30 分钟'}</small>
      </button>
      <h2>检查设置</h2>
      <div className="vision-fields"><label>巡检场景<select value={scene} onChange={e=>{setScene(e.target.value); setChecks(null);}}>{Object.keys(setup?.profiles || {'公区卫生':[]}).map(s=><option key={s}>{s}</option>)}</select></label>
      <label>视频采样间隔（秒）<input type="number" min="1" max="120" value={interval} onChange={e=>setIntervalValue(Number(e.target.value))}/></label></div>
      <fieldset className="vision-options"><legend>检查项</legend>{options.map(name=><label key={name}><input type="checkbox" checked={chosen.includes(name)} onChange={e=>setChecks(e.target.checked ? [...chosen,name] : chosen.filter(x=>x!==name))}/>{name}</label>)}</fieldset>
      <label>现场检查标准（选填）<textarea rows={2} maxLength={1200} value={standard} onChange={e=>setStandard(e.target.value)} placeholder="例如：画面右侧门内为禁入区域；仅在该条件适用于本段素材时填写。"/></label>
      <details className="vision-group"><summary>导入场景检查模板</summary><p>JSON 示例</p><code>{JSON.stringify({scene:'公区卫生',checks:['地面垃圾','物品乱堆'],standard:'只检查通道可见地面。'})}</code><label>模板文件<input type="file" accept=".json" onChange={e=>importTemplate(e.target.files?.[0])}/></label></details>
      <p className="muted">每段选取最多 8 个时间窗口，每个窗口最多 3 帧。长视频会拉大窗口间隔，短时事件可能未被采到。</p>
      <div className="compose-foot"><button className="primary" disabled={busy || !file || !token || !chosen.length || !setup?.installed || interval<1 || interval>120} onClick={submit}>{busy?'正在提交…':'开始本地分析'}</button>
      <small>{setup ? (setup.installed ? setup.model : '等待本地视觉模型安装') : '正在连接巡检服务…'}</small></div></section>}
    {id && !report && !refreshError && <p role="status">正在读取巡检记录…</p>}
    {id && report && <section className="work-card vision-card vision-report"><div className="section-title"><div><h2>{report.filename}</h2><p>{report.scene} · {report.kind}　<StateTag value={report.state}/> <StateTag value={report.review}/></p></div>
      {report.state==='已完成' && canExport && <div className="vision-checks"><a className="secondary small" href={base+'/jobs/'+id+'/export'} download>导出图文报告</a><button className="secondary small" onClick={()=>exportReport(report)}>导出 JSON</button></div>}</div>
      {report.state==='分析中' && <p role="status">正在本机分析，已完成 {report.groups.length} 组画面。</p>}
      {report.state==='已完成' && <p>{report.groups.some(g=>g.findings.some(f=>f.status==='发现异常')) ?
        '待核实异常：'+[...new Set(report.groups.flatMap(g=>g.findings.filter(f=>f.status==='发现异常').map(f=>f.level+' · '+f.name)))].join('、') :
        report.groups.some(g=>g.findings.some(f=>f.status==='无法判断')) ? '采样画面中未报告异常，部分检查项无法判断。' : '所检查的画面中未见所选异常。'}</p>}
      {report.error && <p role="alert">{report.error} <button className="secondary" disabled={busy} onClick={()=>action()}>重新分析</button></p>}
      {report.groups.map((group,i)=><details className="vision-group" key={i} open={report.groups.length===1 || undefined}>
        <summary>{stamp(group.frames[0].seconds)}{group.frames.length>1 && ' – '+stamp(group.frames[group.frames.length-1].seconds)} · {group.findings.filter(x=>x.status==='发现异常').length} 项异常 · {group.findings.filter(x=>x.status==='无法判断').length} 项无法判断</summary>
        <div className="vision-frames">{group.frames.map(f=><figure key={f.file}><a href={base+'/jobs/'+id+'/frames/'+f.file} target="_blank" rel="noreferrer"><img src={base+'/jobs/'+id+'/frames/'+f.file} alt={stamp(f.seconds)+' 证据图'}/></a><figcaption>{stamp(f.seconds)}</figcaption></figure>)}</div>
        {group.findings.map(f=><div className="finding" key={f.name}><strong>{f.name}</strong> <span className={'tag '+(f.status==='发现异常'?'amber':f.status==='未见异常'?'green':'neutral')}>{f.status}</span> {f.level}<p>{f.detail}</p></div>)}</details>)}
      {report.state==='已完成' && <div><label>复核与整改记录<textarea placeholder={report.reviewNote || '填写复核意见或整改结果'} value={note} maxLength={1000} onChange={e=>setNote(e.target.value)}/></label>{report.reviewNote && <p>已记录：{report.reviewNote}</p>}<div className="vision-checks">{['已确认','已标误报','待整改','已复查'].map(state=><button className="secondary small" disabled={busy} key={state} onClick={()=>action(state)}>{state}</button>)}</div></div>}</section>}
    {!id && !creating && <section className="work-card vision-ledger">
      <header className="work-card-head"><ClipboardList size={19}/><h2>巡检记录</h2>
        <div className="work-switch" aria-label="复核状态">{[false,true].map(value=><button key={String(value)} aria-pressed={pending===value}
          onClick={()=>{setOffset(0);setPending(value);}}>{value?'待整改':'全部记录'}</button>)}</div>
      </header>
      {visibleJobs.length ? <ul className="vision-list">{visibleJobs.map(job=>
        <li key={job.id}><button onClick={()=>select(job.id)}>
          <span className="work-icon"><FileImage size={19}/></span>
          <span className="job-name"><strong>{job.filename}</strong><small>{job.scene} · {new Date(job.created).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})}</small></span>
          <span className="job-states"><StateTag value={job.state}/><StateTag value={job.review}/></span><ChevronRight size={16}/>
        </button></li>)}</ul> : <EmptyState icon={Video}
          title={!setup ? (refreshError ? '巡检记录暂不可用' : '正在读取巡检记录…') : pending ? '暂无待整改记录' : '暂无巡检记录'}
          detail={setup ? (pending ? '标记为待整改的巡检会出现在这里。' : '新建一次影像巡检，或从定期巡检接收报告。') : undefined}/>}
      {(offset>0||jobs.length>40)&&<div className="work-pager"><button className="secondary small" disabled={!offset} onClick={()=>setOffset(n=>Math.max(0,n-40))}>上一页</button><span>第 {offset/40+1} 页</span><button className="secondary small" disabled={jobs.length<=40} onClick={()=>setOffset(n=>n+40)}>下一页</button></div>}
    </section>}
  </section>;
}

