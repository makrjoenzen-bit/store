"""
Sham Cash PDF field extraction + metadata forgery heuristics.
Assists admin/staff review — NEVER auto-approves payments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


SUSPICIOUS_TOOLS = (
    "adobe photoshop", "photoshop", "illustrator", "canva", "foxit editor",
    "sejda", "ilovepdf", "nitro", "coreldraw", "pdfescape", "smallpdf",
    "pdf-xchange editor", "inkscape", "gimp",
)


@dataclass
class PdfForensicsResult:
    extracted_amount: Optional[float] = None
    extracted_currency: Optional[str] = None  # USD | SYP
    extracted_tx_ref: Optional[str] = None
    extracted_timestamp: Optional[str] = None
    extracted_recipient: Optional[str] = None
    amount_match: str = "unreadable"  # exact | mismatch | unreadable
    expected_amount: Optional[float] = None
    expected_currency: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    creation_date: Optional[str] = None
    mod_date: Optional[str] = None
    suspicious_tools: List[str] = field(default_factory=list)
    metadata_edited: bool = False
    risk_level: str = "clean"  # clean | review | high
    risk_badge_ar: str = "🟢 نظيف (Clean)"
    risk_score: int = 0
    text_excerpt: Optional[str] = None
    engine: str = "raw"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary_block_ar(self) -> str:
        amt = (
            f"{self.extracted_amount} {self.extracted_currency or ''}".strip()
            if self.extracted_amount is not None else "غير مقروء"
        )
        exp = (
            f"{self.expected_amount} {self.expected_currency or 'USD'}".strip()
            if self.expected_amount is not None else "—"
        )
        match_ar = {
            "exact": "✅ مطابق",
            "mismatch": "❌ غير مطابق",
            "unreadable": "❔ غير مقروء",
        }.get(self.amount_match, self.amount_match)
        tools = ", ".join(self.suspicious_tools) if self.suspicious_tools else "—"
        return (
            "🔬 **Forensics Summary**\n"
            f"{self.risk_badge_ar}\n"
            f"💵 المستخرج: `{amt}` ↔ المتوقع: `{exp}` → {match_ar}\n"
            f"🔢 Tx Ref: `{self.extracted_tx_ref or '—'}`\n"
            f"🖨 Creator/Producer: `{self.creator or '—'}` / `{self.producer or '—'}`\n"
            f"🛠 أدوات مشبوهة: {tools}"
        )


class PdfForensicsService:
    AMOUNT_PATTERNS = [
        re.compile(
            r"(?:amount|المبلغ|قيمة|تحويل|transferred|total)[^\d]{0,20}"
            r"([\d]+(?:[.,]\d{1,2})?)\s*(USD|\$|ل\.?س|SYP|ليرة)?",
            re.I,
        ),
        re.compile(r"\$\s*([\d]+(?:[.,]\d{1,2})?)"),
        re.compile(r"([\d]+(?:[.,]\d{1,2})?)\s*(USD|\$)"),
        re.compile(r"([\d]{3,}(?:[.,]\d{1,2})?)\s*(ل\.?س|SYP|ليرة)"),
    ]
    TX_PATTERNS = [
        re.compile(
            r"(?:ref|reference|رقم\s*(?:العملية|الحوالة|الإيصال)|receipt\s*id|"
            r"transaction\s*(?:id|no\.?|number)?)\s*[:#]?\s*([A-Za-z0-9\-]{6,})",
            re.I,
        ),
        re.compile(r"\b([A-Z0-9]{8,22})\b"),
    ]
    TS_PATTERNS = [
        re.compile(
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)",
        ),
        re.compile(r"(\d{1,2}[-/]\d{1,2}[-/]\d{4}\s+\d{1,2}:\d{2})"),
    ]
    RECIPIENT_PATTERNS = [
        re.compile(
            r"(?:to|recipient|المستلم|إلى|الحساب)\s*[:：]?\s*([^\n]{3,60})",
            re.I,
        ),
    ]

    def analyze(
        self,
        pdf_bytes: bytes,
        *,
        expected_usd: Optional[Decimal] = None,
        expected_syp: Optional[Decimal] = None,
    ) -> PdfForensicsResult:
        result = PdfForensicsResult()
        if expected_usd is not None:
            result.expected_amount = float(expected_usd)
            result.expected_currency = "USD"
        elif expected_syp is not None:
            result.expected_amount = float(expected_syp)
            result.expected_currency = "SYP"

        text, meta, engine = self._extract_text_and_meta(pdf_bytes)
        result.engine = engine
        result.text_excerpt = (text or "")[:500] or None
        result.creator = meta.get("creator")
        result.producer = meta.get("producer")
        result.creation_date = meta.get("creation_date")
        result.mod_date = meta.get("mod_date")

        if text:
            self._parse_fields(text, result)

        self._compare_amount(result, expected_usd, expected_syp)
        self._score_risk(result)
        return result

    def _extract_text_and_meta(
        self, pdf_bytes: bytes
    ) -> Tuple[str, Dict[str, Optional[str]], str]:
        # Prefer PyMuPDF
        try:
            import fitz  # type: ignore
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            parts = [page.get_text() for page in doc]
            meta_raw = doc.metadata or {}
            doc.close()
            meta = {
                "creator": meta_raw.get("creator") or meta_raw.get("Creator"),
                "producer": meta_raw.get("producer") or meta_raw.get("Producer"),
                "creation_date": meta_raw.get("creationDate") or meta_raw.get("CreationDate"),
                "mod_date": meta_raw.get("modDate") or meta_raw.get("ModDate"),
            }
            return "\n".join(parts), meta, "pymupdf"
        except Exception as e:
            logger.debug(f"PyMuPDF unavailable/failed: {e}")

        try:
            import pdfplumber  # type: ignore
            import io
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                parts = [(p.extract_text() or "") for p in pdf.pages]
                info = (pdf.metadata or {}) if hasattr(pdf, "metadata") else {}
            meta = {
                "creator": info.get("Creator") or info.get("/Creator"),
                "producer": info.get("Producer") or info.get("/Producer"),
                "creation_date": info.get("CreationDate") or info.get("/CreationDate"),
                "mod_date": info.get("ModDate") or info.get("/ModDate"),
            }
            return "\n".join(parts), meta, "pdfplumber"
        except Exception as e:
            logger.debug(f"pdfplumber unavailable/failed: {e}")

        # Raw bytes fallback (metadata + Latin text)
        raw = pdf_bytes.decode("latin-1", errors="ignore")
        meta = {
            "creator": self._meta_field(raw, "Creator"),
            "producer": self._meta_field(raw, "Producer"),
            "creation_date": self._meta_field(raw, "CreationDate"),
            "mod_date": self._meta_field(raw, "ModDate"),
        }
        # Strip binary noise for amount/ref heuristics
        text = re.sub(r"[^\x20-\x7E\u0600-\u06FF\n]+", " ", raw)
        return text, meta, "raw"

    @staticmethod
    def _meta_field(raw: str, key: str) -> Optional[str]:
        m = re.search(rf"/{key}\s*\(([^)]*)\)", raw)
        if m:
            return m.group(1).strip() or None
        m = re.search(rf"/{key}\s*<([0-9A-Fa-f]+)>", raw)
        if m:
            try:
                return bytes.fromhex(m.group(1)).decode("utf-16-be", errors="ignore").strip() or None
            except Exception:
                return m.group(1)
        return None

    def _parse_fields(self, text: str, result: PdfForensicsResult) -> None:
        for pat in self.AMOUNT_PATTERNS:
            m = pat.search(text)
            if not m:
                continue
            raw_amt = m.group(1).replace(",", "")
            try:
                result.extracted_amount = float(Decimal(raw_amt))
            except (InvalidOperation, ValueError):
                continue
            cur = (m.group(2) if m.lastindex and m.lastindex >= 2 else None) or ""
            cur_l = cur.lower()
            if "$" in cur or "usd" in cur_l:
                result.extracted_currency = "USD"
            elif "ل" in cur or "syp" in cur_l:
                result.extracted_currency = "SYP"
            elif result.extracted_amount and result.extracted_amount >= 1000:
                result.extracted_currency = "SYP"
            else:
                result.extracted_currency = "USD"
            break

        for pat in self.TX_PATTERNS:
            m = pat.search(text)
            if m:
                cand = m.group(1).strip()
                if cand.lower() not in ("transaction", "reference", "receipt"):
                    result.extracted_tx_ref = cand
                    break

        for pat in self.TS_PATTERNS:
            m = pat.search(text)
            if m:
                result.extracted_timestamp = m.group(1)
                break

        for pat in self.RECIPIENT_PATTERNS:
            m = pat.search(text)
            if m:
                result.extracted_recipient = m.group(1).strip()[:80]
                break

    def _compare_amount(
        self,
        result: PdfForensicsResult,
        expected_usd: Optional[Decimal],
        expected_syp: Optional[Decimal],
    ) -> None:
        if result.extracted_amount is None:
            result.amount_match = "unreadable"
            return
        got = Decimal(str(result.extracted_amount))
        targets: List[Tuple[Decimal, str]] = []
        if expected_usd is not None:
            targets.append((Decimal(str(expected_usd)), "USD"))
        if expected_syp is not None:
            targets.append((Decimal(str(expected_syp)), "SYP"))
        if not targets:
            result.amount_match = "unreadable"
            return
        for exp, cur in targets:
            if abs(got - exp) <= Decimal("0.05"):
                result.amount_match = "exact"
                result.expected_amount = float(exp)
                result.expected_currency = cur
                return
        result.amount_match = "mismatch"

    def _score_risk(self, result: PdfForensicsResult) -> None:
        score = 0
        blob = f"{result.creator or ''} {result.producer or ''}".lower()
        found = []
        for tool in SUSPICIOUS_TOOLS:
            if tool in blob:
                found.append(tool)
                score += 40
        result.suspicious_tools = found

        if result.creation_date and result.mod_date:
            c = self._norm_pdf_date(result.creation_date)
            m = self._norm_pdf_date(result.mod_date)
            if c and m and c != m:
                result.metadata_edited = True
                score += 35

        if result.amount_match == "mismatch":
            score += 25
        elif result.amount_match == "unreadable":
            score += 10

        result.risk_score = min(100, score)
        if score >= 60:
            result.risk_level = "high"
            result.risk_badge_ar = "🔴 اشتباه تزوير مرتفع (High Forgery Risk)"
        elif score >= 25:
            result.risk_level = "review"
            result.risk_badge_ar = "🟡 يحتاج تدقيق (Review)"
        else:
            result.risk_level = "clean"
            result.risk_badge_ar = "🟢 نظيف (Clean)"

    @staticmethod
    def _norm_pdf_date(raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        # D:20240101120000+03'00' or ISO
        digits = re.sub(r"\D", "", raw)[:14]
        return digits or raw.strip()


pdf_forensics_service = PdfForensicsService()
