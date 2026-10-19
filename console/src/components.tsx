import { useState, type FormEvent, type ReactNode } from "react";
import { dateTime, errorText, request, type Session } from "./api";

export function Icon({ name }: { name: "queue" | "decision" | "metrics" | "lock" | "refresh" | "shield" }) {
  const shapes = {
    queue: <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M9 8h6M9 12h6M9 16h3" /></>,
    decision: <><path d="M4 3v17h17M7 15l4-6 4 3 5-7" /></>,
    metrics: <><path d="M5 20v-6M10 20V8M15 20V3M20 20V10" /></>,
    lock: <><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 2M18 18A8 8 0 0 1 5 16" /></>,
    shield: <><path d="M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6z" /><path d="M16 8h-6a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H8" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return <div className="empty"><h3>{title}</h3><p>{children}</p></div>;
}

export function Notice({ children, kind = "error" }: { children: ReactNode; kind?: "error" | "success" }) {
  return <div className={`notice ${kind}`} role={kind === "error" ? "alert" : "status"}>{children}</div>;
}

export function AuthPanel({ session, onSession }: { session: Session | null; onSession: (value: Session | null) => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const result = session ? await request<Session>("/auth/step-up", { otp: fields.get("otp") }) :
        await request<Session>("/auth/login", { username: fields.get("username"), password: fields.get("password"), otp: fields.get("otp") });
      onSession(result);
    } catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  return <section className="panel auth-panel"><h2>{session ? "Workspace session" : "Secure workspace"}</h2>
    {session ? <><p className="muted">{session.roles.join(" · ")}</p><dl className="facts"><dt>Step-up valid until</dt><dd>{dateTime(session.step_up_valid_until)} Baku</dd><dt>Tenant</dt><dd className="mono">{session.tenant_id}</dd></dl></> : null}
    <form onSubmit={(event) => { void submit(event); }}>
      {!session ? <><label>Username<input name="username" autoComplete="username" required placeholder="Username" /></label><label>Password<input name="password" type="password" autoComplete="current-password" required placeholder="Password" /></label></> : null}
      <label>{session ? "Refresh step-up with a new code" : "One-time code"}<input name="otp" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required placeholder="6-digit code" /></label>
      {error ? <Notice>{error}</Notice> : null}<button className="primary" disabled={busy}>{busy ? "Connecting…" : session ? "Verify code" : "Sign in"}</button>
    </form>
    {session ? <button className="text-button" onClick={() => { void request("/auth/logout", {}).then(() => onSession(null)).catch((failure: unknown) => setError(errorText(failure))); }}>Sign out</button> : null}
  </section>;
}
