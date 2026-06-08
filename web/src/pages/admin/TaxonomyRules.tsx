import { useEffect, useState } from "react";
import { api, Category, TaxonomyRule, TaxonomyTestResult } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

const MATCH_TYPES = ["app", "domain", "url_path", "title"] as const;

export function TaxonomyRules() {
  const [rules, setRules] = useState<TaxonomyRule[]>([]);
  const [cats, setCats] = useState<Category[]>([]);

  async function load() {
    setRules((await api.taxonomyRules()).rules);
  }
  useEffect(() => {
    load();
    api.categories().then((c) => setCats(c.categories));
  }, []);

  return (
    <div className="min-h-screen">
      <Masthead title="Taxonomy" />
      <div className="max-w-[940px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">Activity taxonomy</h1>
        <p className="text-muted text-[13px] mb-4">
          Map your agency's apps, carrier portals, raters, AMS, and dialer to categories. Rules
          apply <b>server-side and retroactively</b> — editing one reclassifies past activity too.
          Use <code>url_path</code> to tag a <i>stage</i> within one tool, e.g.{" "}
          <code>app.ezlynx.com/quotes/*/rating</code>. Lower priority wins.
        </p>

        <Tester />

        <div className="flex items-center justify-between mt-6 mb-2">
          <h2 className="font-serif font-semibold text-base">Rules</h2>
          <button
            className="bg-ink text-white rounded px-3 py-1.5 text-[13px]"
            onClick={async () => {
              await api.saveTaxonomyRule({
                match_type: "domain", pattern: "example.com", sub_category: cats[0]?.sub_category || "work_comms",
                priority: 100,
              });
              load();
            }}
          >
            + Add rule
          </button>
        </div>

        <div className="grid gap-2">
          {rules.map((r) => (
            <RuleRow key={r.id} rule={r} cats={cats} onChange={load} />
          ))}
        </div>
      </div>
    </div>
  );
}

function RuleRow({ rule, cats, onChange }: { rule: TaxonomyRule; cats: Category[]; onChange: () => void }) {
  const [r, setR] = useState(rule);
  const dirty = JSON.stringify(r) !== JSON.stringify(rule);
  const up = (k: keyof TaxonomyRule, v: unknown) => setR({ ...r, [k]: v } as TaxonomyRule);

  return (
    <div className="card flex flex-wrap items-center gap-2 text-[13px]">
      <select className="border border-border rounded px-2 py-1" value={r.match_type}
        onChange={(e) => up("match_type", e.target.value)}>
        {MATCH_TYPES.map((m) => <option key={m} value={m}>{m}</option>)}
      </select>
      <input className="border border-border rounded px-2 py-1 flex-1 min-w-[180px] font-mono text-[12px]"
        value={r.pattern} onChange={(e) => up("pattern", e.target.value)} placeholder="pattern" />
      <span className="text-muted">→</span>
      <input className="border border-border rounded px-2 py-1 w-[140px]" list="cats"
        value={r.sub_category} onChange={(e) => up("sub_category", e.target.value)} placeholder="category" />
      <datalist id="cats">{cats.map((c) => <option key={c.sub_category} value={c.sub_category} />)}</datalist>
      <label className="flex items-center gap-1 text-[12px] text-muted">
        <input type="checkbox" checked={!!r.is_meeting} onChange={(e) => up("is_meeting", e.target.checked ? 1 : 0)} />
        meeting
      </label>
      <label className="flex items-center gap-1 text-[12px] text-muted">
        pri
        <input type="number" className="border border-border rounded px-1 py-1 w-14"
          value={r.priority} onChange={(e) => up("priority", Number(e.target.value))} />
      </label>
      <label className="flex items-center gap-1 text-[12px] text-muted">
        <input type="checkbox" checked={!!r.enabled} onChange={(e) => up("enabled", e.target.checked ? 1 : 0)} />
        on
      </label>
      <button
        disabled={!dirty}
        className="bg-ink text-white rounded px-3 py-1 text-[12px] disabled:opacity-30"
        onClick={async () => { await api.updateTaxonomyRule(rule.id, r); onChange(); }}
      >
        Save
      </button>
      <button className="text-danger text-[12px]"
        onClick={async () => { if (confirm("Delete rule?")) { await api.deleteTaxonomyRule(rule.id); onChange(); } }}>
        Delete
      </button>
    </div>
  );
}

function Tester() {
  const [app, setApp] = useState("");
  const [domain, setDomain] = useState("");
  const [url, setUrl] = useState("");
  const [res, setRes] = useState<TaxonomyTestResult | null>(null);

  async function run() {
    setRes(await api.testTaxonomy({ app: app || undefined, domain: domain || undefined, url: url || undefined }));
  }

  return (
    <div className="card bg-[#fbfaf7]">
      <div className="font-serif font-semibold mb-2">Test a URL or app</div>
      <div className="grid grid-cols-3 gap-2 mb-2">
        <input className="border border-border rounded px-2 py-1 text-[13px]" placeholder="app (e.g. outlook)"
          value={app} onChange={(e) => setApp(e.target.value)} />
        <input className="border border-border rounded px-2 py-1 text-[13px]" placeholder="domain"
          value={domain} onChange={(e) => setDomain(e.target.value)} />
        <input className="border border-border rounded px-2 py-1 text-[13px] font-mono" placeholder="full URL"
          value={url} onChange={(e) => setUrl(e.target.value)} />
      </div>
      <button className="bg-gold text-ink font-semibold rounded px-4 py-1.5 text-[13px]" onClick={run}>
        Test
      </button>
      {res && (
        <div className="mt-3 text-[13px]">
          {res.fallback ? (
            <span className="text-muted">No rule matched → falls back to the agent's category.</span>
          ) : (
            <span>
              → <b>{res.sub_category}</b> <span className="text-muted">({res.coarse_class}{res.is_meeting ? ", meeting" : ""})</span>{" "}
              via rule <code className="text-[12px]">{res.matched_pattern}</code>
            </span>
          )}
        </div>
      )}
    </div>
  );
}
