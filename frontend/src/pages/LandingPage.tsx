import { useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import AuthModal from "../components/AuthModal";

const PHASES = [
  {
    n: "01",
    title: "Discovery",
    body: "Map commercial scope, products, and positioning from the live site — then confirm with the client.",
  },
  {
    n: "02",
    title: "Tracking & access",
    body: "Verify analytics, Search Console, and measurement health before strategy gets ahead of the data.",
  },
  {
    n: "03",
    title: "Website situation",
    body: "Crawl, authority, and anomaly signals in one audit the technical specialist can sign off.",
  },
  {
    n: "04",
    title: "Competitors",
    body: "Tiered 16-parameter landscape — who to benchmark, who threatens you next, what to close first.",
  },
];

function useInView<T extends HTMLElement>(margin = "0px 0px -10% 0px") {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          io.disconnect();
        }
      },
      { rootMargin: margin, threshold: 0.12 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [margin]);
  return { ref, visible };
}

export default function LandingPage() {
  const phases = useInView<HTMLElement>();
  const story = useInView<HTMLElement>();
  const close = useInView<HTMLElement>();
  const { token, user, ready, logout } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [authOpen, setAuthOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");

  useEffect(() => {
    const a = params.get("auth");
    if (a === "login" || a === "signup") {
      setAuthMode(a);
      setAuthOpen(true);
    }
  }, [params]);

  function openAuth(mode: "login" | "signup") {
    setAuthMode(mode);
    setAuthOpen(true);
    setParams({ auth: mode });
  }

  function closeAuth() {
    setAuthOpen(false);
    setParams({});
  }

  /** Enter workspace → login/signup if logged out; workspace if already signed in. */
  function enterWorkspace() {
    if (token) {
      navigate("/app");
      return;
    }
    openAuth("login");
  }

  return (
    <div className="landing">
      <header className="landing-nav">
        <a className="landing-brand" href="#top">
          Radius OS
        </a>
        <nav className="landing-nav-links" aria-label="Primary">
          <a href="#phases">Phases</a>
          <a href="#method">Method</a>
          {ready && token && user ? (
            <>
              <span className="landing-nav-user">
                {user.role_label || user.role_name}
              </span>
              <button type="button" className="landing-nav-cta" onClick={() => navigate("/app")}>
                Workspace
              </button>
              <button type="button" className="landing-nav-ghost" onClick={logout}>
                Log out
              </button>
            </>
          ) : (
            <>
              <button type="button" className="landing-nav-ghost" onClick={() => openAuth("login")}>
                Log in
              </button>
              <button type="button" className="landing-nav-cta" onClick={() => openAuth("signup")}>
                Sign up
              </button>
            </>
          )}
        </nav>
      </header>

      <main id="top">
        <section className="landing-hero" aria-label="Hero">
          <div className="landing-hero-media" aria-hidden="true">
            <img src="/landing/hero.jpg" alt="" className="landing-hero-img" />
            <div className="landing-hero-shade" />
          </div>
          <div className="landing-hero-copy">
            <p className="landing-brand-mark">Radius OS</p>
            <h1 className="landing-headline">
              SEO onboarding that earns sign-off.
            </h1>
            <p className="landing-lede">
              Role-gated skills with shared Client Digital Profile memory —
              each specialist sees the context others already published.
            </p>
            <div className="landing-cta-row">
              <button
                type="button"
                className="landing-btn landing-btn-primary"
                onClick={enterWorkspace}
              >
                Enter workspace
              </button>
              <a href="#phases" className="landing-btn landing-btn-ghost">
                See the four phases
              </a>
            </div>
          </div>
        </section>

        <section
          id="phases"
          className={`landing-section landing-phases ${phases.visible ? "is-in" : ""}`}
          ref={phases.ref}
        >
          <div className="landing-section-inner">
            <p className="landing-kicker">The path</p>
            <h2 className="landing-h2">Four phases. One readiness gate.</h2>
            <p className="landing-section-lede">
              Each agent ships a checkpoint your team can approve, edit, or reject
              before the next phase unlocks.
            </p>
            <ol className="landing-phase-list">
              {PHASES.map((p, i) => (
                <li
                  key={p.n}
                  className="landing-phase-item"
                  style={{ transitionDelay: `${i * 90}ms` }}
                >
                  <span className="landing-phase-n">{p.n}</span>
                  <div>
                    <h3>{p.title}</h3>
                    <p>{p.body}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section
          id="method"
          className={`landing-section landing-method ${story.visible ? "is-in" : ""}`}
          ref={story.ref}
        >
          <div className="landing-method-media" aria-hidden="true">
            <img src="/landing/workflow.jpg" alt="" />
          </div>
          <div className="landing-method-copy">
            <p className="landing-kicker">Method</p>
            <h2 className="landing-h2">Research first. Confirm with humans.</h2>
            <p>
              Radius OS pulls live site evidence, scores what it can verify, and
              leaves thin signals in the mid-range — so strategists debate gaps,
              not invented facts.
            </p>
            <ul className="landing-bullets">
              <li>Commercial scope retained across re-runs</li>
              <li>Technical, authority, and anomaly tabs for website sign-off</li>
              <li>16-parameter competitor tiers with monitoring plan</li>
            </ul>
          </div>
        </section>

        <section
          className={`landing-close ${close.visible ? "is-in" : ""}`}
          ref={close.ref}
        >
          <div className="landing-close-media" aria-hidden="true">
            <img src="/landing/radar.jpg" alt="" />
            <div className="landing-close-shade" />
          </div>
          <div className="landing-close-copy">
            <p className="landing-brand-mark landing-brand-mark-light">Radius OS</p>
            <h2 className="landing-h2 landing-h2-light">
              Ready when your client is.
            </h2>
            <p className="landing-lede landing-lede-light">
              Log in or sign up with your SEO role, then open the workspace.
            </p>
            <button
              type="button"
              className="landing-btn landing-btn-primary"
              onClick={enterWorkspace}
            >
              Enter workspace
            </button>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Radius OS</span>
        <span>Phases 1–4 · Role-gated skills · Shared memory</span>
      </footer>

      <AuthModal
        open={authOpen}
        initialMode={authMode}
        onClose={closeAuth}
        onSuccess={() => {
          closeAuth();
          navigate("/app");
        }}
      />
    </div>
  );
}
