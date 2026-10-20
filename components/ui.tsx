import { ArrowUpRight, Check, AlertTriangle, Clock3, XCircle, FileText, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

export function Status({ status }: { status: string }) {
  const text: Record<string, string> = { review_required: "Needs review", needs_review: "Needs review", completed: "Completed", validated: "Validated", human_verified: "Human verified", ready: "Ready", rejected: "Rejected", cancelled: "Cancelled", failed: "Failed", queued: "Queued", pending: "Pending", accepted: "Accepted", corrected: "Corrected", deferred: "Deferred" };
  const green = ["completed", "validated", "human_verified", "ready", "accepted", "corrected"].includes(status);
  const red = ["failed", "rejected"].includes(status);
  const amber = ["review_required", "needs_review", "deferred"].includes(status);
  const Icon = green ? Check : red ? XCircle : amber ? AlertTriangle : Clock3;
  return <span className={`status ${green ? "green" : red ? "red" : amber ? "amber" : "neutral"}`}><Icon size={12}/>{text[status] ?? status.replaceAll("_", " ")}</span>;
}
export function Empty({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><FileText size={26}/></div><h3>{title}</h3><p>{children}</p>{action}</div>;
}
export function Loading({ text = "Loading workspace…" }: { text?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle className="spin" size={22}/><span>{text}</span></div>;
}
export function Metric({ title, value, note, icon, accent = false }: { title: string; value: ReactNode; note: string; icon: ReactNode; accent?: boolean }) {
  return <div className={`metric ${accent ? "metric-accent" : ""}`}><div className="metric-title">{title}<span>{icon}</span></div><div className="metric-value">{value}</div><div className="metric-note">{note}</div></div>;
}
export function SectionHeading({ eyebrow, title, description, children }: { eyebrow?: string; title: string; description: string; children?: ReactNode }) {
  return <div className="section-heading"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{children}</div></div>;
}
export function TextLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return <button className="text-link" onClick={onClick}>{children}<ArrowUpRight size={14}/></button>;
}
