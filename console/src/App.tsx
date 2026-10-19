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

export function App() {
  const [section, setSection] = useState(0); const [session, setSession] = useState<Session | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]); const [vendors, setVendors] = useState<Vendor[]>([]);
  const [status, setStatus] = useState<TenantStatus | null>(null); const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [active, setActive] = useState<Payment | null>(null); const [decision, setDecision] = useState<Decision | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
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
  return <div className="app-shell"><aside className="sidebar"><a className="brand" href="#" aria-label="SafePay Console" onClick={(event) => { event.preventDefault(); setSection(0); }}><Icon name="shield" /><span>SafePay<small>Finance operations</small></span></a><nav aria-label="Console sections">{CONSOLE_SECTIONS.map((label, index) => <button key={label} aria-current={section === index ? "page" : undefined} className={section === index ? "active" : ""} onClick={() => setSection(index)}><Icon name={icons[index] ?? "queue"} />{label}</button>)}</nav><div className="environment"><span className="environment-dot" />Local environment</div></aside>
    <div className="workspace"><div className="disclosure" role="note"><span aria-hidden="true">△</span>{SIMULATION_NOTICE}</div><main><header className="page-header"><div><h1>{CONSOLE_SECTIONS[section]}</h1><p>{descriptions[section]}</p></div><div className="header-actions"><button className="secondary" disabled={!session || busy} onClick={() => { void refresh(session); }}><Icon name="refresh" />{busy ? "Refreshing…" : "Refresh"}</button><span className="avatar" title={session ? session.roles.join(", ") : "Not signed in"}>{session ? session.roles[0]?.slice(0, 2).toUpperCase() : "—"}</span></div></header>
      {error ? <Notice>{error}</Notice> : null}{status?.frozen ? <Notice>Workspace frozen: {status.reason}. New payment outflow is blocked.</Notice> : null}
      {section === 0 ? <><div className="summary-strip"><div><span>Pending review</span><strong>{session ? payments.length : "—"}</strong></div><div><span>Review value</span><strong>{session ? money(payments.reduce((sum, payment) => sum + (payment.amount_minor ?? 0), 0)) : "—"}</strong></div><div><span>Four-eyes required</span><strong>{session ? payments.filter((payment) => payment.approvals_required === 2).length : "—"}</strong></div></div><div className="queue-grid"><ApprovalTable payments={payments} vendors={vendors} active={active?.payment_id ?? null} onSelect={setActive} connected={Boolean(session)} />{active && session ? <ApprovalReview key={active.payment_id} payment={active} session={session} onChanged={() => { void refresh(session); }} onExplain={explain} /> : <AuthPanel session={session} onSession={changeSession} />}</div><p className="policy-note">Policy 2026.10.19-1 · Registry-bound payees · Signed decisions</p><div className="workflow"><strong>Review workflow:</strong><span>Decision</span><span aria-hidden="true">⟶</span><span>Independent approval</span><span aria-hidden="true">⟶</span><span>Reserved funds</span></div></> : null}
      {section === 1 ? <DecisionView key={decision?.decision_id ?? "empty"} initial={decision} /> : null}
      {section === 2 ? <MetricsView metrics={metrics} /> : null}
      {section === 3 ? <FreezeView session={session} status={status} onChanged={() => { void refresh(session); }} /> : null}
      {section !== 0 && !session ? <p className="session-hint">Sign in through the approvals queue to access your workspace.</p> : null}
    </main><footer>SafePay Console · Synthetic data, simulated bank · All displayed times are Baku (UTC+4)</footer></div></div>;
}
