import {useEffect, useState} from 'react';
import { ArrowLeft, Plus, Search, Video, History } from 'lucide-react';
import { EmptyState, WorkHeading, StateTag } from './Workspace';

type Camera = {id:string; name:string; scene:string; standard:string; minutes:number; enabled:number;
  due:number; phase:string; ended:number|null; outcome:string|null; last_job:string|null};
type Attempt = {name:string; started:number; ended:number|null; outcome:string; job:string|null};
type Overview = {cameras:Camera[]; history:Attempt[]; running:boolean; workers:number};
const date = (t:number|null) => t ? new Date(t*1000).toLocaleString('zh-CN',
  {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) : '—';

export default function Patrol({token, openReport}:{token:string; openReport:(id:string)=>void}) {
  const [data,setData] = useState<Overview|null>(null);
  const [edit,setEdit] = useState<string|null>(null);
  const [name,setName] = useState('');
  const [sources,setSources] = useState('');
  const [minutes,setMinutes] = useState(10);
  const [scene,setScene] = useState('公区卫生');
  const [standard,setStandard] = useState('');
  const [filter,setFilter] = useState('');
  const [feedback,setFeedback] = useState('');
  const [offline,setOffline] = useState(false);
  const [busy,setBusy] = useState(false);
  const [revision,setRevision] = useState(0);
  const [removing,setRemoving] = useState('');
  const [scenes,setScenes] = useState<string[]>([]);
  const [showForm,setShowForm] = useState(false);
  useEffect(()=>{
    fetch('/api/vision/status').then(r=>r.json()).then(x=>setScenes(Object.keys(x.profiles))).catch(()=>{});
  },[]);
  useEffect(()=>{
    let alive=true;
    let timer:ReturnType<typeof setTimeout>;
    async function refresh(){
      try {
        const r=await fetch('/api/patrol');
        if(!r.ok) throw Error();
        const result=await r.json();
        if(alive){setData(result);setOffline(false);}
      } catch {if(alive)setOffline(true);}
      if(alive)timer=setTimeout(refresh,3500);
    }
    refresh();
    return ()=>{alive=false; clearTimeout(timer);};
  },[revision]);
  function reset(camera?:Camera){
    setEdit(camera?.id ?? null);setName(camera?.name ?? '');setSources('');
    setMinutes(camera?.minutes ?? 10);setScene(camera?.scene ?? '公区卫生');setStandard(camera?.standard ?? '');
    setFeedback('');setShowForm(true);
  }
  async function mutate(path:string,method:string,body?:unknown){
    const r=await fetch('/api/patrol/cameras'+path,{method,headers:{'x-local-token':token,'Content-Type':'application/json'},
      body:body===undefined?undefined:JSON.stringify(body)});
    if(!r.ok){const x=await r.json().catch(()=>({}));throw Error(typeof x.detail==='string'?x.detail:'操作失败，请重试');}
  }
  async function save(){
    setBusy(true);setFeedback('');
    try {
      if(!Number.isInteger(minutes)||minutes<1||minutes>1440)throw Error('间隔须为 1–1440 分钟的整数');
      if(edit){
        await mutate('/'+edit,'PUT',{name,minutes,scene,standard,url:sources.trim()||null});
        setFeedback('计划已更新，下次抓帧使用新配置。');
      } else {
        const rows=sources.trim().split(/\r?\n/).filter(s=>s.trim()).map((line,i)=>{
          const match=line.trim().match(/^(.+?)\s+(rtsp:\/\/\S+)$/i);
          if(!match)throw Error('第 '+(i+1)+' 行格式应为“点位名称 空格 RTSP地址”');
          return {name:match[1].trim(),url:match[2],minutes,scene,standard};
        });
        if(!rows.length)throw Error('请至少填写一个点位');
        await mutate('','POST',rows);setFeedback('已添加并启用 '+rows.length+' 个点位。');
      }
      setSources('');setShowForm(false);setRevision(n=>n+1);
    } catch(e){setFeedback((e as Error).message);} finally{setBusy(false);}
  }
  async function control(id:string,action:string){
    setBusy(true);setFeedback('');
    try{
      await mutate('/'+id+'/'+action,'POST');setRemoving('');setRevision(n=>n+1);
      setFeedback(({pause:'已暂停后续巡检，在途任务会完成。',resume:'已恢复巡检。',now:'已安排本轮巡检。',remove:'已移除点位，已有报告保留。'} as Record<string,string>)[action]);
    }catch(e){setFeedback((e as Error).message);}finally{setBusy(false);}
  }
  const list=data?.cameras||[];
  const now=Date.now()/1000;
  const active=list.filter(c=>c.enabled);
  const due=active.filter(c=>c.phase==='idle' && c.due<=now);
  const selected=list.filter(c=>(c.name+' '+c.scene).toLowerCase().includes(filter.toLowerCase()));
  function status(c:Camera){
    if(c.phase==='capture')return '抓帧中';
    if(c.phase==='analysis')return '等待或正在分析';
    if(!c.enabled)return '已暂停';
    if(c.due<=now)return '到期候检';
    return '等待下次巡检';
  }
  return <section className="work-page vision">
    {showForm && <button className="work-back" onClick={()=>{setShowForm(false);setSources('');setFeedback('');}}><ArrowLeft size={16}/>返回巡检点位</button>}
    <WorkHeading title={showForm ? (edit ? '修改点位' : '添加视频流') : '点位与定期巡检'}
      description={showForm ? '配置视频流与采样计划。' : '管理摄像头点位，跟进定期巡检的运行情况。'}>
      {!showForm && <button className="primary" onClick={()=>reset()}><Plus size={16}/>添加视频流</button>}
    </WorkHeading>
    {offline&&<p role="alert" className="work-notice">暂时无法连接本地调度服务。</p>}
    {feedback&&<p role="status" className="work-notice">{feedback}</p>}
    {showForm ? <section className="work-card vision-card vision-compose"><h2>{edit?'点位配置':'批量添加点位'}</h2>
      {edit?<><label>点位名称<input value={name} maxLength={80} onChange={e=>setName(e.target.value)}/></label>
        <label>更换视频流地址（留空沿用）<input type="password" autoComplete="off" value={sources} onChange={e=>setSources(e.target.value)}/></label></>:
        <label>每行填写点位名称和 RTSP 地址，用空格分隔<textarea rows={5} spellCheck={false} autoComplete="off" value={sources} onChange={e=>setSources(e.target.value)} placeholder={'大堂东侧 rtsp://用户名:密码@设备地址:554/视频路径'}/></label>}
      <div className="vision-fields"><label>场景<select value={scene} onChange={e=>setScene(e.target.value)}>{(scenes.length?scenes:[scene]).map(s=><option key={s}>{s}</option>)}</select></label>
      <label>巡检间隔（分钟）<input type="number" value={minutes} min={1} max={1440} onChange={e=>setMinutes(Number(e.target.value))}/></label></div>
      <label>现场检查标准（选填）<textarea maxLength={1200} value={standard} onChange={e=>setStandard(e.target.value)}/></label>
      <div className="vision-checks"><button className="primary" disabled={!token||busy} onClick={save}>{edit?'保存修改':'添加并启用'}</button>
      <button className="secondary" onClick={()=>{setSources('');setShowForm(false);}}>取消</button></div></section> :
    <><dl className="patrol-stats">{[
      ['巡检点位',list.length],['已启用',active.length],['到期候检',due.length],['处理中',list.filter(c=>c.phase!=='idle').length]
    ].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{data ? value : '—'}</dd></div>)}</dl>
    <section className="work-card vision-ledger"><header className="work-card-head">
      <Video size={19}/><h2>巡检点位</h2>
      <div className="search-input"><Search size={16}/><input aria-label="搜索点位" placeholder="搜索点位或场景" value={filter} onChange={e=>setFilter(e.target.value)}/></div></header>
    {selected.length?<div className="table-scroll"><table className="patrol-register"><thead><tr><th>点位 / 场景</th><th>计划</th><th>下次计划</th><th>最近结果</th><th>操作</th></tr></thead>
    <tbody>{selected.map(c=><tr key={c.id}><td><strong>{c.name}</strong><small>{c.scene} · 每 {c.minutes} 分钟</small></td><td><StateTag value={status(c)}/></td>
      <td data-label="下次计划">{c.enabled?date(c.due):'—'}</td><td data-label="最近结果">{c.outcome||'尚无结果'}<small>{date(c.ended)}</small>
      {c.last_job&&<button className="text-button" onClick={()=>openReport(c.last_job!)}>最近报告</button>}</td>
      <td><div className="patrol-actions"><button disabled={busy||!token} onClick={()=>control(c.id,c.enabled?'pause':'resume')}>{c.enabled?'暂停':'恢复'}</button>
      <button disabled={busy||!token||!c.enabled||c.phase!=='idle'} onClick={()=>control(c.id,'now')}>立即巡检</button>
      <button onClick={()=>reset(c)}>修改</button>{removing===c.id?<><button disabled={busy} onClick={()=>control(c.id,'remove')}>确认移除</button><button onClick={()=>setRemoving('')}>取消</button></>:
      <button disabled={busy||c.phase!=='idle'} onClick={()=>setRemoving(c.id)}>移除</button>}</div></td></tr>)}</tbody></table></div>:
      <EmptyState icon={Video} title={filter?'没有匹配的点位':data?'暂无巡检点位':offline?'巡检点位暂不可用':'正在读取点位…'} detail={filter?'试试其它点位名称或场景。':data?'添加视频流后，可在这里查看巡检计划与最近结果。':undefined}/>}
      <p className="work-card-note">本地服务运行期间，最多 {data?.workers ?? 4} 个点位同时处理。关闭页面后继续巡检。</p>
    </section>
    <section className="work-card vision-ledger"><header className="work-card-head"><History size={19}/><h2>最近运行记录</h2></header>{data?.history.length?<div className="table-scroll"><table className="patrol-history"><thead><tr><th>点位</th><th>开始</th><th>结果</th><th>证据</th></tr></thead><tbody>
      {data.history.map((r,i)=><tr key={r.started+'-'+i}><td>{r.name}</td><td>{date(r.started)}</td><td>{r.outcome}</td><td>{r.job?<button className="text-button" onClick={()=>openReport(r.job!)}>查看报告</button>:'—'}</td></tr>)}</tbody></table></div>:<p className="report-empty">{data ? '首次巡检后显示运行记录。' : offline ? '运行记录暂不可用。' : '正在读取运行记录…'}</p>}</section></>}
  </section>;
}

