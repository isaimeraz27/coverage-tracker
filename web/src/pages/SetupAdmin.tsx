import { FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, Mode } from "../lib/api";
import { useAuth } from "../auth";

export function SetupAdmin() {
  const { needsAdmin, loading, refresh } = useAuth();
  const nav = useNavigate();
  const [f, setF] = useState({
    username: "",
    password: "",
    display_name: "",
    org_name: "",
    enroll_password: "",
    mode: "coaching" as Mode,
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  if (loading) return <div className="p-10 text-muted">Loading…</div>;
  if (!needsAdmin) return <Navigate to="/" replace />;

  const up = (k: keyof typeof f) => (e: { target: { value: string } }) =>
    setF({ ...f, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!f.username || !f.password) {
      setErr("Username and password are required.");
      return;
    }
    if (!f.enroll_password.trim() || f.enroll_password.trim() === "coverage-setup") {
      setErr("Set a non-default setup password (employees type it to enroll). It can't be blank or the default.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await api.setupAdmin(f);
      await refresh();
      nav("/", { replace: true });
    } catch (e) {
      setErr((e as Error).message || "Setup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg py-10">
      <form onSubmit={submit} className="card w-[460px]">
        <div className="flex items-center gap-3 mb-1">
          <img src="/brand/coverage-mark.png" alt="" className="h-9 w-9" />
          <h1 className="font-serif text-2xl font-bold">Set up your dashboard</h1>
        </div>
        <p className="text-muted mb-5 text-[13px]">
          Create the first admin account and name your organization. This is a one-time step.
        </p>
        {err && <div className="text-danger text-[13px] mb-3">{err}</div>}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Admin username" value={f.username} onChange={up("username")} autoFocus />
          <Field label="Admin password" type="password" value={f.password} onChange={up("password")} />
          <Field label="Your name" value={f.display_name} onChange={up("display_name")} />
          <Field label="Organization name" value={f.org_name} onChange={up("org_name")} />
        </div>
        <Field
          label="Setup password (employees type this to enroll their machine)"
          value={f.enroll_password}
          onChange={up("enroll_password")}
        />
        <p className="text-[12px] text-muted mt-1 leading-snug">
          Choose something only your team knows — this is what employees type to enroll a machine.
        </p>

        <div className="mt-4 mb-2">
          <label className="block text-[12px] text-muted mb-1">Starting mode</label>
          <div className="flex gap-2">
            {(["coaching", "evaluative"] as Mode[]).map((m) => (
              <button
                type="button"
                key={m}
                onClick={() => setF({ ...f, mode: m })}
                className={`flex-1 border rounded px-3 py-2 text-[13px] capitalize ${
                  f.mode === m ? "border-gold bg-gold/10 font-semibold" : "border-border"
                }`}
              >
                {m}
              </button>
            ))}
          </div>
          <p className="text-[12px] text-muted mt-2 leading-snug">
            {f.mode === "coaching"
              ? "Coaching (recommended): people are compared to their own recent baseline. No pass/fail until you've calibrated role targets. This is the honest default before you know what a normal day looks like."
              : "Evaluative: people are compared to fixed role targets. Only meaningful once roles are calibrated — uncalibrated roles still show as coaching."}
          </p>
        </div>

        <button
          disabled={busy}
          className="w-full bg-ink text-white rounded py-2 font-semibold mt-3 disabled:opacity-50"
        >
          {busy ? "Creating…" : "Create admin & continue"}
        </button>
      </form>
    </div>
  );
}

function Field(props: {
  label: string;
  value: string;
  onChange: (e: { target: { value: string } }) => void;
  type?: string;
  autoFocus?: boolean;
}) {
  return (
    <div className="mt-3">
      <label className="block text-[12px] text-muted mb-1">{props.label}</label>
      <input
        type={props.type || "text"}
        className="w-full border border-border rounded px-3 py-2"
        value={props.value}
        onChange={props.onChange}
        autoFocus={props.autoFocus}
      />
    </div>
  );
}
