import { useEffect, useState } from "react";
import { api, Category, Role } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

export function Roles() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [cats, setCats] = useState<Category[]>([]);
  const [newName, setNewName] = useState("");

  async function load() {
    setRoles((await api.roles()).roles);
  }
  useEffect(() => {
    load();
    api.categories().then((c) => setCats(c.categories));
  }, []);

  async function create() {
    if (!newName.trim()) return;
    await api.createRole({ name: newName.trim(), on_task_set: [] });
    setNewName("");
    load();
  }

  return (
    <div className="min-h-screen">
      <Masthead title="Roles" />
      <div className="max-w-[900px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">Roles &amp; calibration</h1>
        <p className="text-muted text-[13px] mb-4">
          Pick the categories that count as on-task for each role. Setting a <b>target score</b>{" "}
          calibrates the role — only then can it show pass/fail in evaluative mode. Leave it empty to
          keep the role in coaching.
        </p>

        <div className="card mb-4 flex gap-2">
          <input
            className="flex-1 border border-border rounded px-3 py-2"
            placeholder="New role name (e.g. developer)"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <button className="bg-ink text-white rounded px-4" onClick={create}>
            Add role
          </button>
        </div>

        <div className="grid gap-3">
          {roles.map((r) => (
            <RoleCard key={r.id} role={r} cats={cats} onChange={load} />
          ))}
          {roles.length === 0 && <p className="text-muted text-[13px]">No roles yet.</p>}
        </div>
      </div>
    </div>
  );
}

function RoleCard({ role, cats, onChange }: { role: Role; cats: Category[]; onChange: () => void }) {
  const [onTask, setOnTask] = useState<Set<string>>(new Set(role.on_task_set));
  const [target, setTarget] = useState(role.target_score?.toString() ?? "");
  const [busy, setBusy] = useState(false);

  function toggle(sub: string) {
    const s = new Set(onTask);
    s.has(sub) ? s.delete(sub) : s.add(sub);
    setOnTask(s);
  }

  async function save() {
    setBusy(true);
    await api.updateRole(role.id, {
      on_task_set: [...onTask],
      target_score: target.trim() === "" ? null : Number(target),
    });
    setBusy(false);
    onChange();
  }

  // group categories by coarse class for the picker
  const byClass: Record<string, Category[]> = {};
  for (const c of cats) (byClass[c.coarse_class] ||= []).push(c);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-serif font-bold text-[17px]">{role.name}</h3>
        <span
          className={`text-[11px] px-2 py-0.5 rounded ${
            role.calibrated ? "bg-good/10 text-good" : "bg-gold/15 text-ink"
          }`}
        >
          {role.calibrated ? "calibrated" : "coaching (uncalibrated)"}
        </span>
      </div>

      <div className="text-[12px] text-muted mb-1">On-task categories</div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {Object.entries(byClass).map(([cls, list]) =>
          list.map((c) => (
            <button
              key={c.sub_category}
              onClick={() => toggle(c.sub_category)}
              title={cls}
              className={`text-[12px] px-2 py-1 rounded border ${
                onTask.has(c.sub_category) ? "border-gold bg-gold/10 font-semibold" : "border-border text-muted"
              }`}
            >
              {c.sub_category}
            </button>
          ))
        )}
      </div>

      <div className="flex items-center gap-2">
        <label className="text-[13px]">Target score (calibration):</label>
        <input
          className="w-24 border border-border rounded px-2 py-1"
          placeholder="none"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        />
        <span className="text-[12px] text-muted">empty = coaching only</span>
        <button disabled={busy} className="ml-auto bg-ink text-white rounded px-4 py-1.5 text-[13px]" onClick={save}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
