import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../auth";

export function Login() {
  const { refresh, needsAdmin, orgName } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (needsAdmin) {
    nav("/setup-admin", { replace: true });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await api.login(username, password);
      await refresh();
      nav("/", { replace: true });
    } catch {
      setErr("Invalid credentials");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg">
      <form onSubmit={submit} className="card w-[340px]">
        <div className="flex items-center gap-3 mb-4">
          <img src="/brand/coverage-mark.png" alt="" className="h-9 w-9" />
          <h1 className="font-serif text-2xl font-bold">{orgName}</h1>
        </div>
        <p className="text-muted mb-4 text-[13px]">Manager sign-in</p>
        {err && <div className="text-danger text-[13px] mb-3">{err}</div>}
        <label className="block text-[12px] text-muted mb-1">Username</label>
        <input
          className="w-full border border-border rounded px-3 py-2 mb-3"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <label className="block text-[12px] text-muted mb-1">Password</label>
        <input
          type="password"
          className="w-full border border-border rounded px-3 py-2 mb-4"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button
          disabled={busy}
          className="w-full bg-ink text-white rounded py-2 font-semibold disabled:opacity-50"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
