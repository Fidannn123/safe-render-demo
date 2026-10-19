import { useState, type FormEvent, type ReactNode } from "react";
import { ChartColumn, FileSearch, House, Inbox, ListChecks, RefreshCw, Search, ShieldCheck, Snowflake } from "lucide-react";
import { dateTime, errorText, request, type Session } from "./api";

const ICONS = { queue: ListChecks, decision: FileSearch, metrics: ChartColumn, lock: Snowflake, refresh: RefreshCw, shield: ShieldCheck, home: House, search: Search, inbox: Inbox };

export function Icon({ name }: { name: keyof typeof ICONS }) {
  const Glyph = ICONS[name];
  return <Glyph aria-hidden="true" strokeWidth={1.6} />;
}

export function EmptyState({ title, icon = "inbox", children }: { title: string; icon?: keyof typeof ICONS; children: ReactNode }) {
  return <div className="empty"><span className="empty-ico"><Icon name={icon} /></span><h3>{title}</h3><p>{children}</p></div>;
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
