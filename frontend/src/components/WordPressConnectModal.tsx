import { FormEvent, useEffect, useState } from "react";
import { api, WordPressStatus } from "../api";

type Props = {
  open: boolean;
  token: string | null;
  clientId: string | null;
  /** Client's public site, so the form is not left blank to be mistyped as /wp-admin. */
  defaultSiteUrl?: string;
  onClose: () => void;
  /** Fires after a successful connect so the caller can refresh the Publishing card. */
  onConnected: (status: WordPressStatus) => void;
};

/** Each client connects their own WordPress site here — there is no shared/global site. */
export default function WordPressConnectModal({
  open,
  token,
  clientId,
  defaultSiteUrl,
  onClose,
  onConnected,
}: Props) {
  const [baseUrl, setBaseUrl] = useState("");
  const [username, setUsername] = useState("");
  const [appPassword, setAppPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setBaseUrl(defaultSiteUrl || "");
    setUsername("");
    setAppPassword("");
    setError("");
    setBusy(false);
  }, [open, defaultSiteUrl]);

  if (!open) return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token || !clientId) return;
    setBusy(true);
    setError("");
    try {
      const status = await api.wordpressConnect(token, clientId, {
        base_url: baseUrl.trim(),
        username: username.trim(),
        app_password: appPassword.trim(),
      });
      onConnected(status);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect to WordPress");
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
          Phase 12 · Publishing
        </p>
        <h2>Connect WordPress</h2>
        <p className="auth-modal-sub">
          This site's own credentials — not a shared account. We test the connection
          before saving anything. Use the public site URL, not /wp-admin or /wp-json.
        </p>

        {error ? <div className="error-banner">{error}</div> : null}

        <form onSubmit={onSubmit}>
          <div className="field">
            <label>Site URL</label>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              required
              placeholder="https://clientsite.com"
              autoComplete="url"
            />
          </div>
          <div className="field">
            <label>Username</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoComplete="username"
            />
          </div>
          <div className="field">
            <label>Application Password</label>
            <input
              type="password"
              value={appPassword}
              onChange={(e) => setAppPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
            <p className="field-hint">
              WordPress username, not the display name. Paste the same password the
              WordPress app accepted — an Application Password, or the account password
              if that is what the app used.
            </p>
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Testing connection…" : "Connect"}
          </button>
        </form>
      </div>
    </div>
  );
}
