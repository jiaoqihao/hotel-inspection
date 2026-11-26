import { useEffect, useState } from 'react';
import { request } from './Store';
import './wecom.css';

export const base = 'api/badge/wecom';
export type Api = (path: string, method?: string, body?: unknown) => Promise<any>;
export const today = () =>
  new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date());

type Binding = { since: string; employee: string };
export function Wearer({ device, api }: { device: number; api: Api }) {
  const [rows, setRows] = useState<Binding[]>([]),
    [name, setName] = useState(''),
    [since, setSince] = useState(today);
  const [message, setMessage] = useState(''),
    [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const path = base + '/bindings/' + device;
  useEffect(() => {
    let live = true;
    setReady(false);
    request<Binding[]>(path)
      .then((data) => {
        if (live) {
          setRows(data);
          setReady(true);
          setName(data.find((r) => r.since <= today())?.employee || '');
        }
      })
      .catch((e) => live && setMessage(e.message));
    return () => {
      live = false;
    };
  }, [path]);
  return (
    <form
      className="wc-binding"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          setRows(await api(path, 'PUT', { employee: name, since }));
          setMessage('绑定记录已保存');
        } catch (e) {
          setMessage((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3>固定佩戴员工</h3>
      <div className="bd-fields">
        <label>
          员工姓名
          <input
            disabled={!ready || busy}
            maxLength={80}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="留空解除绑定"
          />
        </label>
        <label>
          绑定生效日期
          <input required type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
      </div>
      <button className="secondary" disabled={!ready || busy}>
        保存员工绑定
      </button>
      {!!rows.length && (
        <details>
          <summary>绑定记录（{rows.length}）</summary>
          {rows.map((r) => (
            <p key={r.since}>
              {r.since} 起 · {r.employee || '解除绑定'}
            </p>
          ))}
        </details>
      )}
      {message && <p role="status">{message}</p>}
    </form>
  );
}

type Rule = {
  rule: string;
  version: number;
  since: string;
  scene: string;
  title: string;
  trigger: string;
  standard: string;
  alternatives: string;
  enabled: boolean;
};
const example = (): Rule => ({
  rule: '',
  version: 0,
  since: today(),
  scene: '餐厅服务',
  title: '迎宾问候',
  trigger: '客人到店，员工首次接待时',
  standard: '您好，欢迎光临。',
  alternatives: '您好，欢迎来到我们餐厅。',
  enabled: false,
});
export function ScriptRules({ admin, api }: { admin: boolean; api: Api }) {
  const [rows, setRows] = useState<Rule[]>([]),
    [draft, setDraft] = useState<Rule | null>(null);
  const [message, setMessage] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    request<Rule[]>(base + '/rules')
      .then(setRows)
      .catch((e) => setMessage(e.message));
  }, []);
  return (
    <section className="panel wc-card">
      <div className="wc-line">
        <h2>门店规定话术</h2>
        {admin && (
          <button
            className="secondary"
            onClick={() => {
              setDraft(example());
              setMessage('');
            }}
          >
            新增话术
          </button>
        )}
      </div>
      <p>按录制日期使用当时生效的版本。新增时可编辑迎宾示例，确认门店要求后勾选启用。</p>
      {rows.map((r) => (
        <div className="wc-rule" key={r.rule}>
          <div>
            <strong>{r.title}</strong>
            <small>
              {r.scene} · {r.since} 起 · {r.enabled ? '启用' : '停用'} · 版本 {r.version}
            </small>
            <p>{r.standard}</p>
          </div>
          {admin && (
            <button
              className="text-button"
              onClick={() => {
                setDraft({ ...r, since: r.since > today() ? r.since : today() });
                setMessage('');
              }}
            >
              编辑话术
            </button>
          )}
        </div>
      ))}
      {!rows.length && !draft && <p>尚未配置门店话术。</p>}
      {draft && (
        <form
          className="wc-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              setRows(await api(base + '/rules', 'POST', draft));
              setDraft(null);
              setMessage('话术版本已保存');
            } catch (e) {
              setMessage((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="wc-fields">
            <label>
              话术名称
              <input
                required
                maxLength={60}
                value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })}
              />
            </label>
            <label>
              适用场景
              <select value={draft.scene} onChange={(e) => setDraft({ ...draft, scene: e.target.value })}>
                {['会议', '销售沟通', '餐厅服务'].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              话术生效日期
              <input
                required
                type="date"
                value={draft.since}
                onChange={(e) => setDraft({ ...draft, since: e.target.value })}
              />
            </label>
          </div>
          {(
            [
              ['trigger', '触发条件', 300],
              ['standard', '标准说法', 400],
              ['alternatives', '允许的等价表达', 400],
            ] as const
          ).map(([key, label, max]) => (
            <label key={key}>
              {label}
              <input
                required={key !== 'alternatives'}
                maxLength={max}
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            </label>
          ))}
          <label className="wc-check">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            />
            启用此话术
          </label>
          <div className="wc-actions">
            <button className="primary" disabled={busy}>
              保存话术版本
            </button>
            <button type="button" className="secondary" onClick={() => setDraft(null)}>
              取消
            </button>
          </div>
        </form>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
