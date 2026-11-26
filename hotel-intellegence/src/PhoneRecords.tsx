import { useState } from "react";
import type { ReviewDetail } from "./PhoneWorkbench";

export type CallRecord = {
  id: string;
  caller: string;
  callee: string;
  started: string;
  room: string;
  direction: string;
  status: string;
  test: number;
  filename: string;
  category: string;
  summary: string;
  review_state: string;
  level?: string;
  alert_state?: string;
  favorite: boolean | number;
};

function two(n: number) {
  return String(n).padStart(2, "0");
}
export function clockLabel(seconds: number) {
  const n = Math.max(0, Math.floor(seconds));
  return two(Math.floor(n / 60)) + ":" + two(n % 60);
}

function localDay(d: Date) {
  return [d.getFullYear(), two(d.getMonth() + 1), two(d.getDate())].join("-");
}
export function DatePresets({
  since,
  until,
  choose,
}: {
  since: string;
  until: string;
  choose: (from: string, to: string) => void;
}) {
  const today = new Date();
  return (
    <div className="ph-presets" aria-label="快捷日期">
      {[
        ["今天", 1],
        ["近7天", 7],
        ["近30天", 30],
      ].map(([label, period]) => {
        const start = new Date(today);
        start.setDate(start.getDate() - Number(period) + 1);
        const from = localDay(start),
          to = localDay(today);
        return (
          <button
            type="button"
            key={label}
            aria-pressed={since === from && until === to}
            onClick={() => choose(from, to)}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function CallRegister({
  rows,
  open,
  favorite,
  busy,
}: {
  rows: CallRecord[];
  open: (id: string) => void;
  favorite: (call: CallRecord) => void;
  busy: boolean;
}) {
  return (
    <div className="ph-table-wrap">
      <table className="ph-register">
        <thead>
          <tr>
            <th>通话时间</th>
            <th>主叫 / 被叫</th>
            <th>分类与摘要</th>
            <th>处理状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id}>
              <td>
                <time dateTime={c.started}>
                  <strong>{c.started.slice(11, 19)}</strong>
                  <small>{c.started.slice(0, 10)}</small>
                </time>
              </td>
              <td>
                <button className="ph-link" onClick={() => open(c.id)}>
                  {c.caller} → {c.callee}
                </button>
                <small>
                  {c.room ? "房间 " + c.room : "房间待核实"} ·{" "}
                  {c.direction === "待确认" ? "方向待确认" : c.direction}
                </small>
              </td>
              <td>
                <div className="ph-tags">
                  <span className="ph-tag" data-kind={c.category}>
                    {c.category || "待分析"}
                  </span>
                  {c.level && (
                    <span className="ph-risk" data-level={c.level}>
                      {c.level} 级
                    </span>
                  )}
                </div>
                <p>
                  {c.summary ||
                    (c.status === "已转写" ? "暂无事项摘要" : "等待转写结果")}
                </p>
              </td>
              <td>
                <b className="ph-state">{c.alert_state || c.review_state}</b>
                <small>
                  {c.status} · {c.test ? "测试" : "营业"}
                </small>
              </td>
              <td>
                <div className="ph-actions">
                  <button className="ph-link" onClick={() => open(c.id)}>
                    查看详情
                  </button>
                  <button
                    className="ph-star"
                    aria-label={c.favorite ? "取消收藏" : "收藏通话"}
                    aria-pressed={!!c.favorite}
                    disabled={busy}
                    onClick={() => favorite(c)}
                  >
                    {c.favorite ? "★" : "☆"}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Transcript({
  segments,
  position,
  seek,
  audio,
}: {
  segments: ReviewDetail["segments"];
  position: number;
  seek: (s: number) => void;
  audio: boolean;
}) {
  const [query, search] = useState("");
  const term = query.trim().toLocaleLowerCase();
  const matches = segments.filter((s) =>
    s.text.toLocaleLowerCase().includes(term),
  );
  return (
    <section className="ph-transcript">
      <header>
        <h3>逐段转写</h3>
        <span>{matches.length} 段</span>
      </header>
      <input
        type="search"
        aria-label="搜索本通原话"
        placeholder="搜索本通原话"
        value={query}
        onChange={(e) => search(e.target.value)}
      />
      <div className="ph-turns">
        {matches.map((s) => {
          const active = s.start <= position && position < s.end;
          const i = term ? s.text.toLocaleLowerCase().indexOf(term) : -1;
          return (
            <div
              key={s.id}
              className={active ? "ph-turn is-current" : "ph-turn"}
            >
              <div>
                <strong>{s.speaker || "说话人未区分"}</strong>
                <button
                  type="button"
                  disabled={!audio}
                  aria-label={"从 " + clockLabel(s.start) + " 回听"}
                  onClick={() => seek(s.start)}
                >
                  {clockLabel(s.start)} ▷
                </button>
              </div>
              <p>
                {i < 0 ? (
                  s.text
                ) : (
                  <>
                    {s.text.slice(0, i)}
                    <mark>{s.text.slice(i, i + term.length)}</mark>
                    {s.text.slice(i + term.length)}
                  </>
                )}
              </p>
            </div>
          );
        })}
        {!matches.length && (
          <p className="muted">
            {segments.length ? "没有包含该关键词的片段" : "暂无转写片段"}
          </p>
        )}
      </div>
    </section>
  );
}
