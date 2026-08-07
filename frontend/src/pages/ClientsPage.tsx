import { FormEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, Client } from "../api";
import { useAuth } from "../auth";

type ClientForm = {
  name: string;
  primary_url: string;
  industry: string;
  primary_contact_name: string;
  primary_contact_email: string;
};

const emptyForm: ClientForm = {
  name: "",
  primary_url: "https://",
  industry: "",
  primary_contact_name: "",
  primary_contact_email: "",
};

const INDUSTRY_OPTIONS = [
  "Plastic surgery / cosmetic",
  "Dental / oral health",
  "Healthcare / medical clinic",
  "Legal services",
  "Accounting / finance",
  "Real estate",
  "Home services (HVAC, plumbing, electrical)",
  "Construction / trades",
  "Hospitality / hotels",
  "Restaurants / food & beverage",
  "Retail / ecommerce",
  "Fashion / apparel",
  "Beauty / salon / spa",
  "Fitness / wellness",
  "Education / training",
  "SaaS / software",
  "B2B professional services",
  "Manufacturing / wholesale",
  "Automotive",
  "Travel / tourism",
  "Nonprofit",
  "Other",
];

export default function ClientsPage() {
  const { token, user, logout } = useAuth();
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ClientForm>(emptyForm);
  const [industryOpen, setIndustryOpen] = useState(false);
  const industryRef = useRef<HTMLDivElement>(null);

  const industryChoices = useMemo(() => {
    const q = form.industry.trim().toLowerCase();
    if (!q) return INDUSTRY_OPTIONS;
    return INDUSTRY_OPTIONS.filter((opt) => opt.toLowerCase().includes(q));
  }, [form.industry]);

  useEffect(() => {
    if (!industryOpen) return;
    function onDoc(e: Event) {
      if (industryRef.current && !industryRef.current.contains(e.target as Node)) {
        setIndustryOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [industryOpen]);

  async function load() {
    if (!token) return;
    try {
      setClients(await api.clients(token));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }

  useEffect(() => {
    load();
  }, [token]);

  function openCreate() {
    setEditingId(null);
    setForm(emptyForm);
    setShowForm(true);
  }

  function openEdit(c: Client, e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setEditingId(c.id);
    setForm({
      ...emptyForm,
      name: c.name,
      primary_url: c.primary_url,
      industry: c.industry || "",
    });
    setShowForm(true);
    setError("");
  }

  function closeForm() {
    setShowForm(false);
    setEditingId(null);
    setForm(emptyForm);
    setIndustryOpen(false);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    try {
      if (editingId) {
        await api.updateClient(token, editingId, {
          name: form.name,
          primary_url: form.primary_url,
          industry: form.industry,
          primary_contact_name: form.primary_contact_name || undefined,
          primary_contact_email: form.primary_contact_email || undefined,
        });
        closeForm();
        await load();
      } else {
        const created = await api.createClient(token, {
          name: form.name,
          primary_url: form.primary_url,
          industry: form.industry,
          primary_contact_name: form.primary_contact_name || undefined,
          primary_contact_email: form.primary_contact_email || undefined,
        });
        window.location.href = `/clients/${created.id}/chat`;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function onDelete(c: Client, e: MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!token) return;
    if (!window.confirm(`Delete ${c.name}? This cannot be undone.`)) return;
    try {
      await api.deleteClient(token, c.id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <p className="page-kicker">Workspace</p>
          <h1>Clients</h1>
          <p className="page-sub">
            Signed in as {user?.email}
            {user?.role_label ? ` · ${user.role_label}` : ""}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span className="role-chip">{user?.role_label || user?.role_name}</span>
          <button type="button" className="btn btn-ghost" onClick={logout}>
            Log out
          </button>
          <button className="btn btn-primary" onClick={openCreate}>
            New client
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showForm && (
        <form className="create-form" onSubmit={onSubmit}>
          <h3>{editingId ? "Edit client" : "New client onboarding"}</h3>
          <p className="q-help" style={{ marginTop: -6 }}>
            Keep it simple — name, website, and industry. Full CDD details and system
            access credentials are collected in Discovery (upload a CDD or fill the form).
          </p>

          <div className="q-section" style={{ borderTop: "none", paddingTop: 0, marginTop: 8 }}>
            <div className="q-section-head">
              <h4>Basics</h4>
            </div>
            <div className="two-col">
              <div className="field">
                <label>Name</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label>Primary website</label>
                <input
                  value={form.primary_url}
                  onChange={(e) => setForm({ ...form, primary_url: e.target.value })}
                  required
                />
              </div>
              <div className="field industry-combo">
                <label htmlFor="industry-input">Industry / vertical</label>
                <div className="industry-combo-anchor" ref={industryRef}>
                  <div className={`industry-combo-wrap${industryOpen ? " open" : ""}`}>
                    <input
                      id="industry-input"
                      value={form.industry}
                      onChange={(e) => {
                        setForm({ ...form, industry: e.target.value });
                        setIndustryOpen(true);
                      }}
                      onFocus={() => setIndustryOpen(true)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") setIndustryOpen(false);
                        if (e.key === "Enter" && industryOpen && industryChoices[0]) {
                          e.preventDefault();
                          setForm({ ...form, industry: industryChoices[0] });
                          setIndustryOpen(false);
                        }
                      }}
                      placeholder="Pick from the list or type your own"
                      required
                      autoComplete="off"
                      role="combobox"
                      aria-expanded={industryOpen}
                      aria-controls="industry-listbox"
                      aria-autocomplete="list"
                    />
                    <button
                      type="button"
                      className="industry-combo-chevron"
                      aria-label={industryOpen ? "Close industry list" : "Open industry list"}
                      onClick={() => setIndustryOpen((v) => !v)}
                    >
                      ▾
                    </button>
                  </div>
                  {industryOpen ? (
                    <ul id="industry-listbox" className="industry-combo-menu" role="listbox">
                      {industryChoices.length ? (
                        industryChoices.map((opt) => (
                          <li key={opt} role="option" aria-selected={form.industry === opt}>
                            <button
                              type="button"
                              className={form.industry === opt ? "selected" : ""}
                              onClick={() => {
                                if (opt === "Other") {
                                  setForm({ ...form, industry: "" });
                                  setIndustryOpen(true);
                                  return;
                                }
                                setForm({ ...form, industry: opt });
                                setIndustryOpen(false);
                              }}
                            >
                              {opt}
                            </button>
                          </li>
                        ))
                      ) : (
                        <li className="industry-combo-empty">
                          Use “{form.industry.trim()}” as custom industry
                        </li>
                      )}
                    </ul>
                  ) : null}
                </div>
                <p className="field-hint">Choose a suggestion or type a custom industry.</p>
              </div>
            </div>
          </div>

          <div className="q-section">
            <div className="q-section-head">
              <h4>Primary contact</h4>
              <p>Optional — who we work with day to day.</p>
            </div>
            <div className="two-col">
              <div className="field">
                <label>Contact name</label>
                <input
                  value={form.primary_contact_name}
                  onChange={(e) => setForm({ ...form, primary_contact_name: e.target.value })}
                />
              </div>
              <div className="field">
                <label>Contact email</label>
                <input
                  type="email"
                  value={form.primary_contact_email}
                  onChange={(e) => setForm({ ...form, primary_contact_email: e.target.value })}
                />
              </div>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
            <button className="btn btn-primary" type="submit">
              {editingId ? "Save changes" : "Create & open onboarding"}
            </button>
            <button className="btn btn-secondary" type="button" onClick={closeForm}>
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="client-grid">
        {!clients.length && !showForm ? (
          <div className="client-empty">
            <h3>No clients yet</h3>
            <p>Create a client with name, website, and industry to start onboarding.</p>
            <button className="btn btn-primary" onClick={openCreate}>
              New client
            </button>
          </div>
        ) : null}
        {clients.map((c) => (
          <div key={c.id} className="client-tile">
            <Link to={`/clients/${c.id}/chat`} style={{ color: "inherit", textDecoration: "none" }}>
              <h3>{c.name}</h3>
              <div className="client-tile-url">{c.primary_url}</div>
              {c.industry ? <div className="client-tile-meta">{c.industry}</div> : null}
              <div className="client-tile-status">{c.status}</div>
            </Link>
            <div className="client-tile-actions">
              <button
                type="button"
                className="btn btn-ghost"
                style={{ fontSize: 12, padding: "6px 12px" }}
                onClick={(e) => openEdit(c, e)}
              >
                Edit
              </button>
              <button
                type="button"
                className="btn btn-danger"
                style={{ fontSize: 12, padding: "6px 12px" }}
                onClick={(e) => onDelete(c, e)}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
