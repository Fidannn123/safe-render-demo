import { useState, type ChangeEvent } from "react";
import { errorText, money } from "./api";
import { EmptyState, Notice } from "./components";

export interface ConfigMetrics {
  scenarios: number; executed: number; passed: number; not_run: number;
  attacker_loss_minor: number; attack_success_rate: number | null;
  benign_success_rate: number | null; false_hold_rate: number | null;
  latency_ms: { p50: number | null; p95: number | null };
}
export interface Metrics {
  run_id: string; seed: number; synthetic: true; mode: string; disclosure: string;
  configs: Record<"B0" | "B1" | "B2", ConfigMetrics>;
  network_detection_lift: number | null; limitations: string[];
}
const pct = (value: number | null) => value === null ? "Not measured" : `${(value * 100).toFixed(1)}%`;
export function MetricsView({ metrics }: { metrics: Metrics | null }) {
  const [loaded, setLoaded] = useState<Metrics | null>(null); const [error, setError] = useState("");
  const current = loaded ?? metrics;
  async function load(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file) return;
    try {
      const value = JSON.parse(await file.text()) as Partial<Metrics>;
      if (value.synthetic !== true || !value.configs?.B0 || !value.configs.B1 || !value.configs.B2 || !value.run_id || !Array.isArray(value.limitations)) throw new Error("This is not a SafePay synthetic metrics bundle.");
      setLoaded(value as Metrics); setError("");
    } catch (failure) { setError(errorText(failure)); }
  }
  const configs = current ? Object.entries(current.configs) : [];
  const maxLoss = Math.max(1, ...configs.map(([, value]) => value.attacker_loss_minor));
  return <><div className="metrics-toolbar"><p className="muted">Read generated metrics from the authenticated API or load a local metrics.json.</p><label className="file-button">Load metrics.json<input type="file" accept="application/json,.json" onChange={(event) => { void load(event); }} /></label></div>{error ? <Notice>{error}</Notice> : null}
    {current ? <><div className="run-info"><strong>{current.run_id}</strong><span>Seed {current.seed}</span><span>{current.mode}</span></div><Notice kind="success">{current.disclosure}</Notice><section className="panel"><h2>Attacker loss by configuration</h2><p className="muted">USD · integer minor units · same scenario seed</p><div className="bars">{configs.map(([key, value]) => <div className="bar-row" key={key}><strong>{key}</strong><div className="bar-track"><div className={`bar ${key.toLowerCase()}`} style={{ width: `${value.attacker_loss_minor / maxLoss * 100}%` }} /></div><span>{money(value.attacker_loss_minor)}</span></div>)}</div><div className="legend"><span>B0 · Direct-transfer baseline</span><span>B1 · Guardian + tiers</span><span>B2 · Guardian + network</span></div></section><section className="panel metrics-table"><h2>Measured outcomes</h2><table><thead><tr><th>Metric</th>{configs.map(([key]) => <th key={key}>{key}</th>)}</tr></thead><tbody><tr><td>Executed / total scenarios</td>{configs.map(([key, value]) => <td key={key}>{value.executed} / {value.scenarios}</td>)}</tr><tr><td>Benign success</td>{configs.map(([key, value]) => <td key={key}>{pct(value.benign_success_rate)}</td>)}</tr><tr><td>Attack success</td>{configs.map(([key, value]) => <td key={key}>{pct(value.attack_success_rate)}</td>)}</tr><tr><td>False hold rate</td>{configs.map(([key, value]) => <td key={key}>{pct(value.false_hold_rate)}</td>)}</tr><tr><td>Decision latency p50 / p95</td>{configs.map(([key, value]) => <td key={key}>{value.latency_ms.p50 === null ? "Not measured" : `${value.latency_ms.p50.toFixed(2)} / ${value.latency_ms.p95?.toFixed(2)} ms`}</td>)}</tr><tr><td>Not run</td>{configs.map(([key, value]) => <td key={key}>{value.not_run}</td>)}</tr></tbody></table></section><section className="panel"><h2>Scope and verification</h2><p>Network detection lift: <strong>{pct(current.network_detection_lift)}</strong></p><ul className="limitations">{current.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul><p className="muted">Loading this file displays results. Verify its signatures and recompute its metrics with <code>make verify-evidence SEED={current.seed}</code>.</p></section></> : <section className="panel"><EmptyState title="Evidence comes from execution">Generate a bundle with make evidence, then refresh or load its metrics.json. No sample results are displayed.</EmptyState></section>}
  </>;
}
