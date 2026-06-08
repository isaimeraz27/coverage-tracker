import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, Me } from "./lib/api";

interface AuthState {
  me: Me | null;
  loading: boolean;
  needsAdmin: boolean;
  orgName: string;
  refresh: () => Promise<void>;
  setMe: (m: Me | null) => void;
}

const Ctx = createContext<AuthState>(null as unknown as AuthState);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsAdmin, setNeedsAdmin] = useState(false);
  const [orgName, setOrgName] = useState("Coverage");

  async function refresh() {
    setLoading(true);
    try {
      const b = await api.bootstrapStatus();
      setNeedsAdmin(b.needs_admin);
      setOrgName(b.org_name);
      if (!b.needs_admin) {
        try {
          setMe(await api.me());
        } catch {
          setMe(null);
        }
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <Ctx.Provider value={{ me, loading, needsAdmin, orgName, refresh, setMe }}>{children}</Ctx.Provider>
  );
}

export function RequireAuth({ children, admin }: { children: ReactNode; admin?: boolean }) {
  const { me, loading, needsAdmin } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-10 text-muted">Loading…</div>;
  if (needsAdmin) return <Navigate to="/setup-admin" replace />;
  if (!me) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  if (admin && me.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}
