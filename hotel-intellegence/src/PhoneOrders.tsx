import { useState } from "react";
import {
  AlarmClock,
  CalendarClock,
  CheckCheck,
  ClipboardList,
  Clock,
  Headphones,
  Inbox,
  ListRestart,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { ShiftNotes, handoverReasons, type ShiftOrder } from "./PhoneDesk";
import type { User } from "./Store";
import "./PhoneOrders.css";

export type Order = ShiftOrder & {
  call_id: string;
  history: { state: string; note: string; actor: string; at: number }[];
};
type Props = {
  tab: string;
  orders: Order[] | null;
  error: string;
  user: User;
  test: boolean;
  setTest: (test: boolean) => void;
  change: (tab: string) => void;
  open: (id: string) => void;
  refresh: () => void;
  busy: boolean;
  save: (path: string, body?: unknown, method?: string) => Promise<unknown>;
  transition: (id: number, body: Record<string, FormDataEntryValue>) => void;
};
const formatTime = (at: number) =>
  new Date(at * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const icons: Record<string, LucideIcon> = {
  全部: ClipboardList,
  无人接手: UserRound,
  已超时: AlarmClock,
  待回电: CalendarClock,
  再次催办: ListRestart,
  超时满一小时: Clock,
};
function lateTime(due: number, now: number) {
  const mins = Math.max(1, Math.floor((now - due) / 60));
  if (mins < 60) return mins + " 分钟";
  const hours = Math.floor(mins / 60);
  return hours < 24
    ? hours + " 小时 " + (mins % 60) + " 分钟"
    : Math.floor(hours / 24) + " 天 " + (hours % 24) + " 小时";
}

export function PhoneOrders({
  tab,
  orders,
  error,
  user,
  test,
  setTest,
  change,
  open,
  refresh,
  busy,
  save,
  transition,
}: Props) {
  const [mine, setMine] = useState(false),
    [facet, setFacet] = useState("全部");
  const now = Date.now() / 1000,
    handover = tab === "handover",
    overdue = tab === "overdue",
    staff = user.role === "部门员工";
  const title = handover
    ? "交班清单"
    : overdue
      ? "超时工单"
      : staff
        ? "部门工单"
        : "服务工单";
  const scope = test ? "测试" : "营业";
  const all = (orders ?? []).filter((o) => Boolean(o.test) === test);
  const matches = (o: Order, kind: string) =>
    kind === "全部" ||
    (kind === "超时满一小时"
      ? o.state !== "已关闭" && now - o.due >= 3600
      : handoverReasons(o, now).includes(kind));
  const base = all.filter((o) =>
    handover
      ? handoverReasons(o, now).length > 0
      : !overdue || matches(o, "已超时"),
  );
  const pool = mine ? base.filter((o) => o.owner === user.name) : base;
  const facets = overdue
    ? ["全部", "超时满一小时", "无人接手", "待回电"]
    : ["无人接手", "已超时", "待回电", "再次催办"];
  const rows = pool
    .filter((o) => matches(o, facet))
    .sort((a, b) => {
      if (overdue) return a.due - b.due || a.id - b.id;
      if (!handover) return b.id - a.id;
      const next = (o: Order) =>
        Math.min(
          o.state === "已关闭" ? Infinity : o.due,
          o.follow_at ?? Infinity,
        );
      return next(a) - next(b) || a.id - b.id;
    });
  const haveFilters = mine || facet !== "全部";
  const ready = orders !== null && !error;
  const count = ready ? rows.length : "—";
  const emptyTitle = haveFilters
    ? "当前筛选没有工单"
    : handover
      ? "暂无待交接事项"
      : overdue
        ? "暂无超时工单"
        : "暂无服务工单";
  const emptyHelp = haveFilters
    ? "可清除筛选，查看此范围内全部负责人的工单。"
    : handover
      ? "无人接手、已超时、待回电或再次催办的事项会出现在这里。"
      : overdue
        ? "工单超过处理时限且尚未关闭时，会进入此清单。"
        : staff
          ? "前台派给本部门的事项会出现在这里。"
          : "前台确认来电事项并派单后，可以在这里跟进。";
  return (
    <section className="oq" aria-label={title}>
      <header className="oq-head">
        <div className="oq-identity">
          <span className={"oq-symbol " + (overdue ? "warm" : "")}>
            {overdue ? (
              <AlarmClock />
            ) : handover ? (
              <ListRestart />
            ) : (
              <ClipboardList />
            )}
          </span>
          <div>
            <h2>{title}</h2>
            <p>
              {handover
                ? "按最近的处理或回电时限排列"
                : overdue
                  ? "按超时最长优先排列"
                  : "按最新派单顺序排列"}
            </p>
          </div>
        </div>
        <div className="oq-scope" role="group" aria-label="数据范围">
          <span>数据范围</span>
          <div>
            {["营业", "测试"].map((k) => (
              <button
                key={k}
                aria-pressed={k === scope}
                onClick={() => setTest(k === "测试")}
              >
                {k}
              </button>
            ))}
          </div>
        </div>
      </header>
      <div className="oq-metrics" aria-label="按跟进情况筛选">
        {facets.map((k) => {
          const Icon = icons[k],
            num = pool.filter((o) => matches(o, k)).length;
          return (
            <button
              key={k}
              className={
                k === "已超时" || k === "超时满一小时" ? "oq-urgent" : ""
              }
              aria-pressed={facet === k}
              onClick={() => setFacet(facet === k ? "全部" : k)}
            >
              <span>
                <Icon size={16} />
                {k === "全部" ? "全部超时" : k}
              </span>
              <strong>{ready ? num : "—"}</strong>
            </button>
          );
        })}
      </div>
      <div className="oq-listhead">
        <div>
          <h3>{facet === "全部" ? "工单列表" : facet}</h3>
          <span className="oq-count">{count}</span>
        </div>
        <div className="oq-owners" role="group" aria-label="负责人筛选">
          {["全部负责人", "我负责的"].map((v, i) => (
            <button
              key={v}
              aria-pressed={mine === Boolean(i)}
              onClick={() => setMine(Boolean(i))}
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      {!ready ? (
        <div className="oq-empty" role="status">
          <Inbox />
          <strong>{error ? "工单列表暂时无法读取" : "正在读取工单…"}</strong>
          {error && (
            <>
              <p>{error}</p>
              <button onClick={refresh}>重新加载</button>
            </>
          )}
        </div>
      ) : rows.length === 0 ? (
        <div className="oq-empty">
          <div className="oq-seal">
            {haveFilters ? <Inbox /> : <CheckCheck />}
          </div>
          <strong>{emptyTitle}</strong>
          <p>
            {scope}范围 · {emptyHelp}
          </p>
          {haveFilters ? (
            <button
              onClick={() => {
                setMine(false);
                setFacet("全部");
              }}
            >
              清除筛选
            </button>
          ) : tab !== "orders" ? (
            <button onClick={() => change("orders")}>
              查看{staff ? "部门" : "服务"}工单 →
            </button>
          ) : (
            !staff && (
              <button onClick={() => change("pending")}>
                查看待确认事项 →
              </button>
            )
          )}
        </div>
      ) : (
        <div className="oq-orders">
          {rows.map((o) => {
            const tags = handoverReasons(o, now);
            return (
              <article key={o.id} className="oq-order">
                <div className="oq-order-head">
                  <div>
                    <small>
                      工单 #{o.id} · {o.room ? "房间 " + o.room : "房间待核实"}
                    </small>
                    <h3>{o.title}</h3>
                  </div>
                  <div className="oq-tags">
                    <span>{o.state}</span>
                    {tags.map((s) => (
                      <span key={s} data-risk={s === "已超时"}>
                        {s === "已超时" ? "超时 " + lateTime(o.due, now) : s}
                      </span>
                    ))}
                  </div>
                </div>
                <dl>
                  <div>
                    <dt>负责部门 / 人员</dt>
                    <dd>
                      {o.department} · {o.owner || "待接手"}
                    </dd>
                  </div>
                  <div>
                    <dt>处理截止</dt>
                    <dd>{formatTime(o.due)}</dd>
                  </div>
                  <div>
                    <dt>约定回电</dt>
                    <dd>
                      {o.follow_at ? formatTime(o.follow_at) : "尚未约定"}
                    </dd>
                  </div>
                </dl>
                {o.feedback && (
                  <p className="oq-feedback">上次跟进 · {o.feedback}</p>
                )}
                <ShiftNotes o={o} save={save} done={refresh} />
                {o.state !== "已关闭" && (!staff || o.state !== "待复核") && (
                  <details>
                    <summary>更新工单状态</summary>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        transition(
                          o.id,
                          Object.fromEntries(new FormData(e.currentTarget)),
                        );
                      }}
                    >
                      <label>
                        下一状态
                        <select name="state">
                          {(o.state === "待接单"
                            ? ["处理中"]
                            : o.state === "处理中"
                              ? ["待复核"]
                              : ["已关闭", "处理中"]
                          ).map((s) => (
                            <option key={s}>{s}</option>
                          ))}
                        </select>
                      </label>
                      <label>
                        处理结果或退回原因
                        <input
                          name="note"
                          maxLength={1000}
                          required={o.state === "处理中"}
                        />
                      </label>
                      <button type="submit" disabled={busy}>
                        保存状态
                      </button>
                    </form>
                  </details>
                )}
                <details>
                  <summary>处理记录（{o.history.length}）</summary>
                  <ol>
                    {o.history.map((h, i) => (
                      <li key={i}>
                        <time>{formatTime(h.at)}</time> {h.actor} · {h.state}{" "}
                        {h.note}
                      </li>
                    ))}
                  </ol>
                </details>
                {!staff && (
                  <button
                    className="oq-evidence"
                    onClick={() => open(o.call_id)}
                  >
                    <Headphones size={15} />
                    查看关联通话
                  </button>
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
