import { ArrowUpRight, ChevronRight, LockKeyhole, ClipboardList } from "lucide-react";
import { workAreas, WorkHeading, EmptyState } from "./Workspace";
import { useEffect, useState, type ReactNode, type FormEvent } from "react";

export type User = { name: string; role: string; department: string; phone?:{quality:boolean;audio:boolean;numbers:boolean;export:boolean} };
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok)
    throw Error(
      typeof data.detail === "string" ? data.detail : "操作失败，请核对输入",
    );
  return data;
}
export function Store({
  children,
}: {
  children: (user: User, store: boolean) => ReactNode;
}) {
  const [state, setState] = useState<{
    user: User | null;
    store: boolean;
  } | null>(null);
  const [message, setMessage] = useState("");
  const refresh = () =>
    request<{ user: User | null; store: boolean }>("api/session")
      .then(setState)
      .catch((e) => setMessage(e.message));
  useEffect(() => {
    void refresh();
  }, []);
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      await request("api/session/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      setMessage("");
      await refresh();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }
  if (state?.user) return <>{children(state.user, state.store)}</>;
  return (
    <main className="store-login">
      <section className="panel">
        <div className="login-mast"><span className="brand-mark inn-mark" aria-hidden="true"><i/><i/><i/></span><strong>栖鉴</strong><span>酒店智能巡检与服务分析</span></div>
        <div className="login-body"><WorkHeading title="门店登录" description="使用门店账号进入工作台。"/>
        {state ? (
          <form onSubmit={login}>
            <label>
              账号
              <input required name="name" autoComplete="username" />
            </label>
            <label>
              密码
              <input
                required
                name="password"
                type="password"
                autoComplete="current-password"
              />
            </label>
            <button className="primary">登录</button>
          </form>
        ) : (
          <button className="secondary" onClick={refresh}>连接本地服务</button>
        )}
        {message && <p className="work-notice" role="status">{message}</p>}</div>
      </section>
    </main>
  );
}

const metrics: Record<string, [string, string]> = {
  camera: ["待整改巡检", "vision-pending"],
  badge: ["待复核挂牌与胸牌录音", "records"],
  phone: ["待确认电话事项", "pending"],
  duty: ["待接手高风险预警","duty"],
  overdue: ["超时电话工单", "overdue"],
};
export function Overview({
  navigate,
  report, quality, phone, phoneReport,
}: {
  navigate: (page: string) => void;
  report: boolean; quality: boolean; phone: boolean; phoneReport: boolean;
}) {
  const [groups, setGroups] = useState<Record<string, Record<string, number>>>(
    {},
  );
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const refresh = () =>
      request<Record<string, number>>("api/overview")
        .then((v) => {
          if (active) {
            setCounts(v);
            setError("");
          }
        })
        .catch((e) => active && setError(e.message));
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  const [reportError, setReportError] = useState("");
  const [reportsReady, setReportsReady] = useState(false);
  useEffect(() => {
    let active = true;
    if (report)
      void request<Record<string, Record<string, number>>>("api/reports")
        .then(data => { if (active) { setGroups(data); setReportsReady(true); setReportError(""); } })
        .catch((e) => active && setReportError(e.message));
    return () => { active = false; };
  }, [report]);
  const areas = workAreas.filter(area => area.page === "phone" ? phone : quality);
  const measures = Object.entries(counts || {}).filter(([key]) => metrics[key]);
  return <div className="work-page">
    <WorkHeading title={report ? "运营报表" : "门店工作台"}
      description={report ? "查看各模块的归档数量与处理进度。" : "查看门店待办，进入对应业务处理。"}>
      {report && phoneReport && <button className="secondary" onClick={() => navigate("phone-report")}>电话运营分析<ArrowUpRight size={16}/></button>}
    </WorkHeading>
    <section className="work-queue">
      <header><ClipboardList size={18}/><h2>待处理事项</h2></header>
      {measures.length ? <div className="work-measures">{measures.map(([key, value]) =>
        <button key={key} onClick={() => navigate(metrics[key][1])}>
          <span>{metrics[key][0]}</span><strong>{value}<ChevronRight size={16}/></strong>
        </button>)}</div> : <p role="status">{error || (counts ? "暂无待处理事项" : "正在读取待办…")}</p>}
    </section>
    {error && measures.length > 0 && <p className="work-notice" role="alert">{error}</p>}
    {report ? <>
      <h2 className="work-section-title">模块统计</h2>
      {reportError && <p className="work-notice" role="alert">{reportError}</p>}
      <div className="work-reports">{Object.entries(groups).map(([group, entries]) => {
        const area = workAreas.find(a => a.label === group || (group === "胸牌语音" && a.page === "badge"));
        const Icon = area?.Icon || ClipboardList;
        const total = Object.values(entries).reduce((sum, n) => sum + n, 0);
        return <section key={group} className="work-card work-report">
          <header><span className="work-icon"><Icon size={19}/></span><h3>{area?.label || group}</h3><strong>{total} <small>条</small></strong></header>
          {total ? <dl>{Object.entries(entries).map(([label, n]) =>
            <div key={label}><dt>{label}</dt><dd><span aria-hidden="true"><i style={{width: (100*n/total)+"%"}}/></span>{n}</dd></div>)}</dl> :
            <p className="report-empty">暂无记录</p>}
        </section>;
      })}</div>
      {!Object.keys(groups).length && !reportError && <EmptyState title={reportsReady ? "暂无模块统计" : "正在读取报表…"}/>}
    </> : <>
      <h2 className="work-section-title">业务入口</h2>
      <div className="work-areas">{areas.map(({page, label, hint, Icon}) =>
        <button className="work-card work-area" key={page} onClick={() => navigate(page)}>
          <span className={'area-icon area-'+page}><Icon size={23}/></span><ArrowUpRight className="area-arrow" size={17}/>
          <strong>{label}</strong><small>{hint}</small>
        </button>)}</div>
    </>}
    {phone && <p className="work-footnote"><LockKeyhole size={14}/>电话指标仅统计营业通话，测试录音单独归档。</p>}
  </div>;
}
