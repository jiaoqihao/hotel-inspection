import {useEffect, useId, useRef, useState} from 'react';

type Choice = {name:string; number:string; source:string};
type Props = {
  types:string[]; type:string; chooseType:(value:string)=>void;
  choices:Map<string,Choice>; unit:string; chooseUnit:(value:string)=>void;
};

export default function BadgeFilters({types,type,chooseType,choices,unit,chooseUnit}:Props) {
  const [open,setOpen]=useState(false);
  const [query,setQuery]=useState('');
  const picker=useRef<HTMLDivElement>(null);
  const trigger=useRef<HTMLButtonElement>(null);
  const search=useRef<HTMLInputElement>(null);
  const listId=useId();
  useEffect(()=>{
    if(!open)return;
    search.current?.focus();
    const outside=(e:PointerEvent)=>{if(!picker.current?.contains(e.target as Node))setOpen(false)};
    document.addEventListener('pointerdown',outside);
    return ()=>document.removeEventListener('pointerdown',outside);
  },[open]);
  function choose(value:string){chooseUnit(value);setOpen(false);trigger.current?.focus()}
  const options=[...choices].filter(([,d])=>(type==='全部设备'||d.source===type)&&(d.name+" "+d.number).toLowerCase().includes(query.trim().toLowerCase()));
  return <section className="voice-filters" aria-label="设备与数据筛选">
    <div className="vf-field"><span className="vf-label">设备类型</span>
      <div className="vf-segments" role="group" aria-label="按设备类型筛选">{types.map(t=>
        <button type="button" key={t} aria-pressed={type===t} onClick={()=>{setOpen(false);chooseType(t)}}>{t}</button>)}</div>
    </div>
    <div className="vf-field vf-unit" ref={picker} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))setOpen(false)}} onKeyDown={e=>{if(e.key==='Escape'&&open){e.preventDefault();setOpen(false);trigger.current?.focus()}}}>
      <span className="vf-label">具体设备</span>
      <button ref={trigger} type="button" className="vf-select" aria-label={'具体设备，'+(choices.get(unit)?.name||'所有设备')} aria-expanded={open} aria-controls={open?listId:undefined} onClick={()=>{setQuery('');setOpen(!open)}}><span>{choices.get(unit)?.name||'所有设备'}</span></button>
      {open&&<div className="vf-popup" id={listId}>
        <input ref={search} type="search" aria-label="搜索设备编号或名称" placeholder="搜索名称或设备编号" value={query} onChange={e=>setQuery(e.target.value)}/>
        <div className="vf-options">{!query.trim()&&<button aria-pressed={!unit} onClick={()=>choose('')}>所有设备</button>}
          {options.map(([key,d])=><button key={key} aria-pressed={unit===key} onClick={()=>choose(key)}>{d.name}</button>)}
          {options.length===0&&query.trim()&&<p>没有匹配的设备</p>}</div>
      </div>}
    </div>
  </section>;
}

