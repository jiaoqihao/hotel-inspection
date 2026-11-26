import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  ArrowUpRight,
  AudioLines,
  Camera,
  Check,
  ChevronRight,
  ClipboardList,
  FileText,
  History,
  KeyRound,
  LoaderCircle,
  MessageSquare,
  Paperclip,
  Plus,
  ScanLine,
  SlidersHorizontal,
  Square,
  X,
} from 'lucide-react';
import { WorkHeading } from './Workspace';
import { exportHTML } from './export';
import './agent.css';

type Info = {
  kind: string;
  title: string;
  detail: string;
  url?: string;
  start?: number;
  call?: string;
  state?: string;
  tool?: string;
  data?: any;
  outcome?: unknown;
};
type Message = { id: string; role: string; text: string; state: string; evidence: Info[]; progress?: string };
type Thread = { id: string; title: string; updated: number };
const presets = [
  {
    Icon: Camera,
    title: '回顾昨日巡检',
    text: '昨天各摄像头拍到了哪些需要复核的异常行为？请按点位列出依据。',
  },
  {
    Icon: AudioLines,
    title: '复盘员工话术',
    text: '昨天员工的专业话术说得怎么样？请按场景分析，附上可回听的原话。',
  },
  {
    Icon: SlidersHorizontal,
    title: '调整设备岗位',
    text: '我想把一台设备的岗位改为前台，请先列出在用设备供我选择。',
  },
];
const clock = (n: number) => Math.floor(n / 60) + ':' + String(Math.floor(n % 60)).padStart(2, '0');
const stamp = (n: number) =>
  new Date(n * 1000).toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
const kindName: Record<string, string> = {
  image: '巡检画面',
  audio: '录音原话',
  order: '工单记录',
  change: '修改回执',
  receipt: '执行回执',
  error: '未完成',
  confirm: '操作确认',
  download: '报告导出',
  choices: '设备选择',
};
const states: Record<string, string> = {
  pending: '待确认',
  done: '已执行',
  cancelled: '已取消',
  running: '执行中',
  failed: '执行失败',
  interrupted: '运行已中断',
};
const labels: Record<string, string> = {
  number: '设备编号',
  device_id: '设备编号',
  id: '记录编号',
  oid: '工单编号',
  key: '对象编号',
  job_id: '巡检编号',
  record_id: '录音编号',
  item: '事项编号',
  department: '处理部门',
  owner: '负责人',
  room: '房间',
  state: '状态',
  note: '备注',
  title: '事项',
  name: '名称',
  minutes: '时限（分钟）',
  revision: '版本',
  before: '原岗位',
  after: '新岗位',
  position: '岗位',
  day: '业务日期',
  body: '修改内容',
  arguments: '参数',
  actions: '操作清单',
  history: '处理记录',
  actor: '办理人',
  at: '办理时间',
  due: '到期时间',
  call_id: '关联通话',
  update: '更新版本',
  test: '测试数据',
  enabled: '启用',
  retired: '停用',
  item_id: '事项编号',
};
function Facts({ value }: { value: unknown }) {
  if (value == null || value === '') return <>—</>;
  if (Array.isArray(value))
    return (
      <ol className="ai-facts-list">
        {value.map((v, i) => (
          <li key={i}>
            <Facts value={v} />
          </li>
        ))}
      </ol>
    );
  if (typeof value === 'object')
    return (
      <dl className="ai-facts">
        {Object.entries(value)
          .filter(([, v]) => v != null)
          .map(([k, v]) => (
            <div key={k}>
              <dt>{labels[k] || k}</dt>
              <dd>{['at', 'due'].includes(k) && typeof v === 'number' ? stamp(v) : <Facts value={v} />}</dd>
            </div>
          ))}
      </dl>
    );
  return <>{typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</>;
}
function Mark() {
  return (
    <span className="ai-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
function formatLine(text: string, refs: Info[], choose: (i: number) => void) {
  return text.split(/(\[\d+\]|【\d+】|\*\*[^*]+\*\*)/g).map((s, i) => {
    if (s.startsWith('**')) return <strong key={i}>{s.slice(2, -2)}</strong>;
    const n = /^[\[【](\d+)[】\]]$/.exec(s);
    if (n && refs[+n[1] - 1])
      return (
        <button className="ai-cite" key={i} onClick={() => choose(+n[1] - 1)} aria-label={'查看证据 ' + n[1]}>
          {n[1]}
        </button>
      );
    return s;
  });
}
function Reply({ message, choose }: { message: Message; choose: (i: number) => void }) {
  return (
    <div className="ai-answer">
      {message.text.split('\n').map((line, i) => {
        if (!line.trim()) return null;
        if (/^#{1,4}\s/.test(line))
          return <h3 key={i}>{formatLine(line.replace(/^#+\s*/, ''), message.evidence, choose)}</h3>;
        return <p key={i}>{formatLine(line, message.evidence, choose)}</p>;
      })}
    </div>
  );
}
async function api(path: string, token: string, method = 'GET', body?: unknown) {
  const form = body instanceof FormData;
  const res = await fetch('api/agent/' + path, {
    method,
    headers: { 'X-Local-Token': token, ...(form ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw Error(typeof error.detail === 'string' ? error.detail : '操作失败，请检查服务后再试。');
  }
  return res.json();
}
function RefDetail({
  info,
  decide,
  select,
  report,
}: {
  info: Info;
  decide: (accept: boolean) => void;
  select: (number: string) => void;
  report: (msg: string) => void;
}) {
  const [missing, setMissing] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    setMissing(false);
    setWaiting(false);
    if (audio.current) audio.current.currentTime = Math.max(0, info.start ?? 0);
  }, [info.url, info.start]);
  return (
    <div className="ai-ref-detail">
      <div className="ai-ref-type">
        {kindName[info.kind] || '业务依据'}
        {info.start != null && <span>{clock(Math.max(info.start, 0))}</span>}
      </div>
      <h3>{info.title}</h3>
      {info.kind === 'image' && !missing && (
        <img src={info.url} alt={info.title} onError={() => setMissing(true)} />
      )}
      {info.kind === 'audio' ? <blockquote>{info.detail}</blockquote> : <p>{info.detail}</p>}
      {info.kind === 'audio' && !missing && (
        <audio
          ref={audio}
          key={info.url}
          controls
          preload="metadata"
          src={info.url}
          onLoadedMetadata={(e) => {
            e.currentTarget.currentTime = Math.max(0, info.start || 0);
          }}
          onError={() => setMissing(true)}
        />
      )}
      {missing && <p className="ai-error">本机媒体暂时不可用，已归档的文字依据仍可查看。</p>}
      {info.kind === 'change' && (
        <>
          <small>{info.data.number}</small>
          <div className="ai-diff">
            <span>{info.data.before || '未设置'}</span>
            <ChevronRight size={16} />
            <strong>{info.data.after}</strong>
          </div>
        </>
      )}
      {info.kind === 'choices' &&
        info.data.map((d: { name: string; number: string }) => (
          <button key={d.number} onClick={() => select(d.number)}>
            {d.name} · {d.number}
          </button>
        ))}
      {['order', 'receipt', 'confirm'].includes(info.kind) && <Facts value={info.data} />}
      {info.kind === 'confirm' && (
        <>
          <strong>{states[info.state || 'pending']}</strong>
          {info.state === 'pending' && (
            <div className="ai-actions">
              <button
                className="secondary"
                disabled={waiting}
                onClick={async () => {
                  setWaiting(true);
                  try {
                    await decide(false);
                  } finally {
                    setWaiting(false);
                  }
                }}
              >
                取消
              </button>
              <button
                className="primary"
                disabled={waiting}
                onClick={async () => {
                  setWaiting(true);
                  try {
                    await decide(true);
                  } finally {
                    setWaiting(false);
                  }
                }}
              >
                确认执行
              </button>
            </div>
          )}
          {typeof info.outcome === 'string' && <p>{info.outcome}</p>}
        </>
      )}
      {info.kind === 'download' && (
        <div className="ai-actions">
          <a href={info.url} download={info.tool === 'recording_export' ? '录音报告.json' : undefined}>
            下载报告
          </a>
          {info.tool === 'recording_export' && (
            <button
              onClick={async () => {
                try {
                  const r = await fetch(info.url!);
                  if (!r.ok) throw Error();
                  exportHTML(await r.json());
                } catch {
                  report('报告暂时无法导出');
                }
              }}
            >
              导出 HTML
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function Agent({ token }: { token: string }) {
  const [tid, setTid] = useState(''),
    [rows, setRows] = useState<Message[]>([]),
    [text, setText] = useState(''),
    [error, setError] = useState('');
  const [recent, setRecent] = useState<Thread[]>([]),
    [history, setHistory] = useState(false),
    [busy, setBusy] = useState(false),
    [run, setRun] = useState('');
  const [focus, setFocus] = useState<{ id: string; i: number } | null>(null),
    [drawer, setDrawer] = useState(false),
    [attachment, setAttachment] = useState<{ id: string; name: string } | null>(null);
  const [secret, setSecret] = useState(false),
    [privateValue, setPrivate] = useState(''),
    [staging, setStaging] = useState(false);
  const stream = useRef<EventSource | null>(null),
    box = useRef<HTMLTextAreaElement>(null),
    bottom = useRef<HTMLDivElement>(null),
    file = useRef<HTMLInputElement>(null);
  const retry = useRef<{ signature: string; key: string } | null>(null);
  const epoch = useRef(0),
    stick = useRef(true),
    timeline = useRef<HTMLDivElement>(null);
  const all = rows.flatMap((m) => m.evidence.map((r, i) => ({ m: m.id, i, r })));
  const picked = all.find((v) => v.m === focus?.id && v.i === focus.i) || all.at(-1);
  const choose = (id: string, i: number) => {
    setFocus({ id, i });
    setDrawer(true);
  };
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : '请求未完成');
  const refreshHistory = async () => setRecent(await api('threads', token));
  function disconnect() {
    epoch.current++;
    stream.current?.close();
    stream.current = null;
  }
  function watch(key: string) {
    stream.current?.close();
    const es = new EventSource('api/agent/runs/' + key + '/events');
    stream.current = es;
    setRun(key);
    setBusy(true);
    const update = (event: MessageEvent) => {
      const patch = JSON.parse(event.data);
      setRows((old) => old.map((m) => (m.id === key ? { ...m, ...patch } : m)));
    };
    es.onmessage = update;
    es.addEventListener('finished', (event: MessageEvent) => {
      update(event);
      es.close();
      setBusy(false);
      setRun('');
      void refreshHistory().catch(fail);
    });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setError('与服务的连接已中断，请打开历史会话查看执行结果。');
        setBusy(false);
      }
    };
  }
  async function openThread(id: string) {
    disconnect();
    const turn = epoch.current;
    const data = await api('threads/' + id, token);
    if (turn !== epoch.current) return;
    setTid(id);
    setRows(data.messages);
    setFocus(null);
    setHistory(false);
    setDrawer(false);
    setError('');
    setBusy(false);
    setRun('');
    setText('');
    setAttachment(null);
    const active = data.messages.find((m: Message) => m.state === 'running');
    if (active) watch(active.id);
  }
  function fresh() {
    retry.current = null;
    if (location.hash.startsWith('#ai/')) window.history.replaceState(null, '', '#ai');
    disconnect();
    setTid('');
    setRows([]);
    setFocus(null);
    setBusy(false);
    setRun('');
    setHistory(false);
    setDrawer(false);
    setText('');
    setError('');
    setAttachment(null);
    box.current?.focus();
  }
  useEffect(() => {
    void refreshHistory().catch(fail);
    const call = location.hash.match(/^#ai\/action\/([0-9a-f]{32})$/)?.[1];
    if (call)
      void api('calls/' + call, token)
        .then((data) => {
          const ref: Info = {
            kind: 'confirm',
            title: data.preview?.title || 'MCP 操作',
            detail: data.preview
              ? data.preview.impact + ' · 共 ' + data.preview.count + ' 项'
              : '本机客户端提交的操作',
            call,
            state: data.state,
            data: data.preview?.arguments || {},
            outcome: data.error,
          };
          setRows([
            {
              id: call,
              role: 'assistant',
              state: 'done',
              text: '请核对本机 MCP 客户端提交的操作。',
              evidence: [ref],
            },
          ]);
          choose(call, 0);
        })
        .catch(fail);
    return disconnect;
  }, []);
  useEffect(() => {
    if (stick.current && timeline.current) timeline.current.scrollTop = timeline.current.scrollHeight;
  }, [rows]);
  async function send(value = text) {
    if (!value.trim() || busy || staging) return;
    const signature = JSON.stringify([tid, value, attachment?.id]);
    const key =
      retry.current?.signature === signature
        ? retry.current.key
        : crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-');
    retry.current = { signature, key };
    const base = rows,
      turn = epoch.current;
    setBusy(true);
    setError('');
    setHistory(false);
    stick.current = true;
    setRows([
      ...base,
      { id: key + '-u', role: 'user', state: 'done', text: value, evidence: [] },
      { id: key, role: 'assistant', state: 'running', text: '', evidence: [] },
    ]);
    try {
      const data = await api('runs', token, 'POST', {
        thread: tid || null,
        text: value,
        requestKey: key,
        attachment: attachment?.id,
      });
      if (turn !== epoch.current) return;
      retry.current = null;
      setTid(data.thread);
      setText('');
      setAttachment(null);
      watch(data.run);
    } catch (e) {
      if (turn === epoch.current) {
        setBusy(false);
        setRows(base);
        fail(e);
      }
    }
  }
  async function decide(accept: boolean) {
    if (!picked?.r.call) return;
    try {
      const result = await api('calls/' + picked.r.call + '/decision', token, 'POST', { accept });
      setRows((old) =>
        old.map((m) =>
          m.id !== picked.m
            ? m
            : {
                ...m,
                evidence: m.evidence
                  .map((v, i) => (i === picked.i ? { ...v, state: result.state, outcome: result.error } : v))
                  .concat(result.evidence || []),
              },
        ),
      );
    } catch (e) {
      fail(e);
    }
  }
  const composer = (
    <div className="ai-composer">
      {attachment && (
        <button className="ai-file" onClick={() => setAttachment(null)}>
          {attachment.name}
          <X size={13} />
        </button>
      )}
      <textarea
        ref={box}
        rows={2}
        value={text}
        maxLength={12000}
        aria-label="提问或描述要办理的事项"
        placeholder="提问，或告诉我要办理的事项…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void send();
          }
        }}
      />
      <div className="ai-compose-bar">
        <button
          title="选择本地附件"
          aria-label="选择本地附件"
          disabled={busy || staging}
          onClick={() => file.current?.click()}
        >
          <Paperclip size={17} />
        </button>
        <button
          title="输入私密连接参数"
          aria-label="输入私密连接参数"
          disabled={busy}
          onClick={() => setSecret(true)}
        >
          <KeyRound size={15} />
        </button>
        <span>{staging ? '正在保存附件' : 'Shift + Enter 换行'}</span>
        {busy ? (
          <button
            className="ai-send"
            aria-label="停止回答"
            disabled={!run}
            onClick={() => void api('runs/' + run + '/stop', token, 'POST').catch(fail)}
          >
            <Square size={15} />
          </button>
        ) : (
          <button
            className="ai-send"
            aria-label="发送"
            disabled={!text.trim() || !token || staging}
            onClick={() => void send()}
          >
            <ArrowUp size={19} />
          </button>
        )}
      </div>
    </div>
  );
  return (
    <section className="work-page ai-page">
      <WorkHeading title="AI 分析" description="查询门店记录，分析服务表现，办理业务事项。">
        <div className="ai-actions">
          <button
            className="secondary"
            aria-expanded={history}
            onClick={() => {
              setHistory(!history);
              void refreshHistory().catch(fail);
            }}
          >
            <History size={16} />
            历史会话
          </button>
          <button className="primary" onClick={fresh}>
            <Plus size={16} />
            新对话
          </button>
        </div>
      </WorkHeading>
      {history && (
        <div className="ai-history">
          <header>
            <strong>历史会话</strong>
            <button aria-label="关闭历史会话" onClick={() => setHistory(false)}>
              <X size={16} />
            </button>
          </header>
          {recent.length ? (
            recent.map((t) => (
              <button key={t.id} onClick={() => void openThread(t.id).catch(fail)}>
                <MessageSquare size={15} />
                <span>{t.title}</span>
                <small>{stamp(t.updated)}</small>
              </button>
            ))
          ) : (
            <p>暂无历史会话</p>
          )}
        </div>
      )}
      {error && (
        <div role="alert" className="ai-error">
          {error}
          <button aria-label="关闭提示" onClick={() => setError('')}>
            <X size={15} />
          </button>
        </div>
      )}
      <div className="ai-layout">
        <section className={'ai-chat ' + (!rows.length ? 'ai-blank' : '')}>
          <header className="ai-strip">
            <span>
              <MessageSquare size={17} />
              门店对话
            </span>
            {rows.length > 0 && (
              <button className="ai-mobile-ref" onClick={() => setDrawer(true)}>
                查看依据 · {all.length}
              </button>
            )}
          </header>
          {!rows.length ? (
            <div className="ai-welcome">
              <Mark />
              <h2>今天想了解门店的什么情况？</h2>
              {composer}
              <div className="ai-presets">
                {presets.map(({ Icon, title, text: prompt }) => (
                  <button key={title} disabled={!token || busy} onClick={() => void send(prompt)}>
                    <Icon size={16} />
                    {title}
                    <ArrowUpRight size={13} />
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div
                className="ai-thread"
                ref={timeline}
                onScroll={(e) => {
                  const el = e.currentTarget;
                  stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90;
                }}
              >
                {rows.map((m) => (
                  <article key={m.id} className={'ai-msg ai-' + m.role}>
                    {m.role === 'user' ? (
                      <p>{m.text}</p>
                    ) : (
                      <>
                        <div className="ai-by">
                          <Mark />
                          栖鉴
                          {m.state === 'running' && (
                            <small>
                              <LoaderCircle size={12} className="ai-spin" />
                              {m.progress || '正在连接'}
                            </small>
                          )}
                        </div>
                        <Reply message={m} choose={(i) => choose(m.id, i)} />
                        {m.state === 'interrupted' && <p>服务重启中断了本次回答，请核对已有回执。</p>}
                        {!!m.evidence.length && (
                          <button className="ai-open-ref" onClick={() => choose(m.id, 0)}>
                            <FileText size={14} />
                            查看 {m.evidence.length} 条依据与回执
                            <ChevronRight size={13} />
                          </button>
                        )}
                      </>
                    )}
                  </article>
                ))}
                <div ref={bottom} />
              </div>
              <div className="ai-dock">{composer}</div>
            </>
          )}
        </section>
        {drawer && <button className="ai-scrim" aria-label="收起证据" onClick={() => setDrawer(false)} />}
        <aside className={'ai-inspector ' + (drawer ? 'is-open' : '')}>
          <header className="ai-strip">
            <span>
              <ScanLine size={17} />
              依据与回执
            </span>
            <small>{all.length} 条</small>
            <button className="ai-close" onClick={() => setDrawer(false)} aria-label="收起证据">
              <X size={17} />
            </button>
          </header>
          {picked ? (
            <div className="ai-ref-scroll">
              <RefDetail
                key={picked.m + '-' + picked.i}
                info={picked.r}
                decide={decide}
                report={setError}
                select={(n) => {
                  setDrawer(false);
                  void send('设备编号 ' + n + '，请按刚才的要求办理。');
                }}
              />
              {all.length > 1 && (
                <div className="ai-ref-list">
                  {all.map((v) => (
                    <button
                      key={v.m + '-' + v.i}
                      aria-current={v === picked ? 'true' : undefined}
                      onClick={() => choose(v.m, v.i)}
                    >
                      <span>{kindName[v.r.kind]}</span>
                      <strong>{v.r.title}</strong>
                      <small>{v.r.start != null ? clock(v.r.start) : ''}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="ai-evidence-empty">
              <div className="ai-paper" aria-hidden="true">
                <FileText size={35} strokeWidth={1.2} />
                <span>
                  <Check size={11} />
                </span>
              </div>
              <h3>等待引用依据</h3>
              <p>
                回答引用的画面、原话和操作回执
                <br />
                会显示在这里。
              </p>
              <div className="ai-sources">
                <span>
                  <Camera />
                  巡检画面
                </span>
                <span>
                  <AudioLines />
                  录音原话
                </span>
                <span>
                  <ClipboardList />
                  业务回执
                </span>
              </div>
            </div>
          )}
        </aside>
      </div>
      <input
        hidden
        type="file"
        ref={file}
        onChange={async (e) => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (!f) return;
          setStaging(true);
          try {
            const form = new FormData();
            form.set('file', f);
            setAttachment(await api('uploads', token, 'POST', form));
          } catch (err) {
            fail(err);
          } finally {
            setStaging(false);
          }
        }}
      />
      {secret && (
        <div className="confirm-backdrop">
          <form
            className="confirm-dialog"
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                const result = await api('secrets', token, 'POST', { value: privateValue });
                setText((t) => t + ' ' + result.id);
                setPrivate('');
                setSecret(false);
              } catch (err) {
                fail(err);
              }
            }}
          >
            <h2>本机私密参数</h2>
            <p>填写设备连接地址或密钥。对话中只会插入该值的引用编号。</p>
            <input
              aria-label="连接参数"
              type="password"
              autoFocus
              autoComplete="off"
              required
              value={privateValue}
              onChange={(e) => setPrivate(e.target.value)}
            />
            <div className="ai-actions">
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setPrivate('');
                  setSecret(false);
                }}
              >
                取消
              </button>
              <button className="primary">插入引用</button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}
