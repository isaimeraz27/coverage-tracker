import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { api, Mode } from "../lib/api";

export function Masthead({ title, mode, right }: { title?: string; mode?: Mode; right?: React.ReactNode }) {
  const { me, orgName, setMe } = useAuth();
  const nav = useNavigate();

  async function logout() {
    await api.logout();
    setMe(null);
    nav("/login");
  }

  return (
    <header className="bg-ink text-white px-6 py-3 flex items-center gap-4 border-b-[3px] border-gold">
      <img src="/brand/coverage-mark.png" alt="" className="h-7 w-7 rounded-sm" />
      <Link to="/" className="font-serif font-bold text-xl tracking-wide no-underline text-white">
        {orgName}
      </Link>
      {title && <span className="font-serif text-[#eee] text-[15px]">· {title}</span>}
      {mode && (
        <span
          className={`ml-1 text-[11px] uppercase tracking-wide px-2 py-0.5 rounded ${
            mode === "coaching" ? "bg-gold/20 text-gold-l" : "bg-white/15 text-white"
          }`}
          title={
            mode === "coaching"
              ? "Coaching: compared to each person's own baseline. No pass/fail."
              : "Evaluative: compared to calibrated role targets."
          }
        >
          {mode}
        </span>
      )}
      <div className="ml-auto flex items-center gap-4 text-[13px]">
        {right}
        {me?.role === "admin" && (
          <nav className="flex items-center gap-3">
            <Link className="text-gold no-underline font-semibold" to="/admin/roles">Roles</Link>
            <Link className="text-gold no-underline font-semibold" to="/admin/users">People</Link>
            <Link className="text-gold no-underline font-semibold" to="/admin/taxonomy-rules">Taxonomy</Link>
            <Link className="text-gold no-underline font-semibold" to="/admin/workflow-templates">Workflows</Link>
            <Link className="text-gold no-underline font-semibold" to="/admin/managers">Managers</Link>
            <Link className="text-gold no-underline font-semibold" to="/admin/machines">Machines</Link>
            <Link className="text-gold no-underline font-semibold" to="/settings">Settings</Link>
          </nav>
        )}
        {me && (
          <button onClick={logout} className="text-[#ccc] hover:text-white">
            {me.display_name} · sign out
          </button>
        )}
      </div>
    </header>
  );
}
