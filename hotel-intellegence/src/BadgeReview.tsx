import { useEffect, useState } from 'react';

export type Decision = { state: string; value: string; note: string; actor: string; at: string };
export type Finding = { feedback?: Decision[]; complaint?: boolean; evidence: {quote:string;start:number;segment:number}[] };
type Person = {id:string; name:string; entries:{recordId:string;filename:string;index:number;label:string;value:string;evidence:Finding['evidence']}[]};
type Api = (path:string, method?:string, body?:unknown)=>Promise<any>;

export function FindingReview({finding, save}:{finding:Finding; save:(state:string,value:string,note:string)=>Promise<void>}) {
  const last=finding.feedback?.at(-1);
  const [state,setState]=useState('确认');
  const [value,setValue]=useState('');
  const [note,setNote]=useState('');
  const [busy,setBusy]=useState(false);
  const states=['确认','纠正','排除','撤回'];
  if(finding.complaint && last && ['确认','纠正','已处理'].includes(last.state)) states.push('已处理');
  return <details className="badge-review"><summary>{last ? `人工${last.state} · ${last.value || last.note || last.actor}` : '逐项复核'}</summary>
    {finding.feedback?.map((event,i)=><p key={i}>{new Date(event.at).toLocaleString()} · {event.actor} · {event.state} {event.value} {event.note}</p>)}
    <label>复核动作<select value={state} onChange={e=>setState(e.target.value)}>{states.map(s=><option key={s}>{s}</option>)}</select></label>
    {state==='纠正'&&<label>纠正后的结论<input value={value} maxLength={600} onChange={e=>setValue(e.target.value)}/></label>}
    <label>{state==='已处理'?'客诉处理结果':'复核备注'}<input maxLength={1000} value={note} onChange={e=>setNote(e.target.value)}/></label>
    <button type="button" className="secondary small" disabled={busy || (state==='纠正'&&!value.trim()) || (state==='已处理'&&!note.trim()) || (!finding.evidence.length&&['确认','纠正'].includes(state))} onClick={async()=>{
      setBusy(true);
      try { await save(state,value,note); setValue(''); setNote(''); setState('确认'); }
      catch { /* Parent displays the server response and keeps the draft. */ }
      finally {setBusy(false)}
    }}>保存复核</button></details>
}

export function Customers({api,record,onLink,onEvidence,notify,revision=0,deviceRecords}:{deviceRecords?:{id:string;customerId?:string}[];revision?:number;api:Api;record?:{id:string;customerId?:string|null};onLink?:(item:any)=>void;onEvidence?:(id:string,start:number)=>void;notify:(s:string)=>void}) {
  const [people,setPeople]=useState<Person[]>([]);
  const [name,setName]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const path='api/badge/customers';
  useEffect(()=>{let live=true; setPeople([]); setError(''); api(path,'GET').then(p=>{if(live)setPeople(p)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[record?.id,revision]);
  async function link(id:string){
    if(record && onLink) onLink(await api('api/records/'+record.id+'/customer','PATCH',{customerId:id||null}));
  }
  async function change(action:()=>Promise<void>){
    setBusy(true);
    try { await action(); } catch(e){notify((e as Error).message)} finally {setBusy(false)}
  }
  let visiblePeople=people;
  if(deviceRecords) {
    const customers=new Set(deviceRecords.map(r=>r.customerId));
    const recordings=new Set(deviceRecords.map(r=>r.id));
    visiblePeople=people.filter(p=>customers.has(p.id)).map(p=>({...p,entries:p.entries.filter(e=>recordings.has(e.recordId))}));
  }
  if(error) return <p role="alert">{error}</p>;
  return <section className={record?'badge-customer':'panel badge-customer'}><h3>{record?'关联客户':'客户档案'}</h3>
    {record && <><label>客户<select disabled={busy} value={record.customerId||''} onChange={e=>void change(()=>link(e.target.value))}><option value="">未关联</option>{people.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label>新客户名称<input maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label><button className="secondary small" disabled={busy||!name.trim()} onClick={()=>void change(async()=>{
      const person=await api(path,'POST',{name}); setPeople(await api(path,'GET')); await link(person.id); setName('');
    })}>新建并关联</button><p>关联后，确认或纠正的销售信息会汇入该客户档案。</p></>}
    {!record && (!visiblePeople.length?<p>当前范围暂无客户档案。可在销售录音详情中创建。</p>:visiblePeople.map(p=><details key={p.id} className="finding"><summary>{p.name} · {p.entries.length} 条已核实信息</summary>{p.entries.map(row=><article key={row.recordId+'-'+row.index}><strong>{row.label} · {row.value}</strong><p>{row.filename}</p>{row.evidence.map((e,i)=><button className="evidence" key={i} onClick={()=>onEvidence?.(row.recordId,e.start)}>回听 · {e.quote}</button>)}</article>)}</details>))}
  </section>
}


export type Coverage = {scene:string; items:{name:string; group:string; matches:number[]}[]};

export function SceneCoverage({sections,inspect}:{sections:Coverage[];inspect:(group:string,index:number)=>void}) {
  return <>{sections.map(section=><details key={section.scene} className="br-section scene-coverage">
    <summary><strong>{section.scene}分析明细</strong><span>{section.items.length} 项要求</span></summary>
    <table><thead><tr><th>分析要求</th><th>录音依据</th></tr></thead><tbody>
      {section.items.map(item=><tr key={item.name}><td>{item.name}</td><td>{item.matches.length
        ? <button type="button" className="text-button" onClick={()=>inspect(item.group,item.matches[0])}>查看 {item.matches.length} 项结果</button>
        : <small className="muted">未提取到依据</small>}</td></tr>)}
    </tbody></table>
  </details>)}</>
}
