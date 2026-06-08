import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, Card, Overview as OverviewData } from "../lib/api";
import { Masthead } from "../components/Masthead";
import { ScoreDonut } from "../components/ScoreDonut";

const today = () => new Date().toISOString().slice(0, 10);
const shiftDay = (d: string, n: number) => {
  const dt = new Date(d + "T00:00:00");
  dt.setDate(dt.getDate() + n);
  return dt.toISOString().slice(0, 10);
};

export function Overview() {
  const [day, setDay] = useState(today());
  const [data, setData] = useState<OverviewData | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api.overview(day));
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [day]);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000); // live-ish refresh, mirrors the old 20s poll
    return () => clearInterval(t);
  }, [load]);

  const mode = data?.team.mode;
  const coaching = mode === "coaching";

  return (
    <div className="min-h-screen">
      <Masthead title="The Floor" mode={mode} right={<span className="live-dot" />} />
      <div className="max-w-[1100px] mx-auto px-6 py-5">
        {/* day nav */}
        <div className="flex items-center gap-3 mb-4">
          <button className="text-gold font-semibold" onClick={() => setDay(shiftDay(day, -1))}>
            ‹ prev
          </button>
          <span className="font-serif text-lg">{day}</span>
          <button
            className="text-gold font-semibold disabled:opacity-30"
            onClick={() => setDay(shiftDay(day, 1))}
            disabled={day >= today()}
          >
            next ›
          </button>
        </div>

        {err && <div className="text-danger mb-4">{err}</div>}
        {data && <TeamStrip data={data} />}

        {/* coaching banner */}
        <div className="bg-gold-l/20 border border-gold rounded px-4 py-2 my-4 text-[13px]">
          {coaching ? (
            <>
              <b>Coaching view.</b> People are compared to their own recent baseline — these are
              conversation starters, not verdicts. Check confidence and context before acting.
            </>
          ) : (
            <>
              <b>Evaluative view.</b> Calibrated roles are compared to their target. Uncalibrated
              roles still show as coaching. Metrics still need human context.
            </>
          )}
        </div>

        {data && (
          <div className="grid gap-6">
            <Lane
              title={coaching ? "Worth a check-in" : "Needs a look"}
              hint={
                coaching
                  ? "Something moved or a compound signal fired — go ask, don't conclude."
                  : "A compound signal fired (distraction + idle + persistence)."
              }
              cards={data.lanes.needs}
            />
            <Lane title="On track" hint="Steady against their own pattern." cards={data.lanes.ontrack} />
            <Lane
              title="Not enough signal"
              hint="Too little observed time to evaluate — not for evaluation."
              cards={data.lanes.lowconf}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function TeamStrip({ data }: { data: OverviewData }) {
  const t = data.team;
  return (
    <div className="grid grid-cols-4 gap-3">
      <Kpi label="On-task (avg)" value={`${Math.round(t.on_task_pct)}%`} />
      <Kpi label={t.mode === "coaching" ? "Check-ins" : "Below"} value={String(t.n_needs)} />
      <Kpi label="On track" value={String(t.n_ontrack)} />
      <Kpi label="Avg confidence" value={`${Math.round(t.conf * 100)}%`} />
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="font-serif text-[22px] font-bold">{value}</div>
      <div className="text-muted text-[11px] uppercase tracking-wide">{label}</div>
    </div>
  );
}

function Lane({ title, hint, cards }: { title: string; hint: string; cards: Card[] }) {
  return (
    <section>
      <h2 className="font-serif font-semibold text-base mb-0.5">{title}</h2>
      <p className="text-muted text-[12px] mb-3">{hint}</p>
      {cards.length === 0 ? (
        <p className="text-muted text-[13px]">— none —</p>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {cards.map((c) => (
            <Orb key={c.uid} c={c} />
          ))}
        </div>
      )}
    </section>
  );
}

function Orb({ c }: { c: Card }) {
  return (
    <Link
      to={`/person/${c.uid}`}
      className={`card no-underline text-ink flex gap-3 items-center hover:shadow ${
        c.persist ? "border-danger" : ""
      }`}
    >
      <ScoreDonut
        score={c.score}
        target={c.target}
        verdict={c.verdict}
        trend={c.coaching?.trend ?? null}
        dim={c.low_conf}
      />
      <div className="min-w-0">
        <div className="font-serif font-bold text-[16px] flex items-center gap-2">
          {c.name}
          {c.active && <span className="live-dot" title="active" />}
        </div>
        <div className="text-muted text-[12px]">{c.role || "no role"}</div>
        <Caption c={c} />
      </div>
    </Link>
  );
}

function Caption({ c }: { c: Card }) {
  if (c.low_conf)
    return <div className="text-[12px] text-muted mt-1">insufficient data — not for evaluation</div>;
  if (c.coaching?.attention_framing)
    return <div className="text-[12px] font-semibold mt-1">{c.coaching.attention_framing}</div>;
  const pos = c.flags.find((f) => f.positive);
  if (pos) return <div className="text-[12px] text-good mt-1">{pos.message}</div>;
  const bad = c.flags.find((f) => f.severity === "high" || f.severity === "med");
  if (bad) return <div className="text-[12px] text-muted mt-1">{bad.message}</div>;
  return <div className="text-[12px] text-muted mt-1">on task</div>;
}
