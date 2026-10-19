import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  ArrowLeft, ArrowUpRight, BadgeCheck, BookOpen, CalendarClock, ChartLine, CircleCheck, Cpu, Database, FileSearch, Fingerprint,
  Gauge, HardHat, Landmark, Laptop, LayoutDashboard, ListChecks, Menu, MessagesSquare, Network, Pill, Plus, Scale, ScanLine,
  ShieldCheck, Shirt, ShoppingBag, Snowflake, Target, Terminal, TriangleAlert, Truck, UserCheck, UtensilsCrossed, Wheat, X,
} from "lucide-react";
import { Arrow, Btn, Count, Eyebrow, Rv, heroMap, sayMap, useCount, useInOnce, useNavTone, useScrollVar, type Glyph } from "./lp";
import { SIMULATION_NOTICE } from "./labels";
import "./landing.css";

// The public page in front of the console, in the MuhAI design. Every figure on it is an illustration
// on synthetic values; measured results live in the README and in evidence/, never typed in here.

const REPO = "https://github.com/eminroot/safe";
const doc = (path: string) => `${REPO}/blob/main/${path}`;
const CONSOLE = "/console";

/* ---------- the console mock, drawn in CSS and SVG rather than screenshotted ---------- */

const FORECAST = [64, 61, 58, 60, 55, 51, 47, 44, 48, 52, 50, 55, 59, 63, 61, 67];
const FLOOR = 42;

function Spark({ data = FORECAST, floor }: { data?: number[]; floor?: number }) {
  const max = Math.max(...data, floor ?? -Infinity); const min = Math.min(...data, floor ?? Infinity);
  const y = (v: number) => 100 - ((v - min) / (max - min)) * 88 - 6;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * 100, y(v)] as const);
  const d = pts.map(([x, py], i) => {
    const prev = pts[i - 1];
    if (!prev) return `M${x} ${py}`;
    const cx = (prev[0] + x) / 2;
    return `C${cx} ${prev[1]} ${cx} ${py} ${x} ${py}`;
  }).join(" ");
  return <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    {data.map((_, i) => <line key={i} x1={(i / (data.length - 1)) * 100} y1="0" x2={(i / (data.length - 1)) * 100} y2="100" stroke="rgba(36,36,38,.1)" strokeWidth="0.5" vectorEffect="non-scaling-stroke" />)}
    {floor === undefined ? null : <line x1="0" x2="100" y1={y(floor)} y2={y(floor)} stroke="#a4433a" strokeWidth="1" strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />}
    <path d={d} fill="none" stroke="#242426" strokeWidth="2" vectorEffect="non-scaling-stroke" />
  </svg>;
}

// Start poses for the four cards that assemble into the grid as the hero scrolls; the stylesheet
// interpolates them against --p.
const POSE = [
  { tx: -55, ty: -55, rx: -12.1, ry: 2.2, rz: -8.4, sc: 0.9 },
  { tx: -74, ty: -172, rx: -5.6, ry: 8.8, rz: -10.7, sc: 0.9 },
  { tx: 122, ty: -333, rx: -0.9, ry: -9.1, rz: 10.2, sc: 1.1 },
  { tx: 36, ty: -24, rx: 0, ry: 0, rz: 3, sc: 1.1 },
];
const poseVars = (i: number, live: boolean) => {
  const p = POSE[i];
  return live && p ? ({ "--tx": p.tx, "--ty": p.ty, "--rx": p.rx, "--ry": p.ry, "--rz": p.rz, "--sc": p.sc } as CSSProperties) : undefined;
};

const BRK = ["#8b86c9", "#6aa5e0", "#5aa9a2", "#9db45f", "#e2c24f"];
const OUTFLOW = [["Caspian Packaging", "48%", "$18,450", 48], ["Ganja Textile Supply", "25%", "$9,600", 25], ["Absheron Logistics", "14%", "$5,380", 14], ["Baku Office Systems", "9%", "$3,460", 9], ["Utilities", "4%", "$1,550", 4]] as const;

function AppMock({ live = false }: { live?: boolean }) {
  return <div className="lp-app">
    <div className="lp-app__rail" aria-hidden="true">
      <i className="on"><ListChecks /></i><i><FileSearch /></i><i><ChartLine /></i><i><Snowflake /></i>
      <span className="sp" /><span className="lp-app__me" />
    </div>
    <div className="lp-app__main">
      <div className="lp-app__bar">
        <div><div className="lp-app__ttl">Payment guardian</div><div className="lp-app__sub">No payment breaks the cash buffer</div></div>
        <span className="lp-app__pick">Synthetic twin<Plus style={{ width: 13, height: 13, opacity: 0.4 }} /></span>
        <span className="lp-app__plus" aria-hidden="true"><Plus /></span>
      </div>
      <div className="lp-app__grid">
        <div className="lp-hcard lp-hcard--tall" style={poseVars(0, live)}>
          <div className="lp-panel">
            <div className="lp-panel__top"><div><div className="lp-panel__k">Cash forecast</div></div><span className="lp-tag lp-tag--dark lp-ml">13 weeks</span></div>
            <div className="lp-panel__big"><span>$</span>48,200</div>
            <div className="lp-spark"><Spark floor={FLOOR} /><div className="lp-spark__x"><span>Week 1</span><span>Week 7</span><span>Week 13</span></div></div>
            <div className="lp-panel__foot lp-pt14"><div className="lp-panel__cell"><b>$20,000</b><span>Buffer floor</span></div><div className="lp-panel__cell"><b>Week 7</b><span>Tightest week</span></div></div>
          </div>
        </div>
        <div className="lp-hcard" style={poseVars(1, live)}>
          <div className="lp-panel">
            <div className="lp-panel__top"><div><div className="lp-panel__k">Held for review</div><div className="lp-panel__d">Waiting on an approver</div></div><span className="lp-tag lp-tag--warn lp-ml">T2</span></div>
            <div className="lp-panel__big lp-mta">3</div>
          </div>
        </div>
        <div className="lp-hcard" style={poseVars(2, live)}>
          <div className="lp-panel">
            <div className="lp-panel__top"><div><div className="lp-panel__k">Guardian</div></div><span className="lp-tag lp-tag--ok lp-ml">Signed</span></div>
            <div className="lp-days"><i className="on"><b>T1</b>Auto</i><i><b>T2</b>Cool-off</i><i><b>T3</b>Human</i></div>
            <div className="lp-panel__big lp-mta">62<small>/100</small></div>
          </div>
        </div>
        <div className="lp-hcard lp-hcard--wide" style={poseVars(3, live)}>
          <div className="lp-panel">
            <div className="lp-panel__top"><div><div className="lp-panel__k">Outflow this week</div><div className="lp-panel__d">By supplier</div></div><span className="lp-tag lp-ml">USD</span></div>
            <div className="lp-bars" aria-hidden="true">{OUTFLOW.map(([k, , , n], i) => <i key={k} style={{ flex: n, background: BRK[i] }} />)}</div>
            <div className="lp-brk">{OUTFLOW.map(([k, p, v], i) => <div className="lp-brk__row" key={k}><i style={{ background: BRK[i] }} /><span>{k}</span><em>{p}</em><b>{v}</b></div>)}</div>
          </div>
        </div>
      </div>
    </div>
  </div>;
}

/* ---------- slab visuals: small floating panels on the photograph half of each slab ---------- */

function Float({ className = "", style, children }: { className?: string; style?: CSSProperties; children: ReactNode }) {
  return <div className={`lp-panel lp-float ${className}`} style={style}>{children}</div>;
}

type Tone = "ok" | "warn" | "stop" | "lime" | "dark" | "";
const tag = (tone: Tone) => `lp-tag${tone ? ` lp-tag--${tone}` : ""}`;

function ReadMock() {
  const rows: [string, string, Tone][] = [["Vendor", "Caspian Packaging LLC", ""], ["Invoice", "INV-2026-1184", ""], ["Amount", "$18,450.00", ""], ["Bank details in text", "ignored", "warn"]];
  return <Float>
    <div className="lp-panel__top"><div className="lp-panel__k">Extracted fields</div><span className={`${tag("ok")} lp-ml`}>schema ok</span></div>
    <div className="lp-brk lp-pt14">{rows.map(([k, v, tone]) => <div className="lp-brk__row" key={k}><span className="lp-soft">{k}</span>{tone ? <span className={`${tag(tone)} lp-ml`}>{v}</span> : <b className="lp-ml">{v}</b>}</div>)}</div>
  </Float>;
}

function GuardMock() {
  const rows: [string, string, Tone][] = [["Payee in registry", "pass", "ok"], ["Rolling 24 h cap", "pass", "ok"], ["New payee", "+25", "warn"], ["Amount spike", "+22", "warn"]];
  return <Float>
    <div className="lp-panel__top"><div className="lp-panel__k">Policy checks</div><span className={`${tag("")} lp-ml`}>2026.10.19-2</span></div>
    <div className="lp-brk lp-pt14">{rows.map(([k, v, tone]) => <div className="lp-brk__row" key={k}><span>{k}</span><span className={`${tag(tone)} lp-ml`}>{v}</span></div>)}</div>
  </Float>;
}

function CashMock() {
  return <Float>
    <div className="lp-panel__top"><div className="lp-panel__k">13-week forecast</div><span className={`${tag("lime")} lp-ml`}>Buffer</span></div>
    <div className="lp-spark lp-spark--fixed"><Spark floor={FLOOR} /></div>
    <div className="lp-panel__foot lp-pt14"><div className="lp-panel__cell"><b>$48,200</b><span>Projected</span></div><div className="lp-panel__cell"><b>Week 7</b><span>Tightest week</span></div></div>
  </Float>;
}

function NetMock() {
  const rows: [string, string, string, Tone][] = [["Caspian Packaging", "known", "clean", "ok"], ["Absheron Logistics", "known", "clean", "ok"], ["New payee ••4417", "new", "2 reports", "warn"]];
  return <Float>
    <div className="lp-panel__top"><div className="lp-panel__k">Network lookup</div><span className={`${tag("")} lp-ml`}>VOPRF</span></div>
    <div className="lp-brk lp-pt14">{rows.map(([k, v, p, tone]) => <div className="lp-brk__row" key={k}><span>{k}</span><b className="lp-ml">{v}</b><span className={tag(tone)}>{p}</span></div>)}</div>
  </Float>;
}

const SLAB_ART = [ReadMock, GuardMock, CashMock, NetMock];
const SLAB_ICON: Glyph[] = [ScanLine, Scale, ChartLine, Network];
const SLAB_ART_BG = ["/img/art-1.jpg", "/img/art-2.jpg", "/img/art-3.jpg", "/img/art-4.jpg"];

/* ---------- bento visuals ---------- */

function TiersMock() {
  const pills = ["T1 auto pay", "T2 cool-off", "T3 human", "T4 freeze", "Owner veto", "Step-up OTP"];
  // [left offset %, rotation deg]: tilted so the scatter reads as on purpose
  const lay = [[22, -7], [2, 4], [38, -5], [8, 6], [30, -4], [14, 5]];
  return <div className="lp-tiers">{pills.map((x, i) => <span key={x} className="lp-pill" style={{ "--off": `${lay[i]?.[0] ?? 0}%`, transform: `rotate(${lay[i]?.[1] ?? 0}deg)` } as CSSProperties}>{x}</span>)}</div>;
}

function SettleMock() {
  const [ref, on] = useInOnce<HTMLDivElement>();
  return <div ref={ref} className="lp-art-pad">
    <Float className="lp-float--line">
      <div className="lp-panel__top"><span className={tag("ok")}>Settled</span><span className="lp-meta lp-ml">1 min ago</span></div>
      <div className="lp-panel__big lp-pt10"><span>−$</span><Count to={18450} on={on} /></div>
      <div className="lp-rule" />
      <div className="lp-meta lp-pt10">Matched to INV-2026-1184, signed receipt</div>
    </Float>
  </div>;
}

function ScoreMock() {
  const [ref, on] = useInOnce<HTMLDivElement>();
  const r = 56; const c = 2 * Math.PI * r; const pct = 0.62;
  const score = useCount(62, on);
  return <div ref={ref} className="lp-ring">
    <svg viewBox="0 0 140 140" aria-hidden="true">
      <circle cx="70" cy="70" r={r} fill="none" stroke="rgba(36,36,38,.08)" strokeWidth="8" />
      <circle cx="70" cy="70" r={r} fill="none" stroke="#35363b" strokeWidth="8" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={on ? c * (1 - pct) : c} className="lp-ring__arc" transform="rotate(-90 70 70)" />
      <text x="70" y="72" textAnchor="middle" fontSize="32" fill="#242426" letterSpacing="-1.2" className="lp-num">{Math.round(score)}</text>
      <text x="70" y="90" textAnchor="middle" fontSize="9.5" fill="rgba(36,36,38,.5)">risk score</text>
    </svg>
  </div>;
}

function ApproveMock() {
  const rows: [string, string, number, Tone, string][] = [["INV-2026-1184", "Caspian Packaging", 18450, "warn", "Held"], ["INV-2026-1190", "Absheron Logistics", 4126, "ok", "Approved"]];
  const [ref, on] = useInOnce<HTMLDivElement>();
  return <div ref={ref} className="lp-art-pad lp-stack10">{rows.map(([no, who, amt, tone, label]) => <Float key={no} className="lp-float--line">
    <div className="lp-panel__top"><span className="lp-meta">{no}</span><span className={`${tag(tone)} lp-ml`}>{label}</span></div>
    <div className="lp-who">{who}</div>
    <div className="lp-brk__row lp-pt8"><span className="lp-meta">Supplier invoice</span><b className="lp-ml"><Count to={amt} on={on} pre="$" /></b></div>
  </Float>)}</div>;
}

function DueMock() {
  const [ref, on] = useInOnce<HTMLDivElement>();
  const side = (label: string, amount: number) => <Float key={label} className="lp-due__side"><div className="lp-due__v"><Count to={amount} on={on} pre="$" /></div><div className="lp-due__k">{label}</div></Float>;
  return <div ref={ref} className="lp-due">
    {side("Rent", 3200)}
    <Float className="lp-due__mid">
      <div className="lp-due__ico"><CalendarClock /></div>
      <div className="lp-due__big"><Count to={9600} on={on} pre="$" /></div>
      <div className="lp-meta">Due 27 Oct, buffer kept</div>
    </Float>
    {side("Utilities", 610)}
  </div>;
}

function AlertMock() {
  return <div className="lp-art-pad">
    <Float className="lp-float--line lp-alert">
      <div className="lp-alert__top">
        <span className="lp-alert__ico"><TriangleAlert /></span>
        <div className="lp-alert__txt"><div>Duplicate payment</div><div className="lp-meta lp-ellipsis">INV-2026-1184 · $18,450.00</div></div>
      </div>
      <div className="lp-alert__foot">Refused at COMMIT, books stay balanced<span className="lp-alert__ok"><CircleCheck /></span></div>
    </Float>
  </div>;
}

const BENTO_ART = [TiersMock, SettleMock, ScoreMock, ApproveMock, DueMock, AlertMock];
const BENTO_ICON: Glyph[] = [ShieldCheck, Landmark, Gauge, UserCheck, CalendarClock, Database];
// three columns of alternating tall and short cards, so the seams never line up: [card, height, tone]
const BENTO_COLS: [number, "tall" | "short", "a" | "b" | "c"][][] = [
  [[0, "tall", "a"], [4, "short", "c"]],
  [[1, "short", "b"], [3, "tall", "a"]],
  [[2, "tall", "c"], [5, "short", "b"]],
];

/* ---------- copy ---------- */

const SECTORS: [string, Glyph][] = [["Retail", ShoppingBag], ["Construction", HardHat], ["Restaurant", UtensilsCrossed], ["Logistics", Truck], ["Pharmacy", Pill], ["Agriculture", Wheat], ["IT services", Laptop], ["Textiles", Shirt]];

const SAY = "An agent that can pay can be talked into paying the wrong person. SafePay lets it do the work and never lets it decide where the money goes.";
const TRIO: [string, string, Glyph][] = [["Registry-bound payees", "Never from the invoice", Landmark], ["Signed decisions", "Ed25519, one payment", BadgeCheck], ["Blind network lookups", "RFC 9497 VOPRF", Fingerprint]];

const SLABS = [
  { badge: "Extraction", title: "The model only reads.", body: "The extractor has no tools and answers in a strict schema. Invoice text stays tagged untrusted and never reaches the planner, whose only consequential tool is propose_payment.", foot: "Hidden instructions in an invoice go nowhere." },
  { badge: "Guardian", title: "Rules decide where the money goes.", body: "Deterministic checks, caps over a rolling 24 hours and a risk score produce a Decision that is signed and bound to one exact payment. Its tier decides what happens next.", foot: "Every number shown comes from a deterministic function." },
  { badge: "Cash forecast", title: "No payment breaks your cash buffer.", body: "A 13-week forecast runs before every payment. When paying now would break the buffer, SafePay schedules the invoice or raises a cash-flow alert instead.", foot: "Paid on time, never at the cost of payroll." },
  { badge: "Fraud network", title: "Learn from others, tell them nothing.", body: "Bank-attested reports from other businesses flag accounts used in fraud. Lookups are blind, so the network operator never learns who you pay. Signals add risk and can hold a payment; on their own they never deny one.", foot: "Network down: new payees hold, known payees continue." },
];

const BENTO = [
  { badge: "Tiers", title: "Four permission tiers", body: "T1 pays, T2 waits out a cool-off, T3 needs a person, T4 freezes. The agent cannot change its own tier." },
  { badge: "Reconcile", title: "Matched to the bank", body: "Signed receipts and statements from the simulated bank close every payment." },
  { badge: "Explain", title: "Every verdict explained", body: "Each risk signal and its weight, next to the signed decision fields." },
  { badge: "Approve", title: "Four eyes on large payments", body: "Whoever issued the agent's token cannot approve the agent's payments." },
  { badge: "Schedule", title: "Paid on time, not early", body: "Payables are scheduled against the due date and the forecast." },
  { badge: "Ledger", title: "The database says no", body: "Balanced entries at COMMIT, no overdraft, append-only ledger and no double payment." },
];

const SHOW: [string, string, Glyph][] = [["Approvals queue", "Held payments with their risk tier and due date.", LayoutDashboard], ["Decision explainer", "Every signal, its weight and the signed fields.", FileSearch], ["Freeze", "Stop all new outflow for a workspace in one step.", Snowflake]];

type Mode = "docker" | "native";
const STEPS: { name: string; note: string; cmd: Record<Mode, string>; per: Record<Mode, string>; cta: [string, string]; feats: string[] }[] = [
  { name: "Start", note: "Postgres, core, intel, the simulated bank and this console.", cmd: { docker: "make up", native: "make native-up" }, per: { docker: "Console on 127.0.0.1:8080", native: "Console on 127.0.0.1:5173" }, cta: ["Open console", CONSOLE], feats: ["Keys generated on first start", "Every service bound to 127.0.0.1", "Strict CSP, bundled assets only", "No cloud account needed"] },
  { name: "Seed and demo", note: "Eight synthetic businesses and a scripted walkthrough.", cmd: { docker: "make demo", native: "make demo" }, per: { docker: "After make seed", native: "After make seed" }, cta: ["Read the demo script", doc("docs/DEMO_SCRIPT.md")], feats: ["Users with TOTP one-time codes", "Vendor registry and 90 days of history", "Attacks, network and freeze on the live stack", "make hold leaves a payment to approve"] },
  { name: "Measure", note: "B0, B1 and B2 on the same synthetic invoices and seed.", cmd: { docker: "make evidence", native: "make evidence" }, per: { docker: "Then make verify-evidence", native: "Then make verify-evidence" }, cta: ["See the results", `${REPO}#results`], feats: ["Signed evidence bundle", "README results rewritten from it", "Offline re-check of every signature", "No number typed by hand"] },
];

const SPOT = [
  { title: "Extraction", body: "The LLM reads invoices with no tools and a strict output schema. Raw text stays opaque and tagged untrusted.", facts: [["Tools", "None"], ["Output", "Pydantic schema"], ["Proof", "test_injection.py"]] },
  { title: "Payee binding", body: "Money only goes to accounts in the vendor registry. Editing the registry needs a person and a fresh step-up code.", facts: [["Account source", "Vendor registry"], ["From the invoice", "Never"], ["Proof", "make demo, steps 3 and 4"]] },
  { title: "Guardian", body: "Deterministic rules, caps and a risk score. The Decision is signed and bound to the exact payment it allows.", facts: [["Signature", "Ed25519, RFC 8785 JSON"], ["Caps", "Rolling 24 hours"], ["Proof", "core/apps/guardian/tests"]] },
  { title: "Database", body: "Invariants live in PostgreSQL, not only in Python, so a bug in the app still cannot break the books.", facts: [["Postings", "Sum to zero at COMMIT"], ["Overdraft", "CHECK balance ≥ 0"], ["Ledger and audit", "Insert only"]] },
  { title: "Fraud network", body: "Reports come from other businesses and are attested by their bank. Lookups are blinded before they leave the tenant.", facts: [["Lookup", "VOPRF, RFC 9497"], ["Operator learns", "Nothing about the payee"], ["Proof", "intel/tests and make evidence"]] },
  { title: "Freeze", body: "A hijacked agent that keeps probing freezes its own tenant. Only an owner with a fresh code can lift it.", facts: [["Trigger", "Repeated denials"], ["Effect", "All new outflow stops"], ["Proof", "test_api_flows.py"]] },
];
const SPOT_ICON: Glyph[] = [ScanLine, Landmark, Scale, Database, Network, Snowflake];

const FAQ_TABS = ["General", "Safety", "Data", "Running it"];
const FAQ: { q: string; a: string }[][] = [
  [
    { q: "What is SafePay?", a: "A cash-flow-safe payables copilot for small businesses. An AI agent prepares supplier payments, and a payment only moves when deterministic rules, a cash forecast and a privacy-preserving fraud network all agree." },
    { q: "Who is it for?", a: "Owners, approvers and accountants of small businesses who want invoices paid on time without handing an agent the keys to the bank account." },
    { q: "What else does the agent do?", a: "Besides paying invoices it schedules payables, raises cash-flow alerts, answers financial-analysis questions and matches financing options. Every number it shows comes from a deterministic function." },
  ],
  [
    { q: "Can the agent send money on its own?", a: "It can propose a payment. The Guardian, the permission tiers and the database decide whether it moves. The agent cannot choose the payee account, its tier or the policy." },
    { q: "What if an invoice changes the bank details?", a: "Nothing happens. The payee account always comes from the vendor registry, and only a person with a fresh step-up code can edit the registry." },
    { q: "What if the fraud network is down?", a: "New payees are held, so the system fails closed. Payments to suppliers you have paid before continue." },
  ],
  [
    { q: "Is any of this real money?", a: "No. This is a prototype: every business, invoice and payment is synthetic, and the bank is simulated." },
    { q: "What does the network operator see?", a: "Blinded lookups. It cannot tell which account a business asked about; the operator privacy attack in make evidence checks exactly that." },
    { q: "Which LLM does it use?", a: "A deterministic mock by default, in every test and in CI. A real model is optional through SAFEPAY_LLM=anthropic." },
  ],
  [
    { q: "How do I sign in?", a: "make seed prints the users. The harness gives each user's password and current one-time code: uv run python -m safepay_sim.harness otp logistics.approver --password." },
    { q: "Does it need the cloud?", a: "No. One Docker Compose stack on one machine, every service bound to 127.0.0.1. A native runner works without Docker." },
    { q: "Where are the results?", a: "make evidence writes a signed bundle to evidence/run-42 and rewrites the results in the README. make verify-evidence re-checks it offline." },
  ],
];

const FOOT: [string, [string, string][]][] = [
  ["Console", [["Approvals queue", CONSOLE], ["Decision explainer", CONSOLE], ["Metrics", CONSOLE], ["Freeze", CONSOLE]]],
  ["Docs", [["Architecture", doc("docs/architecture.md")], ["Security", doc("docs/SECURITY.md")], ["Decisions", doc("docs/DECISIONS.md")], ["Demo script", doc("docs/DEMO_SCRIPT.md")]]],
  ["Run it", [["Quickstart", `${REPO}#quickstart-with-docker`], ["Without Docker", `${REPO}#without-docker-windows-macos-linux`], ["Real LLM", `${REPO}#measuring-a-real-llm`]]],
  ["Project", [["Source on GitHub", REPO], ["Results", `${REPO}#results`], ["Honest limits", `${REPO}#status-and-honest-limits`]]],
];

const external = (href: string) => (/^https?:/.test(href) ? { target: "_blank", rel: "noreferrer" } : {});

/* ============================================================ the page */

export function Landing() {
  const page = useRef<HTMLDivElement>(null); const stage = useRef<HTMLDivElement>(null);
  const sayRef = useRef<HTMLDivElement>(null); const track = useRef<HTMLDivElement>(null);
  useScrollVar(stage, heroMap); useScrollVar(sayRef, sayMap);
  const [stuck, setStuck] = useState(false); const [navDark, setNavDark] = useState(false); const [menu, setMenu] = useState(false);
  const [mode, setMode] = useState<Mode>("docker"); const [tab, setTab] = useState(0); const [open, setOpen] = useState(0); const [slide, setSlide] = useState(0);
  useNavTone(page, setNavDark);

  useEffect(() => {
    document.title = "SafePay: cash-flow-safe payables for small businesses";
    const onScroll = () => setStuck(window.scrollY > 12);
    const frame = window.requestAnimationFrame(onScroll);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => { window.cancelAnimationFrame(frame); window.removeEventListener("scroll", onScroll); };
  }, []);
  // the drawer owns the scroll lock while it is open
  useEffect(() => {
    if (!menu) return undefined;
    const prev = document.body.style.overflow; document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [menu]);

  const words = SAY.split(/\s+/);
  const onTrack = useCallback(() => {
    const el = track.current; if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setSlide(max <= 0 ? 0 : Math.round((el.scrollLeft / max) * (SPOT.length - 1)));
  }, []);
  const move = (dir: number) => {
    const el = track.current; if (!el) return;
    const card = el.querySelector(".lp-rcard");
    el.scrollBy({ left: dir * (card ? card.getBoundingClientRect().width + 20 : el.clientWidth), behavior: "smooth" });
  };
  const links: [string, string][] = [["#product", "Product"], ["#how", "How it works"], ["#run", "Run it"], ["#faq", "FAQ"]];

  return <div className="lp" ref={page}>
    {/* ---------- nav ---------- */}
    <div className={`lp-navwrap${stuck ? " is-stuck" : ""}${navDark ? " is-dark" : ""}`}>
      <Rv as="nav" y={-56} now className="lp-nav">
        <a className="lp-nav__brand" href="/">SafePay</a>
        <div className="lp-nav__links">{links.map(([href, label]) => <a key={href} href={href}>{label}</a>)}</div>
        <div className="lp-nav__right">
          <button className="lp-burger" onClick={() => setMenu(true)} aria-label="Open menu"><Menu style={{ width: 18, height: 18 }} /></button>
          <a className="lp-nav__login" href={CONSOLE}>Sign in</a>
          <Btn href={CONSOLE} label="Open console" size="sm" />
        </div>
      </Rv>
    </div>
    <div className={`lp-drawer${menu ? " is-open" : ""}`}>
      <div className="lp-drawer__top"><a className="lp-nav__brand" href="/" onClick={() => setMenu(false)}>SafePay</a><button className="lp-burger" onClick={() => setMenu(false)} aria-label="Close menu"><X style={{ width: 18, height: 18 }} /></button></div>
      <div className="lp-drawer__body">
        {links.map(([href, label]) => <a key={href} href={href} onClick={() => setMenu(false)}>{label}</a>)}
        <a href={CONSOLE}>Sign in</a>
        <div className="lp-drawer__foot"><Btn href={CONSOLE} label="Open console" onClick={() => setMenu(false)} /></div>
      </div>
    </div>

    {/* ---------- hero ---------- */}
    <header className="lp-hero">
      <div className="lp-shell">
        <div className="lp-hero__head">
          <Rv as="h1" y={48} delay={60} className="lp-d1 lp-hero__title">Your agent pays. The rules decide.</Rv>
          <Rv as="p" y={72} delay={120} className="lp-lead lp-hero__lead">SafePay prepares supplier payments and holds each one until fixed rules, a cash forecast and a blind fraud check agree. Built for small businesses.</Rv>
          <Rv y={96} delay={180} className="lp-hero__cta">
            <Btn href={CONSOLE} label="Open console" size="lg" />
            <div className="lp-trust">
              <div className="lp-trust__row" aria-hidden="true">{[ShieldCheck, Landmark, Network, Snowflake].map((I, i) => <span className="lp-trust__dot" key={i}><I style={{ width: 14, height: 14 }} /></span>)}</div>
              <span className="lp-trust__label">Synthetic data, simulated bank</span>
            </div>
          </Rv>
        </div>
      </div>
      <div className="lp-shell"><Rv y={128} delay={240} className="lp-rv--zoom"><div className="lp-stage" ref={stage}><AppMock live /></div></Rv></div>
    </header>

    {/* ---------- sector strip ---------- */}
    <section className="lp-sec lp-strip" id="product">
      <div className="lp-shell">
        <Rv className="lp-strip__bar"><p>Tested on eight synthetic small businesses, one per trade</p><a className="lp-strip__link" href="#how"><i><ArrowUpRight /></i>See how a payment flows</a></Rv>
        <Rv delay={80} className="lp-strip__panel">{SECTORS.map(([s, I]) => <div className="lp-strip__item" key={s}><I /><span>{s}</span></div>)}</Rv>
      </div>
    </section>

    {/* ---------- statement + pinned slabs ---------- */}
    <section className="lp-sec lp-stack" id="how">
      <div className="lp-stack__pin">
        <div className="lp-say" ref={sayRef}>
          <div className="lp-shell">
            <h2 className="lp-say__text" style={{ "--n": words.length } as CSSProperties}>{words.map((w, i) => <span className="lp-say__w" key={i} style={{ "--i": i } as CSSProperties}>{w}{i < words.length - 1 ? " " : ""}</span>)}</h2>
            <div className="lp-say__trio">{TRIO.map(([b, s, I]) => <div className="lp-say__cell" key={b}><span className="lp-say__ico"><I /></span><div className="lp-min0"><b>{b}</b><span>{s}</span></div></div>)}</div>
          </div>
        </div>
        <div className="lp-slabs">{SLABS.map((s, i) => {
          const Art = SLAB_ART[i] ?? ReadMock; const I = SLAB_ICON[i] ?? ScanLine;
          return <div className={`lp-slab${i % 2 ? " lp-slab--flip" : ""}`} key={s.badge}>
            <div className="lp-shell lp-w100">
              <div className="lp-slab__card">
                <div className="lp-slab__say"><Eyebrow icon={I} tone="flat">{s.badge}</Eyebrow><h3 className="lp-d4">{s.title}</h3><p className="lp-lead">{s.body}</p><div className="lp-slab__foot"><CircleCheck />{s.foot}</div></div>
                <div className="lp-slab__art" style={{ "--art": `url(${SLAB_ART_BG[i % SLAB_ART_BG.length]})` } as CSSProperties}>
                  <span className="lp-slab__ghost lp-slab__ghost--a" aria-hidden="true" /><span className="lp-slab__ghost lp-slab__ghost--b" aria-hidden="true" />
                  <div className="lp-slab__mock"><Art /></div>
                </div>
              </div>
            </div>
          </div>;
        })}</div>
      </div>
    </section>

    {/* ---------- bento ---------- */}
    <section className="lp-sec lp-sec--pad lp-bento">
      <div className="lp-shell">
        <div className="lp-bento__head">
          <Rv><Eyebrow icon={ListChecks} tone="flat">Inside the console</Eyebrow></Rv>
          <Rv as="h2" delay={60} className="lp-d3">Everything an approver needs, nothing the agent can change</Rv>
          <Rv as="p" delay={120} className="lp-lead">A small surface you can explain in a minute, with a test or a command behind every claim.</Rv>
        </div>
        <div className="lp-bento__grid">{BENTO_COLS.map((col, ci) => <div className="lp-bento__col" key={ci}>{col.map(([i, size, tone], ri) => {
          const c = BENTO[i]; const Art = BENTO_ART[i]; const I = BENTO_ICON[i];
          if (!c || !Art || !I) return null;
          return <Rv key={c.title} y={32} delay={ci * 90 + ri * 60} className={`lp-bcard lp-bcard--${size} lp-bcard--${tone}`}>
            <Eyebrow icon={I}>{c.badge}</Eyebrow><h3 className="lp-d5 lp-bcard__title">{c.title}</h3><p className="lp-bcard__body">{c.body}</p>
            <div className="lp-bcard__art"><Art /></div>
          </Rv>;
        })}</div>)}</div>
      </div>
    </section>

    {/* ---------- showcase ---------- */}
    <section className="lp-sec lp-show">
      <div className="lp-show__slab lp-dark">
        <div className="lp-show__head">
          <Rv><Eyebrow icon={LayoutDashboard} tone="dark">The console</Eyebrow></Rv>
          <Rv as="h2" delay={60} className="lp-d3">One screen for the people who sign off</Rv>
          <Rv as="p" delay={120} className="lp-lead lp-lead--dark">Held payments, the reason behind every verdict, measured results from the evidence harness and a freeze switch. Synthetic data and a simulated bank, labelled on every screen.</Rv>
        </div>
        <Rv delay={160} className="lp-show__frame"><div className="lp-show__glass"><div className="lp-show__clip"><AppMock /></div></div></Rv>
        <div className="lp-show__list">{SHOW.map(([t, d, I], i) => <Rv key={t} delay={i * 90} className="lp-show__cell"><b><i><I /></i>{t}</b><span>{d}</span></Rv>)}</div>
      </div>
    </section>

    {/* ---------- run it ---------- */}
    <section className="lp-sec lp-sec--pad lp-price" id="run">
      <div className="lp-shell">
        <div className="lp-price__head">
          <Rv><Eyebrow icon={Terminal} tone="flat">Run it</Eyebrow></Rv>
          <Rv as="h2" delay={60} className="lp-d3">Three commands to a working <span className="lp-price__hi">stack</span></Rv>
          <Rv as="p" delay={120} className="lp-lead">One machine, no cloud account, every service on 127.0.0.1. Docker, or a native runner for Windows, macOS and Linux.</Rv>
        </div>
        <Rv delay={80} className="lp-price__tabs" role="group">
          <button className={`lp-price__tab${mode === "docker" ? " on" : ""}`} onClick={() => setMode("docker")} aria-pressed={mode === "docker"}>Docker</button>
          <button className={`lp-price__tab${mode === "native" ? " on" : ""}`} onClick={() => setMode("native")} aria-pressed={mode === "native"}>Without Docker</button>
        </Rv>
        <Rv delay={120} className="lp-price__grid">{STEPS.map((p, i) => <div className="lp-plan" key={p.name}>
          <div className="lp-plan__name">{p.name}</div>
          <p className="lp-plan__note">{p.note}</p>
          <div className="lp-plan__price lp-plan__price--cmd"><em>$</em>{p.cmd[mode]}</div>
          <div className="lp-plan__per">{p.per[mode]}</div>
          <Btn href={p.cta[1]} label={p.cta[0]} variant={i === 1 ? "" : "quiet"} size="wide" />
          <ul className="lp-plan__feats">{p.feats.map((f, j) => <li key={f}>{j === 0 ? <CircleCheck /> : <Plus />}{f}</li>)}</ul>
        </div>)}</Rv>
        <Rv delay={120} className="lp-ent">
          <div className="lp-ent__text"><Eyebrow icon={Cpu}>Real model</Eyebrow><h3 className="lp-d4">Measure a real LLM on the same invoices</h3><p className="lp-lead">Tests and evidence runs use a deterministic mock. Set SAFEPAY_LLM=anthropic and run make evidence-llm to measure field accuracy and whether an injection ever reaches a tool argument.</p></div>
          <Btn href={`${REPO}#measuring-a-real-llm`} label="How to run it" />
        </Rv>
      </div>
    </section>

    {/* ---------- defences rail ---------- */}
    <section className="lp-sec lp-rail">
      <div className="lp-rail__slab lp-dark">
        <div className="lp-rail__head"><div>
          <Rv><Eyebrow icon={Target} tone="dark">Security</Eyebrow></Rv>
          <Rv as="h2" delay={60} className="lp-d3">Defences you can check</Rv>
          <Rv as="p" delay={120} className="lp-lead lp-lead--dark">Every layer names the test or the command that proves it. Nothing here is a promise the code does not keep.</Rv>
        </div></div>
        <div className="lp-rail__track" ref={track} onScroll={onTrack}>{SPOT.map((c, i) => {
          const I = SPOT_ICON[i] ?? ScanLine;
          return <article className={`lp-rcard lp-rcard--${i % 4}`} key={c.title}>
            <div className="lp-rcard__say"><h3 className="lp-d4">{c.title}</h3><p>{c.body}</p></div>
            <div className="lp-rcard__facts"><div className="lp-rcard__art" aria-hidden="true"><I /></div>{c.facts.map(([k, v]) => <div className="lp-rcard__fact" key={k}><span>{k}</span><b>{v}</b></div>)}</div>
          </article>;
        })}</div>
        <div className="lp-rail__foot">
          <div className="lp-rail__nav"><button onClick={() => move(-1)} aria-label="Previous"><ArrowLeft /></button><button onClick={() => move(1)} aria-label="Next"><Arrow /></button></div>
          <div className="lp-rail__bar" aria-hidden="true">{SPOT.map((c, i) => <i key={c.title} className={i === slide ? "on" : ""} />)}</div>
        </div>
      </div>
    </section>

    {/* ---------- faq ---------- */}
    <section className="lp-sec lp-sec--pad lp-faq" id="faq">
      <div className="lp-shell">
        <div className="lp-faq__head">
          <Rv><Eyebrow icon={MessagesSquare} tone="flat">FAQ</Eyebrow></Rv>
          <Rv as="h2" delay={60} className="lp-d3">Questions, <em>answered</em></Rv>
          <Rv as="p" delay={120} className="lp-lead">What finance teams and reviewers ask first. The architecture and security documents go deeper.</Rv>
        </div>
        <div className="lp-faq__grid">
          <Rv className="lp-faq__side">{FAQ_TABS.map((x, i) => <button key={x} className={i === tab ? "on" : ""} onClick={() => { setTab(i); setOpen(0); }}>{x}</button>)}</Rv>
          <div className="lp-faq__list">
            {(FAQ[tab] ?? []).map((q, i) => <Rv key={q.q} delay={i * 60} className={`lp-q${open === i ? " on" : ""}`}>
              <button className="lp-q__btn" onClick={() => setOpen(open === i ? -1 : i)} aria-expanded={open === i}>{q.q}<i><Plus /></i></button>
              <div className="lp-q__body"><div><p>{q.a}</p></div></div>
            </Rv>)}
            <Rv delay={120} className="lp-ask">
              <div className="lp-ask__text"><Eyebrow icon={BookOpen}>Security</Eyebrow><h3 className="lp-d4">Read the security self-assessment</h3><p className="lp-lead">Threat model, residual attacks and honest limits, in one document.</p></div>
              <Btn href={doc("docs/SECURITY.md")} label="Open SECURITY.md" />
            </Rv>
          </div>
        </div>
      </div>
    </section>

    {/* ---------- closing ---------- */}
    <div className="lp-closewrap">
      <section className="lp-close lp-dark">
        <div className="lp-close__head">
          <Rv as="h2" className="lp-d2">Let the agent pay. Never the wrong person.</Rv>
          <Rv as="p" delay={60} className="lp-lead lp-lead--dark">Open the console, sign in as a seeded approver and review your first held payment.</Rv>
          <Rv delay={120}><Btn href={CONSOLE} label="Open console" variant="lime" size="lg" /></Rv>
        </div>
        <Rv delay={160} className="lp-close__stage"><AppMock /></Rv>
      </section>
    </div>

    {/* ---------- footer ---------- */}
    <footer className="lp-foot">
      <div className="lp-shell">
        <div className="lp-foot__grid">
          <div className="lp-foot__brand"><a className="lp-nav__brand" href="/">SafePay</a><p>A cash-flow-safe payables copilot for small businesses. FinTechathon 2026, International Track, Topic E.</p></div>
          {FOOT.map(([h, items]) => <div className="lp-foot__col" key={h}><h4>{h}</h4>{items.map(([label, href]) => <a key={label} href={href} {...external(href)}>{label}</a>)}</div>)}
        </div>
        <div className="lp-foot__mark" aria-hidden="true"><span>SafePay</span></div>
        <div className="lp-foot__bar"><p>© 2026 SafePay. {SIMULATION_NOTICE}</p></div>
      </div>
    </footer>
  </div>;
}
