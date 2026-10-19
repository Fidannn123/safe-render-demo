import { useState } from "react";
import { dateTime, errorText, request, type Session, type TenantStatus } from "./api";
import { Notice } from "./components";

export function FreezeView({ session, status, onChanged }: { session: Session | null; status: TenantStatus | null; onChanged: () => void }) {
  const [reason, setReason] = useState(""); const [confirmed, setConfirmed] = useState(false); const [busy, setBusy] = useState(false); const [message, setMessage] = useState(""); const [failed, setFailed] = useState(false);
  async function act() {
    if (!session || !status) return;
    setBusy(true); setMessage("");
    try { await request(`/tenants/${session.tenant_id}/${status.frozen ? "unfreeze" : "freeze"}`, { reason }); setFailed(false); setMessage(status.frozen ? "Workspace unfrozen. Normal policy controls remain active." : "Workspace frozen. New outflow is blocked."); setConfirmed(false); onChanged(); }
    catch (failure) { setFailed(true); setMessage(errorText(failure)); } finally { setBusy(false); }
  }
  return <section className="panel freeze-panel"><span className={`status-dot ${status?.frozen ? "frozen" : ""}`} /><h2>{status ? status.frozen ? "Outflow is frozen" : "Payment controls are active" : "Workspace status unavailable"}</h2><p className="muted">A T4 freeze blocks new payment outflow for this workspace. Unfreezing requires an owner and a fresh step-up code.</p>{status?.frozen ? <dl className="facts"><dt>Frozen at</dt><dd>{dateTime(status.frozen_at)} Baku</dd><dt>Reason</dt><dd>{status.reason}</dd></dl> : null}<label>Reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Describe the incident or resolution" maxLength={500} /></label><label className="checkbox"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>I confirm {status?.frozen ? "the incident is resolved and outflow can resume" : "new payment outflow should stop immediately"}.</span></label>{message ? <Notice kind={failed ? "error" : "success"}>{message}</Notice> : null}<button className={status?.frozen ? "primary" : "danger"} disabled={!session || !status || !confirmed || reason.trim().length < 3 || busy || (status.frozen && !session.roles.includes("owner"))} onClick={() => { void act(); }}>{busy ? "Recording…" : status?.frozen ? "Unfreeze workspace" : "Freeze all outflow"}</button></section>;
}
