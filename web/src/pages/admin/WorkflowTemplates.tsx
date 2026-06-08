import { useEffect, useState } from "react";
import { api, Category, WorkflowTemplate } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

export function WorkflowTemplates() {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [cats, setCats] = useState<Category[]>([]);
  const [name, setName] = useState("");

  async function load() {
    setTemplates((await api.workflowTemplates()).templates);
  }
  useEffect(() => {
    load();
    api.categories().then((c) => setCats(c.categories));
  }, []);

  return (
    <div className="min-h-screen">
      <Masthead title="Workflows" />
      <div className="max-w-[900px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">Workflow templates</h1>
        <p className="text-muted text-[13px] mb-4">
          Define what a task looks like — e.g. a <b>new-business quote</b> touches{" "}
          <code>rating</code>, <code>carrier_portal</code>, and <code>ams</code> within a window.
          The dashboard then groups each producer's day into task instances (duration, tool-switches,
          re-opens). <b>Coaching signal only</b> — never a pass/fail. Tune against real data.
        </p>

        <div className="card mb-4 flex gap-2">
          <input className="flex-1 border border-border rounded px-3 py-2" placeholder="template name (e.g. new_business_quote)"
            value={name} onChange={(e) => setName(e.target.value)} />
          <button className="bg-ink text-white rounded px-4"
            onClick={async () => {
              if (!name.trim()) return;
              await api.saveWorkflowTemplate({ name: name.trim(), steps: [] });
              setName("");
              load();
            }}>
            Add template
          </button>
        </div>

        <div className="grid gap-3">
          {templates.map((t) => <TemplateCard key={t.id} t={t} cats={cats} onChange={load} />)}
          {templates.length === 0 && <p className="text-muted text-[13px]">No templates yet.</p>}
        </div>
      </div>
    </div>
  );
}

function TemplateCard({ t, cats, onChange }: { t: WorkflowTemplate; cats: Category[]; onChange: () => void }) {
  const [steps, setSteps] = useState<Set<string>>(new Set(t.steps.map((s) => s.sub_category)));
  const [mode, setMode] = useState(t.match_mode);
  const [windowMin, setWindowMin] = useState(Math.round(t.window_s / 60));
  const [expMin, setExpMin] = useState(t.expected_duration_s ? Math.round(t.expected_duration_s / 60) : "");

  function toggle(sub: string) {
    const s = new Set(steps);
    s.has(sub) ? s.delete(sub) : s.add(sub);
    setSteps(s);
  }

  async function save() {
    await api.saveWorkflowTemplate({
      name: t.name,
      match_mode: mode,
      window_s: windowMin * 60,
      expected_duration_s: expMin === "" ? null : Number(expMin) * 60,
      steps: [...steps].map((sub, i) => ({ sub_category: sub, required: true, step_order: i })),
    });
    onChange();
  }

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-serif font-bold text-[17px]">{t.name}</h3>
        <button className="text-danger text-[12px]"
          onClick={async () => { if (confirm("Delete template?")) { await api.deleteWorkflowTemplate(t.id); onChange(); } }}>
          Delete
        </button>
      </div>

      <div className="text-[12px] text-muted mb-1">Steps (categories that make up this task)</div>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {cats.map((c) => (
          <button key={c.sub_category} onClick={() => toggle(c.sub_category)}
            className={`text-[12px] px-2 py-1 rounded border ${
              steps.has(c.sub_category) ? "border-gold bg-gold/10 font-semibold" : "border-border text-muted"
            }`}>
            {c.sub_category}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 text-[13px]">
        <label className="flex items-center gap-1">mode
          <select className="border border-border rounded px-2 py-1" value={mode}
            onChange={(e) => setMode(e.target.value as WorkflowTemplate["match_mode"])}>
            <option value="set_within_window">set (any order)</option>
            <option value="sequence">sequence</option>
          </select>
        </label>
        <label className="flex items-center gap-1">window
          <input type="number" className="border border-border rounded px-2 py-1 w-16"
            value={windowMin} onChange={(e) => setWindowMin(Number(e.target.value))} />min
        </label>
        <label className="flex items-center gap-1">expected
          <input type="number" className="border border-border rounded px-2 py-1 w-16"
            value={expMin} onChange={(e) => setExpMin(e.target.value)} placeholder="—" />min
        </label>
        <button className="ml-auto bg-ink text-white rounded px-4 py-1.5 text-[13px]" onClick={save}>
          Save
        </button>
      </div>
    </div>
  );
}
