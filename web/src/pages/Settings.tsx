import { useEffect, useState } from "react";
import { api, Mode, Settings as S } from "../lib/api";
import { Masthead } from "../components/Masthead";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function Settings() {
  const [s, setS] = useState<S | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getSettings().then(setS);
  }, []);

  if (!s) return <Shell><div className="text-muted">Loading…</div></Shell>;

  const days = new Set((s.work_days || "").split(",").filter(Boolean).map(Number));
  const set = (patch: Partial<S>) => setS({ ...s, ...patch } as S);

  async function save(patch: Partial<S>) {
    const next = await api.putSettings(patch);
    setS(next);
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  function toggleDay(i: number) {
    const d = new Set(days);
    d.has(i) ? d.delete(i) : d.add(i);
    set({ work_days: [...d].sort().join(",") });
  }

  return (
    <Shell mode={s.mode}>
      <div className="max-w-[640px]">
        <h1 className="font-serif text-2xl font-bold mb-4">Settings</h1>

        {/* MODE TOGGLE — the coaching/evaluative switch */}
        <div className="card mb-4">
          <h3 className="font-serif font-semibold mb-1">Presentation mode</h3>
          <p className="text-muted text-[13px] mb-3">
            Coaching compares people to their own baseline (no pass/fail). Evaluative compares
            calibrated roles to their target. Uncalibrated roles always show as coaching.
          </p>
          <div className="flex gap-2">
            {(["coaching", "evaluative"] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => save({ mode: m })}
                className={`flex-1 border rounded px-3 py-2 capitalize ${
                  s.mode === m ? "border-gold bg-gold/10 font-semibold" : "border-border"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        <div className="card mb-4">
          <h3 className="font-serif font-semibold mb-3">Work hours</h3>
          <div className="flex items-center gap-2 mb-3 text-[13px]">
            <span>From</span>
            <input
              type="number"
              className="w-16 border border-border rounded px-2 py-1"
              value={s.work_start}
              onChange={(e) => set({ work_start: e.target.value })}
            />
            <span>to</span>
            <input
              type="number"
              className="w-16 border border-border rounded px-2 py-1"
              value={s.work_end}
              onChange={(e) => set({ work_end: e.target.value })}
            />
            <span>(local hour)</span>
          </div>
          <div className="flex gap-1.5 mb-3">
            {DAYS.map((d, i) => (
              <button
                key={d}
                onClick={() => toggleDay(i)}
                className={`px-2.5 py-1 rounded text-[12px] border ${
                  days.has(i) ? "border-gold bg-gold/10 font-semibold" : "border-border text-muted"
                }`}
              >
                {d}
              </button>
            ))}
          </div>
          <button
            className="bg-ink text-white rounded px-4 py-1.5 text-[13px]"
            onClick={() => save({ work_start: s.work_start, work_end: s.work_end, work_days: s.work_days })}
          >
            Save work hours
          </button>
        </div>

        <div className="card mb-4">
          <h3 className="font-serif font-semibold mb-3">Organization</h3>
          <LabeledSave label="Organization name" value={s.org_name} onSave={(v) => save({ org_name: v })} />
          <LabeledSave
            label="Setup password (employees type this to enroll)"
            value={s.enroll_password}
            onSave={(v) => save({ enroll_password: v })}
          />
        </div>

        {saved && <div className="text-good text-[13px]">Saved.</div>}
      </div>
    </Shell>
  );
}

function LabeledSave({ label, value, onSave }: { label: string; value: string; onSave: (v: string) => void }) {
  const [v, setV] = useState(value);
  return (
    <div className="mb-3">
      <label className="block text-[12px] text-muted mb-1">{label}</label>
      <div className="flex gap-2">
        <input className="flex-1 border border-border rounded px-3 py-1.5" value={v} onChange={(e) => setV(e.target.value)} />
        <button className="bg-ink text-white rounded px-3 text-[13px]" onClick={() => onSave(v)}>
          Save
        </button>
      </div>
    </div>
  );
}

function Shell({ children, mode }: { children: React.ReactNode; mode?: Mode }) {
  return (
    <div className="min-h-screen">
      <Masthead title="Settings" mode={mode} />
      <div className="max-w-[1000px] mx-auto px-6 py-5">{children}</div>
    </div>
  );
}
