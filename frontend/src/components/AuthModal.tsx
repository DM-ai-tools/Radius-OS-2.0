import { FormEvent, useEffect, useRef, useState } from "react";
import { api, SeoRole } from "../api";
import { useAuth } from "../auth";

type Mode = "login" | "signup";

type Props = {
  open: boolean;
  initialMode?: Mode;
  onClose: () => void;
  onSuccess: () => void;
};

/** Always available so signup works even if /auth/roles fails. */
const FALLBACK_ROLES: SeoRole[] = [
  {
    name: "head_of_department",
    label: "Head of Department",
    description: "Full access — can run and approve every skill across all phases.",
  },
  {
    name: "client_success_manager",
    label: "Client Success Manager",
    description: "Owns Discovery (D1–D5); supports Tracking known-changes (T5).",
  },
  {
    name: "technical_seo_specialist",
    label: "Technical SEO Specialist",
    description: "Owns Tracking sign-off (T6) and Website Situation Analysis.",
  },
  {
    name: "seo_strategist",
    label: "SEO Strategist",
    description: "Owns Competitor & Market; escalation lead across phases.",
  },
  {
    name: "content_seo_specialist",
    label: "Content SEO Specialist",
    description: "Phases 5+ — view-only on Phases 1–4.",
  },
  {
    name: "on_page_seo_specialist",
    label: "On-Page SEO Specialist",
    description: "On-page SEO, internal linking — view-only on Phases 1–4.",
  },
  {
    name: "structured_data_specialist",
    label: "Structured Data Specialist",
    description: "Schema markup — view-only on Phases 1–4.",
  },
  {
    name: "seo_qa_lead",
    label: "SEO QA Lead",
    description: "Final readiness gate before Phase 5 handoff.",
  },
];

export default function AuthModal({
  open,
  initialMode = "login",
  onClose,
  onSuccess,
}: Props) {
  const { login, signup } = useAuth();
  const [mode, setMode] = useState<Mode>(initialMode);
  const [roles, setRoles] = useState<SeoRole[]>(FALLBACK_ROLES);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [roleName, setRoleName] = useState("head_of_department");
  const [roleOpen, setRoleOpen] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const roleMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setError("");
    setRoleOpen(false);
    api
      .roles()
      .then((list) => {
        if (Array.isArray(list) && list.length) {
          setRoles(list);
          setRoleName((current) =>
            list.some((r) => r.name === current) ? current : list[0].name
          );
        }
      })
      .catch(() => setRoles(FALLBACK_ROLES));
  }, [open, initialMode]);

  useEffect(() => {
    if (!roleOpen) return;
    function onDoc(e: MouseEvent) {
      if (!roleMenuRef.current?.contains(e.target as Node)) setRoleOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [roleOpen]);

  if (!open) return null;

  const selected = roles.find((r) => r.name === roleName) || roles[0];

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        if (!roleName) {
          setError("Pick an SEO role");
          setBusy(false);
          return;
        }
        await signup({
          email,
          password,
          full_name: fullName,
          role_name: roleName,
        });
      }
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auth failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-modal-backdrop" role="dialog" aria-modal="true">
      <div className="auth-modal">
        <button type="button" className="auth-modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <p className="page-kicker" style={{ marginBottom: 6 }}>
          Radius OS
        </p>
        <h2>{mode === "login" ? "Log in" : "Create account"}</h2>
        <p className="auth-modal-sub">
          {mode === "login"
            ? "Your role is loaded from your account and gates Phase 1–4 skills."
            : "Choose your SEO role — it is stored with your email and limits which skills you can run."}
        </p>

        <div className="auth-tabs">
          <button
            type="button"
            className={mode === "login" ? "active" : ""}
            onClick={() => {
              setMode("login");
              setRoleOpen(false);
            }}
          >
            Log in
          </button>
          <button
            type="button"
            className={mode === "signup" ? "active" : ""}
            onClick={() => setMode("signup")}
          >
            Sign up
          </button>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        <form onSubmit={onSubmit}>
          {mode === "signup" && (
            <div className="field">
              <label>Full name</label>
              <input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                autoComplete="name"
              />
            </div>
          )}
          <div className="field">
            <label>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </div>
          <div className="field">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </div>
          {mode === "signup" && (
            <div className="field role-picker-field" ref={roleMenuRef}>
              <label htmlFor="seo-role-trigger">SEO role</label>
              <button
                id="seo-role-trigger"
                type="button"
                className={`role-picker-trigger ${roleOpen ? "open" : ""}`}
                aria-haspopup="listbox"
                aria-expanded={roleOpen}
                onClick={() => setRoleOpen((v) => !v)}
              >
                <span>{selected?.label || "Select a role…"}</span>
                <span className="role-picker-chevron" aria-hidden>
                  ▾
                </span>
              </button>
              {roleOpen && (
                <ul className="role-picker-menu" role="listbox">
                  {roles.map((r) => (
                    <li key={r.name}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={r.name === roleName}
                        className={r.name === roleName ? "selected" : ""}
                        onClick={() => {
                          setRoleName(r.name);
                          setRoleOpen(false);
                        }}
                      >
                        {r.label}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {/* Hidden input so native form validation still has a value */}
              <input type="hidden" name="role_name" value={roleName} required />
              <p className="field-hint">
                Role is saved with this email and applied on every login.
              </p>
            </div>
          )}
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy
              ? "Please wait…"
              : mode === "login"
                ? "Log in"
                : "Create account"}
          </button>
        </form>
        <p className="auth-demo-hint">
          Demo: hod@trafficradius.com (Head of Department) · csm@ / tech@ /
          strategist@ / qa@trafficradius.com — password123
        </p>
      </div>
    </div>
  );
}
