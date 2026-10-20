"use client";
import { useState } from "react";
import { ZoomIn, ZoomOut, ScanLine, ImageOff } from "lucide-react";
import { labels, validBox, formatValue } from "@/lib/domain";
import type { DocumentItem, RateRecord, FieldName } from "@/lib/types";

export default function SourcePreview({ document, records, selected, field, apiBase }: { document: DocumentItem; records: RateRecord[]; selected: RateRecord; field: FieldName; apiBase: string }) {
  const [zoom, setZoom] = useState(1);
  const [failed, setFailed] = useState(false);
  const evidence = selected.fields[field].evidence[0];
  const box = evidence?.bounding_box;
  // Use the immutable original snapshot for demo previews, even after a correction.
  return <section className="source-panel">
    <div className="panel-heading"><span><ScanLine size={16} /> Source document</span><div className="button-group">
      <button className="icon-button" aria-label="Zoom out" disabled={zoom <= 0.75} onClick={() => setZoom(z => Math.max(.75, z - .25))}><ZoomOut size={16}/></button>
      <span className="mono small">{Math.round(zoom * 100)}%</span>
      <button className="icon-button" aria-label="Zoom in" disabled={zoom >= 2} onClick={() => setZoom(z => Math.min(2, z + .25))}><ZoomIn size={16}/></button>
    </div></div>
    <div className="source-scroll">
      <div className="paper" style={{ width: `${zoom * 100}%`, minWidth: `${zoom * 100}%` }}>
        {document.is_demo ? <svg viewBox="0 0 800 1000" role="img" aria-label={`Reconstructed rate sheet for ${document.institution_name}`}>
          <rect width="800" height="1000" fill="#fff"/>
          <rect x="56" y="55" width="27" height="27" rx="4" fill="#283d3a"/>
          <path d="M62 74 L69 62 L77 74Z" fill="#dcf1be"/>
          <text x="96" y="74" fill="#4a5d57" fontSize="13" fontFamily="sans-serif" letterSpacing="2">DEPOSIT SERVICES</text>
          <text x="56" y="140" fill="#22352e" fontSize="29" fontFamily="Georgia, serif">{document.institution_name}</text>
          <text x="56" y="180" fill="#5c6660" fontSize="17" fontFamily="sans-serif">Personal deposit rates</text>
          <line x1="56" x2="744" y1="213" y2="213" stroke="#263b32" strokeWidth="2"/>
          <text x="56" y="241" fill="#7a817b" fontSize="11" fontFamily="sans-serif">RECONSTRUCTED SAMPLE · VALUES FROM THE ORGANIZER CSV</text>
          <rect x="48" y="261" width="704" height="25" fill="#edf1ed"/>
          {(["Product", "Term", "Rate", "APY", "Minimum", "Effective"] as const).map((label, i) =>
            <text key={label} x={[56, 256, 344, 440, 536, 640][i]} y="278" fill="#56645c" fontSize="10" fontWeight="bold" fontFamily="sans-serif">{label}</text>)}
          {records.map((r, i) => <g key={r.id}>
            {i % 2 === 1 && <rect x="48" y={290 + i * 65} width="704" height="45" fill="#f7f8f5"/>}
            {(["product_type", "term_months", "rate_pct", "apy_pct", "min_deposit", "effective_date"] as FieldName[]).map((f, col) =>
              <text key={f} x={[56, 256, 344, 440, 536, 640][col]} y={318 + i * 65} fill="#34453b" fontSize={f === "product_type" ? 11 : 12} fontFamily="sans-serif">{formatValue(f, r.fields[f].raw_value)}</text>)}
            <line x1="48" x2="752" y1={338 + i * 65} y2={338 + i * 65} stroke="#e8ebe7"/>
          </g>)}
          <line x1="56" x2="744" y1="887" y2="887" stroke="#d6ded6"/>
          <text x="56" y="913" fill="#6e786f" fontSize="11" fontFamily="sans-serif">For interface demonstration only. This is not the original bank document.</text>
          <text x="56" y="936" fill="#6e786f" fontSize="11" fontFamily="sans-serif">Source: ps06_sample_rate_sheets.csv · No model extraction has been performed.</text>
          <text x="730" y="968" fill="#7b867c" fontSize="11" fontFamily="sans-serif">1</text>
        </svg> : failed ? <div className="empty-state"><ImageOff/><h3>Preview unavailable</h3><p>The backend could not serve this page.</p><button className="button" onClick={() => setFailed(false)}>Retry preview</button></div> :
          // Backend serves a canonical page image; overlay uses its natural aspect ratio.
          // eslint-disable-next-line @next/next/no-img-element
          <img alt={`Source page ${evidence?.page_number ?? selected.page}`} src={`${apiBase.replace(/\/$/, "")}/documents/${document.id}/pages/${evidence?.page_number ?? selected.page}/preview`} onError={() => setFailed(true)} />}
        {!failed && box && validBox(box) && <div className="evidence-box" style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }}><span>{labels[field]}</span></div>}
      </div>
    </div>
    <div className="source-footer"><span>Page {document.is_demo ? 1 : evidence?.page_number ?? selected.page} of {document.page_count}</span><span className="small">{document.is_demo ? "Reconstructed preview" : "Canonical source preview"}</span></div>
  </section>;
}
