import { useEffect, useState } from "react";
import { request, type User } from "./Store";
import "./PhoneDesk.css";

type Save = (path: string, body?: unknown, method?: string) => Promise<unknown>;
type Props = { user: User; save: Save; open: (id: string) => void };
type Task = {
  call_id: string;
  level: string;
  room: string;
  started: string;
  summary: string;
  state: string;
  owner: string;
  recipient: string;
  backup: string;
  due: number;
  escalated: number | null;
  revision: number;
  events: {
    action: string;
    actor: string;
    recipient: string;
    at: number;
    note: string;
  }[];
};
type DutySettings = {
  primary_name: string;
  backup_name: string;
  minutes: number;
};
type Board = {
  rows: Task[];
  settings: DutySettings;
  roster: string[];
  error: string;
};
const stamp = (time: number) =>
  new Date(time * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
function Scope({ test, set }: { test: boolean; set: (v: boolean) => void }) {
  return (
    <label className="desk-scope">
      数据范围
      <select
        value={test ? "test" : "live"}
        onChange={(e) => set(e.target.value === "test")}
      >
        <option value="live">营业</option>
        <option value="test">测试</option>
      </select>
    </label>
  );
}
function useFeed<T>(path: string, refresh: number) {
  const [result, show] = useState<{ path: string; data: T } | null>(null),
    [error, fail] = useState("");
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    fail("");
    const read = async () => {
      try {
        const data = await request<T>(path);
        if (active) {
          show({ path, data });
          fail("");
        }
      } catch (e) {
        if (active) fail((e as Error).message);
      } finally {
        if (active) timer = setTimeout(read, 5000);
      }
    };
    void read();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [path, refresh]);
  return { data: result?.path === path ? result.data : null, error };
}
function useAction(save: Save) {
  const [version, bump] = useState(0),
    [busy, setBusy] = useState(false),
    [notice, say] = useState("");
  async function submit(
    path: string,
    body: unknown,
    message: string,
    method = "POST",
  ) {
    if (busy) return;
    setBusy(true);
    try {
      await save(path, body, method);
      say(message);
    } catch (e) {
      say((e as Error).message);
    } finally {
      setBusy(false);
      bump((v) => v + 1);
    }
  }
  return { version, busy, notice, submit };
}
function AlertActions({
  t,
  board,
  action,
}: {
  t: Task;
  board: Board;
  action: ReturnType<typeof useAction>;
}) {
  const [mode, set] = useState("");
  const run = (name: string, recipient = "", note = "") =>
    action.submit(
      "api/phone/duty/" + t.call_id,
      { revision: t.revision, action: name, recipient, note },
      "值班待办已更新",
    );
  return (
    <div className="desk-task-actions">
      {t.state === "待接手" && (
        <button
          className="desk-primary"
          disabled={action.busy}
          onClick={() => run("接手")}
        >
          我来接手
        </button>
      )}
      {(t.state === "跟进中" ? ["转交", "办结", "排除"] : ["转交", "排除"]).map(
        (k) => (
          <button
            key={k}
            aria-pressed={mode === k}
            onClick={() => set(mode === k ? "" : k)}
          >
            {k}
          </button>
        ),
      )}
      {mode && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            void run(
              mode,
              String(f.get("target") || ""),
              String(f.get("note") || ""),
            );
          }}
        >
          {mode === "转交" && (
            <label>
              下一位接收人
              <select name="target">
                {board.roster.map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
            </label>
          )}
          <label>
            {mode}说明
            <input name="note" maxLength={500} required />
          </label>
          <button disabled={action.busy}>确认{mode}</button>
        </form>
      )}
    </div>
  );
}
type ScopeProps = { test: boolean; setTest: (v: boolean) => void };
export function DutyDesk({
  user,
  save,
  open,
  test,
  setTest,
}: Props & ScopeProps) {
  const [bucket, filter] = useState("全部");
  const a = useAction(save),
    { data, error } = useFeed<Board>("api/phone/duty?test=" + test, a.version);
  const rows = data?.rows || [],
    waiting = rows.filter((t) => t.state === "待接手").length;
  return (
    <section className="desk-shell">
      <header className="desk-head">
        <div>
          <h2>值班处理台</h2>
        </div>
        <Scope test={test} set={setTest} />
      </header>
      <div className="desk-metrics">
        {[
          ["待接手", waiting],
          [
            "已升级",
            rows.filter((t) => t.escalated && t.state === "待接手").length,
          ],
          ["跟进中", rows.length - waiting],
        ].map(([k, v]) => (
          <button
            key={k}
            data-kind={k}
            aria-pressed={bucket === k}
            onClick={() => filter(bucket === k ? "全部" : String(k))}
          >
            <span>{k}</span>
            <strong>{data ? v : "—"}</strong>
          </button>
        ))}
      </div>
      {(error || data?.error || a.notice) && (
        <p className="inline-notice" role="status">
          {error || data?.error || a.notice}
        </p>
      )}
      <div className="desk-split">
        <section className="desk-card">
          <header>
            <h3>{bucket === "全部" ? "高风险待办" : bucket}</h3>
            {bucket !== "全部" && (
              <button onClick={() => filter("全部")}>查看全部</button>
            )}
          </header>
          {data &&
            !rows.some(
              (t) =>
                bucket === "全部" ||
                t.state === bucket ||
                (bucket === "已升级" && t.escalated && t.state === "待接手"),
            ) && (
              <p className="desk-empty">
                当前范围没有{bucket === "全部" ? "高风险待办" : bucket + "事项"}
                。
              </p>
            )}
          {rows
            .filter(
              (t) =>
                bucket === "全部" ||
                t.state === bucket ||
                (bucket === "已升级" && t.escalated && t.state === "待接手"),
            )
            .map((t) => (
              <article className="desk-task" key={t.call_id}>
                <div className="desk-line">
                  <span className="desk-grade">{t.level} 级</span>
                  <b>{t.room ? "房间 " + t.room : "房间待核实"}</b>
                  <small>{t.started.slice(5, 16).replace("T", " ")}</small>
                </div>
                <p>{t.summary || "待核实客诉原话"}</p>
                <div className="desk-meta">
                  {t.state} · {t.owner || t.recipient}{" "}
                  {t.state === "待接手" &&
                    (t.escalated
                      ? " · 已提醒 " + t.backup
                      : " · " + stamp(t.due) + " 前接手")}
                </div>
                <button className="desk-text" onClick={() => open(t.call_id)}>
                  查看通话原话 →
                </button>
                {data && ["经理", "管理员"].includes(user.role) && (
                  <AlertActions
                    key={t.call_id + "-" + t.revision}
                    t={t}
                    board={data}
                    action={a}
                  />
                )}
                <details>
                  <summary>通知与处理记录</summary>
                  {t.events.map((e, i) => (
                    <p key={i}>
                      {stamp(e.at)} · {e.action} · {e.actor} → {e.recipient}{" "}
                      {e.note}
                    </p>
                  ))}
                </details>
              </article>
            ))}
        </section>
        {data && (
          <aside className="desk-card desk-roster">
            <h3>当班安排</h3>
            <dl>
              <dt>主值班</dt>
              <dd>{data.settings.primary_name}</dd>
              <dt>替班</dt>
              <dd>{data.settings.backup_name}</dd>
              <dt>无人接手时升级</dt>
              <dd>{data.settings.minutes} 分钟</dd>
            </dl>
            {user.role === "管理员" && (
              <details>
                <summary>调整后续通知</summary>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const f = new FormData(e.currentTarget);
                    void a.submit(
                      "api/phone/duty",
                      {
                        primary_name: f.get("primary"),
                        backup_name: f.get("backup"),
                        minutes: Number(f.get("minutes")),
                      },
                      "当班安排已保存",
                      "PUT",
                    );
                  }}
                >
                  {[
                    ["主值班", "primary", data.settings.primary_name],
                    ["替班", "backup", data.settings.backup_name],
                  ].map(([label, name, v]) => (
                    <label key={name}>
                      {label}
                      <select name={name} defaultValue={v}>
                        {data.roster.map((n) => (
                          <option key={n}>{n}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label>
                    接手时限（分钟）
                    <input
                      name="minutes"
                      type="number"
                      min="1"
                      max="240"
                      defaultValue={data.settings.minutes}
                      required
                    />
                  </label>
                  <button disabled={a.busy}>保存安排</button>
                </form>
              </details>
            )}
          </aside>
        )}
      </div>
    </section>
  );
}
type Difference = {
  id: number;
  reference: string;
  leg: string;
  started: string;
  caller: string;
  callee: string;
  call_id: string | null;
  original: string;
  reason: string;
  status: string;
  actor: string;
  note: string;
};
type Ledger = {
  rows: Difference[];
  counts: Record<string, number>;
  batch: { id: number; at: number } | null;
};
export function LedgerDesk({
  save,
  open,
  test,
  setTest,
}: { save: Save; open: Props["open"] } & ScopeProps) {
  const [status, filter] = useState("待核查"),
    [offset, page] = useState(0);
  const a = useAction(save),
    { data, error } = useFeed<Ledger>(
      "api/phone/ledger?" +
        new URLSearchParams({
          test: String(test),
          status,
          offset: String(offset),
        }),
      a.version,
    );
  const total = data
    ? status === "全部"
      ? Object.values(data.counts).reduce((s, n) => s + n, 0)
      : data.counts[status] || 0
    : 0;
  return (
    <section className="desk-card desk-ledger">
      <header>
        <div>
          <h3>逐通对账</h3>
          <small>
            {data?.batch
              ? "最近话单 · " + stamp(data.batch.at)
              : "选择独立话单，核对录音是否齐全"}
          </small>
        </div>
        <Scope
          test={test}
          set={(v) => {
            setTest(v);
            page(0);
          }}
        />
      </header>
      <form
        className="desk-upload"
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          f.set("test", String(test));
          page(0);
          void a.submit("api/phone/reconcile", f, "话单已核对");
        }}
      >
        <label>
          独立话单 CSV
          <input type="file" accept=".csv" name="file" required />
        </label>
        <button className="desk-primary" disabled={a.busy}>
          核对{test ? "测试" : "营业"}话单
        </button>
      </form>
      <details>
        <summary>CSV 列与整日核对范围</summary>
        <p>
          必填 call_id、caller、callee。可填
          started、disposition、source_id、segment_id。带日期时请提供当日完整话单。
        </p>
      </details>
      <div className="desk-filters">
        {["待核查", "已销差", "已核查", "正常", "全部"].map((k) => (
          <button
            key={k}
            aria-pressed={status === k}
            onClick={() => {
              filter(k);
              page(0);
            }}
          >
            {k} {k !== "全部" && (data?.counts[k] || 0)}
          </button>
        ))}
      </div>
      {(a.notice || error) && <p role="status">{error || a.notice}</p>}
      {data && !data.rows.length && (
        <p className="desk-empty">
          {data.batch ? "当前筛选没有逐通明细" : "此范围尚未导入独立话单"}
        </p>
      )}
      {data?.rows.map((r) => (
        <article key={r.id}>
          <div className="desk-line">
            <strong>
              {r.caller} → {r.callee}
            </strong>
            <span className="desk-pill">{r.reason}</span>
          </div>
          <small>
            话单 {r.reference} · {r.started || "话单未提供时间"}{" "}
            {r.leg && " · 分段 " + r.leg} · {r.status}
            {r.reason !== r.original && " · 原因由「" + r.original + "」更新"}
          </small>
          {r.call_id && (
            <button className="desk-text" onClick={() => open(r.call_id!)}>
              查看对应录音
            </button>
          )}
          {r.note && (
            <p>
              {r.actor} · {r.note}
            </p>
          )}
          {r.status === "待核查" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void a.submit(
                  "api/phone/ledger/" + r.id,
                  {
                    reason: r.reason,
                    note: new FormData(e.currentTarget).get("note"),
                  },
                  "已记录核查结果",
                );
              }}
            >
              <label>
                核查结果
                <input required maxLength={500} name="note" />
              </label>
              <button disabled={a.busy}>标记已核查</button>
            </form>
          )}
        </article>
      ))}
      {total > 50 && (
        <div className="desk-filters">
          <button disabled={!offset} onClick={() => page(offset - 50)}>
            上一页
          </button>
          <span>
            {offset + 1}–{Math.min(offset + 50, total)} / {total}
          </span>
          <button
            disabled={offset + 50 >= total}
            onClick={() => page(offset + 50)}
          >
            下一页
          </button>
        </div>
      )}
    </section>
  );
}
export type ShiftOrder = {
  id: number;
  owner: string;
  room: string;
  title: string;
  state: string;
  department: string;
  due: number;
  test: number;
  follow_at: number | null;
  feedback: string;
  desk_rev: number;
  last_repeat: number | null;
  last_follow: number | null;
};
export function ShiftNotes({
  o,
  save,
  done,
}: {
  o: ShiftOrder;
  save: Save;
  done: () => void;
}) {
  const a = useAction(save);
  const local = o.follow_at ? new Date(o.follow_at * 1000) : null;
  return (
    <details className="desk-notes">
      <summary>交班与回访</summary>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const when = String(f.get("when") || "");
          await a.submit(
            "api/phone/orders/" + o.id + "/follow",
            {
              revision: o.desk_rev,
              claim: f.has("claim"),
              follow_at: when ? new Date(when).toISOString() : null,
              feedback: f.get("note"),
            },
            "交班记录已保存",
          );
          done();
        }}
      >
        <label>
          <input name="claim" type="checkbox" />
          由我负责此单
        </label>
        <label>
          下次回电
          <input
            type="datetime-local"
            name="when"
            defaultValue={
              local
                ? new Date(+local - local.getTimezoneOffset() * 60000)
                    .toISOString()
                    .slice(0, 16)
                : ""
            }
          />
        </label>
        <label>
          回访结果 / 交班说明
          <input name="note" maxLength={500} />
        </label>
        <button disabled={a.busy}>保存跟进</button>
      </form>
      {a.notice && <p role="status">{a.notice}</p>}
    </details>
  );
}
export function handoverReasons(o: ShiftOrder, now = Date.now() / 1000) {
  const reasons = [];
  if (o.state !== "已关闭" && !o.owner) reasons.push("无人接手");
  if (o.state !== "已关闭" && o.due < now) reasons.push("已超时");
  if (o.follow_at) reasons.push("待回电");
  if (o.last_repeat && (o.last_follow || 0) < o.last_repeat)
    reasons.push("再次催办");
  return reasons;
}
type Candidate = {
  id: number;
  title: string;
  state: string;
  owner: string;
  progress: string;
  started: string;
  quote: string;
  follow_at: number | null;
  department: string;
};
export function Suggestions({
  item,
  room,
  save,
  done,
}: {
  item: number;
  room: string;
  save: Save;
  done: () => void;
}) {
  const [input, set] = useState(room),
    [rows, list] = useState<Candidate[] | null>(null),
    [error, fail] = useState(""),
    [pending, loading] = useState(false);
  const a = useAction(save);
  return (
    <details className="desk-suggest">
      <summary>查找同房间的相关工单</summary>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          list(null);
          fail("");
          loading(true);
          try {
            list(
              await request<Candidate[]>(
                "api/phone/items/" +
                  item +
                  "/suggestions?room=" +
                  encodeURIComponent(input.trim()),
              ),
            );
          } catch (e) {
            fail((e as Error).message);
          } finally {
            loading(false);
          }
        }}
      >
        <label>
          已核实房间
          <input
            value={input}
            onChange={(e) => {
              set(e.target.value);
              list(null);
            }}
            required
            maxLength={80}
            disabled={pending}
          />
        </label>
        <button disabled={pending || !input.trim()}>查询近 7 天工单</button>
      </form>
      {rows?.length === 0 && <p>该房间近期没有相关工单。</p>}
      {rows?.map((r) => (
        <section key={r.id} className="desk-match">
          <b>
            #{r.id} · {r.title}
          </b>
          <p>
            {r.department} · {r.owner || "无人接手"} · {r.state}
          </p>
          <p>
            {r.started.slice(0, 16).replace("T", " ")} · {r.quote}
          </p>
          {r.progress && <p>最近进展 · {r.progress}</p>}
          {r.follow_at && <p>约定回电 · {stamp(r.follow_at)}</p>}
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              await a.submit(
                "api/phone/items/" + item + "/link",
                {
                  order: r.id,
                  reopen: r.state === "已关闭",
                  note: new FormData(e.currentTarget).get("note"),
                },
                "已关联原单",
              );
              done();
            }}
          >
            <label>
              判断为同一事项的依据
              <input name="note" required maxLength={500} />
            </label>
            <button disabled={a.busy}>
              {r.state === "已关闭" ? "标记复发并续办" : "确认关联此单"}
            </button>
          </form>
        </section>
      ))}
      {(error || a.notice) && <p role="status">{error || a.notice}</p>}
    </details>
  );
}
