import { FormEvent, useState } from "react";
import { api } from "../lib/api";
import { useAuth } from "../auth";

// Public, employee-facing self-serve enrollment. The employee enters the setup password
// their manager gave them and receives a one-line PowerShell install command.
export function EmployeeSetup() {
  const { orgName } = useAuth();
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [result, setResult] = useState<{ one_liner: string } | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      setResult(await api.selfEnroll({ password, name }));
    } catch (e) {
      setErr((e as Error).message || "Could not enroll");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg py-10">
      <div className="card w-[480px]">
        <div className="flex items-center gap-3 mb-1">
          <img src="/brand/coverage-mark.png" alt="" className="h-9 w-9" />
          <h1 className="font-serif text-2xl font-bold">{orgName} — set up tracking</h1>
        </div>
        <p className="text-muted text-[13px] mb-4 leading-snug">
          This installs a <b>visible</b> activity agent on your work computer. It records app and site
          names, active vs idle time, and input <i>counts</i> — <b>never</b> your keystrokes, files, or
          messages — only during business hours. It shows a tray icon and you can pause it.
        </p>

        {!result ? (
          <form onSubmit={submit}>
            {err && <div className="text-danger text-[13px] mb-3">{err}</div>}
            <label className="block text-[12px] text-muted mb-1">Your name</label>
            <input className="w-full border border-border rounded px-3 py-2 mb-3"
              value={name} onChange={(e) => setName(e.target.value)} />
            <label className="block text-[12px] text-muted mb-1">Setup password (from your manager)</label>
            <input type="password" className="w-full border border-border rounded px-3 py-2 mb-4"
              value={password} onChange={(e) => setPassword(e.target.value)} />
            <button disabled={busy} className="w-full bg-ink text-white rounded py-2 font-semibold disabled:opacity-50">
              {busy ? "Working…" : "Get my install command"}
            </button>
          </form>
        ) : (
          <div>
            <p className="text-[13px] mb-2">
              In <b>Windows PowerShell</b>, paste and run this. It installs the agent for your account
              (no admin needed):
            </p>
            <code className="block text-[12px] break-all bg-[#f7f7f7] border border-border rounded p-2">
              {result.one_liner}
            </code>
            <button className="text-gold text-[13px] font-semibold mt-2"
              onClick={() => navigator.clipboard?.writeText(result.one_liner)}>
              Copy command
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
