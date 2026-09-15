import { useNavigate, useSearchParams } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";
import AuthModal from "../components/AuthModal";

// Architecture v1.9 splits the pipeline into foundations (established once, read by
// everything downstream) and a genuinely order-dependent execution band. Keep that
// distinction here: only the band is numbered, because only there does order carry
// information the reader needs.
const FOUNDATIONS = [
  {
    title: "Discovery",
    body: "Commercial scope, products, and positioning read off the live site — then confirmed with the client, not assumed.",
  },
  {
    title: "Tracking & access",
    body: "Analytics, Search Console, and conversion health verified before any strategy gets ahead of the data.",
  },
  {
    title: "Competitors",
    body: "Tiered landscape every role re-runs against their own task — content compares content, technical benchmarks site health.",
  },
];

const SEQUENCE = [
  {
    n: "01",
    title: "Website situation",
    body: "Crawl, authority, and anomaly signals in one audit the technical specialist signs off.",
  },
  {
    n: "02",
    title: "Search demand",
    body: "Keyword clusters from live Ahrefs / DataForSEO metrics — scored into best, evergreen, trend, and avoid. Volumes are never invented.",
  },
  {
    n: "03",
    title: "Content audit",
    body: "What already exists, and what it should become: keep, refresh, consolidate, or retire.",
  },
  {
    n: "04",
    title: "Content strategy",
    body: "Pillars, priority queue, and calendar — built on the audit so decay and cannibalisation are visible first.",
  },
  {
    n: "05",
    title: "Site architecture",
    body: "Hubs, click depth, and URL ownership. Architecture wins every URL conflict downstream.",
  },
  {
    n: "06",
    title: "Technical SEO",
    body: "Crawlability, indexation, and the redirect work the new URL tree implies.",
  },
  {
    n: "07",
    title: "Content planning",
    body: "Strategy and architecture merged into one locked roadmap — every page carries a URL, parent, and disposition.",
  },
  {
    n: "08",
    title: "Content production",
    body: "Writer-ready briefs off the locked roadmap: intent, outline, coverage, and internal link targets.",
  },
  {
    n: "09",
    title: "On-page SEO",
    body: "Titles, metas, headings, schema, and internal links — with competitor brand terms blocked from client copy.",
  },
  {
    n: "10",
    title: "Publishing",
    body: "The client's own brand rendered into a dry-run preview, then a WordPress draft that is read back and verified.",
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
            <img
              src="/landing/hero.jpg"
              alt=""
              className="landing-hero-img"
              width={1920}
              height={1080}
              decoding="async"
              fetchPriority="high"
            />
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
                See how it runs
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
            <h2 className="landing-h2">
              Three foundations. Ten steps that must run in order.
            </h2>
            <p className="landing-section-lede">
              Not every phase is the same kind of work. Three are foundations —
              established once, then read by everything downstream. The rest is a
              genuinely order-dependent band where each step consumes the last
              one's locked output. Every step stops at a human before shared
              memory updates.
            </p>

            <p className="landing-group-label">Foundations · referenced throughout</p>
            <ul className="landing-phase-list landing-phase-list-flat">
              {FOUNDATIONS.map((p, i) => (
                <li
                  key={p.title}
                  className="landing-phase-item"
                  style={{ transitionDelay: `${i * 90}ms` }}
                >
                  <span className="landing-phase-mark" aria-hidden="true" />
                  <div>
                    <h3>{p.title}</h3>
                    <p>{p.body}</p>
                  </div>
                </li>
              ))}
            </ul>

            <p className="landing-group-label">Sequential band · output feeds the next step</p>
            <ol className="landing-phase-list">
              {SEQUENCE.map((p, i) => (
                <li
                  key={p.n}
                  className="landing-phase-item"
                  style={{ transitionDelay: `${(i + 3) * 70}ms` }}
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
            <img
              src="/landing/workflow.jpg"
              alt=""
              width={1200}
              height={900}
              loading="lazy"
              decoding="async"
            />
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
              <li>Keyword opportunities → content calendar → URL architecture</li>
            </ul>
          </div>
        </section>

        <section
          className={`landing-close ${close.visible ? "is-in" : ""}`}
          ref={close.ref}
        >
          <div className="landing-close-media" aria-hidden="true">
            <img
              src="/landing/radar.jpg"
              alt=""
              width={1920}
              height={1080}
              loading="lazy"
              decoding="async"
            />
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
        <span>Discovery to publish · Role-gated skills · Shared memory</span>
        <nav className="landing-footer-nav" aria-label="Footer">
          <a href="#phases">Phases</a>
          <a href="#method">Method</a>
        </nav>
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
