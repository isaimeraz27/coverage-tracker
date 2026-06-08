import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, Person } from "../lib/api";
import { Masthead } from "../components/Masthead";
import { ScoreDonut } from "../components/ScoreDonut";

const h = (s: number) => `${(s / 3600).toFixed(1)}h`;
const pct = (x: number) => `${Math.round(x * 100)}%`;

export function PersonPage() {
  const { uid } = useParams();
  const [sp] = useSearchParams();
  const day = sp.get("day") || new Date().toISOString().slice(0, 10);
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

      {data.timeline.length > 0 && (
        <div className="card mt-4">
          <h3 className="font-serif font-semibold mb-2">Day timeline</h3>
          <div className="relative h-6 bg-[#f1f1f1] rounded overflow-hidden">
            {data.timeline.map((s, i) => (
              <span
                key={i}
                title={s.t}
                className="absolute top-0 h-full"
                style={{ left: `${s.l}%`, width: `${Math.max(s.w, 0.4)}%`, background: s.c }}
              />
            ))}
          </div>
          <div className="flex justify-between text-[11px] text-muted mt-1">
            <span>8a</span><span>10a</span><span>12p</span><span>2p</span><span>4p</span><span>6p</span>
          </div>
        </div>
      )}

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
        <div className="card">
          <h3 className="font-serif font-semibold mb-2">Where the hours went</h3>
          {data.top.length === 0 && <div className="text-muted text-[13px]">no activity</div>}
          {data.top.map((t) => {
            const on = data.on_task_set.includes(t.sub);
            const max = data.top[0]?.secs || 1;
            return (
              <div key={t.sub} className="flex items-center gap-2 mb-1.5 text-[13px]">
                <span className="h-2 w-2 rounded-full" style={{ background: on ? "#D4AF37" : "#cfcfcf" }} />
                <span className="flex-1">
                  {t.sub} <span className="text-muted">{on ? "· expected" : "· not in role"}</span>
                </span>
                <span className="w-[120px]">
                  <span
                    className="inline-block h-2 rounded"
                    style={{ width: `${Math.min(100, (t.secs / max) * 100)}%`, background: on ? "#D4AF37" : "#cfcfcf" }}
                  />
                </span>
                <b className="w-[46px] text-right">{(t.secs / 3600).toFixed(1)}h</b>
              </div>
            );
          })}
        </div>
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

      <TasksCard tasks={data.tasks} />
    </Shell>
  );
}

function TasksCard({ tasks }: { tasks: Person["tasks"] }) {
  if (!tasks || tasks.length === 0) return null;
  const mins = (s: number) => `${Math.round(s / 60)}m`;
  return (
    <div className="card mt-4">
      <h3 className="font-serif font-semibold mb-1">Tasks today</h3>
      <p className="text-muted text-[12px] mb-3">
        Detected workflows — a coaching signal for training conversations, not an evaluation.
      </p>
      <div className="grid gap-2">
        {tasks.map((t, i) => (
          <div key={i} className="border border-border rounded p-3">
            <div className="flex items-center gap-2 mb-1">
              <b className="text-[14px]">{t.template.replace(/_/g, " ")}</b>
              {!t.matched && (
                <span className="text-[11px] px-2 py-0.5 rounded bg-gold/15">
                  incomplete{t.steps_missing.length ? ` — missing ${t.steps_missing.join(", ")}` : ""}
                </span>
              )}
              <span className="ml-auto text-[13px] text-muted">
                {mins(t.duration_s)}
                {t.vs_expected != null && (
                  <> · {t.vs_expected > 1 ? "+" : ""}{Math.round((t.vs_expected - 1) * 100)}% vs expected</>
                )}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1 text-[12px] text-muted">
              {t.steps_hit.map((s, j) => (
                <span key={j} className="flex items-center gap-1">
                  {j > 0 && <span>→</span>}
                  <span className="px-1.5 py-0.5 rounded bg-gold/10 text-ink">{s}</span>
                </span>
              ))}
              <span className="ml-2">· {t.tool_switches} tool-switches · {Math.round(t.on_task_ratio * 100)}% on-task</span>
            </div>
          </div>
        ))}
      </div>
    </div>
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
