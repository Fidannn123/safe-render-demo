import { useState } from "react";
import { dateTime, errorText, money, request, type Payment, type Session, type Vendor } from "./api";
import { EmptyState, Icon, Notice } from "./components";

export function ApprovalTable({ payments, total, vendors, active, onSelect, connected }: { payments: Payment[]; total: number; vendors: Vendor[]; active: string | null; onSelect: (payment: Payment) => void; connected: boolean }) {
  const names = new Map(vendors.map((vendor) => [vendor.vendor_id, vendor.name]));
  return <section className="panel queue-panel"><div className="panel-head"><span className="panel-head-ico"><Icon name="queue" /></span><div><h2>Held payments</h2><p>{payments.length} / {total}</p></div></div><table><thead><tr><th>Payment</th><th>Risk</th><th className="amount">Amount</th></tr></thead><tbody>
    {payments.map((payment) => <tr key={payment.payment_id} className={active === payment.payment_id ? "selected" : ""}><td><button className="row-button" onClick={() => onSelect(payment)}><strong>{names.get(payment.vendor_id ?? "") ?? "Registry vendor"}</strong><span>{payment.reference ?? payment.payment_id.slice(0, 8)} · Due {payment.due_date ?? "—"}</span></button></td><td><span className="risk">{payment.tier ?? "—"} · {payment.state}</span></td><td className="amount"><strong>{money(payment.amount_minor, payment.currency ?? "USD")}</strong></td></tr>)}
  </tbody></table>{payments.length === 0 ? total > 0 ? <EmptyState title="No payments match" icon="search">Change the filter or the search to see the rest of the queue.</EmptyState> : <EmptyState title={connected ? "All caught up" : "Connect your workspace"}>{connected ? "No held payments require review." : "Sign in to load authenticated payments."}</EmptyState> : null}</section>;
}

export function ApprovalReview({ payment, session, onChanged, onExplain }: { payment: Payment; session: Session; onChanged: () => void; onExplain: () => void }) {
  const [reason, setReason] = useState(""); const [ack, setAck] = useState<string[]>([]); const [rejectReason, setRejectReason] = useState("not_now");
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState(""); const [failed, setFailed] = useState(false);
  const initiator = payment.initiated_by as { id?: string; actor_id?: string } | null;
  const self = (initiator?.id ?? initiator?.actor_id) === session.user_id;
  const canApprove = session.roles.some((role) => role === "owner" || role === "approver");
  async function act(action: "approve" | "reject" | "veto") {
    setBusy(true); setMessage("");
    try {
      await request(`/payments/${payment.payment_id}/${action}`, { reason, ...(action === "approve" ? { acknowledged_warnings: ack } : action === "reject" ? { reject_reason: rejectReason } : {}) });
      setFailed(false); setMessage(`${action === "approve" ? "Approval" : action === "veto" ? "Veto" : "Rejection"} recorded. The server rechecks every required control.`); onChanged();
    } catch (failure) { setFailed(true); setMessage(errorText(failure)); } finally { setBusy(false); }
  }
  return <section className="panel review-panel"><div className="panel-title"><h2>Payment review</h2><button className="text-button" onClick={onExplain}>Explain decision →</button></div>
    <p className="review-amount">{money(payment.amount_minor, payment.currency ?? "USD")}</p><dl className="facts"><dt>Reference</dt><dd>{payment.reference}</dd><dt>Approval progress</dt><dd>{payment.approvals.filter((approval) => approval.action === "APPROVE").length} / {payment.approvals_required} independent approvers</dd><dt>Cool-off ends</dt><dd>{dateTime(payment.cooloff_until)}</dd></dl>
    {self ? <Notice>You initiated this payment. An independent person must approve it.</Notice> : null}
    <label>Review reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} minLength={3} maxLength={500} placeholder="Record the checks you performed" /></label>
    {payment.approval_warnings.map((warning) => <label key={warning.code} className="checkbox"><input type="checkbox" checked={ack.includes(warning.code)} onChange={(event) => setAck(event.target.checked ? [...ack, warning.code] : ack.filter((code) => code !== warning.code))} /><span>{warning.code}: {warning.detail}</span></label>)}
    <label>Rejection category<select value={rejectReason} onChange={(event) => setRejectReason(event.target.value)}><option value="not_now">Not now — retain payable</option><option value="fraud_or_invalid">Fraud or invalid — reverse accrual</option></select></label>
    {message ? <Notice kind={failed ? "error" : "success"}>{message}</Notice> : null}<div className="actions"><button className="primary" disabled={busy || reason.trim().length < 3 || self || !canApprove || payment.state !== "HELD"} onClick={() => { void act("approve"); }}>Approve payment</button><button className="secondary" disabled={busy || reason.trim().length < 3 || !canApprove || payment.state !== "HELD"} onClick={() => { void act("reject"); }}>Reject</button>{payment.state === "RESERVED" && payment.tier === "T2" ? <button className="danger" disabled={busy || reason.trim().length < 3} onClick={() => { void act("veto"); }}>Veto</button> : null}</div>
  </section>;
}
