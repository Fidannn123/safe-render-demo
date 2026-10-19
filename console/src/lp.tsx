import { useEffect, useRef, useState, type ComponentType, type CSSProperties, type ElementType, type ReactNode, type RefObject } from "react";
import { ReceiptText } from "lucide-react";

// Landing page primitives, ported from the MuhAI design: the arrow, the chip button, the eyebrow pill,
// one shared reveal observer, a count-up and a scroll progress variable. Every rule they read is in
// landing.css, so anything rendering them sits inside a `.lp` element.

export type Glyph = ComponentType<{ className?: string; style?: CSSProperties }>;

const REDUCED = typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// The reference draws its arrow across the whole 24 grid (x 3 to 21), which is where its buttons get their length.
export function Arrow({ className, style }: { className?: string; style?: CSSProperties }) {
  return <svg className={`lucide ${className ?? ""}`} style={style} viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 12h18m0 0-7-7m7 7-7 7" /></svg>;
}

// The signature control: a pill with a lime chip pinned inside it that sweeps the label into view on hover.
// The label is rendered twice on purpose: the copy in the chip is what the sweep reveals, the outer one
// fades out under it. The duplicate is hidden from assistive tech.
export function Btn({ href, label, variant = "", size = "", icon: Icon = Arrow, onClick, className = "" }: { href: string; label: string; variant?: "" | "lime" | "quiet" | "ghost"; size?: "" | "sm" | "lg" | "wide"; icon?: Glyph; onClick?: () => void; className?: string }) {
  const cls = ["lp-btn", variant && `lp-btn--${variant}`, size && `lp-btn--${size}`, className].filter(Boolean).join(" ");
  const external = /^https?:/.test(href);
  return <a className={cls} href={href} onClick={onClick} {...(external ? { target: "_blank", rel: "noreferrer" } : {})}>
    <span className="lp-btn__chip" aria-hidden="true"><span>{label}</span><i className="lp-btn__ico"><Icon /></i></span>
    <span className="lp-btn__ghost" aria-hidden="true" />
    <span className="lp-btn__label">{label}</span>
  </a>;
}

export function Eyebrow({ icon: Icon = ReceiptText, tone = "", children }: { icon?: Glyph; tone?: "" | "flat" | "dark"; children: ReactNode }) {
  return <span className={`lp-eyebrow${tone ? ` lp-eyebrow--${tone}` : ""}`}><span className="lp-eyebrow__ico"><Icon /></span>{children}</span>;
}

// One IntersectionObserver for the whole page, opening each element once. Everything opens at once when
// the API is missing, so a trigger that never fires cannot leave a blank section.
let revealIO: IntersectionObserver | null = null;
const revealJobs = new WeakMap<Element, () => void>();

// Anything already on screen when it mounts opens on the next frame rather than waiting on the observer:
// a hero panel that starts translated down by its reveal offset can sit just under the observer's margin.
function onReveal(el: Element, fire: () => void): () => void {
  const frame = window.requestAnimationFrame(() => {
    const r = el.getBoundingClientRect();
    if (typeof IntersectionObserver === "undefined" || (r.top < window.innerHeight && r.bottom > 0)) fire();
  });
  if (typeof IntersectionObserver === "undefined") return () => window.cancelAnimationFrame(frame);
  revealIO ??= new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const job = revealJobs.get(entry.target);
      revealIO?.unobserve(entry.target); revealJobs.delete(entry.target); job?.();
    }
  }, { rootMargin: "0px 0px -12% 0px", threshold: 0.01 });
  revealJobs.set(el, fire); revealIO.observe(el);
  return () => { window.cancelAnimationFrame(frame); revealIO?.unobserve(el); revealJobs.delete(el); };
}

export function useInOnce<T extends Element>(): [RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (on || !el) return undefined;
    return onReveal(el, () => setOn(true));
  }, [on]);
  return [ref, on];
}

type Tag = "div" | "nav" | "h1" | "h2" | "h3" | "p" | "section";

// `now` opens on the next frame instead of waiting for the observer: the nav starts translated off the
// top, measures as off-screen and would never intersect.
export function Rv({ as = "div", y, delay = 0, now = false, className = "", style, id, role, children }: { as?: Tag; y?: number; delay?: number; now?: boolean; className?: string; style?: CSSProperties; id?: string; role?: string; children?: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (shown || !el) return undefined;
    if (now) {
      const frame = window.requestAnimationFrame(() => setShown(true));
      return () => window.cancelAnimationFrame(frame);
    }
    return onReveal(el, () => setShown(true));
  }, [shown, now]);
  const As = as as ElementType;
  const vars = { ...style, ...(delay ? { "--rv-d": `${delay}ms` } : {}), ...(y === undefined ? {} : { "--rv-y": `${y}px` }) } as CSSProperties;
  return <As ref={ref} id={id} role={role} style={vars} className={`lp-rv ${className}${shown ? " is-in" : ""}`}>{children}</As>;
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export function useCount(to: number, on: boolean, ms = 1100): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!on || REDUCED) return undefined;
    let raf = 0; let t0 = -1;
    const step = (now: number) => {
      if (t0 < 0) t0 = now;
      const t = Math.min(1, (now - t0) / ms);
      setN(to * easeOut(t));
      if (t < 1) raf = window.requestAnimationFrame(step);
    };
    raf = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(raf);
  }, [to, on, ms]);
  return REDUCED ? to : n;
}

const usd = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

// Tabular figures, or the digits change width as they climb and the card twitches all the way up.
export function Count({ to, on, pre = "", ms }: { to: number; on: boolean; pre?: string; ms?: number }) {
  const n = useCount(to, on, ms);
  return <span className="lp-num">{pre}{usd.format(Math.round(n))}</span>;
}

const clamp01 = (n: number) => (n < 0 ? 0 : n > 1 ? 1 : n);
const smooth = (n: number) => n * n * (3 - 2 * n);
export const heroMap = (rect: DOMRect, vh: number) => smooth(clamp01((vh * 0.8 - rect.top) / (vh * 0.95)));
export const sayMap = (rect: DOMRect, vh: number) => clamp01((vh * 0.6 - rect.top) / (vh * 0.95));

// Writes a 0 to 1 progress into a CSS custom property instead of React state, so scrolling never renders
// and the transforms stay on the compositor. With motion reduced the page sits in its settled state.
export function useScrollVar(ref: RefObject<HTMLElement | null>, map: (rect: DOMRect, vh: number) => number, name = "--p") {
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (REDUCED) { el.style.setProperty(name, "1"); return undefined; }
    let raf = 0;
    const write = () => { raf = 0; el.style.setProperty(name, map(el.getBoundingClientRect(), window.innerHeight).toFixed(4)); };
    const onScroll = () => { if (!raf) raf = window.requestAnimationFrame(write); };
    write();
    window.addEventListener("scroll", onScroll, { passive: true }); window.addEventListener("resize", onScroll);
    return () => { if (raf) window.cancelAnimationFrame(raf); window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); };
  }, [ref, map, name]);
}

// The nav pill is white over light bands and dark over the dark ones, switching at the band edge. Reads
// the dark bands' live rects against the pill's centre line, rAF-throttled.
export function useNavTone(root: RefObject<HTMLElement | null>, setDark: (dark: boolean) => void) {
  useEffect(() => {
    const el = root.current;
    if (!el) return undefined;
    const bands = [...el.querySelectorAll(".lp-dark")];
    let raf = 0; let last: boolean | null = null;
    const read = () => {
      raf = 0;
      const over = bands.some((band) => { const r = band.getBoundingClientRect(); return r.top <= 40 && r.bottom >= 40; });
      if (over !== last) { last = over; setDark(over); }
    };
    const onScroll = () => { if (!raf) raf = window.requestAnimationFrame(read); };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true }); window.addEventListener("resize", onScroll);
    return () => { if (raf) window.cancelAnimationFrame(raf); window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); };
  }, [root, setDark]);
}
