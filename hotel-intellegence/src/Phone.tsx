import { useEffect, useRef, useState } from "react";
import {
  BatchImport,
  Finder,
  initialFilters,
  registerParams,
  ReviewPanel,
  type ReviewDetail,
} from "./PhoneWorkbench";
import { CallRegister, Transcript, type CallRecord } from "./PhoneRecords";
import "./PhoneConsole.css";
import { DutyDesk, LedgerDesk, Suggestions } from "./PhoneDesk";
import { PhoneOrders, type Order } from "./PhoneOrders";
import { Activity, ArrowUpRight, Headphones, PhoneCall } from "lucide-react";
import { PhoneReports } from "./PhoneReports";
import { request, type User } from "./Store";

type Item = {
  orderId?: number;
  orderState?: string;
  confirmedRoom?: string;
  id: number;
  title: string;
  department: string;
  quote: string;
  start: number;
  state: string;
};
type Detail = CallRecord &
  ReviewDetail & {
    roomConflict: boolean;
    mentionedRooms: string[];
    items: Item[];
    error: string;
    source: string;
    quality?: string[];
  };
type Extension = { number: string; kind: string; label: string };
const departments = ["工程部", "客房部", "前台", "值班经理"];
function values(form: HTMLFormElement) {
  return Object.fromEntries(new FormData(form));
}
function Field({
  name,
  title,
  type = "text",
  value,
  required = true,
}: {
  name: string;
  title: string;
  type?: string;
  value?: string;
  required?: boolean;
}) {
  return (
    <label>
      {title}
      <input name={name} type={type} required={required} defaultValue={value} />
    </label>
  );
}
function Dept({ value }: { value?: string }) {
  return (
    <label>
      责任部门
      <select name="department" defaultValue={value}>
        {departments.map((d) => (
          <option key={d}>{d}</option>
        ))}
      </select>
    </label>
  );
}
export default function Phone({
  token,
  user,
  initial = "calls",
}: {
  token: string;
  user: User;
  initial?: string;
}) {
  const staff = user.role === "部门员工";
  const [tab, setTab] = useState(() => {
    const view = new URLSearchParams(window.location.hash.split("?")[1]).get(
      "view",
    );
    if (
      initial === "calls" &&
      view &&
      ["orders", "handover", "overdue"].includes(view) &&
      user.role !== "质检员"
    )
      return view;
    return staff && initial !== "overdue" ? "orders" : initial;
  });
  const [calls, setCalls] = useState<CallRecord[]>([]),
    [orders, setOrders] = useState<Order[] | null>(null),
    [ext, setExt] = useState<Extension[]>([]);
  const [selected, setSelected] = useState(() =>
      !staff && initial === "calls"
        ? window.location.hash.match(/^#phone\/([a-f0-9]{32})(?:\?|$)/)?.[1] ||
          ""
        : "",
    ),
    [detail, setDetail] = useState<Detail | null>(null);
  const [offset, setOffset] = useState(0),
    [version, setVersion] = useState(0),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<{
    configured: boolean;
    test_inbox?: string;
    last_simulated?: string;
    last_received?: string;
    issues?: string[];
    reconciliation?: {
      test: boolean;
      at: number;
      counts: Record<string, number>;
    }[];
    last?: string;
    error: string;
    failed: number;
    queue: number;
    rejected: number;
  } | null>(null);
  const [orderError, setOrderError] = useState("");
  const player = useRef<HTMLAudioElement>(null);
  const [position, setPosition] = useState(-1);
  const [detailTab, setDetailTab] = useState<"analysis" | "items">("analysis");
  const [loading, setLoading] = useState(true);
  const navRef = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const [filters, setFilters] = useState(() => ({
      ...initialFilters,
      scope:
        window.location.hash.startsWith("#phone") &&
        new URLSearchParams(window.location.hash.split("?")[1]).get("scope") ===
          "test"
          ? "测试"
          : "营业",
    })),
    [total, setTotal] = useState(0);
  const shiftScope = filters.scope === "测试";
  function setShiftScope(test: boolean) {
    setOffset(0);
    setFilters((f) => ({ ...f, scope: test ? "测试" : "营业" }));
  }
  useEffect(() => {
    const nav = navRef.current;
    if (!nav || nav.scrollWidth <= nav.clientWidth) return;
    const button = nav.querySelector('[aria-current="page"]');
    if (!button) return;
    const bounds = nav.getBoundingClientRect(),
      box = button.getBoundingClientRect();
    if (box.left < bounds.left) nav.scrollLeft += box.left - bounds.left;
    else if (box.right > bounds.right)
      nav.scrollLeft += box.right - bounds.right;
  }, [tab]);
  const [originTab, setOriginTab] = useState("calls");
  const supervisor =
    user.phone?.export ?? ["经理", "管理员"].includes(user.role);
  const reviewer = user.role === "质检员";
  const audioAllowed = user.phone?.audio ?? !staff;
  useEffect(() => {
    const params = new URLSearchParams();
    if (!selected && ["orders", "handover", "overdue"].includes(tab))
      params.set("view", tab);
    if (filters.scope === "测试") params.set("scope", "test");
    const query = params.toString();
    window.history.replaceState(
      null,
      "",
      "#phone" + (selected ? "/" + selected : "") + (query ? "?" + query : ""),
    );
  }, [selected, filters.scope, tab]);
  useEffect(() => {
    if (selected) heading.current?.scrollIntoView({ block: "start" });
  }, [selected]);

  useEffect(() => {
    if (staff) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function pollHealth() {
      try {
        const result =
          await request<NonNullable<typeof health>>("/api/phone/health");
        if (active) setHealth(result);
      } catch {
        if (active) setHealth(null);
      } finally {
        if (active) timer = setTimeout(pollHealth, 5000);
      }
    }
    void pollHealth();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [staff]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        if (["orders", "overdue", "handover"].includes(tab)) {
          const v = await request<Order[]>("/api/phone/orders");
          if (alive) {
            setOrders(v);
            setOrderError("");
          }
        } else if (tab === "extensions") {
          const v = await request<Extension[]>("/api/phone/extensions");
          if (alive) setExt(v);
        } else if (selected) {
          const v = await request<Detail>("/api/phone/calls/" + selected);
          if (alive) {
            setDetail(v);
            if (v.alert && !v.alert.first_read && v.alert.state !== "已排除") {
              await mutate("/api/phone/calls/" + selected + "/read");
            }
          }
        } else if (["calls", "pending", "alerts"].includes(tab)) {
          const params = registerParams(filters);
          params.set("offset", String(offset));
          if (tab === "alerts") params.set("alerts", "true");
          if (tab === "pending") params.set("pending", "true");
          const result = await request<{ total: number; rows: CallRecord[] }>(
            "/api/phone/register?" + params,
          );
          if (alive) {
            if (offset > 0 && offset >= result.total) {
              setOffset(Math.max(0, Math.floor((result.total - 1) / 50) * 50));
              return;
            }
            setLoading(false);
            setCalls(result.rows);
            setTotal(result.total);
          }
        }
      } catch (e) {
        if (alive) {
          setLoading(false);
          if (["orders", "overdue", "handover"].includes(tab))
            setOrderError((e as Error).message);
          else setMessage((e as Error).message);
        }
      } finally {
        if (alive) timer = setTimeout(refresh, 5000);
      }
    };
    void refresh();

    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [tab, selected, offset, version, filters, staff]);
  async function mutate(path: string, body?: unknown, method = "POST") {
    const form = body instanceof FormData;
    const headers: Record<string, string> = { "x-local-token": token };
    if (!form && body) headers["Content-Type"] = "application/json";
    return request<{ id?: string; duplicate?: boolean }>(path, {
      method,
      headers,
      body: form ? body : body ? JSON.stringify(body) : undefined,
    });
  }
  async function act(action: () => Promise<unknown>, success: string) {
    if (busy) return;
    setBusy(true);
    setMessage("");
    try {
      const result = await action();
      setMessage(typeof result === "string" ? result : success);
      setVersion((v) => v + 1);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function selectCall(id: string) {
    setPosition(-1);
    setDetailTab("analysis");
    setMessage("");
    setDetail(null);
    setSelected(id);
  }
  function open(id: string) {
    setOriginTab(tab);
    selectCall(id);
    setTab("calls");
  }
  function back() {
    setTab(originTab);
    setSelected("");
    setDetail(null);
    setMessage("");
  }
  function change(t: string) {
    if (t === tab && !selected) return;
    setLoading(true);
    setCalls([]);
    setTotal(0);
    setMessage("");
    setSelected("");
    setDetail(null);
    setOffset(0);
    setTab(t);
  }
  function seek(start: number) {
    if (!audioAllowed) {
      setMessage("当前账号未获完整录音回听权限");
      return;
    }
    if (player.current) {
      player.current.currentTime = start;
      void player.current
        .play()
        .catch(() => setMessage("请点击播放器开始回听"));
    }
  }

  const recordIndex = calls.findIndex((c) => c.id === selected);
  const tabs = staff
    ? [
        ["orders", "部门工单"],
        ["handover", "交班清单"],
        ["overdue", "超时工单"],
      ]
    : [
        ["report", "电话总览"],
        ["calls", "通话记录"],
        ["alerts", "客诉与预警"],
        ["duty", "值班处理台"],
        ["pending", "待确认事项"],
        ["import", "录音与接入"],
        ["orders", "服务工单"],
        ["handover", "交班清单"],
        ["overdue", "超时工单"],
        ["extensions", "分机映射"],
      ];
  return (
    <section className="phone">
      <div className="phone-mast">
        <div className="ph-mast-title">
          <span className="ph-mast-symbol">
            <PhoneCall size={24} />
          </span>
          <div>
            <h1 ref={heading}>前台电话</h1>
            <small>通话分析与服务跟进</small>
          </div>
        </div>
        {!staff && !reviewer && tab !== "duty" && (
          <button className="ph-duty-entry" onClick={() => change("duty")}>
            <Headphones size={17} />
            进入值班台 <ArrowUpRight size={15} />
          </button>
        )}
      </div>
      <nav ref={navRef} className="module-tabs" aria-label="电话模块导航">
        {tabs
          .filter(
            ([k]) => !reviewer || ["calls", "pending", "alerts"].includes(k),
          )
          .map(([k, v]) => (
            <button
              key={k}
              aria-current={tab === k ? "page" : undefined}
              className={tab === k ? "primary" : "secondary"}
              onClick={() => change(k)}
            >
              {v}
            </button>
          ))}
      </nav>
      {health && !selected && (
        <div className="ph-healthbar">
          <span
            className="ph-health-state"
            data-state={
              health.error ? "error" : health.last ? "ready" : "waiting"
            }
          >
            <Activity size={15} />
            {health.error
              ? "录音接入异常"
              : health.last
                ? "已收到营业录音"
                : health.configured
                  ? "接收目录就绪 · 营业录音待验证"
                  : "自动录音源待配置"}
          </span>
          <div>
            {[
              ["队列", health.queue],
              ["转写失败", health.failed],
              ["异常清单", health.rejected],
            ].map(([label, n]) => (
              <span key={label}>
                {label} <b>{n}</b>
              </span>
            ))}
          </div>
          {(health.error || health.last) && (
            <small>{health.error || "最近接入通话 " + health.last}</small>
          )}
        </div>
      )}
      {message && (
        <p role="status" className="inline-notice">
          {message}
        </p>
      )}
      {!staff &&
        (tab === "report" || (originTab === "report" && !!selected)) && (
          <PhoneReports
            canExport={supervisor}
            open={open}
            visible={tab === "report"}
          />
        )}
      {tab === "duty" && !reviewer && !staff && (
        <DutyDesk
          test={shiftScope}
          setTest={setShiftScope}
          user={user}
          save={mutate}
          open={open}
        />
      )}
      {tab === "import" && (
        <>
          {user.role === "管理员" && (
            <LedgerDesk
              test={shiftScope}
              setTest={setShiftScope}
              save={mutate}
              open={open}
            />
          )}
          <BatchImport token={token} open={open} />
          <details className="panel">
            <summary>交换机接入状态</summary>
            <p>
              最近营业收件　{health?.last_received || "尚无"}　最近模拟收件　
              {health?.last_simulated || "尚无"}
            </p>
            {health?.test_inbox && <p>模拟录音接收目录　{health.test_inbox}</p>}
            {health?.issues?.map((x, i) => (
              <p key={i}>{x}</p>
            ))}
            {health?.reconciliation?.map((r) => (
              <p key={String(r.test)}>
                {r.test ? "测试" : "营业"} ·{" "}
                {new Date(r.at * 1000).toLocaleString()} ·{" "}
                {Object.entries(r.counts)
                  .map(([k, v]) => k + " " + v)
                  .join(" · ")}
              </p>
            ))}
          </details>
        </>
      )}
      {tab === "extensions" && (
        <section className="panel">
          <h2>分机映射</h2>
          {user.role === "管理员" && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                const v = values(e.currentTarget);
                void act(
                  () => mutate("/api/phone/extensions", v, "PUT"),
                  "分机映射已保存",
                );
              }}
            >
              <Field title="分机号" name="number" />
              <label>
                类型
                <select name="kind">
                  <option>前台</option>
                  <option>客房</option>
                </select>
              </label>
              <Field title="房号或前台名称" name="label" />
              <button disabled={busy || !token}>保存</button>
            </form>
          )}
          {ext.map((r) => (
            <p key={r.number}>
              {r.number} · {r.kind} · {r.label}
            </p>
          ))}
        </section>
      )}
      {["calls", "pending", "alerts"].includes(tab) && !selected && (
        <section className="panel ph-archive">
          <div className="ph-section-heading">
            <h2>
              {tab === "pending"
                ? "待确认事项"
                : tab === "alerts"
                  ? "客诉与预警"
                  : "通话档案"}
            </h2>
            <span className="ph-count" aria-live="polite">
              {loading ? "查询中" : "共 " + total + " 通"}
            </span>
          </div>
          <Finder
            f={filters}
            set={(f) => {
              setLoading(true);
              setCalls([]);
              setTotal(0);
              setFilters(f);
              setOffset(0);
            }}
            alerts={tab === "alerts"}
          />
          {!loading && calls.length === 0 && (
            <p className="ph-empty">
              {filters.scope === "营业"
                ? "当前条件下没有营业通话"
                : "当前条件下没有测试通话"}
            </p>
          )}
          {calls.length > 0 && (
            <CallRegister
              rows={calls}
              open={open}
              busy={busy || !token}
              favorite={(c) =>
                act(
                  () =>
                    mutate(
                      "/api/phone/calls/" +
                        c.id +
                        "/favorite?enabled=" +
                        !c.favorite,
                      undefined,
                      "PUT",
                    ),
                  c.favorite ? "已取消收藏" : "已收藏通话",
                )
              }
            />
          )}
          <div className="ph-pager">
            <span>
              {total > 0 ? Math.floor(offset / 50) + 1 : 0} /{" "}
              {Math.ceil(total / 50)} 页
            </span>
            <button disabled={!offset} onClick={() => setOffset((v) => v - 50)}>
              上一页
            </button>{" "}
            <button
              disabled={offset + 50 >= total}
              onClick={() => setOffset((v) => v + 50)}
            >
              下一页
            </button>
          </div>
        </section>
      )}
      {selected && !detail && (
        <section className="panel">
          <button onClick={back}>返回列表</button>
          <p>{message ? "无法读取这通电话" : "正在读取通话…"}</p>
        </section>
      )}
      {tab === "calls" && selected && detail && (
        <section className="panel ph-detail">
          <div className="ph-detail-nav">
            <button onClick={back}>
              ← 返回{originTab === "report" ? "报表" : "列表"}
            </button>
            <div className="ph-actions">
              {recordIndex >= 0 &&
                ["calls", "alerts", "pending"].includes(originTab) && (
                  <>
                    <button
                      disabled={recordIndex === 0}
                      onClick={() => selectCall(calls[recordIndex - 1].id)}
                    >
                      上一通
                    </button>
                    <button
                      disabled={recordIndex === calls.length - 1}
                      onClick={() => selectCall(calls[recordIndex + 1].id)}
                    >
                      下一通
                    </button>
                  </>
                )}
              {supervisor && (
                <a href={"/api/phone/calls/" + selected + "/export"}>
                  导出通话报告
                </a>
              )}
            </div>
          </div>
          <div className="ph-detail-title">
            <h2>
              {detail.caller} → {detail.callee}
            </h2>
            <span className="ph-tag" data-kind={detail.category}>
              {detail.category || "待分析"}
            </span>
          </div>
          <dl className="ph-facts">
            {[
              ["通话时间", detail.started.slice(0, 19).replace("T", " ")],
              ["方向", detail.direction],
              ["服务房间", detail.room || "待核实"],
              ["录音来源", detail.source],
              [
                "档案状态",
                (detail.test ? "测试" : "营业") + " · " + detail.status,
              ],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value || "未知"}</dd>
              </div>
            ))}
          </dl>
          {audioAllowed && (
            <div className="ph-player">
              <audio
                key={selected}
                controls
                ref={player}
                onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
                src={"/api/phone/calls/" + selected + "/audio"}
              />
              <div className="phone-options">
                <button
                  onClick={() =>
                    seek(Math.max(0, (player.current?.currentTime || 0) - 5))
                  }
                >
                  后退 5 秒
                </button>
                <button
                  onClick={() =>
                    seek(
                      Math.min(
                        player.current?.duration || 0,
                        (player.current?.currentTime || 0) + 5,
                      ),
                    )
                  }
                >
                  前进 5 秒
                </button>
                <label>
                  倍速
                  <select
                    defaultValue="1"
                    onChange={(e) => {
                      if (player.current)
                        player.current.playbackRate = Number(e.target.value);
                    }}
                  >
                    {[0.75, 1, 1.25, 1.5, 2].map((n) => (
                      <option key={n}>{n}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          )}
          {detail.quality?.map((w) => (
            <p key={w}>{w}</p>
          ))}
          {!reviewer && detail.status === "失败" && (
            <p>
              {detail.error}{" "}
              <button
                disabled={busy || !token}
                onClick={() =>
                  act(
                    () => mutate("/api/phone/calls/" + selected + "/retry"),
                    "已重新排队",
                  )
                }
              >
                重试
              </button>
            </p>
          )}
          {detail.roomConflict && (
            <p role="alert" className="inline-notice">
              分机映射为 {detail.room}，转写中提到{" "}
              {detail.mentionedRooms.join("、")}。请回听并填写实际服务房号。
            </p>
          )}
          <div className="ph-review-grid">
            <Transcript
              key={selected}
              segments={detail.segments}
              position={position}
              seek={seek}
              audio={audioAllowed}
            />
            <div className="ph-side">
              <nav className="ph-side-tabs" aria-label="通话复核">
                <button
                  aria-pressed={detailTab === "analysis"}
                  onClick={() => setDetailTab("analysis")}
                >
                  通话分析
                </button>
                <button
                  aria-pressed={detailTab === "items"}
                  onClick={() => setDetailTab("items")}
                >
                  事项处理（{detail.items.length}）
                </button>
              </nav>
              <div hidden={detailTab !== "analysis"}>
                {" "}
                <ReviewPanel
                  key={selected}
                  d={detail}
                  seek={seek}
                  save={mutate}
                  done={() => setVersion((v) => v + 1)}
                />
                {detail.status !== "已转写" && <p>转写完成后显示分析。</p>}
              </div>
              <div hidden={detailTab !== "items"}>
                <h3>事项复核</h3>
                {!detail.items.length && <p>暂无待处理事项。</p>}
                {detail.items.map((i) => (
                  <article key={i.id}>
                    <h4>
                      {i.title} <span>{i.department}</span>
                    </h4>
                    <button onClick={() => seek(i.start)}>
                      回听 · {i.quote}
                    </button>
                    <p>
                      {i.state}
                      {i.orderId
                        ? " · 工单 #" +
                          i.orderId +
                          " · " +
                          i.confirmedRoom +
                          " · " +
                          i.orderState
                        : ""}
                    </p>
                    {!reviewer && i.state === "待确认" && (
                      <Suggestions
                        item={i.id}
                        room={detail.roomConflict ? "" : detail.room}
                        save={mutate}
                        done={() => setVersion((v) => v + 1)}
                      />
                    )}
                    {!reviewer && i.state === "待确认" && (
                      <details>
                        <summary>已知工单编号，手动关联</summary>
                        <form
                          onSubmit={(e) => {
                            e.preventDefault();
                            const v = values(e.currentTarget);
                            void act(
                              () =>
                                mutate("/api/phone/items/" + i.id + "/link", {
                                  order: Number(v.order),
                                  note: v.note,
                                }),
                              "已关联工单",
                            );
                          }}
                        >
                          <Field
                            title="已有工单编号"
                            type="number"
                            name="order"
                          />
                          <Field title="关联原因" name="note" />
                          <button disabled={busy}>确认关联</button>
                        </form>
                      </details>
                    )}
                    {!reviewer && i.state === "待确认" && (
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          const v = values(e.currentTarget);
                          void act(
                            () =>
                              mutate(
                                "/api/phone/items/" + i.id + "/dispatch",
                                v,
                              ),
                            "已派单",
                          );
                        }}
                      >
                        <Field name="title" title="事项" value={i.title} />
                        <Field
                          name="room"
                          title="服务房号（上门事项必填）"
                          required={false}
                          value={detail.roomConflict ? "" : detail.room}
                        />
                        <Dept value={i.department} />
                        <Field
                          name="minutes"
                          type="number"
                          title="处理时限（分钟）"
                          value="30"
                        />
                        <button disabled={busy || !token}>确认派单</button>{" "}
                        <button
                          type="button"
                          disabled={busy || !token}
                          onClick={() =>
                            act(
                              () =>
                                mutate("/api/phone/items/" + i.id + "/dismiss"),
                              "已忽略",
                            )
                          }
                        >
                          忽略
                        </button>
                      </form>
                    )}
                  </article>
                ))}
                {!reviewer && (
                  <details>
                    <summary>根据回听手动补充事项</summary>
                    <form
                      onSubmit={(e) => {
                        e.preventDefault();
                        const v = values(e.currentTarget);
                        void act(
                          () =>
                            mutate(
                              "/api/phone/calls/" + selected + "/items",
                              v,
                            ),
                          "已添加待确认事项",
                        );
                      }}
                    >
                      <Field title="事项" name="title" />
                      <Dept />
                      <Field title="原话" name="quote" />
                      <Field
                        title="开始秒数"
                        type="number"
                        name="start"
                        value="0"
                      />
                      <button disabled={busy || !token}>保存事项</button>
                    </form>
                  </details>
                )}
              </div>
            </div>
          </div>
        </section>
      )}
      {["orders", "overdue", "handover"].includes(tab) && (
        <PhoneOrders
          key={tab}
          tab={tab}
          orders={orders}
          user={user}
          error={orderError}
          test={shiftScope}
          setTest={setShiftScope}
          change={change}
          open={open}
          refresh={() => setVersion((v) => v + 1)}
          busy={busy || !token}
          save={mutate}
          transition={(id, body) =>
            void act(
              () => mutate("/api/phone/orders/" + id, body),
              "工单已更新",
            )
          }
        />
      )}
    </section>
  );
}
