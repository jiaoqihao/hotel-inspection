import BadgeWecom from './BadgeWecom';
import {ScriptRules} from './BadgeScripts';
import RecordingManager from './RecordingManager';
import Agent from './Agent';
import BadgeFilters from './BadgeFilters';
import BadgeDevices, {type Registry,type Unit} from './BadgeSync';
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import './platform.css';
import Phone from './Phone';
import {Overview, Store, type User, request} from './Store';
import Vision from './Vision';
import Patrol from './Patrol';
import './workspace.css';
import './vision.css';
import { WorkHeading } from './Workspace';
import { Database, HardDrive } from 'lucide-react';
import { Customers, FindingReview } from './BadgeReview';
import BadgeReport, {RecordingScene, RecordingTabs, type Report, type Entry} from './BadgeReport';
import { exportHTML, reportMode, sceneLabel } from './export';

type Evidence = {segment: number; quote: string; start: number};
type Recording = {detectedScenes?:string[];note?:string;deviceId?:number;test?:boolean; customerId?:string; customerName?:string;source?:string;badge?:string;recordedAt?:string;reviewNote?:string;language?:string; diarization?:boolean; speakerCount?:number; sourceId?:string; asrRevision?:number; speakers?:Record<string,{name:string;source:string}>; id: string; filename: string; employee: string; createdAt: string; status: string; progress: number; duration?: number; sceneHint: string; size: number; report?: Report; reviewed: boolean; error?: string; segments: {id:number; start:number; end:number; text:string; speaker:string; speakerId?:string|null; overlap?:boolean; needsReview?:boolean}[]; quality?: {rmsDb:number; clippedPercent:number; warnings:string[]}};
type Health = {badgeReportVersion:number; diarization:boolean; speakerModel:string; asr: boolean; analysis: boolean; semantic:boolean; token:string; storage:string; asrModel:string; analysisModel:string};
const statuses: Record<string,string> = {uploaded:'待分析',queued:'排队中',diarizing:'识别说话人',transcribing:'正在转写',analyzing:'正在分析',done:'分析完成',error:'分析失败'};
const scenes = ['自动识别','餐厅服务','销售沟通','会议'];
const navs = [
  {id:'dashboard',name:'门店工作台',icon:'grid'}, {id:'vision',name:'摄像头巡检',icon:'camera'},
  {id:'badge',name:'挂牌与胸牌语音',icon:'audio'}, {id:'phone',name:'前台电话',icon:'phone'},
  {id:'reports',name:'运营报表',icon:'chart'}, {id:'settings',name:'系统设置',icon:'gear'}, {id:'ai',name:'AI 分析',icon:'chat'}
];
const time = (v=0) => `${Math.floor(v/60).toString().padStart(2,'0')}:${Math.floor(v%60).toString().padStart(2,'0')}`;
const date = (v:string) => new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
function Icon({name,size=20}: {name:string;size?:number}) {
  const paths:Record<string,React.ReactNode> = {
    chat:<><path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9l-6 4V5a1 1 0 0 1 1-1Z"/><path d="M7 9h10M7 13h6"/></>,
    camera:<><rect x="2" y="6" width="14" height="12" rx="3"/><path d="m16 10 5-3v10l-5-3"/></>,
    phone:<path d="M7 3H4a1 1 0 0 0-1 1c0 9 8 17 17 17a1 1 0 0 0 1-1v-3l-5-2-2 2a16 16 0 0 1-7-7l2-2Z"/>,
    chart:<><path d="M4 3v17h17M8 15v-4m5 4V7m5 8v-6"/></>,
    grid:<><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    audio:<><path d="M4 9v6m4-10v14m4-17v20m4-17v14m4-10v6"/></>,
    check:<><rect x="3" y="3" width="18" height="18" rx="4"/><path d="m7 12 3 3 7-7"/></>,
    book:<><path d="M12 5v16M3 4c4-1 6 0 9 2 3-2 5-3 9-2v15c-4-1-6 0-9 2-3-2-5-3-9-2Z"/></>,
    gear:<><circle cx="12" cy="12" r="4"/><path d="m9 3 1-1h4l1 3 3 1 3 3-1 3 1 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 1-3-1-3 3-3 3-1Z"/></>,
    upload:<><path d="M12 16V3m-5 5 5-5 5 5M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/></>,
    shield:<><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/><path d="m8 12 3 3 5-6"/></>,
    file:<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8m-8 4h5"/></>,
    close:<path d="m6 6 12 12M6 18 18 6"/>,
    play:<path d="m8 4 12 8-12 8Z"/>,
    warning:<><path d="m12 3 10 18H2Z"/><path d="M12 9v5m0 3v1"/></>,
    search:<><circle cx="10" cy="10" r="7"/><path d="m15 15 6 6"/></>,
    download:<><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/></>,
    dot:<circle cx="12" cy="12" r="4"/>,
    chevron:<path d="m9 5 7 7-7 7"/>,
    trash:<><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></>,
    clock:<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    mic:<><rect x="8" y="2" width="8" height="13" rx="4"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.file}</svg>;
}
function Tag({children,tone='neutral'}:{children:React.ReactNode;tone?:string}) {return <span className={`tag ${tone}`}>{children}</span>}
const demo:Recording = {id:'demo',filename:'餐厅午市接待 · 示例录音',employee:'示例员工',createdAt:'2026-11-23T04:30:00Z',status:'done',progress:100,duration:68,sceneHint:'餐厅服务',size:0,reviewed:false,segments:[{id:0,start:0,end:20,speaker:'示例：服务员',text:'您好，欢迎光临。请问几位用餐？有没有忌口或者过敏的食材？'},{id:1,start:20,end:45,speaker:'示例：服务员',text:'今天推荐清蒸鲈鱼，口味比较清淡。我帮您确认一下，两位用餐，一份清蒸鲈鱼，两份米饭。'},{id:2,start:45,end:68,speaker:'示例：服务员',text:'不好意思让您久等了，我去厨房确认一下出餐进度，五分钟后回来跟您说明。'}],report:{scene:'餐厅服务',summary:'示例展示了迎宾、忌口询问、菜品推荐与订单复核。员工承诺核实出餐进度；当前示例没有后续反馈，不能判定问题已经解决。',scope:'这是预置示例，不是对你上传录音的分析结果。',checks:[{name:'迎宾与需求询问',status:'观察到',detail:'出现主动问候、人数询问与忌口询问。',evidence:[{segment:0,quote:'有没有忌口或者过敏的食材？',start:0}]},{name:'菜品介绍与推荐',status:'观察到',detail:'介绍了具体菜品及口味。',evidence:[{segment:1,quote:'今天推荐清蒸鲈鱼，口味比较清淡。',start:20}]},{name:'问题处理闭环',status:'待复核',detail:'仅观察到处理承诺，未确认后续落实。',evidence:[{segment:2,quote:'五分钟后回来跟您说明。',start:45}]}],risks:[{title:'等待问题需要后续确认',level:'关注',detail:'出现等待与催菜回应，不能据此判断客人情绪或是否满意。',evidence:[{segment:2,quote:'不好意思让您久等了',start:45}]}],todos:[{title:'确认出餐进度并向客人反馈',owner:'待确认',due:'待确认',done:false,evidence:[{segment:2,quote:'我去厨房确认一下出餐进度',start:45}]}],insights:[{label:'待确认',value:'顾客回答和后续处理结果未包含在示例中。',evidence:[{segment:0,quote:'请问几位用餐？',start:0}]}]}};

function App({user,store}:{user:User;store:boolean}){
  const quality=['管理员','经理','质检员'].includes(user.role);
  const supervise=['管理员','经理'].includes(user.role);
  const phone=user.role!=='质检员'||!!user.phone?.quality;
  const [source,setSource]=useState('胸牌');
  const [profileRevision,setProfileRevision]=useState(0);
  const [registry,setRegistry]=useState<Registry>({devices:[],connected:false,enabled:false,current:null,syncError:''});
  const [unitScope,setUnitScope]=useState('');
  const [deviceScope,setDeviceScope]=useState('全部设备');
  const seekAfterLoad=useRef<number|null>(null);
  const [badge,setBadge]=useState('');
  const [uploadPosition,setUploadPosition]=useState('');
  const [recordedAt,setRecordedAt]=useState('');
  const [reviewNote,setReviewNote]=useState('');
  const [pendingBadge,setPendingBadge]=useState(false);
  const [cameraFilter,setCameraFilter]=useState(false);
  const [page,setPage]=useState(/^#ai(?:$|\/)/.test(location.hash) ? 'ai' : phone && /^#phone(?:$|[/?])/.test(window.location.hash) ? 'phone' : quality && window.location.hash.startsWith('#vision') ? 'vision' : quality && window.location.hash==='#patrol'?'patrol':quality && window.location.hash==='#badge'?'badge':'dashboard');
  useEffect(()=>{
    if(page==='ai'&&!/^#ai(?:$|\/)/.test(location.hash)) history.replaceState(null,'','#ai');
    if(page!=='ai'&&/^#ai(?:$|\/)/.test(location.hash)) history.replaceState(null,'',location.pathname+location.search);
  },[page]);
  useEffect(()=>{const changed=()=>{if(/^#ai(?:$|\/)/.test(location.hash))setPage('ai')};window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed)},[]);
  useEffect(()=>{if(!['phone','phone-report','orders','pending','overdue','duty'].includes(page)&&/^#phone(?:$|[/?])/.test(window.location.hash))window.history.replaceState(null,'',window.location.pathname+window.location.search+(page==='vision'?'#vision':page==='patrol'?'#patrol':''))},[page]);

  const [recordsVisit,setRecordsVisit]=useState(0);
  function openCameraRecords(){
    window.history.replaceState(null,'','#vision');
    setPage('vision');
    setRecordsVisit(n=>n+1);
  }
  const activeModule = page === 'patrol' ? 'vision'
    : ['records','tasks','customers','standards','wecom','import'].includes(page) ? 'badge'
    : ['phone-report','orders','pending','overdue','duty'].includes(page) ? 'phone' : page;
  useEffect(() => {
    const hash = window.location.hash;
    if (page === 'vision' && !hash.startsWith('#vision')) window.history.replaceState(null,'','#vision');
    else if (page === 'patrol') window.history.replaceState(null,'','#patrol');
    else if (page !== 'vision' && /^#(?:vision|patrol)(?:$|\/)/.test(hash)) window.history.replaceState(null,'',window.location.pathname + window.location.search);
  }, [page]);
  const [records,setRecords]=useState<Recording[]>([]);
  const [health,setHealth]=useState<Health|null>(null);
  const [connected,setConnected]=useState(false);
  const [selected,setSelected]=useState<Recording|null>(null);
  const [tab,setTab]=useState('report');
  const [file,setFile]=useState<File|null>(null);
  const [testRecording,setTestRecording]=useState(false);
  const [employee,setEmployee]=useState('');
  const [scene,setScene]=useState('自动识别');
  const [note,setNote]=useState('');
  const [language,setLanguage]=useState('zh');
  const [speakerCount,setSpeakerCount]=useState(2);
  const [preferSpeakers,setUseSpeakers]=useState(true);
  const useSpeakers=preferSpeakers && !!health?.diarization;
  const [retryOpen,setRetryOpen]=useState(false);
  const [retryLanguage,setRetryLanguage]=useState('zh');
  const [retryCount,setRetryCount]=useState(2);
  const [speakerNames,setSpeakerNames]=useState<Record<string,string>>({});
  const [uploading,setUploading]=useState(false);
  const [uploadProgress,setUploadProgress]=useState(0);
  const [drag,setDrag]=useState(false);
  const [toast,setToast]=useState('');
  const [query,setQuery]=useState('');
  const [filter,setFilter]=useState('全部场景');
  const [busy,setBusy]=useState<string|null>(null);
  const [managed,setManaged]=useState<Recording|null>(null);
  const [deleteTarget,setDeleteTarget]=useState<Recording|null>(null);
  const input=useRef<HTMLInputElement>(null);
  const player=useRef<HTMLAudioElement>(null);
  const token=useRef('');
  const recordRevision=useRef(0);
  useEffect(()=>{
    let alive=true;
    let pending=false;
    const refresh=async()=>{
      if(pending) return;
      pending=true;
      const revision=recordRevision.current;
      try {
        const [h,items]=await Promise.all([
          request<Health>('api/status'),
          quality ? request<Recording[]>('api/records') : Promise.resolve([] as Recording[])
        ]);
        if(!alive) return;
        setHealth(h);
        token.current=h.token;
        setConnected(true);
        // An older list response must not close a newly uploaded recording.
        if(revision!==recordRevision.current) return;
        setRecords(items);
        setSelected(s=>s&&s.id!=='demo' ? items.find(r=>r.id===s.id)||null : s);
      } catch {
        if(alive) setConnected(false);
        if(store) fetch('api/session').then(r=>r.json()).then(s=>{if(!s.user)location.reload()}).catch(()=>{});
      } finally { pending=false; }
    };
    void refresh();
    const interval=setInterval(refresh,3000);
    return()=>{alive=false;clearInterval(interval)};
  },[quality,store]);
  useEffect(()=>{if(!toast)return;const t=setTimeout(()=>setToast(''),5500);return()=>clearTimeout(t)},[toast]);
  useEffect(()=>{const esc=(e:KeyboardEvent)=>{if(e.key==='Escape'){setSelected(null);setDeleteTarget(null);setRetryOpen(false)}};window.addEventListener('keydown',esc);return()=>window.removeEventListener('keydown',esc)},[]);
  async function api(path:string,method='POST',body?:unknown){const r=await fetch(path,{method,headers:{'x-local-token':token.current,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'操作失败，请重试');return data;}
  function replace(item:Recording){recordRevision.current++;setProfileRevision(v=>v+1);setRecords(all=>all.map(r=>r.id===item.id?item:r));setSelected(s=>s?.id===item.id?item:s)}
  async function analyze(item:Recording){setBusy(item.id);try{const result=await api(`api/records/${item.id}/analyze`);replace(result);setToast('已加入分析队列');}catch(e){setToast((e as Error).message)}finally{setBusy(null)}}
  async function analyzeCopy(item:Recording){
    setBusy(item.id);
    try {
      const result:Recording=await api(`api/records/${item.id}/deepseek-copy`);
      recordRevision.current++;
      setRecords(rows=>[result,...rows.filter(r=>r.id!==result.id)]);
      open(result);
    } catch(e){setToast((e as Error).message)} finally {setBusy(null)}
  }
  function chooseFile(f?:File){if(!f)return;if(!/\.(wav|mp3|m4a|aac|ogg|flac|mp4|webm)$/i.test(f.name)){setToast('请选择支持的音频格式');return}if(f.size>150*1024*1024){setToast('文件不能超过 150 MB');return}if(!f.size){setToast('文件为空');return}setFile(f)}
  async function upload(){if(!file||uploading)return;setUploading(true);setUploadProgress(0);try{const data=new FormData();data.append('file',file);data.append('test',String(testRecording));data.append('employee',employee);data.append('source',source);data.append('position',uploadPosition);data.append('badge',badge);data.append('recordedAt',recordedAt);data.append('scene',scene);data.append('note',note);data.append('language',language);data.append('diarization',String(useSpeakers));data.append('speakerCount',String(speakerCount));const result=await new Promise<Recording>((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','api/upload');xhr.setRequestHeader('x-local-token',token.current);xhr.upload.onprogress=e=>{if(e.lengthComputable)setUploadProgress(Math.round(e.loaded/e.total*100))};xhr.onerror=()=>reject(Error('本地服务连接失败'));xhr.onload=()=>{try{const d=JSON.parse(xhr.responseText);xhr.status<300?resolve(d):reject(Error(d.detail||'上传失败'))}catch{reject(Error('上传失败'))}};xhr.send(data)});recordRevision.current++;setRecords(r=>[result,...r.filter(x=>x.id!==result.id)]);if(unitScope)setUnitScope(unitChoices.has(recordUnit(result))?recordUnit(result):'');if(deviceScope!=='全部设备')setDeviceScope(source);setFile(null);if(input.current)input.current.value='';setToast('录音已保存到本机');setReviewNote('');setSelected(result);setTab('report');}catch(e){setToast((e as Error).message)}finally{setUploading(false)}}
  async function retrySpeech(){if(!selected)return;setBusy(selected.id);try{const result=await api(`api/records/${selected.id}/retranscribe`,'POST',{language:retryLanguage,diarization:true,speakerCount:retryCount});recordRevision.current++;setRecords(r=>[result,...r.filter(x=>x.id!==result.id)]);setSelected(result);setTab('transcript');setRetryOpen(false);setToast('已创建转写副本，原稿和原报告均保留');}catch(e){setToast((e as Error).message)}finally{setBusy(null)}}
  async function saveSpeaker(sid:string,name:string){if(!selected)return;try{replace(await api(`api/records/${selected.id}/speakers/${sid}`,'PATCH',{name}));setToast('已保存本次录音的说话人标签；自动分组仍需回听核对');}catch(e){setToast((e as Error).message)}}
  function open(item:Recording){seekAfterLoad.current=null;setReviewNote(item.reviewNote||'');setSelected(item);setTab('report')}
  function seek(sec:number){if(selected?.id==='demo'){setTab('transcript');return}if(player.current){player.current.currentTime=sec;player.current.play().catch(()=>setToast('请先点击播放器开始播放'))}}
  function exportReport(item:Recording){const blob=new Blob([JSON.stringify(item,null,2)],{type:'application/json;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`${item.filename.replace(/\.[^.]+$/,'')}-分析报告.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
  async function task(item:Recording,index:number,done:boolean){if(item.id==='demo'){setToast('示例任务不会写入本地记录');return}try{replace(await api(`api/records/${item.id}/tasks/${index}`,'PATCH',{done}))}catch(e){setToast((e as Error).message)}}
  async function markReviewed(item:Recording){try{replace(await api(`api/records/${item.id}/review`,'PATCH',{reviewed:!item.reviewed,note:reviewNote}));setToast(item.reviewed?'已撤销复核':'已标记为人工复核')}catch(e){setToast((e as Error).message)}}
  async function remove(){if(!deleteTarget)return;try{await api(`api/records/${deleteTarget.id}`,'DELETE');recordRevision.current++;setProfileRevision(v=>v+1);setRecords(r=>r.filter(x=>x.id!==deleteTarget.id));if(selected?.id===deleteTarget.id)setSelected(null);setDeleteTarget(null);setRetryOpen(false);setToast('录音及分析记录已从本机删除')}catch(e){setToast((e as Error).message)}}
  const registryPage=['badge','records','tasks','customers','import'].includes(page);
  useEffect(()=>{
    if(!quality||!registryPage||page==='badge')return;
    let live=true;
    request<Registry>('api/badge/devices').then(r=>{if(live)setRegistry(r)}).catch(()=>{if(live)setToast('无法读取设备清单')});
    return()=>{live=false}
  },[quality,registryPage,page]);
  function recordUnit(r:Recording) { return r.deviceId ? 'id-'+r.deviceId : ''; }
  const unitsById=new Map(registry.devices.map(d=>[d.id,d]));
  function deviceName(r:Recording) { return r.deviceId ? unitsById.get(r.deviceId)?.name.trim() || '' : ''; }
  function employeeName(r:Recording) { return r.employee?.trim()==='未指定员工' ? '' : r.employee?.trim() || ''; }
  function recordingTitle(r:Recording) {
    const name=employeeName(r)||deviceName(r);
    if(!name || !r.badge || !r.filename.startsWith(r.badge)) return r.filename;
    const suffix=r.filename.slice(r.badge.length);
    if(!/^(?:-\d{12,}[^/]*|)\.[a-z\d]+$/i.test(suffix)) return r.filename;
    const when=new Date((r.recordedAt||r.createdAt).replace(' ','T'));
    const stamp=Number.isNaN(when.getTime())?'录音':when.toLocaleString('zh-CN',{
      month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false
    });
    return name+' · '+stamp;
  }
  function recordingCaption(r:Recording) {
    const device=deviceName(r), employee=employeeName(r), source=r.source||'历史导入';
    if(!device&&!employee) return [r.employee,source,r.badge].filter(Boolean).join(' · ');
    const primary=recordingTitle(r)===r.filename?'':employee||device;
    return [...new Set([employee,device].filter(name=>name&&name!==primary)),source].join(' · ');
  }
  const unitChoices=new Map<string,{name:string;number:string;source:string}>();
  for(const d of registry.devices) {
    const key='id-'+d.id;
    if(!d.retired||unitScope===key) unitChoices.set(key,{
      name:(d.name.trim()||d.number)+(d.retired?' · 已停用':''),number:d.number,source:d.source
    });
  }
  function viewUnit(d:Unit) {
    setDeviceScope(d.source);setUnitScope('id-'+d.id);
    setQuery('');setFilter('全部场景');setPendingBadge(false);setPage('records');
  }
  const inRange=records;
  const sourceOf=(r:Recording)=>r.source==='挂牌'||r.source==='胸牌'?r.source:'历史导入';
  const scoped=inRange.filter(r=>(deviceScope==='全部设备'||sourceOf(r)===deviceScope)&&(!unitScope||recordUnit(r)===unitScope));
  const done=scoped.filter(r=>r.status==='done');
  const pending=done.filter(r=>!r.reviewed);
  const tasks=done.flatMap(r=>(r.report?.todos||[]).map((t,i)=>({record:r,task:t,index:i}))).filter(({task:t})=>t.feedback?.at(-1)?.state!=='排除');
  const filtered=scoped.filter(r=>(!pendingBadge||r.status==='done'&&!r.reviewed)&&[recordingTitle(r),r.filename,r.employee,deviceName(r),r.badge].join(' ').toLowerCase().includes(query.toLowerCase())&&(filter==='全部场景'||r.report?.scene===filter||r.report?.scenes?.includes(filter)||(!r.report&&r.detectedScenes?.includes(filter))));
  const badgeFilters=<BadgeFilters types={['全部设备','挂牌','胸牌',...(deviceScope==='历史导入'||inRange.some(r=>sourceOf(r)==='历史导入')?['历史导入']:[])]}
    type={deviceScope} chooseType={type=>{setDeviceScope(type);setUnitScope('')}} choices={unitChoices}
    unit={unitScope} chooseUnit={setUnitScope}/>;
  function evidence(items:Evidence[]){
    const bySegment=new Map<number,{start:number;quotes:Set<string>}>();
    for(const ev of items||[]){
      const group=bySegment.get(ev.segment)??{start:ev.start,quotes:new Set<string>()};
      group.quotes.add(ev.quote);
      bySegment.set(ev.segment,group);
    }
    return <div className="evidence-list">{Array.from(bySegment,([id,group])=><button key={id} className="evidence" onClick={()=>seek(group.start)} title="定位到原话所在片段"><span><Icon name="play" size={12}/>{time(group.start)}</span><q>{Array.from(group.quotes).join(' … ')}</q></button>)}</div>
  }
  const newUpload=()=>{const d=registry.devices.find(u=>unitScope==='id-'+u.id&&!u.retired);if(d){setSource(d.source);setBadge(d.number)}setPage('import')};
  function feedback(item:Recording,group:string,index:number,row:Entry){
    if(item.id==='demo') return null;
    return <FindingReview finding={row} save={async(state,value,note)=>{try{replace(await api(`api/records/${item.id}/findings/${group}/${index}`,'PATCH',{state,value,note}));setToast('逐项复核已保存')}catch(e){setToast((e as Error).message);throw e}}}/>;
  }
  function table(items:Recording[]){return items.length?<div className="table-scroll"><table><thead><tr><th>录音 / 员工</th><th>场景</th><th>时长</th><th>状态</th><th>上传时间</th><th>操作</th></tr></thead><tbody>{items.map(r=><tr key={r.id} onClick={()=>open(r)}><td><div className="file-cell"><span className="file-icon"><Icon name="audio"/></span><div><button className="row-title" onClick={e=>{e.stopPropagation();open(r)}}>{recordingTitle(r)}</button><small>{recordingCaption(r)}</small></div></div></td><td>{sceneLabel(r.report,r.detectedScenes)}</td><td className="mono">{r.duration?time(r.duration):'—'}</td><td><Tag tone={r.status==='done'?'green':r.status==='error'?'red':r.status==='uploaded'?'neutral':'amber'}>{r.status==='done'?(r.reviewed?'已复核':'待复核'):statuses[r.status]}</Tag>{['diarizing','transcribing','analyzing'].includes(r.status)&&<small className="row-progress">{r.progress}%</small>}</td><td className="muted">{date(r.createdAt)}</td><td>{supervise?<button className="rec-action" aria-label={`管理 ${recordingTitle(r)}`} onClick={e=>{e.stopPropagation();setManaged(r)}}>管理</button>:<button className="text-button" onClick={e=>{e.stopPropagation();open(r)}}>查看</button>}</td></tr>)}</tbody></table></div>:<div className="empty"><span className="empty-icon"><Icon name="audio" size={27}/></span><strong>{query||unitScope||filter!=='全部场景'||deviceScope!=='全部设备'||pendingBadge?'没有符合条件的录音':'从第一段录音开始'}</strong><p>{query||filter!=='全部场景'||deviceScope!=='全部设备'||pendingBadge?'可切换设备或调整搜索条件。':'上传员工录音，建立可回溯的服务记录。'}</p><button className="text-button" onClick={()=>open(demo)}>先看看示例报告</button></div>}
  const selectedScenes=selected?.report?.scenes??(selected?.report?.scene?[selected.report.scene]:selected?.detectedScenes||[]);
  const canCopyReport=selected?.status==='done'&&selected.id!=='demo'&&(selected.report?.reportVersion??1)<(health?.badgeReportVersion??4)&&selected.segments.length>0;
  const canRetryEmpty=canCopyReport&&selected.report?.mode==='noEvidence'&&!selected.report.scene&&!selected.reviewed&&!selected.reviewNote
    &&![selected.report.conclusions,selected.report.todos,selected.report.flow,selected.report.checks,selected.report.risks,selected.report.insights].some(rows=>rows?.length);
  const oldReview=canCopyReport&&(selected?.report?.reportVersion??1)<3;
  const canManageAudio=!!selected&&selected.id!=='demo'&&['done','error','uploaded'].includes(selected.status);
  const hasMoreActions=(supervise&&!!selected?.report)||(canManageAudio&&(supervise||health?.diarization));
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark inn-mark" aria-hidden="true"><i/><i/><i/></span><div><strong>栖鉴</strong><small>酒店智能巡检与服务分析</small></div></div><nav>{navs.filter(n=>!(['vision','badge'].includes(n.id)&&!quality)&&!(n.id==='phone'&&!phone)&&!(n.id==='settings'&&!supervise)).map(n=><button key={n.id} aria-current={activeModule===n.id?'page':undefined} className={activeModule===n.id?'nav-item active':'nav-item'} onClick={()=>{setPendingBadge(false);setCameraFilter(false);setPage(n.id)}}><Icon name={n.icon}/>{n.name}</button>)}</nav><div className="sidebar-bottom"><p>{user.name} · {user.role}</p>{store&&<button onClick={async()=>{await request('api/session/logout',{method:'POST'});location.reload()}}>退出登录</button>}</div></aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">酒店质检 <span>/</span> <strong>{[...navs,{id:'import',name:'导入录音'},{id:'customers',name:'客户档案'},{id:'records',name:'挂牌与胸牌录音档案'},{id:'wecom',name:'企微连接'},{id:'standards',name:'服务标准'},{id:'tasks',name:'改进待办'},{id:'patrol',name:'摄像头巡检'},{id:'duty',name:'电话值班'},{id:'phone-report',name:'电话运营分析'},{id:'pending',name:'待确认电话事项'},{id:'overdue',name:'超时工单'}].find(n=>n.id===page)?.name}</strong></div><div className="top-right"><span className={`connection ${connected?'':'offline'}`}><i/>{connected?'本地服务已连接':'正在连接本地服务'}</span><span className="top-divider"/><span className="today">{new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'})}</span></div></header>
    <main>
      {page==='ai'&&<Agent token={health?.token||''}/>}
      {(page==='dashboard'||page==='reports')&&<Overview quality={quality} phone={phone} phoneReport={phone&&!['部门员工','质检员'].includes(user.role)} report={page==='reports'} navigate={target=>{
        setPendingBadge(target==='records');if(target==='records'){setDeviceScope('全部设备');setUnitScope('');setFilter('全部场景');setQuery('')}setCameraFilter(target==='vision-pending');setPage(target==='vision-pending'?'vision':target)
      }}/>}
      {['phone','phone-report','orders','pending','overdue','duty'].includes(page)&&phone&&<Phone key={page} user={user} token={health?.token||''} initial={page==='phone-report'?'report':page==='phone'?'calls':page}/>}
      {['badge','records','tasks','standards','wecom','customers','import'].includes(page)&&<nav className="module-tabs bd-nav" aria-label="语音模块导航">
        {[['badge','设备'],['records','录音档案'],['tasks','改进待办'],...(supervise?[['wecom','企微连接']]:[])].map(([id,label])=><button key={id} aria-current={(page===id||(page==='import'&&id==='records'))?'page':undefined} onClick={()=>{setPendingBadge(false);setPage(id)}}>{label}</button>)}
        <details><summary aria-current={page==='customers'||page==='standards'?'page':undefined}>更多</summary><div>{[['customers','客户档案'],['standards','服务标准']].map(([id,label])=><button className="text-button" key={id} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');setPage(id)}}>{label}</button>)}</div></details>
      </nav>}
      {['vision','patrol'].includes(page)&&<nav className="module-tabs bd-nav" aria-label="摄像头巡检导航">
        <button aria-current={page==='vision'?'page':undefined} onClick={openCameraRecords}>巡检记录</button>
        <button aria-current={page==='patrol'?'page':undefined} onClick={()=>setPage('patrol')}>添加点位</button>
      </nav>}
      {page==='vision'&&<Vision key={recordsVisit} pending={cameraFilter} setPending={setCameraFilter} canExport={supervise} token={health?.token || ''}/>}
      {page==='patrol'&&<Patrol token={health?.token || ''} openReport={id=>{window.history.replaceState(null,'','#vision/'+id);setPage('vision');}}/>}
      {page==='wecom'&&supervise&&<BadgeWecom admin={user.role==='管理员'} api={api} records={records} listen={(id,start)=>{const rec=records.find(r=>r.id===id);if(rec){open(rec);seekAfterLoad.current=start}else{setToast('该录音已移除，请刷新日报')}}}/>}
      {quality&&<BadgeDevices visible={page==='badge'} api={api} admin={user.role==='管理员'} registry={registry} setRegistry={setRegistry} records={records} viewRecords={viewUnit} openRecord={id=>{const r=records.find(v=>v.id===id);if(r)open(r)}}/>}
      {page==='import'&&<><section className="page-heading"><h1>导入录音</h1><button className="secondary" onClick={()=>setPage('records')}>返回录音档案</button></section>
      <div className="bd-import"><section className="panel upload-panel"><div className="section-title"><div><h2>上传服务录音</h2><p>支持胸牌与挂牌导出的音频</p></div><Tag tone="green">本地转写 · DeepSeek 分析</Tag></div><input ref={input} type="file" accept=".wav,.mp3,.m4a,.aac,.ogg,.flac,.mp4,.webm" hidden onChange={e=>chooseFile(e.target.files?.[0])}/><button className={`dropzone ${drag?'dragging':''} ${file?'has-file':''}`} onClick={()=>input.current?.click()} onDragOver={e=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={e=>{e.preventDefault();setDrag(false);chooseFile(e.dataTransfer.files[0])}} disabled={uploading}><span className="upload-icon"><Icon name={file?'audio':'upload'} size={28}/></span><strong>{file?file.name:'点击选择录音，或拖拽文件到这里'}</strong><span>{file?`${(file.size/1048576).toFixed(1)} MB · 点击更换文件`:'WAV / MP3 / M4A 等 · 单段最长 30 分钟 · 最大 150 MB'}</span></button><div className="form-row"><label>员工姓名或编号 <span>选填</span><input value={employee} onChange={e=>setEmployee(e.target.value)} maxLength={80} placeholder="例如：餐饮部 · 张经理"/></label><label>场景提示<select value={scene} onChange={e=>setScene(e.target.value)}>{scenes.map(s=><option key={s}>{s}</option>)}</select></label></div><div className="form-row"><label>选择已登记设备<select value={registry.devices.find(d=>d.source===source&&d.number===badge&&!d.retired)?.id||''} onChange={e=>{const d=registry.devices.find(d=>d.id===Number(e.target.value));if(d){setSource(d.source);setBadge(d.number)}else setBadge('')}}><option value="">手动填写</option>{registry.devices.filter(d=>!d.retired).map(d=><option key={d.id} value={d.id}>{d.source} · {d.name||d.number}{d.name?' · '+d.number:''}</option>)}</select></label><label>设备来源<select value={source} onChange={e=>setSource(e.target.value)}><option>胸牌</option><option>挂牌</option></select></label></div><div className="form-row"><label>设备编号<input value={badge} onChange={e=>setBadge(e.target.value)} maxLength={80}/></label><label>实际录制时间<input type="datetime-local" value={recordedAt} onChange={e=>setRecordedAt(e.target.value)}/></label></div>{!registry.devices.some(d=>d.source===source&&d.number===badge&&!d.retired)&&<label>录音岗位<select value={uploadPosition} onChange={e=>setUploadPosition(e.target.value)}><option value="">未指定</option><option>前台</option></select></label>}<div className="form-row speech-options"><label>转写语言<select value={language} onChange={e=>setLanguage(e.target.value)}><option value="zh">中文 / 普通话</option><option value="auto">自动识别 / 中英混说</option></select></label><label>对话人数<select value={speakerCount} disabled={!useSpeakers} onChange={e=>setSpeakerCount(Number(e.target.value))}><option value={0}>不确定，自动判断</option>{[1,2,3,4,5,6,7,8].map(n=><option key={n} value={n}>{n} 人</option>)}</select></label></div><label className="diarization-toggle"><input type="checkbox" disabled={!health?.diarization} checked={useSpeakers} onChange={e=>setUseSpeakers(e.target.checked)}/>按声纹区分说话人 <span>{useSpeakers?'声纹分组转写':'普通转写'} · {health?.semantic?health.analysisModel:'待配置 DeepSeek 密钥'}</span></label><label className="diarization-toggle"><input type="checkbox" checked={testRecording} onChange={e=>setTestRecording(e.target.checked)}/>测试录音（排除在正式日报外）</label><label className="note-label">录音备注 <span>选填，仅作归档说明</span><input value={note} onChange={e=>setNote(e.target.value)} maxLength={1500} placeholder="例如：午市接待，靠近厨房，环境较嘈杂"/></label><div className="upload-bottom"><span><Icon name="shield" size={15}/>音频保存在本机，转写原文（可能含姓名、房号和联系方式）将发送给 DeepSeek</span><button className="primary" disabled={!file||uploading||!connected} onClick={upload}><Icon name="upload" size={17}/>{uploading?`正在保存 ${uploadProgress}%`:health?.asr&&health.analysis&&(!useSpeakers||health.diarization)?'上传并分析':'保存录音'}</button></div>{(!health?.asr||!health.analysis||(useSpeakers&&!health.diarization))&&<div className="inline-notice"><Icon name="clock" size={15}/>模型准备中，录音可先保存，转写模型就绪后自动分析。</div>}</section>
      </div></>}
      {page==='records'&&<><section className="page-heading"><div><h1>录音档案</h1><p>{scoped.length} 段录音 · {pending.length} 段待复核 · {tasks.filter(t=>!t.task.done).length} 项待跟进</p></div><button className="primary" onClick={newUpload}><Icon name="upload" size={17}/>上传录音</button></section>{badgeFilters}{pendingBadge&&<button className="text-button" onClick={()=>setPendingBadge(false)}>取消待复核筛选</button>}<section className="panel"><div className="filters"><div className="search-input"><Icon name="search" size={18}/><input aria-label="搜索录音或员工" placeholder="搜索录音名称、员工" value={query} onChange={e=>setQuery(e.target.value)}/></div><select aria-label="按场景筛选" value={filter} onChange={e=>setFilter(e.target.value)}>{['全部场景',...scenes.slice(1),'混合场景'].map(s=><option key={s}>{s}</option>)}</select><span className="muted">共 {filtered.length} 条</span></div>{table(filtered)}</section></>}
      {page==='customers'&&<>{badgeFilters}<Customers deviceRecords={deviceScope==='全部设备'&&!unitScope?undefined:scoped} revision={profileRevision} api={api} notify={setToast} onEvidence={(id,start)=>{const rec=records.find(r=>r.id===id);if(rec){open(rec);seekAfterLoad.current=start}}}/></>}
      {page==='tasks'&&<><section className="page-heading"><div><h1>改进待办</h1></div><Tag tone="green">{tasks.filter(t=>!t.task.done).length} 项待跟进</Tag></section>{badgeFilters}<section className="panel tasks-panel">{tasks.length?tasks.map(({record:r,task:t,index})=><div className={`task-row ${t.done?'completed':''}`} key={r.id+index}><input type="checkbox" checked={!!t.done} onChange={e=>task(r,index,e.target.checked)} aria-label={`完成 ${t.title}`}/><div><strong>{t.feedback?.at(-1)?.value||t.title}</strong><p>负责人：{t.owner} · 截止时间：{t.due}</p><button className="text-button" onClick={()=>open(r)}>{r.filename}</button></div><Tag>{t.done?'已完成':'待处理'}</Tag></div>):<div className="empty vf-empty"><span className="empty-icon"><Icon name="check" size={24}/></span><strong>暂无待办事项</strong><p>当前筛选范围内没有待跟进的服务事项。</p><button className="text-button" onClick={()=>setPage('records')}>查看录音档案</button></div>}</section></>}
      {page==='standards'&&<><section className="page-heading"><div><div className="eyebrow">REVIEW FRAMEWORK</div><h1>服务标准</h1><p>当前内置检查范围 · 适用于试点复盘，正式考核前需校准。</p></div></section>{supervise&&<ScriptRules admin={user.role==='管理员'} api={api}/>}<div className="standard-grid">{[{title:'餐厅服务',icon:'book',items:['迎宾与人数询问','忌口、过敏等需求询问','菜品介绍与推荐理由','订单内容复核','问题回应与处理承诺']},{title:'销售沟通',icon:'check',items:['需求、预算与时间','客户顾虑及回应','竞品提及的原话','约定的下一步','有证据的标签建议']},{title:'会议',icon:'file',items:['议题与讨论摘要','明确决策与建议的区分','责任人与截止时间','待办事项提取','分歧和待确认事项']}].map(s=><section className="panel standard-card" key={s.title}><span className="standard-icon"><Icon name={s.icon} size={24}/></span><h2>{s.title}</h2><ul>{s.items.map(i=><li key={i}><Icon name="check" size={16}/>{i}</li>)}</ul></section>)}</div><section className="principles panel"><h2>如何理解检查结果</h2><div><p><Tag tone="green">观察到</Tag>在录音里找到明确表达；是否由指定员工说出仍需复核。</p><p><Tag tone="amber">待复核</Tag>存在相关线索，需要回听或确认后续处理。</p><p><Tag>无法判断</Tag>缺少可靠信息，不因未录到而判定未服务。</p></div><div className="inline-notice"><Icon name="warning" size={18}/>不自动给员工打分。声纹用于本段录音内分组；未登记姓名声纹，不自动将标签写入 PMS。</div></section></>}
      {page==='settings'&&<div className="work-page">
        <WorkHeading title="系统设置" description="查看本机分析服务、模型与数据保存位置。"/>
        <div className="work-settings">
          <section className="work-card"><header className="work-card-head"><HardDrive size={19}/><h2>模型运行状态</h2></header>
            {[
              {label:'语音转写', icon:'mic', model:health?.asrModel || 'SenseVoice Small', ready:health?.asr, waiting:'等待模型下载'},
              {label:'场景与业务分析', icon:'file', model:health?.analysisModel || 'DeepSeek', ready:health?.semantic, waiting:'待配置环境变量'},
              {label:'本地声纹分组', icon:'audio', model:health?.speakerModel || '正在读取模型信息', ready:health?.diarization, waiting:'等待模型下载'}
            ].map(row=><div className="work-model" key={row.label}>
              <span className="work-icon"><Icon name={row.icon}/></span><div><strong>{row.label}</strong><p>{row.model}</p></div>
              <Tag tone={!health?'neutral':row.ready?'green':'amber'}>{!health?'正在连接':row.ready?(row.label==='场景与业务分析'?'已配置':'已就绪'):row.waiting}</Tag>
            </div>)}
            <p className="work-card-note">胸牌与挂牌使用服务器环境变量 DEEPSEEK_API_KEY。分析失败后可从已有转写重试。</p>
          </section>
          <section className="work-card"><header className="work-card-head"><Database size={19}/><h2>数据与隐私</h2></header>
            <div className="work-storage"><h3>本机保存位置</h3><code>{health?.storage||'正在获取存储目录…'}</code>
              <p>录音、转写、报告和待办保存在本机。可在录音详情导出记录，删除后无法恢复。</p></div>
            <p className="work-card-note"><Icon name="shield" size={15}/>{store?'店内多人登录':'本机模式'}</p>
          </section>
        </div>
      </div>}
      <footer><span>栖鉴 · 酒店智能巡检与服务分析</span><span><Icon name="shield" size={13}/>原话可回听 · 人工复核</span></footer>
    </main></div>
    {selected&&<div className="modal-backdrop" onClick={()=>setSelected(null)}><section className="report-modal" role="dialog" aria-modal="true" aria-label="录音分析详情" onClick={e=>e.stopPropagation()}><div className="modal-heading"><div><h2>{recordingTitle(selected)}</h2><p>{employeeName(selected)||deviceName(selected)||selected.employee} <span>·</span> {selected.duration?time(selected.duration):'时长待识别'}{tab!=='report'&&selectedScenes.length>0&&<> · {selectedScenes.length>1?'混合场景 · ':''}{selectedScenes.join(' · ')}</>}</p></div><button autoFocus className="icon-button" aria-label="关闭详情" onClick={()=>setSelected(null)}><Icon name="close"/></button></div><div className="report-toolbar"><div><Tag tone={selected.status==='done'?'green':selected.status==='error'?'red':'amber'}>{selected.id==='demo'?'预置示例':statuses[selected.status]}</Tag>{selected.report?.mode&&<Tag>{reportMode(selected.report.mode)}</Tag>}{selected.sourceId&&<button className="text-button" onClick={()=>{const original=records.find(r=>r.id===selected.sourceId);if(original)open(original)}}>查看保留的原稿</button>}{selected.reviewed&&<Tag tone="green">已人工复核</Tag>}</div><div>{selected.status==='done'&&selected.id!=='demo'&&<button className="primary small" onClick={()=>markReviewed(selected)}><Icon name="check" size={15}/>{selected.reviewed?'撤销复核':'确认已复核'}</button>}{hasMoreActions?<details className="br-actions" onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))e.currentTarget.open=false}}>
      <summary className="secondary small">更多操作</summary><div onClick={e=>{if((e.target as HTMLElement).closest('button'))e.currentTarget.closest('details')?.removeAttribute('open')}}>{selected.report&&supervise&&<button className="secondary small" onClick={async()=>{try{exportReport(selected.id==='demo'?selected:await request<Recording>('api/records/'+selected.id+'/export'))}catch(e){setToast((e as Error).message)}}}>导出 JSON</button>}{selected.report&&supervise&&<button className="secondary small" onClick={async()=>{try{exportHTML(selected.id==='demo'?selected:await request<Recording>('api/records/'+selected.id+'/export'))}catch(e){setToast((e as Error).message)}}}><Icon name="download" size={15}/>导出报告</button>}{health?.diarization&&canManageAudio&&<button className="secondary small" onClick={()=>{setRetryLanguage(selected.language||'zh');setRetryCount(selected.asrRevision&&selected.asrRevision>=3?(selected.speakerCount??2):2);setRetryOpen(true)}}>按说话人重新转写</button>}{supervise&&canManageAudio&&<button className="icon-button danger" title="删除录音" aria-label="删除录音" onClick={()=>setDeleteTarget(selected)}><Icon name="trash" size={18}/></button>}</div></details>:null}</div></div>{selected.id!=='demo'?<div className="audio-player"><Icon name="audio"/><audio key={selected.id} ref={player} onLoadedMetadata={()=>{if(seekAfterLoad.current!==null){const start=seekAfterLoad.current;seekAfterLoad.current=null;seek(start)}}} src={`api/records/${selected.id}/audio`} controls preload="metadata"/><span>本地录音回听</span></div>:<div className="demo-banner">这是用于了解产品的预置示例，未使用你的录音；示例不包含音频。</div>}
    <div className="module-meta br-meta"><span>{selected.source||'历史导入'} · {deviceName(selected)||employeeName(selected)||selected.badge||'未登记设备'} · {selected.recordedAt||'未登记录制时间'}</span>{selected.status==='done'&&<details><summary>复核意见</summary><label><input aria-label="复核意见" value={reviewNote} disabled={selected.reviewed} onChange={e=>setReviewNote(e.target.value)}/></label></details>}</div><RecordingTabs value={tab} select={setTab}/><div className="report-body" id="recording-panel" role="tabpanel" aria-labelledby={'module-'+tab} tabIndex={0} key={tab}>
    {tab==='report'&&<RecordingScene names={selectedScenes} label={sceneLabel(selected.report,selected.detectedScenes)}
      source={selected.id==='demo'?'示例场景':selected.report?.sceneSource==='上传提示'?'上传时指定':selected.report?.sceneSource==='转写明示'?'录音明示场景':selected.report?.sceneSource==='待重新识别'?'历史报告场景':selected.report&&!selected.report.sceneSource?'历史报告场景':selected.report?.sceneSource==='手选场景'?'历史手选场景':'识别结果'}
      action={canCopyReport&&<button className="secondary small" disabled={busy===selected.id} onClick={()=>void (canRetryEmpty?analyze(selected):analyzeCopy(selected))}>重新识别并分析</button>}/>}
    {selected.status!=='done'&&tab==='report'&&<div className="processing"><span className="empty-icon"><Icon name={selected.status==='error'?'warning':'audio'} size={32}/></span><h3>{selected.status==='analyzing'?(selected.detectedScenes?.length?'正在生成场景报告':'正在识别录音场景'):statuses[selected.status]}</h3><p>{selected.error||(['queued','diarizing','transcribing','analyzing'].includes(selected.status)?'本地转写 · DeepSeek 分析。可以关闭详情，稍后查看结果。':'录音已保存，可开始转写与场景分析。')}</p>{['queued','diarizing','transcribing','analyzing'].includes(selected.status)?<><progress value={selected.progress} max={100}/><span>{selected.progress}%</span></>:<button className="primary" disabled={busy===selected.id||(!selected.segments.length&&!health?.asr)} onClick={()=>analyze(selected)}>{selected.segments.length?'用已有转写重试分析':!health?.asr?'等待本地转写模型':selected.status==='error'?'重新转写与分析':'开始转写与分析'}</button>}</div>}
    {tab==='report'&&oldReview&&<p className="muted">这份历史报告使用旧版分析方式。重新识别后可查看服务质量专项报告，原始记录保存在本机。</p>}
    {tab==='report'&&selected.report&&!oldReview&&<BadgeReport key={selected.id} report={selected.report} evidence={evidence}
      emptyAction={<button className="text-button" onClick={()=>setTab(selected.segments.length?'transcript':'quality')}>{selected.segments.length?'查看对话逐字稿':'查看录音质量'}</button>}
      review={(group,index,row)=>feedback(selected,group,index,row)} task={(index,done)=>void task(selected,index,done)}
      customer={(selected.report.scene==='销售沟通'||selected.report.scenes?.includes('销售沟通'))&&selected.id!=='demo'
        ? <Customers record={selected} api={api} onLink={replace} notify={setToast}/> : undefined}/>}
    {tab==='report'&&oldReview&&selected.report&&<details className="br-section"><summary><strong>查看历史报告</strong></summary>
      <BadgeReport key={selected.id} report={selected.report} evidence={evidence} review={(group,index,row)=>feedback(selected,group,index,row)}
        task={(index,done)=>void task(selected,index,done)}/></details>}
    {tab==='transcript'&&<><div className="br-module-heading"><div><small>原话撰写</small><h3>对话逐字稿</h3></div><span>{selected.segments.length} 段发言</span></div>{selected.speakers&&Object.keys(selected.speakers).length>0&&<details className="speaker-panel"><summary>说话人 · {Object.keys(selected.speakers).length} 人 · 编辑名称</summary>{Object.keys(selected.speakers).length>8&&<div className="inline-notice"><Icon name="warning" size={17}/>自动分组较多，可能把同一人拆成多组，不能视为实际人数。请回听；若知道人数，可指定人数重新转写。</div>}<p>可将分组命名为员工、顾客或姓名。标签仅用于本段录音，不会登记为跨录音身份，也不自动改写已有报告。</p><div className="speaker-grid">{Object.entries(selected.speakers).map(([sid,meta])=><div className="speaker-editor" key={sid}><label>{`分组 ${Number(sid.split('_')[1])+1}`}<input aria-label={`分组 ${Number(sid.split('_')[1])+1} 的名称`} maxLength={40} value={speakerNames[selected.id+sid]??meta.name} onChange={e=>setSpeakerNames(n=>({...n,[selected.id+sid]:e.target.value}))}/></label><button className="secondary small" disabled={!['done','error'].includes(selected.status)} onClick={()=>saveSpeaker(sid,speakerNames[selected.id+sid]??meta.name)}>保存标签</button></div>)}</div></details>}{selected.segments.length?selected.segments.map(s=><div className="transcript-row" key={s.id}><button className="timestamp" onClick={()=>seek(s.start)}><Icon name="play" size={12}/>{time(s.start)}</button><div><small><span className="speaker-chip">{s.speaker}</span> · {time(s.start)}–{time(s.end)} {s.overlap&&<Tag tone="amber">重叠 · 已排除业务分析</Tag>}{s.needsReview&&!s.overlap&&<Tag tone="amber">短语需复核</Tag>}</small><p>{s.text}</p></div></div>):<div className="empty"><p>转写完成后，对话片段会显示在这里。</p></div>}</>}
    {tab==='quality'&&<>{selected.quality?<><div className="br-module-heading"><div><small>录音质量</small><h3>声音检测结果</h3></div></div><div className="quality-metrics"><div><small>整体均方根电平</small><strong>{selected.quality.rmsDb}<span> dBFS</span></strong></div><div><small>接近满幅采样占比</small><strong>{selected.quality.clippedPercent}<span> %</span></strong></div></div><h3 className="br-quality-title">回听提示</h3>{selected.quality.warnings.map(w=><p className="quality-warning" key={w}><Icon name="warning" size={18}/>{w}</p>)}</>:<div className="empty"><Icon name="mic" size={30}/><strong>{selected.id==='demo'?'示例未包含音频质量检测':'等待音频检测'}</strong><p>分析完成后可查看录音电平与采样信息。</p></div>}</>}
    </div></section></div>}
    {retryOpen&&selected&&<div className="confirm-backdrop"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="重新转写设置"><h2>按说话人重新转写</h2><p>生成一份新的转写和报告，保留原稿、原报告及已有待办。</p><label className="note-label">转写语言<select value={retryLanguage} onChange={e=>setRetryLanguage(e.target.value)}><option value="zh">中文 / 普通话</option><option value="auto">自动识别 / 中英混说</option></select></label><label className="note-label">对话人数<select value={retryCount} onChange={e=>setRetryCount(Number(e.target.value))}><option value={0}>不确定，自动判断</option>{[1,2,3,4,5,6,7,8].map(n=><option key={n} value={n}>{n} 人</option>)}</select></label><p className="settings-help">默认按 2 人对话处理；会议或多人场景可修改人数。连续发言合并展示，重叠讲话仍需回听。</p><div><button className="secondary" onClick={()=>setRetryOpen(false)}>取消</button><button className="primary" disabled={busy===selected.id||!health?.diarization} onClick={retrySpeech}>{busy===selected.id?'正在提交':'保留原稿并开始'}</button></div></section></div>}
    {managed&&<RecordingManager key={managed.id} record={{...managed,filename:recordingTitle(managed)}} close={()=>setManaged(null)}
      save={async values=>{replace(await api('api/records/'+managed.id+'/metadata','PATCH',{...values,filename:values.filename===recordingTitle(managed)?managed.filename:values.filename}));setToast('录音信息已保存')}}
      remove={()=>{setDeleteTarget(managed);setManaged(null)}}/>}
    {deleteTarget&&<div className="confirm-backdrop"><section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-label="确认删除录音"><Icon name="trash" size={28}/><h2>删除这条录音？</h2><p>“{recordingTitle(deleteTarget)}”的音频、转写、报告和待办将从本机删除，无法恢复。</p><div><button className="secondary" onClick={()=>setDeleteTarget(null)}>保留录音</button><button className="danger-button" onClick={remove}>确认删除</button></div></section></div>}
    {toast&&<div className="toast" role="status">{toast}<button aria-label="关闭提示" onClick={()=>setToast('')}><Icon name="close" size={16}/></button></div>}
  </div>
}
createRoot(document.getElementById('root')!).render(<Store>{(user,store)=><App user={user} store={store}/>}</Store>);
