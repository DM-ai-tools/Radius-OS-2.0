type Props = {
  agentName?: string | null;
  status: string;
};

/** Claude-style shimmer “Thinking” with a live status line underneath. */
export default function ThinkingIndicator({ agentName, status }: Props) {
  return (
    <div className="msg agent thinking-block" aria-live="polite" aria-label="Agent thinking">
      {agentName && (
        <div className="agent-label" style={{ opacity: 0.85 }}>
          <span className="agent-dot" style={{ background: "var(--primary)" }} />
          {agentName}
        </div>
      )}
      <div className="thinking-shimmer">Thinking</div>
      {status && <div className="thinking-status">{status}</div>}
    </div>
  );
}
