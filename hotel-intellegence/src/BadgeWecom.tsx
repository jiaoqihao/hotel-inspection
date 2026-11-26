import { useEffect, useState } from 'react';
import { base, type Api } from './BadgeScripts';
import { request } from './Store';

type Settings = { clock: string; enabled: boolean; configured: boolean; yesterday: string };
type Run = {
  id: number;
  day: string;
  revision: number;
  state: string;
  sent: number;
  total: number;
  info: string;
};
type Finding = {
  record: string;
  employee: string;
  title: string;
  time: string;
  status: string;
  quote?: string;
  version: number;
  segment?: number;
  start?: number;
};
type Entry = {
  record: string;
  start: number;
  time: string;
  title: string;
  state: string;
  owner?: string;
  due?: string;
};
type Report = {
  day: string;
  count: number;
  done: number;
  pending: number;
  failed: number;
  undated: number;
  people: Record<string, { count: number; checks: Record<string, Record<string, number>> }>;
  events: Entry[];
  tasks: Entry[];
  evidence: Finding[];
};
type Recording = { id: string; segments: { id: number; start: number; text: string; overlap?: boolean }[] };
type Listen = (id: string, start: number) => void;

function Guide() {
  return (
    <details className="wc-guide">
      <summary>第一次使用？查看企业微信群配置步骤</summary>
      <ol>
        <li>在企业微信电脑端登录酒店所属企业，打开仅含门店管理人员的内部群。</li>
        <li>
          右键群聊选择「管理聊天信息」，在「消息推送」中选择「自定义消息推送」。部分客户端显示「添加群机器人」。
        </li>
        <li>名称填“栖鉴录音日报”，按提示填写简介、复制 Webhook 地址，并保存机器人。</li>
        <li>把地址粘贴在下方并保存。点击「发送测试消息」，到目标群核对“栖鉴连接测试”。</li>
        <li>
          在「设备」中登记固定佩戴员工，在「更多 → 服务标准」启用门店规定话术，核对昨日报告后启用每日发送。
        </li>
      </ol>
      <a href="https://cloud.tencent.com/document/product/1759/128391" target="_blank" rel="noreferrer">
        查看腾讯图文操作指引 ↗
      </a>
    </details>
  );
}

function Evidence({
  row,
  records,
  listen,
  api,
  changed,
}: {
  row: Finding;
  records: Recording[];
  listen: Listen;
  api: Api;
  changed: () => void;
}) {
  const segments = records.find((r) => r.id === row.record)?.segments || [];
  const [sid, setSid] = useState(String(row.segment ?? '')),
    [result, setResult] = useState('符合'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const seg = segments.find((s) => String(s.id) === sid);
  return (
    <details className="wc-evidence">
      <summary>
        {row.time} · {row.employee || '佩戴者待确认'} · {row.title} · {row.status}
      </summary>
      {row.quote && <p>{row.quote}</p>}
      <div className="wc-fields">
        <label>
          复核发言
          <select value={sid} onChange={(e) => setSid(e.target.value)}>
            <option value="">选择录音发言</option>
            {segments.map((s) => (
              <option key={s.id} value={s.id}>
                {Math.floor(s.start)} 秒 · {s.text.slice(0, 40)}
              </option>
            ))}
          </select>
        </label>
        <label>
          复核结论
          <select value={result} onChange={(e) => setResult(e.target.value)}>
            {['符合', '已确认偏差', '证据不足', '排除'].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="wc-actions">
        <button className="secondary" disabled={!seg} onClick={() => seg && listen(row.record, seg.start)}>
          回听发言
        </button>
        <button
          className="secondary"
          disabled={!seg || busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api(base + '/reviews/' + row.record, 'POST', {
                segment: Number(sid),
                version: row.version,
                status: result,
              });
              changed();
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          保存复核
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </details>
  );
}

function Entries({ title, items, listen }: { title: string; items: Entry[]; listen: Listen }) {
  return (
    <section>
      <h3>{title}</h3>
      {items.length ? (
        items.map((r, i) => (
          <div className="wc-rule" key={r.record + ':' + i}>
            <div>
              <strong>{r.title}</strong>
              <small>
                {r.time} · {r.state}
                {r.owner && ' · 负责人 ' + r.owner + ' · 期限 ' + r.due}
              </small>
            </div>
            <button className="text-button" onClick={() => listen(r.record, r.start)}>
              回听
            </button>
          </div>
        ))
      ) : (
        <p>已分析录音中暂无相关条目。</p>
      )}
    </section>
  );
}

export default function BadgeWecom({
  admin,
  api,
  records,
  listen,
}: {
  admin: boolean;
  api: Api;
  records: Recording[];
  listen: Listen;
}) {
  const [settings, setSettings] = useState<Settings>(),
    [hook, setHook] = useState(''),
    [clock, setClock] = useState('09:00'),
    [enabled, setEnabled] = useState(false);
  const [day, setDay] = useState(''),
    [report, setReport] = useState<Report>(),
    [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState(''),
    [error, setError] = useState(''),
    [revision, setRevision] = useState(0);
  const [parts, setParts] = useState<{ label: string; items: string[] } | null>(null);
  useEffect(() => {
    let live = true;
    request<Settings>(base + '/settings')
      .then((s) => {
        if (live) {
          setSettings(s);
          setClock(s.clock);
          setEnabled(s.enabled);
          setDay(s.yesterday);
        }
      })
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => {
    if (!day) return;
    let live = true;
    setReport(undefined);
    setParts(null);
    request<Report>(base + '/preview?day=' + day)
      .then((r) => live && setReport(r))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [day, revision]);
  useEffect(() => {
    let live = true;
    const poll = () =>
      request<Run[]>(base + '/history')
        .then((r) => live && setRuns(r))
        .catch((e) => live && setError(e.message));
    void poll();
    const timer = setInterval(poll, 5000);
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, []);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await fn();
      setRuns(await request<Run[]>(base + '/history'));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const latest = runs.find((r) => r.day === day),
    update = !!latest && ['已发送', '已取消'].includes(latest.state);
  return (
    <div className="wc-page">
      <header className="bd-heading">
        <div>
          <h1>企微连接</h1>
          <p>挂牌与胸牌录音日报 · 内部门店管理群</p>
        </div>
        <span className="tag">
          {settings ? (settings.enabled ? '自动发送已开启' : '自动发送已关闭') : '读取中'}
        </span>
      </header>
      <section className="panel wc-card">
        <div className="wc-line">
          <h2>群消息推送</h2>
          <span className="tag">{settings?.configured ? '已保存群地址' : '待配置'}</span>
        </div>
        <Guide />
        {admin && settings && (
          <form
            className="wc-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () => {
                const s = await api(base + '/settings', 'PUT', {
                  webhook: hook.trim() || null,
                  clock,
                  enabled,
                });
                setSettings(s);
                setHook('');
                setMessage('推送设置已保存');
              });
            }}
          >
            <label>
              群 Webhook
              <input
                type="password"
                autoComplete="new-password"
                value={hook}
                onChange={(e) => setHook(e.target.value)}
                placeholder={
                  settings.configured ? '地址已加密保存，留空沿用' : '粘贴从企业微信内部群复制的完整地址'
                }
              />
            </label>
            <div className="wc-actions">
              <label className="wc-check">
                <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
                每日自动发送
              </label>
              <label>
                北京时间{' '}
                <input required type="time" value={clock} onChange={(e) => setClock(e.target.value)} />
              </label>
            </div>
            <div className="wc-actions">
              <button className="primary" disabled={busy}>
                保存推送设置
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy || !settings.configured || !!hook}
                onClick={() => act(async () => setMessage((await api(base + '/test')).message))}
              >
                发送测试消息
              </button>
            </div>
          </form>
        )}
        <p>请保持门店电脑和栖鉴后台运行。关掉网页后仍会按时发送。</p>
      </section>
      {error && (
        <p className="inline-notice" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="inline-notice" role="status">
          {message}
        </p>
      )}
      <section className="panel wc-card">
        <div className="wc-line">
          <h2>录音日报预览</h2>
          <label>
            录制日期{' '}
            <input
              type="date"
              max={settings?.yesterday}
              value={day}
              onChange={(e) => {
                setDay(e.target.value);
                setReport(undefined);
              }}
            />
          </label>
        </div>
        {report && (
          <>
            <p>
              {report.count} 段正式录音 · 已分析 {report.done} · 待分析 {report.pending} · 分析失败{' '}
              {report.failed}
            </p>
            {report.undated > 0 && <p>另有 {report.undated} 段录音缺录制时间，日期待核实。</p>}
            <Entries title="重要事件" items={report.events} listen={listen} />
            <h3>逐员工话术</h3>
            {Object.entries(report.people).map(([name, p]) => (
              <div className="wc-rule" key={name}>
                <strong>{name}</strong>
                <div>
                  {p.count ? p.count + ' 段录音' : '无录音'}
                  {Object.entries(p.checks).map(([title, counts]) => (
                    <small key={title}>
                      {title} ·{' '}
                      {Object.entries(counts)
                        .map(([k, v]) => k + ' ' + v)
                        .join('、')}
                    </small>
                  ))}
                  {p.count > 0 && !Object.keys(p.checks).length && <small>暂无适用话术结果</small>}
                </div>
              </div>
            ))}
            {!Object.keys(report.people).length && <p>当天没有绑定员工或可归属的录音。</p>}
            {report.evidence.length > 0 && (
              <details>
                <summary>话术依据与人工复核（{report.evidence.length}）</summary>
                {report.evidence.map((r, i) => (
                  <Evidence
                    key={[day, r.record, r.segment, r.version, i].join('-')}
                    row={r}
                    records={records}
                    api={api}
                    listen={listen}
                    changed={() => {
                      setMessage('复核已保存');
                      setRevision((v) => v + 1);
                    }}
                  />
                ))}
              </details>
            )}
            <Entries title="待解决事项" items={report.tasks} listen={listen} />
            <div className="wc-actions">
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    setMessage((await api(base + '/prepare', 'POST', { day })).message);
                    setRevision((v) => v + 1);
                  })
                }
              >
                用已有转写检查话术
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    setParts({
                      label: '群消息预览',
                      items: (await api(base + '/message-preview', 'POST', { day, update })).parts,
                    });
                  })
                }
              >
                预览群消息
              </button>
              {admin && (
                <button
                  className="primary"
                  disabled={busy || !settings?.configured || (!!latest && !update)}
                  onClick={() =>
                    act(async () => {
                      await api(base + '/send', 'POST', { day, update, previous: latest?.revision || 0 });
                      setMessage('日报已加入发送队列，可在发送记录查看回执');
                    })
                  }
                >
                  {update ? '发送更新版' : '发送该日日报'}
                </button>
              )}
            </div>
          </>
        )}
      </section>
      <section className="panel wc-card">
        <h2>发送记录</h2>
        {!runs.length && <p>启用推送后，可在这里核对每份日报的发送进度。</p>}
        {runs.map((r) => (
          <div className="wc-rule" key={r.id}>
            <div>
              <strong>
                {r.day} · 第 {r.revision} 版
              </strong>
              <small>
                {r.state} · 已送达 {r.sent}/{r.total} 条　{r.info}
              </small>
            </div>
            <div className="wc-actions">
              {r.total > 0 && (
                <button
                  className="text-button"
                  onClick={() =>
                    act(async () =>
                      setParts({
                        label: r.day + ' · 第 ' + r.revision + ' 版',
                        items: (await request<{ parts: string[] }>(base + '/history/' + r.id)).parts,
                      }),
                    )
                  }
                >
                  查看消息
                </button>
              )}
              {admin &&
                (r.state === '回执未知'
                  ? ['已收到', '确认未收到']
                  : ['生成失败', '发送失败'].includes(r.state)
                    ? ['重试']
                    : []
                ).map((a) => (
                  <button
                    key={a}
                    className="secondary"
                    disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await api(base + '/history/' + r.id, 'POST', { action: a });
                        setMessage('已更新发送进度');
                      })
                    }
                  >
                    {a}
                  </button>
                ))}
            </div>
          </div>
        ))}
      </section>
      {parts && (
        <section className="panel wc-card" role="region" aria-label={parts.label}>
          <div className="wc-line">
            <h2>{parts.label}</h2>
            <button className="secondary" onClick={() => setParts(null)}>
              收起消息
            </button>
          </div>
          {parts.items.map((s, i) => (
            <pre key={i}>{s}</pre>
          ))}
        </section>
      )}
    </div>
  );
}
