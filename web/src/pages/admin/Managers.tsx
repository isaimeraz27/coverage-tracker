import { useEffect, useState } from "react";
import { api, ManagerRow, UserRow } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

export function Managers() {
  const [managers, setManagers] = useState<ManagerRow[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [form, setForm] = useState({ username: "", password: "", display_name: "", role: "manager" });
  const [err, setErr] = useState("");

  async function load() {
    setManagers((await api.managers()).managers);
  }
  useEffect(() => {
    load();
    api.users().then((u) => setUsers(u.users));
  }, []);

  async function create() {
    setErr("");
    if (!form.username || !form.password) {
      setErr("Username and password required.");
      return;
    }
    try {
      await api.createManager(form);
      setForm({ username: "", password: "", display_name: "", role: "manager" });
      load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function toggleScope(m: ManagerRow, uid: number) {
    const s = new Set(m.scope_user_ids);
    s.has(uid) ? s.delete(uid) : s.add(uid);
    await api.setManagerScope(m.id, [...s]);
    load();
  }

  return (
    <div className="min-h-screen">
      <Masthead title="Managers" />
      <div className="max-w-[900px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">Managers</h1>
        <p className="text-muted text-[13px] mb-4">
          Admins see everyone. Managers see only the people in their scope. Every view is audited.
        </p>

        <div className="card mb-4">
          <h3 className="font-serif font-semibold mb-2">Add a manager</h3>
          {err && <div className="text-danger text-[13px] mb-2">{err}</div>}
          <div className="grid grid-cols-4 gap-2">
            <input className="border border-border rounded px-2 py-1.5" placeholder="username"
              value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
            <input className="border border-border rounded px-2 py-1.5" type="password" placeholder="password"
              value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            <input className="border border-border rounded px-2 py-1.5" placeholder="display name"
              value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
            <select className="border border-border rounded px-2 py-1.5"
              value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="manager">manager</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <button className="bg-ink text-white rounded px-4 py-1.5 text-[13px] mt-2" onClick={create}>
            Create manager
          </button>
        </div>

        <div className="grid gap-3">
          {managers.map((m) => (
            <div key={m.id} className="card">
              <div className="flex items-center gap-2 mb-2">
                <b className="font-serif text-[16px]">{m.display_name || m.username}</b>
                <span className="text-[11px] text-muted">@{m.username}</span>
                <span className={`text-[11px] px-2 py-0.5 rounded ${m.role === "admin" ? "bg-ink text-white" : "bg-gold/15"}`}>
                  {m.role}
                </span>
              </div>
              {m.role === "admin" ? (
                <div className="text-[12px] text-muted">Sees everyone (admin).</div>
              ) : (
                <>
                  <div className="text-[12px] text-muted mb-1">Scope (visible people)</div>
                  <div className="flex flex-wrap gap-1.5">
                    {users.map((u) => (
                      <button key={u.id} onClick={() => toggleScope(m, u.id)}
                        className={`text-[12px] px-2 py-1 rounded border ${
                          m.scope_user_ids.includes(u.id) ? "border-gold bg-gold/10 font-semibold" : "border-border text-muted"
                        }`}>
                        {u.display_name || u.username}
                      </button>
                    ))}
                    {users.length === 0 && <span className="text-[12px] text-muted">no people yet</span>}
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
