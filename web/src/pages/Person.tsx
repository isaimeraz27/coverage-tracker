import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, Person, localDay } from "../lib/api";
import { Masthead } from "../components/Masthead";
import { ScoreDonut } from "../components/ScoreDonut";

const h = (s: number) => `${(s / 3600).toFixed(1)}h`;
const pct = (x: number) => `${Math.round(x * 100)}%`;

const TL_COLORS = {
  productive: "#D4AF37",
  meeting: "#F4D77A",
  distracting: "rgba(176,0,32,.55)",
  idle: "#cdcdcd",
} as const;

export function PersonPage() {
  const { uid } = useParams();
  const [sp] = useSearchParams();
  const day = sp.get("day") || localDay();   // LOCAL date, not UTC
  const [data, setData] = useState<Person | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api
      .person(Number(uid), day)
      .then(setData)
      .catch((e) => setErr((e as Error).message));
  }, [uid, day]);

  if (err) return <Shell><div className="text-danger">{err}</div></Shell>;
  if (!data) return <Shell><div className="text-muted">Loading…</div></Shell>;

  const ins = data.insight;
  const coaching = ins.mode === "coaching" || !ins.calibrated;

  return (
    <Shell mode={ins.mode}>
      <Link to="/" className="text-gold font-semibold">‹ back to the floor</Link>

      <div className="flex items-center gap-4 mt-3 mb-2">
        <ScoreDonut
          score={ins.score}
          target={ins.target}
          verdict={ins.verdict}
          trend={ins.coaching?.trend ?? null}
          dim={ins.low_confidence}
          size={96}
        />
        <div>
          <div className="font-serif text-2xl font-bold">{data.person.name}</div>
          <div className="text-muted">
            {data.person.role || "no role"} · {day} · confidence {pct(ins.confidence)}
          </div>
          {coaching ? (
            <CoachingHeader ins={ins} />
          ) : (
            <div className="mt-1 text-[13px]">
              Target {Math.round(ins.target!)} ·{" "}
              <b className={ins.verdict === "pass" ? "text-good" : "text-danger"}>
                {ins.verdict === "pass" ? "meets target" : "below target"}
              </b>
            </div>
          )}
        </div>
      </div>

      {ins.low_confidence && (
        <Warn>
          <b>Insufficient data — not for evaluation.</b> Only {pct(ins.data_completeness)} of the work
          window was observed.
        </Warn>
      )}
      {!ins.low_confidence && ins.needs_context && (
        <Warn>
          This day <b>needs human context</b> — e.g. an offsite/phone meeting reads as idle. Ask before
          concluding.
        </Warn>
      )}

      {data.timeline.hours.length > 0 && <HourlyTimeline tl={data.timeline} />}

      <div className="card mt-4 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
        <span>On-task <b>{pct(ins.adherence)}</b></span>
        <span>Distraction <b>{pct(ins.distract_ratio)}</b></span>
        <span>Meeting <b>{h(ins.meeting_s)}</b></span>
        <span>Idle <b>{h(ins.idle_long_s)}</b></span>
        {data.breakdown[0] && (
          <span className="text-muted">
            Top: {data.breakdown[0].category.replace(/_/g, " ")} ({(data.breakdown[0].secs / 3600).toFixed(1)}h)
          </span>
        )}
      </div>

      <div className="grid md:grid-cols-2 gap-4 mt-4">
        <div className="card">
          <Row label="On-task (adherence)" value={pct(ins.adherence)} />
          <Row label="Distraction" value={pct(ins.distract_ratio)} />
          <Row label="Focus quality" value={pct(ins.focus_quality)} />
          <Row label="Engagement (cursor+keys)" value={pct(ins.engagement)} note="context, not score" />
          <Row label="Present" value={h(ins.present_s)} />
          <Row label="Meeting" value={h(ins.meeting_s)} />
          <Row label="Idle (long)" value={h(ins.idle_long_s)} />
        </div>
        <CategoryBreakdown key={day} breakdown={data.breakdown} onTask={data.on_task_set} />
      </div>

      <div className="card mt-4">
        <h3 className="font-serif font-semibold mb-2">Flags &amp; insights</h3>
        <div className="flex flex-wrap gap-2">
          {ins.flags.length === 0 && <span className="text-muted text-[13px]">no flags</span>}
          {ins.flags.map((f, i) => (
            <span
              key={i}
              className={`text-[12px] px-2 py-1 rounded ${
                f.positive
                  ? "bg-good/10 text-good"
                  : f.severity === "high"
                  ? "bg-danger/10 text-danger"
                  : f.severity === "info"
                  ? "bg-[#eee] text-muted"
                  : "bg-gold/15 text-ink"
              }`}
            >
              {f.message}
            </span>
          ))}
        </div>
      </div>

    </Shell>
  );
}

function CoachingHeader({ ins }: { ins: Person["insight"] }) {
  const c = ins.coaching;
  if (!c || c.trend == null)
    return <div className="mt-1 text-[13px] text-muted">No baseline yet — building history.</div>;
  const up = c.trend > 0.5;
  const dn = c.trend < -0.5;
  return (
    <div className="mt-1 text-[13px]">
      <b className={up ? "text-good" : dn ? "text-danger" : "text-muted"}>
        {up ? "▲" : dn ? "▼" : "•"} {c.trend > 0 ? "+" : ""}
        {Math.round(c.trend)}
      </b>{" "}
      vs your {c.baseline_days}-day usual ({Math.round(c.baseline ?? 0)})
    </div>
  );
}

function Shell({ children, mode }: { children: React.ReactNode; mode?: Person["insight"]["mode"] }) {
  return (
    <div className="min-h-screen">
      <Masthead title="Person" mode={mode} />
      <div className="max-w-[1000px] mx-auto px-6 py-5">{children}</div>
    </div>
  );
}

function Row({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex justify-between py-1 text-[13px]">
      <span>{label}</span>
      <b>
        {value} {note && <span className="text-muted text-[11px] font-normal">{note}</span>}
      </b>
    </div>
  );
}

function Warn({ children }: { children: React.ReactNode }) {
  return <div className="bg-gold-l/20 border border-gold rounded px-4 py-2 my-3 text-[13px]">{children}</div>;
}

function HourlyTimeline({ tl }: { tl: Person["timeline"] }) {
  const ampm = (n: number) =>
    n === 0 ? "12a" : n < 12 ? `${n}a` : n === 12 ? "12p" : `${n - 12}p`;
  const max = 3600;
  const seg = (s: number, c: string, key: string) =>
    s > 0 ? <div key={key} style={{ height: `${(s / max) * 100}%`, background: c }} /> : null;
  return (
    <div className="card mt-4">
      <h3 className="font-serif font-semibold mb-2">Activity by hour</h3>
      <div className="flex items-end gap-1 h-28">
        {tl.hours.map((hr) => {
          const total = hr.productive_s + hr.meeting_s + hr.distracting_s + hr.idle_s;
          return (
            <div key={hr.hour} className="flex-1 flex flex-col items-center justify-end h-full">
              <div
                className="w-full flex flex-col-reverse rounded-sm overflow-hidden bg-[#f4f4f4] h-full"
                title={`${ampm(hr.hour)} — ${Math.round(total / 60)}m active`}
              >
                {seg(hr.productive_s, TL_COLORS.productive, "p")}
                {seg(hr.meeting_s, TL_COLORS.meeting, "m")}
                {seg(hr.distracting_s, TL_COLORS.distracting, "d")}
                {seg(hr.idle_s, TL_COLORS.idle, "i")}
              </div>
              <span className="text-[10px] text-muted mt-1">{ampm(hr.hour)}</span>
            </div>
          );
        })}
      </div>
      <div className="flex gap-3 mt-2 text-[11px] text-muted">
        <Legend c={TL_COLORS.productive} t="productive" />
        <Legend c={TL_COLORS.meeting} t="meeting" />
        <Legend c={TL_COLORS.distracting} t="distracting" />
        <Legend c={TL_COLORS.idle} t="idle" />
      </div>
    </div>
  );
}

function Legend({ c, t }: { c: string; t: string }) {
  return (
    <span className="flex items-center gap-1">
      <span className="h-2 w-2 rounded-full" style={{ background: c }} /> {t}
    </span>
  );
}

function CategoryBreakdown({
  breakdown, onTask,
}: { breakdown: Person["breakdown"]; onTask: string[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const pretty = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
  const maxSecs = breakdown[0]?.secs || 1;
  return (
    <div className="card">
      <h3 className="font-serif font-semibold mb-2">Where the hours went</h3>
      {breakdown.length === 0 && <div className="text-muted text-[13px]">no activity</div>}
      {breakdown.map((c) => {
        const on = onTask.includes(c.category);
        const isOpen = open.has(c.category);
        return (
          <div key={c.category} className="mb-1.5">
            <button
              className="w-full flex items-center gap-2 text-[13px] text-left"
              onClick={() =>
                setOpen((p) => {
                  const n = new Set(p);
                  if (n.has(c.category)) n.delete(c.category);
                  else n.add(c.category);
                  return n;
                })
              }
            >
              <span className="text-muted w-3">{isOpen ? "▾" : "▸"}</span>
              <span className="h-2 w-2 rounded-full" style={{ background: on ? "#D4AF37" : "#cfcfcf" }} />
              <span className="flex-1">
                {pretty(c.category)} <span className="text-muted">{on ? "· expected" : "· not in role"}</span>
              </span>
              <span className="w-[120px]">
                <span className="inline-block h-2 rounded"
                  style={{ width: `${Math.min(100, (c.secs / maxSecs) * 100)}%`, background: on ? "#D4AF37" : "#cfcfcf" }} />
              </span>
              <b className="w-[46px] text-right">{h(c.secs)}</b>
            </button>
            {isOpen && (
              <div className="ml-7 mt-1 mb-2">
                {c.children.map((ch) => (
                  <div key={ch.label} className="flex items-center gap-2 text-[12px] text-muted py-0.5">
                    <span className="flex-1 truncate" title={ch.label}>{ch.label}</span>
                    <span className="text-[10px] px-1 rounded bg-[#eee]">{ch.kind}</span>
                    <b className="w-[46px] text-right text-ink">{h(ch.secs)}</b>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
