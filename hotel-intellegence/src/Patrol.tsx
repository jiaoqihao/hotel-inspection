import {useEffect, useRef, useState} from 'react';
import { Plus, Search, Video } from 'lucide-react';
import { EmptyState, WorkHeading, StateTag } from './Workspace';
import UniformStyles from './UniformStyles';

type Camera = {id:string; name:string; scene:string; standard:string; tieStyle?:string; hairStyle?:string; gestureStatus?:string; gesture_job?:string; minutes:number; enabled:number;
  due:number; phase:string; ended:number|null; outcome:string|null; last_job:string|null};
type Overview = {cameras:Camera[]; workers:number};
const date = (t:number|null) => t ? new Date(t*1000).toLocaleString('zh-CN',
  {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}) : '—';

export default function Patrol({token, openReport}:{token:string; openReport:(id:string)=>void}) {
  const [data,setData] = useState<Overview|null>(null);
  const [edit,setEdit] = useState<string|null>(null);
  const [name,setName] = useState('');
  const [url,setUrl] = useState('');
  const [sources,setSources] = useState('');
  const [minutes,setMinutes] = useState(10);
  const [scene,setScene] = useState('公区卫生');
  const [standard,setStandard] = useState('');
  const [tie,setTie] = useState('');
  const [hair,setHair] = useState('');
  const [filter,setFilter] = useState('');
  const [feedback,setFeedback] = useState('');
  const [offline,setOffline] = useState(false);
  const [busy,setBusy] = useState(false);
  const [revision,setRevision] = useState(0);
  const [removing,setRemoving] = useState('');
  const [scenes,setScenes] = useState<string[]>([]);
  const [batch,setBatch] = useState(false);
  const nameInput = useRef<HTMLInputElement>(null);
  useEffect(()=>{
    fetch('api/vision/status').then(r=>r.json()).then(x=>setScenes(Object.keys(x.profiles))).catch(()=>{});
  },[]);
  useEffect(()=>{
    let alive=true;
    let timer:ReturnType<typeof setTimeout>;
    async function refresh(){
      try {
        const r=await fetch('api/patrol');
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
    setEdit(camera?.id ?? null);setName(camera?.name ?? '');setUrl('');setSources('');
    setMinutes(camera?.minutes ?? 10);setScene(camera?.scene ?? '公区卫生');setStandard(camera?.standard ?? '');
    setTie(camera?.tieStyle ?? '');setHair(camera?.hairStyle ?? '');
    setBatch(false);setRemoving('');setFeedback('');
  }
  async function mutate(path:string,method:string,body?:unknown){
    const r=await fetch('api/patrol/cameras'+path,{method,headers:{'x-local-token':token,'Content-Type':'application/json'},
      body:body===undefined?undefined:JSON.stringify(body)});
    if(!r.ok){const x=await r.json().catch(()=>({}));throw Error(typeof x.detail==='string'?x.detail:'操作失败，请重试');}
  }
  async function save(){
    setBusy(true);setFeedback('');
    try {
      if(!Number.isInteger(minutes)||minutes<1||minutes>1440)throw Error('间隔须为 1–1440 分钟的整数');
      const plan = {minutes,scene,standard,tieStyle:tie,hairStyle:hair};
      let message: string;
      if(edit){
        await mutate('/'+edit,'PUT',{...plan,name:name.trim(),url:url.trim()||null});
        message='点位已更新，下次巡检使用新配置。';
      } else {
        const rows=batch ? sources.trim().split(/\r?\n/).filter(s=>s.trim()).map((line,i)=>{
          const match=line.trim().match(/^(.+?)\s+(rtsp:\/\/\S+)$/i);
          if(!match)throw Error('第 '+(i+1)+' 行请填写点位名称，空一格后填写 RTSP 地址。');
          return {...plan,name:match[1].trim(),url:match[2]};
        }) : [{...plan,name:name.trim(),url:url.trim()}];
        if(!rows.length)throw Error('请至少填写一个点位');
        await mutate('','POST',rows);
        message='已添加并启用 '+rows.length+' 个点位。';
      }
      reset();setFilter('');setFeedback(message);setRevision(n=>n+1);
    } catch(e){setFeedback((e as Error).message);} finally{setBusy(false);}
  }
  async function control(id:string,action:string){
    setBusy(true);setFeedback('');
    try{
      await mutate('/'+id+'/'+action,'POST');setRemoving('');setRevision(n=>n+1);
      if(action==='remove' && id===edit)reset();
      setFeedback(({pause:'已暂停后续巡检，在途任务会完成。',resume:'已恢复巡检。',now:'已安排本轮巡检。',remove:'已移除点位，已有报告保留。'} as Record<string,string>)[action]);
    }catch(e){setFeedback((e as Error).message);}finally{setBusy(false);}
  }
  const list=data?.cameras||[];
  const now=Date.now()/1000;
  const selected=list.filter(c=>(c.name+' '+c.scene).toLowerCase().includes(filter.toLowerCase()));
  function status(c:Camera){
    if(c.phase==='capture')return '抓帧中';
    if(c.phase==='analysis')return '等待或正在分析';
    if(!c.enabled)return '已暂停';
    if(c.due<=now)return '到期候检';
    return '等待下次巡检';
  }
  return <section className="work-page vision">
    <WorkHeading title="添加点位" description="接入摄像头视频流，设置巡检场景与采样间隔。"/>
    {offline&&<p role="alert" className="work-notice">暂时无法连接本地调度服务。</p>}
    {feedback&&<p role="status" className="work-notice">{feedback}</p>}
    <form className="work-card vision-card patrol-form" onSubmit={e=>{e.preventDefault();void save();}}>
      <div className="patrol-form-head"><Video size={19}/><h2>{edit?'修改点位':'点位配置'}</h2>
        {!edit && <div className="work-switch" aria-label="添加方式">{[false,true].map(value=>
          <button key={String(value)} type="button" disabled={busy} aria-pressed={batch===value} onClick={()=>{
            setBatch(value);setFeedback('');
          }}>{value?'批量添加':'单个点位'}</button>)}</div>}
      </div>
      <fieldset className="patrol-inputs" disabled={busy}>
        {batch ? <label>点位名称和 RTSP 地址<textarea required rows={3} spellCheck={false} autoComplete="off" value={sources} onChange={e=>setSources(e.target.value)} placeholder={'每行一个点位，例如：大堂东侧 rtsp://设备地址:554/视频路径'}/></label> :
          <div className="vision-fields"><label>点位名称<input ref={nameInput} required value={name} maxLength={80} placeholder="例如：大堂东侧" onChange={e=>setName(e.target.value)}/></label>
          <label>{edit?'更换视频流地址（留空沿用）':'RTSP 视频流地址'}<input type="password" required={!edit} autoComplete="off" placeholder="rtsp://设备地址:554/视频路径" value={url} onChange={e=>setUrl(e.target.value)}/></label></div>}
        <div className="vision-fields"><label>巡检场景<select value={scene} onChange={e=>setScene(e.target.value)}>{(scenes.length?scenes:[scene]).map(s=><option key={s}>{s}</option>)}</select></label>
        <label>巡检间隔（分钟）<input type="number" required value={minutes||''} min={1} max={1440} onChange={e=>setMinutes(Number(e.target.value))}/></label></div>
        {scene==='前台接待' && <><UniformStyles tie={tie} hair={hair} onTie={setTie} onHair={setHair}/><p className="muted">点位启用期间持续监听举手，常规巡检按上方间隔运行。</p></>}
        <label>现场检查标准（选填）<textarea rows={2} maxLength={1200} value={standard} onChange={e=>setStandard(e.target.value)}/></label>
      </fieldset>
      <div className="vision-checks"><button className="primary" disabled={!token||busy}>{!edit && <Plus size={16}/>}{edit?'保存修改':'添加并启用'}</button>
      {edit && <button type="button" className="secondary" disabled={busy} onClick={()=>reset()}>取消修改</button>}</div>
    </form>
    <section className="work-card vision-ledger"><header className="work-card-head">
      <h2>已配置点位</h2>{data && <small className="muted">{list.length} 个点位 · {list.filter(c=>c.enabled).length} 个已启用</small>}
      <div className="search-input"><Search size={16}/><input aria-label="搜索点位" placeholder="搜索点位或场景" value={filter} onChange={e=>setFilter(e.target.value)}/></div></header>
    {selected.length?<div className="table-scroll"><table className="patrol-register"><thead><tr><th>点位 / 场景</th><th>计划</th><th>下次计划</th><th>最近巡检</th><th>操作</th></tr></thead>
    <tbody>{selected.map(c=><tr key={c.id}><td><strong>{c.name}</strong><small>{c.scene} · 每 {c.minutes} 分钟</small></td><td><StateTag value={status(c)}/>{c.gestureStatus && <small>{c.gestureStatus}</small>}</td>
      <td data-label="下次计划">{c.enabled?date(c.due):'—'}</td><td data-label="最近巡检">{c.outcome||'尚无结果'}<small>{date(c.ended)}</small>
      <div className="patrol-actions">{c.last_job&&<button className="text-button" onClick={()=>openReport(c.last_job!)}>最近报告</button>}
      {c.gesture_job&&<button className="text-button" onClick={()=>openReport(c.gesture_job!)}>最近举手检查</button>}</div></td>
      <td><div className="patrol-actions"><button disabled={busy||!token} onClick={()=>control(c.id,c.enabled?'pause':'resume')}>{c.enabled?'暂停':'恢复'}</button>
      <button disabled={busy||!token||!c.enabled||c.phase!=='idle'} onClick={()=>control(c.id,'now')}>立即巡检</button>
      <button disabled={busy||!token} onClick={()=>{reset(c);requestAnimationFrame(()=>nameInput.current?.focus());}}>修改</button>{removing===c.id?<><button disabled={busy} onClick={()=>control(c.id,'remove')}>确认移除</button><button onClick={()=>setRemoving('')}>取消</button></>:
      <button disabled={busy||!token||c.phase!=='idle'} onClick={()=>setRemoving(c.id)}>移除</button>}</div></td></tr>)}</tbody></table></div>:
      <EmptyState icon={Video} title={filter?'没有匹配的点位':data?'暂无巡检点位':offline?'巡检点位暂不可用':'正在读取点位…'} detail={filter?'试试其它点位名称或场景。':data?'添加视频流后，可在这里查看巡检计划与最近结果。':undefined}/>}
      <p className="work-card-note">本地服务运行期间，最多 {data?.workers ?? 4} 个点位同时处理。关闭页面后继续巡检。</p>
    </section>
  </section>;
}
