import { FormEvent, useEffect, useId, useRef, useState } from "react";
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
    description: "Full access — up to 5 accounts. Can run and approve every skill across all phases.",
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
    description: "Owns Competitor & Market and Phase 6 Content Strategy / IA.",
  },
  {
    name: "content_seo_specialist",
    label: "Content SEO Specialist",
    description: "Owns Phase 5 Search Demand (topics + clustering).",
  },
  {
    name: "on_page_seo_specialist",
    label: "On-Page SEO Specialist",
    description: "On-page SEO, internal linking — view-only until later phases.",
  },
  {
    name: "structured_data_specialist",
    label: "Structured Data Specialist",
    description: "Schema markup — view-only until later phases.",
  },
  {
    name: "seo_qa_lead",
    label: "SEO QA Lead",
    description: "QA / reporting — reviews findings across phases.",
  },
];

const COPY: Record<Mode, { title: string; sub: string; submit: string; busy: string }> = {
  login: {
    title: "Log in",
    sub: "Use the email you signed up with. Your role decides which phases you can run and approve.",
    submit: "Log in",
    busy: "Logging in…",
  },
  signup: {
    title: "Create account",
    sub: "Pick the role you work in. It decides which phases you can run and approve, and stays with this email.",
    submit: "Create account",
    busy: "Creating account…",
  },
};

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
  const [roleName, setRoleName] = useState("client_success_manager");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const firstField = useRef<HTMLInputElement>(null);
  const ids = useId();

  useEffect(() => {
    if (!open) return;
    setMode(initialMode);
    setError("");
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
    if (!open) return;
    firstField.current?.focus();
  }, [open, mode]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const copy = COPY[mode];
  const selected = roles.find((r) => r.name === roleName) || roles[0];

  function switchMode(next: Mode) {
    setMode(next);
    setError("");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        if (!roleName) {
          setError("Pick the role you work in.");
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
      const message = err instanceof Error ? err.message : "";
      setError(
        /invalid credentials/i.test(message)
          ? "That email and password don't match. Check both and try again."
          : message || "That didn't work. Check your details and try again."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="login-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="login-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${ids}-title`}
        aria-describedby={`${ids}-sub`}
      >
        <div className="login-top">
          <span className="login-brand">Radius OS</span>
          <button type="button" className="login-close" onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
              <path d="M3.5 3.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <h2 id={`${ids}-title`} className="login-title">
          {copy.title}
        </h2>
        <p id={`${ids}-sub`} className="login-sub">
          {copy.sub}
        </p>

        <div className="login-switch" role="tablist" aria-label="Log in or create an account">
          {(["login", "signup"] as Mode[]).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              className={mode === m ? "is-active" : ""}
              onClick={() => switchMode(m)}
            >
              {m === "login" ? "Log in" : "Sign up"}
            </button>
          ))}
        </div>

        <form className="login-form" onSubmit={onSubmit}>
          {error ? (
            <p className="login-error" role="alert">
              {error}
            </p>
          ) : null}

          {mode === "signup" && (
            <div className="login-field">
              <label htmlFor={`${ids}-name`}>Full name</label>
              <input
                id={`${ids}-name`}
                ref={firstField}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                autoComplete="name"
              />
            </div>
          )}
          <div className="login-field">
            <label htmlFor={`${ids}-email`}>Email</label>
            <input
              id={`${ids}-email`}
              ref={mode === "login" ? firstField : undefined}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              inputMode="email"
            />
          </div>
          <div className="login-field">
            <label htmlFor={`${ids}-password`}>Password</label>
            <input
              id={`${ids}-password`}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              aria-describedby={mode === "signup" ? `${ids}-pw-hint` : undefined}
            />
            {mode === "signup" ? (
              <p id={`${ids}-pw-hint`} className="login-hint">
                At least 8 characters.
              </p>
            ) : null}
          </div>
          {mode === "signup" && (
            <div className="login-field">
              <label htmlFor={`${ids}-role`}>Your role</label>
              <div className="login-select">
                <select
                  id={`${ids}-role`}
                  value={roleName}
                  onChange={(e) => setRoleName(e.target.value)}
                  required
                  aria-describedby={`${ids}-role-hint`}
                >
                  {roles.map((r) => (
                    <option key={r.name} value={r.name}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </div>
              <p id={`${ids}-role-hint`} className="login-hint">
                {selected?.description || "Your role is applied every time you log in."}
              </p>
            </div>
          )}

          <button className="login-submit" type="submit" disabled={busy}>
            {busy ? copy.busy : copy.submit}
          </button>
        </form>
      </div>
    </div>
  );
}
