import { useEffect, useState } from "react";
import { api, Role, UserRow } from "../../lib/api";
import { Masthead } from "../../components/Masthead";

export function Users() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);

  async function load() {
    setUsers((await api.users()).users);
  }
  useEffect(() => {
    load();
    api.roles().then((r) => setRoles(r.roles));
  }, []);

  async function update(u: UserRow, patch: { display_name?: string; role_fk?: number | null }) {
    await api.updateUser(u.id, patch);
    load();
  }

  return (
    <div className="min-h-screen">
      <Masthead title="People" />
      <div className="max-w-[900px] mx-auto px-6 py-5">
        <h1 className="font-serif text-2xl font-bold mb-1">People</h1>
        <p className="text-muted text-[13px] mb-4">
          People appear automatically when their agent first sends data. Set a display name and role
          here. The role decides what counts as on-task.
        </p>
        {users.length === 0 && (
          <p className="text-muted text-[13px]">
            No people yet — once an enrolled machine ships activity, the user shows up here.
          </p>
        )}
        <div className="grid gap-2">
          {users.map((u) => (
            <div key={u.id} className="card flex items-center gap-3">
              <div className="w-40">
                <div className="font-semibold">{u.username}</div>
                <div className="text-[11px] text-muted">{u.machine_id || "—"}</div>
              </div>
              <input
                className="flex-1 border border-border rounded px-2 py-1 text-[13px]"
                defaultValue={u.display_name || ""}
                placeholder="display name"
                onBlur={(e) => e.target.value !== (u.display_name || "") && update(u, { display_name: e.target.value })}
              />
              <select
                className="border border-border rounded px-2 py-1 text-[13px]"
                value={u.role_fk ?? ""}
                onChange={(e) => update(u, { role_fk: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">no role</option>
                {roles.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
