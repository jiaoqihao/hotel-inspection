import {useEffect, useRef, useState} from 'react';

type Fields = {filename:string; employee:string; note?:string};
type Props = {record:Fields; close:()=>void; save:(values:Fields)=>Promise<void>; remove:()=>void};

export default function RecordingManager({record,close,save,remove}:Props) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [name,setName]=useState(record.filename);
  const [employee,setEmployee]=useState(record.employee);
  const [note,setNote]=useState(record.note||'');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{
    const el=dialog.current!;
    el.showModal();
    return ()=>el.close();
  },[]);
  return <dialog ref={dialog} className="bd-dialog rec-manager" aria-label="管理录音" onCancel={event=>{event.preventDefault();if(!busy)close()}}>
    <header><h2>管理录音</h2><button className="text-button" disabled={busy} onClick={close}>关闭</button></header>
    <form onSubmit={async e=>{
      e.preventDefault();setBusy(true);
      try {await save({filename:name.trim(),employee:employee.trim(),note:note.trim()});close()}
      catch(e){setError((e as Error).message)}finally{setBusy(false)}
    }}>
      <label>录音名称<input required maxLength={240} value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>员工姓名或编号<input maxLength={80} value={employee} onChange={e=>setEmployee(e.target.value)}/></label>
      <label>备注<textarea rows={3} maxLength={1500} value={note} onChange={e=>setNote(e.target.value)}/></label>
      {error&&<p role="alert">{error}</p>}
      <footer><button className="text-button rec-remove" type="button" disabled={busy} onClick={remove}>删除录音</button><button className="primary" disabled={busy||!name.trim()}>保存修改</button></footer>
    </form>
  </dialog>;
}

