const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const stamp = (s=0) => `${Math.floor(s/60).toString().padStart(2,'0')}:${Math.floor(s%60).toString().padStart(2,'0')}`;
type SceneResult = {scene?:string|null; scenes?:string[]; sceneSource?:string; mode?:string; checks?:{standardFlow?:string}[]};

export function sceneLabel(report?:SceneResult|null, detected:string[] = []) {
  const names = report?.scenes ?? detected;
  if (names.length > 1) return '混合场景';
  if (report?.scene === '销售沟通') {
    const flows=[...new Set((report.checks||[]).map(row=>row.standardFlow).filter(Boolean))];
    if(flows.length) return '销售沟通 · '+flows.map(flow=>flow+'办理').join('、');
  }
  if (report?.scene || names.length) return report?.scene || names[0];
  if (report?.sceneSource === '业务依据不足') return '场景依据不足';
  if (report?.sceneSource === '待重新识别') return '待重新识别';
  if (report?.mode === 'noEvidence') return '无可分析原话';
  return report ? '未归类' : '待识别';
}

export function reportMode(mode?:string) {
  if(mode==='deepseek') return 'DeepSeek 场景分析';
  if(mode==='noEvidence') return '缺少可分析片段';
  return mode==='rules'?'规则初筛':'本地语义分析';
}

export function scriptSummary(rows:{status?:string}[]) {
  const counts=new Map<string,number>();
  for(const {status} of rows) if(status) counts.set(status,(counts.get(status)||0)+1);
  return ['疑似偏差','部分体现','符合','证据不足','门店待确认','不适用','暂不要求']
    .filter(status=>counts.has(status)).map(status=>`${counts.get(status)} 项${status}`).join('，');
}
export function exportHTML(item:any){
  const report=item.report;
  const checks=report.checks||[];
  const scriptSections=[];
  if(report.frontDeskStandard) {
    for(const flow of ['入住','退房']) {
      const rows=checks.filter((row:any)=>row.standardFlow===flow);
      if(rows.length) scriptSections.push([`前台${flow}标准话术 · ${escape(report.frontDeskStandard)} · ${scriptSummary(rows)}`,rows]);
    }
  }
  const emptyDesk=report.frontDeskStandard&&!scriptSections.length?'<h2>前台标准话术核对</h2><p>本段原话不足以确认入住或退房办理。</p>':'';
  const sections=[...scriptSections,...(report.conclusions?.length ? [['结论依据',report.conclusions]] : []),['服务环节与后续回应',report.flow||[]],['服务检查',checks.filter((r:any)=>!r.standardFlow)],['关注事项',report.risks],['关键信息',report.insights],['待办与跟进',report.todos]] as const;
  const content=sections.map(([title,rows])=>`<h2>${title}</h2>${rows.length?rows.map((r:any)=>`<article><h3>${escape([report.scene==='混合场景'&&r.scene,r.name||r.title||r.label].filter(Boolean).join(' · '))} ${r.status?`<small>· ${escape(r.status)}</small>`:''}</h3><p>${escape(r.detail||r.value||'')}</p>${r.owner?`<p>负责人：${escape(r.owner)} · 截止：${escape(r.due)} · ${r.done?'已完成':'待跟进'}</p>`:''}${r.evidence.map((e:any)=>`<blockquote><span>${stamp(e.start)} · 片段 ${e.segment+1}</span><br>${escape(e.quote)}</blockquote>`).join('')}${(r.feedback||[]).map((f:any)=>`<p>人工${escape(f.state)} · ${escape(f.actor)} · ${escape(f.at)}<br>${escape(f.value)} ${escape(f.note)}</p>`).join('')}</article>`).join(''):'<p class="muted">未提取到有可靠依据的项目。</p>'}`).join('');
  const coverage=(report.coverage||[]).map((section:any)=>`<h2>${escape(section.scene)} · 场景分析明细</h2><table>${section.items.map((item:any)=>`<tr><th>${escape(item.name)}${item.example?`<br><small>参考话术 · ${escape(item.example)}</small>`:''}</th><td>${item.matches.length?`${item.matches.length} 项结果`:'未提取到依据'}</td></tr>`).join('')}</table>`).join('');
  const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escape(item.filename)} · 分析报告</title><style>body{font:16px/1.8 system-ui,"Microsoft YaHei",sans-serif;color:#2b483b;max-width:900px;margin:45px auto;padding:0 25px}h1{font-size:28px;word-break:break-all}h2{font-size:20px;border-bottom:1px solid #dfe8df;padding-bottom:8px;margin-top:32px}h3{font-size:17px}p{white-space:pre-wrap}.muted,small{color:#738273;font-size:14px}table{border-collapse:collapse}td,th{text-align:left;padding:4px 16px;border-bottom:1px solid #dfe8df}article{border:1px solid #e2e9e0;border-radius:8px;padding:10px 20px;margin:14px 0}blockquote{background:#f4f7f2;padding:12px;margin:12px 0;font-size:14px}blockquote span{color:#738873}header{border-bottom:3px solid #367353;padding-bottom:24px}.summary{background:#f1f6ee;padding:18px;border-radius:8px}.transcript{font-size:15px}footer{border-top:1px solid #dfe8df;margin-top:30px;padding-top:12px;color:#81917e;font-size:13px}@media print{body{margin:0;max-width:none}article{break-inside:avoid}}</style></head><body><header><small>栖鉴 · 酒店语音质检 / ${item.id==='demo'?'预置示例':reportMode(report.mode)} ${escape(report.analysisModel)} ${report.reportVersion?'· v'+escape(report.reportVersion):''}</small><h1>${escape(item.filename)}</h1><p>${escape(item.source||'历史导入')} · ${escape(item.badge)} · ${escape(item.recordedAt)} · ${escape(item.employee)} · 时长 ${stamp(item.duration)} · ${item.reviewed?'已人工复核':'待人工复核'}</p><p>${escape(item.reviewNote)} ${item.customerName?'关联客户 · '+escape(item.customerName):''}</p></header><h2>${report.sceneSource==='手选场景'?'手选场景':report.sceneSource?'场景识别':'报告场景'}</h2><p>${escape(report.scenes?.length>1?'混合场景 · '+report.scenes.join(' · '):sceneLabel(report))}</p><h2>${report.scene==='会议'?'会议纪要':'分析结论'}</h2><div class="summary"><p>${escape(report.summary)}</p><p class="muted">${escape(report.scope)}</p></div>${emptyDesk}${coverage}${content}${item.segments?'<h2>原话转写</h2>':''}${(item.segments||[]).map((s:any)=>`<article class="transcript"><small>${stamp(s.start)}—${stamp(s.end)} · ${escape(s.speaker)}</small><p>${escape(s.text)}</p></article>`).join('')}<footer>原话存在不代表判断一定正确。时间为片段级，员工身份未自动确认；请结合录音复核。报告导出时间：${new Date().toLocaleString('zh-CN')}</footer></body></html>`;
  const url=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download=`${item.filename.replace(/\.[^.]+$/,'')}-分析报告.html`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
