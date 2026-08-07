import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, Permission, User } from "./api";

const TOKEN_KEY = "radius_os_token";

type AuthCtx = {
  token: string | null;
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (body: {
    email: string;
    password: string;
    full_name: string;
    role_name: string;
  }) => Promise<void>;
  logout: () => void;
  canTrigger: (agentKey: string) => boolean;
  canApprove: (agentKey: string) => boolean;
  permissions: Permission[];
};

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() =>
    localStorage.getItem(TOKEN_KEY)
  );
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  const refreshMe = useCallback(async (t: string) => {
    const me = await api.me(t);
    setUser(me);
    return me;
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!token) {
        setUser(null);
        setReady(true);
        return;
      }
      try {
        await refreshMe(token);
      } catch {
        localStorage.removeItem(TOKEN_KEY);
        if (!cancelled) {
          setToken(null);
          setUser(null);
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, refreshMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api.login(email, password);
      localStorage.setItem(TOKEN_KEY, res.access_token);
      setToken(res.access_token);
      await refreshMe(res.access_token);
    },
    [refreshMe]
  );

  const signup = useCallback(
    async (body: {
      email: string;
      password: string;
      full_name: string;
      role_name: string;
    }) => {
      const res = await api.signup(body);
      localStorage.setItem(TOKEN_KEY, res.access_token);
      setToken(res.access_token);
      await refreshMe(res.access_token);
    },
    [refreshMe]
  );

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const permissions = user?.permissions || [];

  const canTrigger = useCallback(
    (agentKey: string) =>
      permissions.some((p) => p.agent_key === agentKey && p.can_trigger),
    [permissions]
  );

  const canApprove = useCallback(
    (agentKey: string) =>
      permissions.some((p) => p.agent_key === agentKey && p.can_approve),
    [permissions]
  );

  const value = useMemo(
    () => ({
      token,
      user,
      ready,
      login,
      signup,
      logout,
      canTrigger,
      canApprove,
      permissions,
    }),
    [
      token,
      user,
      ready,
      login,
      signup,
      logout,
      canTrigger,
      canApprove,
      permissions,
    ]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth requires AuthProvider");
  return ctx;
}
