import { useEffect, useState } from "react";
import { DatePresets } from "./PhoneRecords";
import { request } from "./Store";

type Metric = {
  value: number | null;
  previous: number | null;
  unit: string;
  numerator: number;
  denominator: number;
};
type Report = {
  metrics: Record<string, Metric>;
  groups: Record<string, number>;
  previousSince: string;
  previousUntil: string;
};
type Observation = {
  id: string;
  date: string;
  room: string;
  unit: string;
  value: number;
};
function localDate(d: Date) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 10);
}
const definitions: Record<string, string> = {
  录音量: "有效且已去重的通话",
  分析覆盖率: "完成分析的通话 / 有效录音",
  确认客诉率: "已确认客诉的通话 / 完成分析的通话",
  待复核: "已完成分析且等待人工复核的通话",
  未读高优预警: "尚未阅读的 S 或 A 级有效预警",
  预警查看率: "已阅读预警 / 期间产生的有效预警",
  首次查看时长: "已读预警的首次阅读平均耗时",
  一次解决率: "一次解决的需求 / 已复核完成且观察满 24 小时的可核实需求",
  工单超时率: "超时工单 / 已到期或完成的工单",
  平均服务时长: "已复核工单从派单到完成的平均时长",
};
export function PhoneReports({
  open,
  canExport,
  visible = true,
}: {
  open: (key: string) => void;
  canExport: boolean;
  visible?: boolean;
}) {
  const [since, start] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 6);
    return localDate(d);
  });
  const [until, end] = useState(() => localDate(new Date())),
    [test, scope] = useState(false),
    [metric, drill] = useState("");
  const [data, show] = useState<Report | null>(null),
    [rows, list] = useState<Observation[]>([]),
    [error, fail] = useState("");
  const params = new URLSearchParams({
    since,
    until,
    test: String(test),
  }).toString();
  useEffect(() => {
    if (!visible) return;
    let active = true;
    show(null);
    list([]);
    fail("");
    const refresh = async () => {
      try {
        const [r, details] = await Promise.all([
          request<Report>("/api/phone/reports?" + params),
          metric
            ? request<{ rows: Observation[] }>(
                "/api/phone/reports?" +
                  params +
                  "&metric=" +
                  encodeURIComponent(metric),
              )
            : Promise.resolve({ rows: [] }),
        ]);
        if (active) {
          show(r);
          list(details.rows);
          fail("");
        }
      } catch (e) {
        if (active) fail((e as Error).message);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [params, metric, visible]);
  return (
    <section className="phone-report panel" hidden={!visible}>
      <h2>电话运营分析</h2>
      <div className="phone-fields">
        <label>
          数据范围
          <select
            value={String(test)}
            onChange={(e) => scope(e.target.value === "true")}
          >
            <option value="false">营业</option>
            <option value="true">测试</option>
          </select>
        </label>
        <label>
          开始日期
          <input
            type="date"
            value={since}
            onChange={(e) => start(e.target.value)}
          />
        </label>
        <label>
          结束日期
          <input
            type="date"
            value={until}
            onChange={(e) => end(e.target.value)}
          />
        </label>
      </div>
      <DatePresets
        since={since}
        until={until}
        choose={(from, to) => {
          start(from);
          end(to);
        }}
      />
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p>正在读取报表…</p>}
      {data && (
        <>
          <p className="muted">
            比较周期 {data.previousSince} 至 {data.previousUntil}
          </p>
          <div className="phone-kpis">
            {Object.entries(data.metrics).map(([k, v]) => (
              <button
                key={k}
                aria-pressed={metric === k}
                onClick={() => drill(k)}
              >
                <span>{k}</span>
                <strong>{v.value === null ? "—" : v.value + v.unit}</strong>
                <small>
                  {v.denominator} 条明细 · 上期{" "}
                  {v.previous === null ? "—" : v.previous + v.unit}
                </small>
              </button>
            ))}
          </div>
          {["日期", "时段", "处理状态", "分类", "等级", "房间", "问题"].map(
            (g) => {
              const entries = Object.entries(data.groups)
                .filter(([k]) => k.startsWith(g + " · "))
                .sort((a, b) =>
                  ["日期", "时段"].includes(g)
                    ? a[0].localeCompare(b[0])
                    : b[1] - a[1],
                );
              return (
                entries.length > 0 && (
                  <details key={g} open={g === "问题" || undefined}>
                    <summary>{g}分布</summary>
                    <div className="phone-options">
                      {entries.map(([k, v]) => (
                        <button key={k} onClick={() => drill(k)}>
                          {k.slice(g.length + 3)} · {v}
                        </button>
                      ))}
                    </div>
                  </details>
                )
              );
            },
          )}
          {["观察中需求", "无法核实需求"]
            .filter((k) => data.groups[k])
            .map((k) => (
              <button key={k} onClick={() => drill(k)}>
                {k} · {data.groups[k]}
              </button>
            ))}
          {metric && (
            <section>
              <h3>{metric}明细</h3>
              <p>
                {definitions[metric] || "按选定条件统计"} · 共 {rows.length} 条
              </p>
              {canExport && (
                <a
                  href={
                    "/api/phone/reports/export?" +
                    params +
                    "&metric=" +
                    encodeURIComponent(metric)
                  }
                >
                  导出本组明细 CSV
                </a>
              )}
              {rows.map((r, i) => (
                <button
                  key={r.id + "-" + i}
                  className="phone-row"
                  onClick={() => open(r.id)}
                >
                  <b>
                    {r.room || "房间未知"} · {r.unit}
                  </b>
                  <span>
                    {r.date.replace("T", " ")} · 贡献值 {r.value}
                  </span>
                </button>
              ))}
            </section>
          )}
        </>
      )}
    </section>
  );
}
