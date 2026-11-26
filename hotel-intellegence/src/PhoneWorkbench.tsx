import { useEffect, useState, type FormEvent } from "react";
import { request } from "./Store";

import { DatePresets } from "./PhoneRecords";

export const categories = ["客诉", "潜在客诉", "客需", "商机", "咨询", "其他"];
export type Filters = {
  scope: string;
  q: string;
  category: string;
  since: string;
  until: string;
  source: string;
  state: string;
  level: string;
  department: string;
  favorites: boolean;
  unread: boolean;
  repeat: boolean;
};
export const initialFilters: Filters = {
  scope: "营业",
  q: "",
  category: "",
  since: "",
  until: "",
  source: "",
  state: "",
  level: "",
  department: "",
  favorites: false,
  unread: false,
  repeat: false,
};
export function registerParams(filters: Filters) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) p.set(k, String(v));
  return p;
}
function Select({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: string[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((v) => (
          <option key={v} value={v}>
            {v || "全部"}
          </option>
        ))}
      </select>
    </label>
  );
}
export function Finder({
  f,
  set,
  alerts,
}: {
  f: Filters;
  set: (f: Filters) => void;
  alerts: boolean;
}) {
  const update = (k: keyof Filters, v: string | boolean) =>
    set({ ...f, [k]: v });
  return (
    <div className="phone-finder">
      <div className="phone-options" aria-label="通话主分类">
        {["", ...categories].map((c) => (
          <button
            key={c}
            aria-pressed={c === f.category}
            onClick={() => update("category", c)}
          >
            {c || "全部分类"}
          </button>
        ))}
      </div>
      <div className="phone-fields">
        <Select
          label="数据范围"
          options={["营业", "测试"]}
          value={f.scope}
          onChange={(v) => update("scope", v)}
        />
        <label>
          搜索通话
          <input
            type="search"
            placeholder="原话、事项、房间或号码"
            value={f.q}
            onChange={(e) => update("q", e.target.value)}
          />
        </label>
        <label>
          开始日期
          <input
            type="date"
            value={f.since}
            onChange={(e) => update("since", e.target.value)}
          />
        </label>
        <label>
          结束日期
          <input
            type="date"
            value={f.until}
            onChange={(e) => update("until", e.target.value)}
          />
        </label>
      </div>
      <div className="ph-filter-tools">
        <DatePresets
          since={f.since}
          until={f.until}
          choose={(since, until) => set({ ...f, since, until })}
        />
        <button
          type="button"
          onClick={() => set({ ...initialFilters, scope: f.scope })}
        >
          重置筛选
        </button>
      </div>
      <details open={alerts || undefined}>
        <summary>更多筛选</summary>
        <div className="phone-fields">
          <Select
            label="来源"
            value={f.source}
            options={["", "手工导入", "模拟交换机", "交换机目录"]}
            onChange={(v) => update("source", v)}
          />
          <Select
            label="复核"
            value={f.state}
            options={["", "待复核", "需跟进", "已确认", "已排除"]}
            onChange={(v) => update("state", v)}
          />
          <Select
            label="等级"
            value={f.level}
            options={["", "S", "A", "B", "C"]}
            onChange={(v) => update("level", v)}
          />
          <Select
            label="部门"
            value={f.department}
            options={["", "客房部", "工程部", "前台", "值班经理"]}
            onChange={(v) => update("department", v)}
          />
          <label>
            <input
              type="checkbox"
              checked={f.favorites}
              onChange={(e) => update("favorites", e.target.checked)}
            />
            我的收藏
          </label>
          <label>
            <input
              type="checkbox"
              checked={f.unread}
              onChange={(e) => update("unread", e.target.checked)}
            />
            未读预警
          </label>
          <label>
            <input
              type="checkbox"
              checked={f.repeat}
              onChange={(e) => update("repeat", e.target.checked)}
            />
            已关联重复需求
          </label>
        </div>
      </details>
    </div>
  );
}

export function BatchImport({
  token,
  open,
}: {
  token: string;
  open: (id: string) => void;
}) {
  const [files, setFiles] = useState<File[]>([]),
    [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<
    { name: string; status: string; id?: string }[]
  >([]);
  const [stamp] = useState(() => {
    const now = new Date();
    return new Date(now.getTime() - now.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  });
  useEffect(() => {
    const pending = entries.filter(
      (r) => r.id && ["排队中", "转写中", "分析中"].includes(r.status),
    );
    if (!pending.length) return;
    let active = true;
    const timer = setTimeout(async () => {
      const states = await Promise.all(
        pending.map(async (r) => {
          try {
            const call = await request<{ status: string; error: string }>(
              "/api/phone/calls/" + r.id,
            );
            return [
              r.id,
              call.status === "失败" ? "失败 · " + call.error : call.status,
            ] as const;
          } catch {
            return [r.id, r.status] as const;
          }
        }),
      );
      if (active)
        setEntries((old) =>
          old.map((r) => ({
            ...r,
            status: states.find((s) => s[0] === r.id)?.[1] || r.status,
          })),
        );
    }, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [entries]);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!files.length || busy) return;
    const shared = new FormData(e.currentTarget);
    shared.set("caller", String(shared.get("caller") || "未知"));
    shared.set("callee", String(shared.get("callee") || "未知"));
    shared.set("test", String(shared.has("test")));
    setEntries(files.map((f) => ({ name: f.name, status: "待导入" })));
    setBusy(true);
    try {
      for (const [idx, file] of files.entries()) {
        shared.set("file", file);
        try {
          const r = await request<{ id: string; duplicate: boolean }>(
            "/api/phone/calls",
            {
              method: "POST",
              headers: { "x-local-token": token },
              body: shared,
            },
          );
          setEntries((old) =>
            old.map((x, i) =>
              i === idx
                ? {
                    ...x,
                    id: r.id,
                    status: r.duplicate ? "已有记录" : "排队中",
                  }
                : x,
            ),
          );
        } catch (err) {
          setEntries((old) =>
            old.map((x, i) =>
              i === idx ? { ...x, status: (err as Error).message } : x,
            ),
          );
        }
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>本地录音导入</h2>
      <form onSubmit={submit}>
        <label
          className="phone-drop"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            if (!busy) setFiles(Array.from(e.dataTransfer.files));
          }}
        >
          拖入录音或选择多个文件
          <input
            type="file"
            accept="audio/*,.mp4,.webm"
            multiple
            disabled={busy}
            onChange={(e) => setFiles(Array.from(e.target.files || []))}
          />
          {files.length > 0 && <span>已选择 {files.length} 个文件</span>}
        </label>
        <div className="phone-fields">
          {[
            ["caller", "主叫分机"],
            ["callee", "被叫分机"],
            ["room", "房间"],
          ].map(([name, label]) => (
            <label key={name}>
              {label}
              <input name={name} placeholder="未知可留空" />
            </label>
          ))}
          <label>
            方向
            <select name="direction">
              <option value="">根据分机映射</option>
              {["客房 → 前台", "外线 → 前台", "前台 → 客房", "前台 → 外线"].map(
                (d) => (
                  <option key={d}>{d}</option>
                ),
              )}
            </select>
          </label>
          <label>
            本批通话时间
            <input
              type="datetime-local"
              name="started"
              defaultValue={stamp}
              required
            />
          </label>
        </div>
        <label>
          <input type="checkbox" name="test" defaultChecked />
          测试录音（营业统计排除）
        </label>
        <button className="primary" disabled={busy || !files.length || !token}>
          {busy ? "正在导入" : "保存并转写"}
        </button>
      </form>
      {entries.map((r, i) => (
        <p key={i}>
          {r.name} · {r.status}{" "}
          {r.id && <button onClick={() => open(r.id!)}>查看通话</button>}
        </p>
      ))}
    </section>
  );
}

type Finding = {
  title: string;
  quote: string;
  start: number;
  category: string;
};
type Analysis = {
  category: string;
  method: string;
  summary: string;
  level: string;
  findings: Finding[];
  checks: { label: string; result: string; evidence: Finding | null }[];
  warnings: number[];
};
type Segment = {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string;
};
export type ReviewDetail = {
  id: string;
  revision: number;
  category: string;
  summary: string;
  complaint: boolean;
  review_state: string;
  status: string;
  room: string;
  favorite: boolean;
  segments: Segment[];
  analysis: Analysis | null;
  alert: {
    state: string;
    level: string;
    first_read: number | null;
    reader: string;
  } | null;
  versions: { version: number; category: string }[];
  reviews: { actor: string; action: string; note: string; at: number }[];
};
type Save = (path: string, body?: unknown, method?: string) => Promise<unknown>;
export function ReviewPanel({
  d,
  seek,
  save,
  done,
}: {
  d: ReviewDetail;
  seek: (n: number) => void;
  save: Save;
  done: () => void;
}) {
  const [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, edit] = useState(false);
  const [historic, setHistoric] = useState<{
    result: Analysis;
    segments: Segment[];
  } | null>(null);
  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      setMessage("已保存");
      edit(false);
      done();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (d.status !== "已转写") return null;
  const a = d.analysis,
    base = "/api/phone/calls/" + d.id;
  return (
    <section className="phone-analysis">
      <header>
        <h3>
          {d.category || "历史记录待分析"}{" "}
          {d.alert && (
            <small>
              {d.alert.level} 级 · {d.alert.state}
            </small>
          )}
        </h3>
        <button
          onClick={() =>
            run(() =>
              save(base + "/favorite?enabled=" + !d.favorite, undefined, "PUT"),
            )
          }
          disabled={busy}
        >
          {d.favorite ? "取消收藏" : "收藏"}
        </button>
      </header>
      <h4>通话摘要</h4>
      <p>{d.summary || "暂无带原话的摘要"}</p>
      {a && (
        <>
          <small>
            {a.method} · {d.review_state}
          </small>
          {a.warnings.length > 0 && (
            <p>有 {a.warnings.length} 段重叠或不清晰，请回听核实。</p>
          )}
          <div className="phone-options">
            {a.findings.map((f) => (
              <button
                key={f.title}
                onClick={() => seek(f.start)}
                title={f.quote}
              >
                {f.title} · {Math.floor(f.start)} 秒
              </button>
            ))}
          </div>
          <details>
            <summary>服务质检与原话</summary>
            {a.checks.map((c) => (
              <p key={c.label}>
                {c.label} · {c.result}{" "}
                {c.evidence && (
                  <button onClick={() => seek(c.evidence!.start)}>
                    {c.evidence.quote}
                  </button>
                )}
              </p>
            ))}
          </details>
        </>
      )}
      <div className="phone-options">
        <button onClick={() => edit(!editing)}>
          {editing ? "收起复核" : "复核 / 纠正转写"}
        </button>
        <button
          disabled={busy}
          onClick={() =>
            run(() => save(base + "/reanalyze", { revision: d.revision }))
          }
        >
          重新分析
        </button>
        {d.alert && !d.alert.first_read && (
          <button onClick={() => run(() => save(base + "/read"))}>
            确认已读预警
          </button>
        )}
      </div>
      {editing && (
        <RevisionForm
          key={d.id}
          initial={d}
          busy={busy}
          submit={(body) => run(() => save(base + "/review", body))}
        />
      )}
      <details>
        <summary>分析版本与处理历史</summary>
        {d.versions.map((v) => (
          <button
            key={v.version}
            onClick={async () => {
              try {
                setHistoric(await request(base + "/versions/" + v.version));
              } catch (e) {
                setMessage((e as Error).message);
              }
            }}
          >
            第 {v.version} 版 · {v.category}
          </button>
        ))}
        {historic && (
          <div>
            <p>
              {historic.result.summary || "无事项"} · {historic.result.method}
            </p>
            {historic.segments.map((s) => (
              <p key={s.id}>
                {s.start}s　{s.text}
              </p>
            ))}
          </div>
        )}
        {d.reviews.map((r, i) => (
          <p key={i}>
            {new Date(r.at * 1000).toLocaleString()} · {r.actor} · {r.action}　
            {r.note}
          </p>
        ))}
      </details>
      {message && <p role="status">{message}</p>}
    </section>
  );
}

function RevisionForm({
  initial,
  busy,
  submit,
}: {
  initial: ReviewDetail;
  busy: boolean;
  submit: (body: unknown) => void;
}) {
  // Pin the version at edit start so background refresh cannot silently overwrite a colleague.
  const [original] = useState(initial);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        const segments = original.segments
          .map((s) => ({
            id: s.id,
            text: String(f.get("text" + s.id)),
            speaker: String(f.get("speaker" + s.id) || ""),
          }))
          .filter(
            (s, i) =>
              s.text !== original.segments[i].text ||
              s.speaker !== (original.segments[i].speaker || ""),
          );
        submit({
          revision: original.revision,
          complaint: f.has("complaint"),
          category: f.get("category"),
          room: f.get("room"),
          summary: f.get("summary"),
          level: f.get("level"),
          state: f.get("state"),
          note: f.get("note"),
          segments,
        });
      }}
    >
      <div className="phone-fields">
        <label>
          主分类
          <select name="category" defaultValue={original.category || "其他"}>
            {categories.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label>
          服务房间
          <input name="room" defaultValue={original.room} />
        </label>
        <label>
          预警等级
          <select name="level" defaultValue={original.alert?.level || ""}>
            <option value="">无预警</option>
            {["S", "A", "B", "C"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
        <label>
          复核结果
          <select
            name="state"
            defaultValue={
              original.review_state === "待复核"
                ? "已确认"
                : original.review_state
            }
          >
            {["已确认", "需跟进", "已排除"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        <input
          type="checkbox"
          name="complaint"
          defaultChecked={original.complaint}
        />
        本通另有已核实的客诉事项
      </label>
      <label>
        摘要
        <textarea name="summary" defaultValue={original.summary} />
      </label>
      <details>
        <summary>修订片段和说话人</summary>
        {original.segments.map((s) => (
          <div key={s.id}>
            <label>
              {s.start} 秒 · 说话人
              <input name={"speaker" + s.id} defaultValue={s.speaker} />
            </label>
            <label>
              片段原文
              <textarea name={"text" + s.id} defaultValue={s.text} />
            </label>
          </div>
        ))}
      </details>
      <label>
        复核说明
        <input name="note" required maxLength={500} />
      </label>
      <button disabled={busy}>保存复核</button>
    </form>
  );
}
