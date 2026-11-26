import {useRef, type ReactNode} from 'react';
import {AudioLines, ClipboardList, MessagesSquare, ScanLine, Utensils, Users} from 'lucide-react';
import {SceneCoverage, type Coverage, type Finding} from './BadgeReview';
import {scriptSummary} from './export';
import './badge-report.css';

type Evidence = Finding['evidence'][number];
export type Entry = Finding & {
  scene?:string; name?:string; title?:string; detail?:string; status?:string; level?:string;
  label?:string; value?:string; owner?:string; due?:string; done?:boolean; standardFlow?:string;
};
type Group = 'conclusions'|'todos'|'flow'|'checks'|'risks'|'insights';
export type Report = {
  coverage?:Coverage[]; sceneSource?:string; scenes?:string[]; analysisModel?:string;
  reportVersion?:number; frontDeskStandard?:string|null; conclusions?:Entry[]; scene:string|null; summary:string;
  scope?:string; mode?:string; flow?:Entry[]; checks:Entry[]; risks:Entry[]; todos:Entry[]; insights:Entry[];
};
type Props = {
  report:Report; evidence:(ev:Evidence[])=>ReactNode;
  review:(group:Group,index:number,row:Entry)=>ReactNode; task:(index:number,done:boolean)=>void;
  customer?:ReactNode; emptyAction?:ReactNode;
};

const focus:Record<string,string>={
  '会议':'会议纪要、决定与待办',
  '销售沟通':'销售话术、意向、顾虑与服务需求',
  '餐厅服务':'服务话术、情绪表达与客诉预警',
};

const modules=[
  {id:'report',title:'场景识别',icon:ScanLine},
  {id:'transcript',title:'原话撰写',icon:MessagesSquare},
  {id:'quality',title:'录音质量',icon:AudioLines},
];

export function RecordingTabs({value,select}:{value:string;select:(id:string)=>void}) {
  return <div className="br-tabs" role="tablist" aria-label="录音详情模块">
    {modules.map(({id,title,icon:Icon},i)=><button key={id} id={`module-${id}`} role="tab"
      aria-selected={value===id} aria-controls="recording-panel" tabIndex={value===id?0:-1}
      onClick={()=>select(id)} onKeyDown={event=>{
        const next={ArrowRight:(i+1)%3,ArrowLeft:(i+2)%3,Home:0,End:2}[event.key];
        if(next===undefined) return;
        event.preventDefault();select(modules[next].id);
        document.getElementById('module-'+modules[next].id)?.focus();
      }}><Icon size={17} aria-hidden="true"/>{title}</button>)}
  </div>;
}

export function RecordingScene({names,label,source,action}:{names:string[];label:string;source:string;action?:ReactNode}) {
  const Icon=names.length!==1?ScanLine:names[0]==='餐厅服务'?Utensils:names[0]==='会议'?ClipboardList:Users;
  return <section className="br-scene" aria-label="录音场景" aria-live="polite">
    <span className="br-scene-icon"><Icon size={22} aria-hidden="true"/></span><div><small>{source}</small><h3>{label}</h3>
      {names.length>1&&<p>{names.join(' · ')}</p>}
      {names.length===1&&focus[names[0]]&&<p>{focus[names[0]]}</p>}</div>{action}
  </section>;
}

export default function BadgeReport({report,evidence,review,task,customer,emptyAction}:Props) {
  const root=useRef<HTMLDivElement>(null);
  const mixed=report.scene==='混合场景';
  const scriptFlows=[...new Set((report.checks||[]).map(row=>row.standardFlow).filter((flow):flow is string=>!!flow))];
  const titles:Record<Exclude<Group,'conclusions'>,string>={
    todos:report.scene==='会议'?'会议待办':'待办与跟进',
    risks:report.scene==='餐厅服务'?'客诉预警与关注事项':'关注事项',
    flow:report.scene==='销售沟通'?'销售话术闭环':report.scene==='餐厅服务'?'餐厅服务闭环':'服务环节与回应',
    insights:report.scene==='销售沟通'?'客户意向、需求与标签':report.scene==='会议'?'议题与决定':report.scene==='餐厅服务'?'顾客情绪与反馈':'关键信息',
    checks:'服务检查',
  };
  const order:(keyof typeof titles)[]=report.scene==='会议'?['todos','insights','risks','flow','checks']:
    report.scene==='餐厅服务'?['risks','flow','insights','todos','checks']:['flow','insights','todos','risks','checks'];
  function inspect(group:string,index:number) {
    const row=root.current?.querySelector<HTMLElement>(`#finding-${group}-${index}`);
    if(!row) return;
    const section=row.closest('details.br-section');
    if(section instanceof HTMLDetailsElement) section.open=true;
    const citation=row.querySelector('details.br-citations');
    if(citation instanceof HTMLDetailsElement) citation.open=true;
    row.scrollIntoView({block:'center'});
    row.focus({preventScroll:true});
  }
  function citations(group:Group,index:number,row:Entry) {
    return <details className="br-citations"><summary>依据与复核 · {new Set(row.evidence.map(ev=>ev.segment)).size} 段</summary>
      {evidence(row.evidence)}{review(group,index,row)}</details>;
  }
  return <div ref={root} className="badge-report">
    {report.frontDeskStandard&&(scriptFlows.length>0||!report.scenes?.length||report.scenes.includes('销售沟通'))&&<section className="br-summary br-script">
      <h3>前台标准话术核对</h3>
      {scriptFlows.length?scriptFlows.map(flow=>{
        const rows=report.checks.map((row,index)=>({row,index})).filter(({row})=>row.standardFlow===flow);
        return <div key={flow}><p><strong>{flow}</strong> · {scriptSummary(rows.map(({row})=>row))}</p>
          <details className="br-section"><summary><strong>逐项查看{flow}话术</strong><span>{rows.length} 项</span></summary>
            {rows.map(({row,index})=><article key={index} id={`finding-checks-${index}`} tabIndex={-1}>
              <div className="br-line"><strong>{row.name}</strong><span className={`tag ${row.status==='符合'?'green':row.status==='疑似偏差'?'amber':'neutral'}`}>{row.status}</span></div>
              <p>{row.detail}</p>{!!row.evidence.length&&citations('checks',index,row)}
            </article>)}
          </details></div>;
      }):<p>本段原话不足以确认入住或退房办理。</p>}
      <small>依据 {report.frontDeskStandard} 版标准按语义核对。证据不足的环节需回听确认。</small>
    </section>}
    <section className="br-summary"><h3>{report.scene==='会议'?'会议纪要':report.scene==='销售沟通'?'销售沟通结论':report.scene==='餐厅服务'?'餐厅服务结论':'分析结论'}</h3>
      {report.conclusions?.length?report.conclusions.map((row,i)=><article id={`finding-conclusions-${i}`} tabIndex={-1} key={i}>
        <p>{mixed&&<span className="br-context">{row.scene}</span>}{row.value}</p>{citations('conclusions',i,row)}
      </article>):<p>{report.summary}</p>}
      {report.scope&&<small>{report.scope}</small>}
      {!report.scene&&emptyAction}
    </section>
    {order.map(group=>{
      const rows=(report[group]||[]).map((row,i)=>({row,i})).filter(({row})=>!row.standardFlow);
      if(!rows.length) return null;
      return <details className="br-section" key={group} open={group==='todos'&&report.scene==='会议'||group==='risks'&&rows.some(({row})=>row.complaint)}>
        <summary><strong>{titles[group]}</strong><span>{rows.length} 项</span></summary>
        {rows.map(({row,i})=><article key={i} id={`finding-${group}-${i}`} tabIndex={-1}>
          <div className="br-line"><div>{mixed&&<span className="br-context">{row.scene}</span>}
            {group==='todos'?<label><input type="checkbox" checked={!!row.done} onChange={e=>task(i,e.target.checked)}/>{row.title}</label>
            :<strong>{row.name||row.title||row.value}</strong>}</div>
            {(row.status||row.level||row.label)&&<span className={group==='risks'?'tag amber':'tag neutral'}>{row.status||row.level||row.label}</span>}
          </div>
          {row.detail&&<p>{row.detail}</p>}{group==='todos'&&<p>负责人：{row.owner} · 截止时间：{row.due}</p>}
          {citations(group,i,row)}
        </article>)}
      </details>;
    })}
    {customer&&<details className="br-section"><summary><strong>客户画像与档案</strong></summary>{customer}</details>}
    {report.coverage&&<SceneCoverage sections={report.coverage} inspect={inspect}/>}
  </div>;
}
