import { useEffect, useState } from "react";
import { api, MachineRow } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

export function Machines() {
  const [machines, setMachines] = useState<MachineRow[]>([]);
  const [label, setLabel] = useState("");
  const [issued, setIssued] = useState<{ code: string; one_liner: string } | null>(null);

  async function load() {
    setMachines((await api.machines()).machines);
  }
  useEffect(() => {
    load();
  }, []);

  async function issue() {
    setIssued(await api.enrollCode({ label }));
    setLabel("");
  }

  async function revoke(id: string) {
    if (!confirm(`Revoke ${id}? Its agent stops being accepted immediately.`)) return;
    await api.revokeMachine(id);
    load();
  }

  return (
    <div className="min-h-screen">
      <Masthead title="Machines" />
      <div className="max-w-[900px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">Machines</h1>
        <p className="text-muted text-[13px] mb-4">
          Issue a one-time enrollment code, then run the install one-liner on the employee's machine.
          Revoking a machine stops its agent immediately without affecting others.
        </p>

        <div className="card mb-4">
          <h3 className="font-serif font-semibold mb-2">Issue enrollment code</h3>
          <div className="flex gap-2">
            <input className="flex-1 border border-border rounded px-3 py-2" placeholder="label (e.g. Sam's laptop)"
              value={label} onChange={(e) => setLabel(e.target.value)} />
            <button className="bg-ink text-white rounded px-4" onClick={issue}>
              Issue code
            </button>
          </div>
          {issued && (
            <div className="mt-3 bg-[#f7f7f7] border border-border rounded p-3">
              <div className="text-[12px] text-muted mb-1">
                Run this in PowerShell on the employee's Windows machine:
              </div>
              <code className="block text-[12px] break-all bg-white border border-border rounded p-2">
                {issued.one_liner}
              </code>
              <button
                className="text-gold text-[12px] font-semibold mt-1"
                onClick={() => navigator.clipboard?.writeText(issued.one_liner)}
              >
                Copy
              </button>
            </div>
          )}
        </div>

        <div className="grid gap-2">
          {machines.map((m) => (
            <div key={m.machine_id} className="card flex items-center gap-3">
              <div className="flex-1">
                <b>{m.hostname || m.machine_id}</b>
                <span className="text-[11px] text-muted ml-2">{m.machine_id}</span>
                <div className="text-[11px] text-muted">
                  last seen {m.last_seen_ts ? new Date(m.last_seen_ts).toLocaleString() : "never"}
                </div>
              </div>
              {m.revoked ? (
                <span className="text-[12px] text-danger">revoked</span>
              ) : (
                <button className="text-[12px] text-danger font-semibold" onClick={() => revoke(m.machine_id)}>
                  Revoke
                </button>
              )}
            </div>
          ))}
          {machines.length === 0 && <p className="text-muted text-[13px]">No machines enrolled yet.</p>}
        </div>
      </div>
    </div>
  );
}
