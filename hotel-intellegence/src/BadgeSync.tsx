import {useEffect, useRef, useState, type ReactNode} from 'react';
import {request} from './Store';
import './badge-devices.css';

type Kind = '挂牌'|'胸牌';
type Draft = {number:string;source:Kind;name:string;position:string;test:boolean};
export type Unit = Omit<Draft,'test'> & {id:number;test:number;enabled:number;retired:number;revision:number;synced:string|null;error:string};
export type Registry = {devices:Unit[];connected:boolean;enabled:boolean;current:number|null;syncing?:number[];syncError:string};
type Signal = {group:string;detail:string;heartbeat:string|null;battery:number|null;vendorStatus:string|null};
type Snapshot = {states:Record<number,Signal>;updated:string|null;error:string};
type Api = (path:string,method?:string,body?:unknown)=>Promise<any>;
type Recording = {id:string;deviceId?:number;badge?:string;filename:string;recordedAt?:string;createdAt:string};
const base='/api/badge/devices';
const types:Kind[]=['挂牌','胸牌'];
const emptyDraft=(number='',source:Kind='挂牌'):Draft=>({number,source,name:'',position:'',test:false});
const stamp=(value?:string|null)=>value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';

function Dialog({title,close,children}:{title:string;close:()=>void;children:ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=ref.current!;node.showModal();return()=>node.close()},[]);
  return <dialog ref={ref} className="bd-dialog" aria-label={title} onCancel={e=>{e.preventDefault();close()}}>
    <header><h2>{title}</h2><button className="secondary" onClick={close} aria-label="关闭窗口">关闭</button></header>{children}
  </dialog>;
}

function ProfileFields({value,change}:{value:Draft;change:(d:Draft)=>void}) {
  return <div className="bd-fields"><label>设备名称<input maxLength={80} placeholder="例如：前台一号" value={value.name} onChange={e=>change({...value,name:e.target.value})}/></label>
    <label>岗位<input maxLength={80} value={value.position} onChange={e=>change({...value,position:e.target.value})}/></label></div>;
}

function AddUnits({known,api,finish,close}:{known:Unit[];api:Api;finish:(r:Registry)=>void;close:()=>void}) {
  const [catalog,setCatalog]=useState<{app:string;devices:Draft[];error:string}>();
  const [search,setSearch]=useState('');
  const [manual,setManual]=useState(true);
  const [number,setNumber]=useState('');
  const [kind,setKind]=useState<Kind>('挂牌');
  const [drafts,setDrafts]=useState<Draft[]>([]);
  const [error,setError]=useState('');
  const [saving,setSaving]=useState(false);
  useEffect(()=>{let live=true;request<NonNullable<typeof catalog>>(base+'/catalog').then(c=>live&&setCatalog(c)).catch(e=>live&&setError(e.message));return()=>{live=false}},[]);
  function add(n:string,k:Kind) {
    if(!/^[A-Za-z0-9_-]{1,80}$/.test(n)){setError('请填写有效的设备编号');return}
    if([...known,...drafts].some(d=>d.number===n)){setError('编号已在清单中，可从原档案编辑或恢复');return}
    setDrafts([...drafts,emptyDraft(n,k)]);setNumber('');setError('');
  }
  return <Dialog title="添加设备" close={close}><div className="bd-tools"><button className="secondary" aria-pressed={!manual} onClick={()=>setManual(false)}>平台清单</button><button className="secondary" aria-pressed={manual} onClick={()=>setManual(true)}>手动录入</button></div>
    {manual?<div className="bd-fields"><label>类型<select value={kind} onChange={e=>setKind(e.target.value as Kind)}>{types.map(t=><option key={t}>{t}</option>)}</select></label>
      <label>设备编号<input value={number} onChange={e=>setNumber(e.target.value.trim())}/></label><button className="secondary" onClick={()=>add(number,kind)}>加入待添加清单</button></div>:
      <><label>搜索平台设备<input value={search} placeholder="设备编号或类型" onChange={e=>setSearch(e.target.value)}/></label>
      <div className="bd-pick">{!catalog?'正在读取平台清单…':catalog.error||(!catalog.devices.length?'平台暂无可选设备':'')}
      {catalog?.devices.filter(d=>(d.number+d.source).toLowerCase().includes(search.toLowerCase())).map(d=>{
        const existing=known.find(u=>u.number===d.number);
        return <label key={d.number}><input type="checkbox" disabled={!!existing} checked={!!existing||drafts.some(u=>u.number===d.number)} onChange={e=>e.target.checked?add(d.number,d.source):setDrafts(drafts.filter(u=>u.number!==d.number))}/>{d.source} · {d.number} {existing?(existing.retired?'已停用':'已添加'):''}</label>
      })}</div></>}
    {drafts.map(d=><fieldset key={d.number}><legend>{d.source} · {d.number}</legend><ProfileFields value={d} change={v=>setDrafts(drafts.map(i=>i.number===v.number?v:i))}/><button className="text-button" onClick={()=>setDrafts(drafts.filter(i=>i.number!==d.number))}>移出清单</button></fieldset>)}
    {error&&<p role="alert">{error}</p>}<footer><button className="primary" disabled={!drafts.length||!catalog?.app||saving} onClick={async()=>{
      setSaving(true);try{finish(await api(base,'POST',{app:catalog?.app,devices:drafts}))}catch(e){setError((e as Error).message)}finally{setSaving(false)}
    }}>添加 {drafts.length} 台</button></footer></Dialog>;
}

function Connection({api,close,saved}:{api:Api;close:()=>void;saved:()=>void}) {
  const [app,setApp]=useState(''),[secret,setSecret]=useState(''),[enabled,setEnabled]=useState(false),[error,setError]=useState(''),[ready,setReady]=useState(false),[busy,setBusy]=useState(false);
  useEffect(()=>{let live=true;request<{appId:string;enabled:boolean}>('/api/badge/sync').then(s=>{if(live){setApp(s.appId);setEnabled(s.enabled);setReady(true)}}).catch(e=>live&&setError(e.message));return()=>{live=false}},[]);
  async function action(pause:boolean) {
    setBusy(true);try{if(pause)await api('/api/badge/sync/pause');else await api('/api/badge/sync','PUT',{appId:app.trim(),secret:secret||null});saved()}catch(e){setError((e as Error).message)}finally{setBusy(false)}
  }
  return <Dialog title="平台连接" close={close}><form onSubmit={e=>{e.preventDefault();action(false)}}><label>悦航益 AppId<input value={app} required onChange={e=>setApp(e.target.value)}/></label>
    <label>AppSecret<input type="password" autoComplete="new-password" value={secret} placeholder="沿用当前密钥可留空" onChange={e=>setSecret(e.target.value)}/></label>
    {error&&<p role="alert">{error}</p>}<footer>{enabled&&<button className="secondary" type="button" disabled={busy} onClick={()=>action(true)}>暂停全部同步</button>}<button className="primary" disabled={!ready||busy}>保存并启用平台</button></footer></form></Dialog>;
}

function EditUnit({unit,replace,api,close,finish}:{unit:Unit;replace:boolean;api:Api;close:()=>void;finish:(v:Registry)=>void}) {
  const [initial]=useState(unit);
  const [draft,setDraft]=useState<Draft>({...unit,test:!!unit.test,number:replace?'':unit.number});
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  return <Dialog title={replace?'更换设备':'编辑设备'} close={close}><p>{replace?'原设备将停用，历史录音保留在原编号下。':'设备编号'} {initial.number}</p>
    <form onSubmit={async e=>{e.preventDefault();setBusy(true);try{finish(await api(base+'/'+initial.id,'PATCH',{...draft,enabled:!!initial.enabled,retired:!!initial.retired,revision:initial.revision,replacement:replace?{number:draft.number,source:draft.source}:null}))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}}>
      {replace&&<div className="bd-fields"><label>新设备编号<input required pattern="[A-Za-z0-9_\-]{1,80}" value={draft.number} onChange={e=>setDraft({...draft,number:e.target.value.trim()})}/></label><label>新设备类型<select value={draft.source} onChange={e=>setDraft({...draft,source:e.target.value as Kind})}>{types.map(t=><option key={t}>{t}</option>)}</select></label></div>}
      <ProfileFields value={draft} change={setDraft}/>{error&&<p role="alert">{error}</p>}<footer><button className="primary" disabled={busy}>{replace?'确认更换设备':'保存修改'}</button></footer></form>
  </Dialog>;
}

export default function BadgeDevices({api,visible,admin,registry,setRegistry,records,viewRecords,openRecord}:{api:Api;visible:boolean;admin:boolean;registry:Registry;setRegistry:(s:Registry)=>void;records:Recording[];viewRecords:(d:Unit)=>void;openRecord:(id:string)=>void}) {
  const [query,setQuery]=useState(''),[kind,setKind]=useState('全部类型'),[state,setState]=useState('全部状态'),[retired,setRetired]=useState(false);
  const [selected,setSelected]=useState<number|null>(null),[panel,setPanel]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
  const [snapshot,setSnapshot]=useState<Snapshot>({states:{},updated:null,error:''});
  const [refresh,setRefresh]=useState(0);
  const [refreshing,setRefreshing]=useState(false);
  useEffect(()=>{
    if(!visible)return;
    let live=true,pending=false;
    async function poll(force=false){
      if(pending||document.hidden)return;
      pending=true;if(live)setRefreshing(true);
      try{
        const list=await request<Registry>(base);
        if(!live)return;setRegistry(list);
        const s=await request<Snapshot>(base+'/presence'+(force?'?force=true':''));
        if(live){setSnapshot(s);setMessage('')}
      }catch(e){if(live)setMessage((e as Error).message)}
      finally{pending=false;if(live)setRefreshing(false)}
    }
    void poll(refresh>0);
    const tick=setInterval(()=>poll(),30000);
    const focus=()=>poll();document.addEventListener('visibilitychange',focus);
    return()=>{live=false;clearInterval(tick);document.removeEventListener('visibilitychange',focus)}
  },[visible,refresh,setRegistry]);
  const units=registry.devices;
  const unit=units.find(d=>d.id===selected);
  const current=units.filter(d=>!d.retired);
  function signal(d:Unit){
    const s=snapshot.states[d.id];
    if(d.retired)return {group:'停用',detail:'已停用'};
    if(!s||!snapshot.updated)return {group:'待确认',detail:'待确认'};
    if(Date.now()-Date.parse(snapshot.updated)>=90000)return {group:'待确认',detail:'状态已过期'};
    return s;
  }
  const matches=units.filter(d=>(retired||!d.retired)&&(kind==='全部类型'||d.source===kind)&&(state==='全部状态'||signal(d).group===state)&&[d.name,d.number,d.position].join(' ').toLowerCase().includes(query.trim().toLowerCase()));
  const syncLabel=(d:Unit)=>d.retired?'已停用':!registry.enabled||!d.enabled?'已暂停':(registry.syncing?.includes(d.id)||registry.current===d.id)?'同步中':d.error?'下载异常':'已启用';
  function finish(result:Registry){setRegistry(result);setPanel('');setRefresh(n=>n+1)}
  const byUnit=new Map<number,Recording[]>();
  for(const r of records){if(r.deviceId){const rows=byUnit.get(r.deviceId)||[];rows.push(r);byUnit.set(r.deviceId,rows)}}
  for(const rows of byUnit.values())rows.sort((a,b)=>(b.recordedAt||b.createdAt).localeCompare(a.recordedAt||a.createdAt));
  async function toggle(d:Unit,change:{enabled?:boolean;retired?:boolean}){
    setBusy(true);try{finish(await api(base+'/'+d.id,'PATCH',{...d,...change,test:!!d.test,enabled:change.enabled??!!d.enabled,retired:change.retired??!!d.retired}))}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}
  }
  if(!visible)return null;
  return <section className="bd-page"><header className="bd-heading"><div><h1>设备</h1><p>共 {current.length} 台 <span>·</span> 在线 {current.filter(d=>signal(d).group==='在线').length} 台 <span>·</span> 离线 {current.filter(d=>signal(d).group==='离线').length} 台 <span>·</span> 待确认 {current.filter(d=>signal(d).group==='待确认').length} 台</p></div>
    {admin&&<div className="bd-actions"><button className="secondary" onClick={()=>setPanel('connection')}>平台连接</button><button className="primary" onClick={()=>setPanel(registry.connected?'add':'connection')}>手动添加</button></div>}</header>
    <section className="panel bd-panel"><div className="bd-tools"><input aria-label="搜索设备" placeholder="搜索名称、编号或岗位" value={query} onChange={e=>setQuery(e.target.value)}/>
      <select aria-label="设备类型" value={kind} onChange={e=>setKind(e.target.value)}>{['全部类型',...types].map(k=><option key={k}>{k}</option>)}</select>
      <select aria-label="在线状态" value={state} onChange={e=>setState(e.target.value)}>{['全部状态','在线','离线','待确认'].map(k=><option key={k}>{k}</option>)}</select>
      <label><input type="checkbox" checked={retired} onChange={e=>setRetired(e.target.checked)}/>含已停用</label></div>
    <div className="table-scroll"><table><thead><tr>{['设备名称 / 编号','类型','岗位','在线状态','录音同步','最近录音'].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>{matches.map(d=>{
      const s=signal(d),latest=byUnit.get(d.id)?.[0];
      return <tr key={d.id} onClick={()=>{setSelected(d.id);setPanel('detail')}}><td><button className="row-title" onClick={()=>{setSelected(d.id);setPanel('detail')}}>{d.name||d.number}</button>{d.name&&<small>{d.number}</small>}</td><td>{d.source}</td><td>{d.position||'—'}</td><td><span className={'bd-state bd-'+s.group}>{s.group==='在线'&&s.detail!=='在线'?'在线 · ':''}{s.detail}</span></td><td>{syncLabel(d)}</td><td>{stamp(latest?.recordedAt||latest?.createdAt)}</td></tr>
    })}</tbody></table></div>
    {!matches.length&&<div className="empty"><strong>{units.length?'没有符合条件的设备':'尚未添加设备'}</strong>{units.length?<button className="text-button" onClick={()=>{setQuery('');setKind('全部类型');setState('全部状态');setRetired(true)}}>清除筛选</button>:admin&&<button className="text-button" onClick={()=>setPanel(registry.connected?'add':'connection')}>添加第一台设备</button>}</div>}
    <div className="bd-foot"><span>{registry.enabled?'自动发现设备 · 每分钟同步在线设备录音':'自动同步已暂停'} · {snapshot.updated?'状态更新于 '+stamp(snapshot.updated):'等待厂商状态'} {snapshot.error}</span><button className="text-button" disabled={refreshing} onClick={()=>setRefresh(n=>n+1)}>{refreshing?'刷新中…':'刷新状态'}</button></div></section>
    {(message||registry.syncError)&&<p role="status">{message||registry.syncError}</p>}
    {panel==='connection'&&<Connection api={api} close={()=>setPanel('')} saved={()=>{setPanel('');setRefresh(n=>n+1)}}/>}
    {panel==='add'&&<AddUnits api={api} known={units} close={()=>setPanel('')} finish={finish}/>}
    {unit&&panel==='detail'&&<Dialog title={unit.name||unit.number} close={()=>setPanel('')}><dl className="bd-facts">{Object.entries({'设备编号':unit.number,'类型':unit.source,'岗位':unit.position||'—','硬件状态':signal(unit).detail,'最后心跳':stamp(snapshot.states[unit.id]?.heartbeat),'剩余电量':snapshot.states[unit.id]?.battery!=null?snapshot.states[unit.id].battery+'%':'—','同步状态':syncLabel(unit),'上次成功同步':stamp(unit.synced)}).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      {unit.error&&<p role="status">{unit.error}</p>}<h3>最近录音</h3>{byUnit.get(unit.id)?.length?byUnit.get(unit.id)!.slice(0,3).map(r=><button key={r.id} className="bd-record" onClick={()=>{setPanel('');openRecord(r.id)}}>{stamp(r.recordedAt||r.createdAt)}　{r.filename}</button>):<p>暂无关联录音</p>}
      <footer><button className="primary" onClick={()=>{setPanel('');viewRecords(unit)}}>查看这台设备的录音</button>
      {admin&&<details><summary>管理设备</summary><div className="bd-actions"><button className="secondary" onClick={()=>setPanel('edit')}>编辑设备</button>
      {!unit.retired&&<><button className="secondary" disabled={busy} onClick={()=>toggle(unit,{enabled:!unit.enabled})}>{unit.enabled?'暂停同步':'恢复同步'}</button><button className="secondary" onClick={()=>setPanel('replace')}>更换设备</button></>}
      <button className="secondary" disabled={busy} onClick={()=>unit.retired?toggle(unit,{retired:false}):setPanel('retire')}>{unit.retired?'恢复设备':'停用设备'}</button></div></details>}</footer></Dialog>}
    {unit&&['edit','replace'].includes(panel)&&<EditUnit key={panel+unit.id} unit={unit} replace={panel==='replace'} api={api} close={()=>setPanel('detail')} finish={finish}/>}
    {unit&&panel==='retire'&&<Dialog title="停用设备" close={()=>setPanel('detail')}><p>停用 {unit.name||unit.number} 后会停止自动同步，历史录音仍可回听。</p><button className="primary" disabled={busy} onClick={()=>toggle(unit,{retired:true})}>确认停用</button></Dialog>}
  </section>
}

