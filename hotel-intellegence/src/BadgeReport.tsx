import {useRef, type ReactNode} from 'react';
import {SceneCoverage, type Coverage, type Finding} from './BadgeReview';
import './badge-report.css';

type Evidence = Finding['evidence'][number];
export type Entry = Finding & {
  scene?:string; name?:string; title?:string; detail?:string; status?:string; level?:string;
  label?:string; value?:string; owner?:string; due?:string; done?:boolean;
};
type Group = 'conclusions'|'todos'|'flow'|'checks'|'risks'|'insights';
export type Report = {
  coverage?:Coverage[]; sceneSource?:string; scenes?:string[]; analysisModel?:string;
  reportVersion?:number; conclusions?:Entry[]; scene:string|null; summary:string;
  scope?:string; mode?:string; flow?:Entry[]; checks:Entry[]; risks:Entry[]; todos:Entry[]; insights:Entry[];
};
type Props = {
  report:Report; evidence:(ev:Evidence[])=>ReactNode;
  review:(group:Group,index:number,row:Entry)=>ReactNode; task:(index:number,done:boolean)=>void;
  customer?:ReactNode;
};

const focus:Record<string,string>={
  '会议':'会议纪要、决定与待办',
  '销售沟通':'销售话术、意向、顾虑与服务需求',
  '餐厅服务':'服务话术、情绪表达与客诉预警',
};

export function RecordingScene({names,label,source,action}:{names:string[];label:string;source:string;action?:ReactNode}) {
  return <section className="br-scene" aria-label="录音场景" aria-live="polite">
    <div><small>{source}</small><h3>{label}</h3>
      {names.length>1&&<p>{names.join(' · ')}</p>}
      {names.length===1&&focus[names[0]]&&<p>{focus[names[0]]}</p>}</div>{action}
  </section>;
}

export default function BadgeReport({report,evidence,review,task,customer}:Props) {
  const root=useRef<HTMLDivElement>(null);
  const mixed=report.scene==='混合场景';
  const sections:[Group,string][]=[
    ['todos','待办与跟进'],['risks','关注事项'],['flow',report.scene==='会议'?'讨论环节':'服务环节与回应'],
    ['insights',report.scene==='销售沟通'?'需求与标签':report.scene==='会议'?'议题与决定':'关键信息'],
    ['checks','服务检查'],
  ];
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
    <section className="br-summary"><h3>{report.scene==='会议'?'会议纪要':report.scene==='销售沟通'?'销售沟通结论':report.scene==='餐厅服务'?'餐厅服务结论':'分析结论'}</h3>
      {report.conclusions?.length?report.conclusions.map((row,i)=><article id={`finding-conclusions-${i}`} tabIndex={-1} key={i}>
        <p>{mixed&&<span className="br-context">{row.scene}</span>}{row.value}</p>{citations('conclusions',i,row)}
      </article>):<p>{report.summary}</p>}
      {report.scope&&<small>{report.scope}</small>}
    </section>
    {report.coverage&&<SceneCoverage sections={report.coverage} inspect={inspect}/>}
    {sections.map(([group,title])=>{
      const rows=report[group]||[];
      if(!rows.length&&group!=='todos') return null;
      return <details className="br-section" key={group} open={group==='todos'&&rows.length>0}>
        <summary><strong>{title}</strong><span>{rows.length} 项</span></summary>
        {rows.length?rows.map((row,i)=><article key={i} id={`finding-${group}-${i}`} tabIndex={-1}>
          <div className="br-line"><div>{mixed&&<span className="br-context">{row.scene}</span>}
            {group==='todos'?<label><input type="checkbox" checked={!!row.done} onChange={e=>task(i,e.target.checked)}/>{row.title}</label>
            :<strong>{row.name||row.title||row.value}</strong>}</div>
            {(row.status||row.level||row.label)&&<span className={group==='risks'?'tag amber':'tag neutral'}>{row.status||row.level||row.label}</span>}
          </div>
          {row.detail&&<p>{row.detail}</p>}{group==='todos'&&<p>负责人：{row.owner} · 截止时间：{row.due}</p>}
          {citations(group,i,row)}
        </article>):<p className="br-empty">本段录音未提取到有原话依据的待办。</p>}
      </details>;
    })}
    {customer&&<details className="br-section"><summary><strong>客户档案</strong></summary>{customer}</details>}
  </div>;
}
