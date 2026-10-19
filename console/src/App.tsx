import { useCallback, useEffect, useState } from "react";
import { ApprovalReview, ApprovalTable } from "./Approvals";
import { errorText, money, request, type Decision, type Payment, type Session, type TenantStatus, type Vendor } from "./api";
import { AuthPanel, Icon, Notice } from "./components";
import { DecisionView } from "./DecisionView";
import { FreezeView } from "./FreezeView";
import { CONSOLE_SECTIONS, SIMULATION_NOTICE } from "./labels";
import { MetricsView, type Metrics } from "./MetricsView";
import "./styles.css";

const descriptions = ["Review held payments before funds are reserved.", "Understand every policy check and inspect its signed fields.", "Compare reproducible outcomes from generated execution evidence.", "Stop new outflow and manage the incident response."];
const icons = ["queue", "decision", "metrics", "lock"] as const;
const FILTERS: [string, (payment: Payment) => boolean][] = [["All", () => true], ["T1", (payment) => payment.tier === "T1"], ["T2", (payment) => payment.tier === "T2"], ["Four-eyes", (payment) => payment.approvals_required === 2]];

export function App() {
  const [section, setSection] = useState(0); const [session, setSession] = useState<Session | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]); const [vendors, setVendors] = useState<Vendor[]>([]);
  const [status, setStatus] = useState<TenantStatus | null>(null); const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [active, setActive] = useState<Payment | null>(null); const [decision, setDecision] = useState<Decision | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState(0); const [query, setQuery] = useState("");
  const refresh = useCallback(async (current: Session | null) => {
    if (!current) return; setBusy(true); setError("");
    const results = await Promise.allSettled([
      request<{ items: Payment[] }>("/payments?state=HELD&limit=200"), request<Vendor[]>("/vendors"),
      request<TenantStatus>(`/tenants/${current.tenant_id}/status`), request<Metrics>("/evidence/metrics"),
    ]);
    const [queue, registry, tenant, evidence] = results;
    if (queue?.status === "fulfilled") { setPayments(queue.value.items); setActive((previous) => previous ? queue.value.items.find((item) => item.payment_id === previous.payment_id) ?? null : null); }
    if (registry?.status === "fulfilled") setVendors(registry.value);
    if (tenant?.status === "fulfilled") setStatus(tenant.value);
    if (evidence?.status === "fulfilled") setMetrics(evidence.value);
    const failure = results.slice(0, 3).find((result) => result.status === "rejected");
    if (failure?.status === "rejected") setError(errorText(failure.reason));
    setBusy(false);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void request<Session>("/auth/session", undefined, controller.signal).then((current) => { setSession(current); void refresh(current); }).catch(() => {});
    return () => controller.abort();
  }, [refresh]);
  function changeSession(current: Session | null) {
    setSession(current); setActive(null); setDecision(null); setPayments([]); setVendors([]); setStatus(null); setMetrics(null); setError("");
    void refresh(current);
  }
  function explain() {
    if (!active?.decision_id) return;
    setBusy(true); void request<Decision>(`/decisions/${active.decision_id}`).then((value) => { setDecision(value); setSection(1); }).catch((failure: unknown) => setError(errorText(failure))).finally(() => setBusy(false));
  }
  const names = new Map(vendors.map((vendor) => [vendor.vendor_id, vendor.name]));
  const needle = query.trim().toLowerCase();
  const shown = payments.filter((payment) => (FILTERS[filter]?.[1] ?? (() => true))(payment) && (!needle || `${names.get(payment.vendor_id ?? "") ?? ""} ${payment.reference ?? ""}`.toLowerCase().includes(needle)));
  const who = session ? session.roles.map((role) => role.charAt(0).toUpperCase() + role.slice(1)).join(" · ") : "Not signed in";
  return <div className="app-shell"><nav className="rail" aria-label="Console sections">{CONSOLE_SECTIONS.map((label, index) => <button key={label} aria-current={section === index ? "page" : undefined} className={`rail-item${section === index ? " active" : ""}`} onClick={() => setSection(index)}><span className="rail-tile"><Icon name={icons[index] ?? "queue"} /></span><span className="rail-label">{label}</span></button>)}<span className="rail-rule" /><a className="rail-item rail-foot" href="/"><span className="rail-tile"><Icon name="home" /></span><span className="rail-label">Website</span></a></nav>
    <div className="workspace"><header className="topbar"><a className="brand" href="/console" aria-label="SafePay Console" onClick={(event) => { event.preventDefault(); setSection(0); }}>SafePay</a><div className="topbar-meta"><span className="chip optional"><Icon name="shield" />Policy 2026.10.19-2</span><span className="chip env-chip"><span className="environment-dot" />Local environment</span><span className="user" title={session ? session.roles.join(", ") : "Not signed in"}><span className="avatar">{session ? session.roles[0]?.slice(0, 1).toUpperCase() : "—"}</span><span className="user-text"><b>{who}</b><small>{session ? `Tenant ${session.tenant_id.slice(0, 8)}` : "Synthetic workspace"}</small></span></span></div></header><div className="disclosure" role="note"><span aria-hidden="true">△</span>{SIMULATION_NOTICE}</div><main><header className="page-header"><div><p className="eyebrow"><Icon name={icons[section] ?? "queue"} />{CONSOLE_SECTIONS[section]}</p><h1>{CONSOLE_SECTIONS[section]}</h1><p>{descriptions[section]}</p></div><div className="header-actions"><button className="secondary" disabled={!session || busy} onClick={() => { void refresh(session); }}><Icon name="refresh" />{busy ? "Refreshing…" : "Refresh"}</button></div></header>
      {error ? <Notice>{error}</Notice> : null}{status?.frozen ? <Notice>Workspace frozen: {status.reason}. New payment outflow is blocked.</Notice> : null}
      {section === 0 ? <><div className="summary-strip"><div><span>Pending review</span><strong>{session ? payments.length : "—"}</strong></div><div><span>Review value</span><strong>{session ? money(payments.reduce((sum, payment) => sum + (payment.amount_minor ?? 0), 0)) : "—"}</strong></div><div><span>Four-eyes required</span><strong>{session ? payments.filter((payment) => payment.approvals_required === 2).length : "—"}</strong></div></div><div className="filter-row"><div className="seg" role="group" aria-label="Filter held payments">{FILTERS.map(([label], index) => <button key={label} aria-pressed={filter === index} className={filter === index ? "on" : ""} onClick={() => setFilter(index)}>{label}</button>)}</div><label className="search"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Vendor or reference…" aria-label="Search held payments" /></label></div><div className="queue-grid"><ApprovalTable payments={shown} total={payments.length} vendors={vendors} active={active?.payment_id ?? null} onSelect={setActive} connected={Boolean(session)} />{active && session ? <ApprovalReview key={active.payment_id} payment={active} session={session} onChanged={() => { void refresh(session); }} onExplain={explain} /> : <AuthPanel session={session} onSession={changeSession} />}</div><p className="policy-note">Policy 2026.10.19-2 · Registry-bound payees · Signed decisions</p><div className="workflow"><strong>Review workflow:</strong><span>Decision</span><span aria-hidden="true">⟶</span><span>Independent approval</span><span aria-hidden="true">⟶</span><span>Reserved funds</span></div></> : null}
      {section === 1 ? <DecisionView key={decision?.decision_id ?? "empty"} initial={decision} /> : null}
      {section === 2 ? <MetricsView metrics={metrics} /> : null}
      {section === 3 ? <FreezeView session={session} status={status} onChanged={() => { void refresh(session); }} /> : null}
      {section !== 0 && !session ? <p className="session-hint">Sign in through the approvals queue to access your workspace.</p> : null}
    </main><footer>SafePay Console · Synthetic data, simulated bank · All displayed times are Baku (UTC+4)</footer></div></div>;
}
