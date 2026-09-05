"""
LLM Escalation Layer — Reconciliation Copilot (PRD §7.3)

For each unmatched ledger row:
  1. Gather top-N bank/gateway candidates by amount proximity (±window %).
  2. Prompt the LLM with ledger row + candidates.
  3. Parse structured JSON response: {proposed_match, reasoning, confidence}.
  4. HARD GATE (code-level): confidence < threshold → unresolved, always.
  5. Return a MatchRecord per PRD §6.5.

Error handling:
  - Malformed / missing-field JSON   → unresolved, exception_category="malformed_response"
  - API timeout / network exception  → unresolved, exception_category="api_error"
  - LLM returns null proposed_match  → unresolved, exception_category="no_llm_match"
  - Confidence below threshold       → unresolved, exception_category="low_confidence"

The LLMClient is a Protocol — swap AnthropicLLMClient for any other provider
by implementing two methods: complete(system, user) → str.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Protocol, Tuple, runtime_checkable

from src.config import EscalatorConfig
from src.matching.deterministic_matcher import (
    BankRecord,
    GatewayRecord,
    LedgerRecord,
    MatchRecord,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM client protocol (thin, swappable interface — PRD §8)
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface every LLM backend must implement."""

    def complete(self, system: str, user: str) -> str:
        """
        Send *system* and *user* messages; return the raw text response.
        Must raise an exception on unrecoverable failure (escalator catches it).
        """
        ...


# ---------------------------------------------------------------------------
# Anthropic implementation
# ---------------------------------------------------------------------------

class AnthropicLLMClient:
    """
    Production LLM client backed by Anthropic Claude.

    Reads ANTHROPIC_API_KEY from the environment (or pass explicitly).
    Model is configurable; defaults to claude-3-5-haiku for cost efficiency.
    """

    def __init__(
        self,
        model: str = "claude-3-5-haiku-20241022",
        api_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        try:
            import anthropic as _anthropic  # lazy import — keeps tests free of the SDK
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            ) from exc

        self._anthropic = _anthropic
        self.model = model
        self.timeout = timeout
        self._client = _anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=float(timeout),
        )

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text


# ---------------------------------------------------------------------------
# OpenRouter implementation (OpenAI-compatible — supports GLM, Mistral, etc.)
# ---------------------------------------------------------------------------

class OpenRouterLLMClient:
    """
    LLM client backed by OpenRouter's OpenAI-compatible API.

    Supports any model available on https://openrouter.ai (e.g. GLM 5.2,
    Mistral, Llama, etc.).  Reads OPENROUTER_API_KEY from the environment.

    Parameters
    ----------
    model : str
        OpenRouter model slug, e.g. ``"thudm/glm-4-9b:free"``.
        Check https://openrouter.ai/models for available slugs.
    api_key : str, optional
        Defaults to the OPENROUTER_API_KEY environment variable.
    timeout : int
        Request timeout in seconds (default 30).
    """

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        model: str = "thudm/glm-4-9b:free",
        api_key: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        try:
            from openai import OpenAI as _OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai package not installed. Run: pip install openai"
            ) from exc

        self.model = model
        self._client = _OpenAI(
            base_url=self.OPENROUTER_BASE_URL,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            timeout=float(timeout),
        )

    def complete(self, system: str, user: str) -> str:
        import time

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=512,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                # Check for rate-limit (429) — retry with backoff
                is_rate_limit = "429" in str(exc) or "RateLimitError" in type(exc).__name__
                if is_rate_limit and attempt < max_retries - 1:
                    wait = 10 * (attempt + 1)   # 10s, 20s
                    logger.warning("Rate-limited; retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                    time.sleep(wait)
                    continue
                raise  # non-retryable or final attempt


# ---------------------------------------------------------------------------
# Parsed LLM response
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    proposed_match: Optional[str]   # UTR or payment_id, or None
    reasoning: str
    confidence: float               # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Main escalator
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a financial reconciliation assistant helping match internal ledger \
transactions to bank and payment-gateway settlement records.

Your task: decide whether any of the provided candidates plausibly matches \
the ledger transaction, and if so which one.

A match is plausible when ALL of the following hold:
  • Amounts are close (minor rounding drift of ₹0.01 – ₹2 is acceptable).
  • A reference link exists — the order ID appears in the bank narration, \
or the UTR / payment_id corresponds.
  • Settlement date is within a few days of the ledger timestamp.

Rules:
  • If no candidate satisfies all three criteria, return null for proposed_match.
  • Be conservative: a low-confidence guess is worse than an honest "no match".
  • Do NOT invent or hallucinate identifiers not shown in the candidates list.

Respond ONLY with valid JSON in this exact schema — no prose, no markdown fences:
{
  "proposed_match": "<UTR or payment_id string, or null>",
  "reasoning": "<1-2 sentence explanation of why this is or is not a match>",
  "confidence": <float between 0.0 and 1.0>
}"""


class LLMEscalator:
    """
    Escalate unmatched ledger rows to the LLM for fuzzy reconciliation.

    Parameters
    ----------
    client : LLMClient
        Any object satisfying the LLMClient protocol.
    config : EscalatorConfig
        Tuning knobs (threshold, top_n, candidate window, …).
    """

    def __init__(
        self,
        client: LLMClient,
        config: Optional[EscalatorConfig] = None,
    ) -> None:
        self.client = client
        self.cfg = config or EscalatorConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def escalate(
        self,
        unmatched: List[LedgerRecord],
        bank: List[BankRecord],
        gateway: List[GatewayRecord],
    ) -> List[MatchRecord]:
        """
        Process every unmatched ledger row through the LLM.

        Returns one MatchRecord per input row (match_type "llm" or "unresolved").
        """
        # Pre-build lookup sets for proposed_match resolution
        bank_utr_set: dict[str, BankRecord] = {b.utr: b for b in bank if b.utr}
        gw_pay_set: dict[str, GatewayRecord] = {g.payment_id: g for g in gateway}
        gw_by_utr: dict[str, GatewayRecord] = {g.utr: g for g in gateway if g.utr}

        results: List[MatchRecord] = []
        for i, ledger_row in enumerate(unmatched):
            if i > 0 and self.cfg.call_delay_seconds > 0:
                import time
                time.sleep(self.cfg.call_delay_seconds)
            record = self._process_one(
                ledger_row, bank, gateway, bank_utr_set, gw_pay_set, gw_by_utr
            )
            results.append(record)
        return results

    # ------------------------------------------------------------------
    # Per-row pipeline
    # ------------------------------------------------------------------

    def _process_one(
        self,
        ledger_row: LedgerRecord,
        bank: List[BankRecord],
        gateway: List[GatewayRecord],
        bank_utr_set: dict[str, BankRecord],
        gw_pay_set: dict[str, GatewayRecord],
        gw_by_utr: dict[str, GatewayRecord],
    ) -> MatchRecord:
        candidates = self._get_candidates(ledger_row, bank, gateway, gw_by_utr)
        system_msg, user_msg = self._build_prompt(ledger_row, candidates)

        raw_response = ""
        try:
            raw_response = self.client.complete(system_msg, user_msg)
            logger.debug("LLM raw response for %s: %s", ledger_row.txn_id, raw_response)
        except Exception as exc:  # noqa: BLE001 — catch-all by design
            logger.warning(
                "API error for %s: %s", ledger_row.txn_id, exc, exc_info=True
            )
            return self._unresolved(ledger_row, "api_error", reasoning=str(exc))

        llm_resp = self._parse_response(raw_response, ledger_row.txn_id)
        if llm_resp is None:
            return self._unresolved(ledger_row, "malformed_response")

        if llm_resp.proposed_match is None:
            return self._unresolved(
                ledger_row,
                "no_llm_match",
                reasoning=llm_resp.reasoning,
                llm_confidence=llm_resp.confidence,
            )

        # Hard gate — code-level, never in the prompt
        if llm_resp.confidence < self.cfg.confidence_threshold:
            logger.info(
                "Hard gate: %s confidence %.2f < %.2f — forced to unresolved",
                ledger_row.txn_id,
                llm_resp.confidence,
                self.cfg.confidence_threshold,
            )
            return self._unresolved(
                ledger_row,
                "low_confidence",
                reasoning=llm_resp.reasoning,
                llm_confidence=llm_resp.confidence,
            )

        # Resolve proposed_match to bank UTR and/or gateway payment_id
        matched_bank_utr, matched_gw_pay = self._resolve_match(
            llm_resp.proposed_match, bank_utr_set, gw_pay_set, gw_by_utr
        )

        return MatchRecord(
            match_id=f"LLM_{ledger_row.txn_id}",
            ledger_txn_id=ledger_row.txn_id,
            matched_bank_utr=matched_bank_utr,
            matched_gateway_payment_id=matched_gw_pay,
            match_type="llm",
            confidence=round(llm_resp.confidence, 4),
            reasoning=llm_resp.reasoning,
            exception_category=None,
        )

    # ------------------------------------------------------------------
    # Candidate selection
    # ------------------------------------------------------------------

    def _get_candidates(
        self,
        ledger_row: LedgerRecord,
        bank: List[BankRecord],
        gateway: List[GatewayRecord],
        gw_by_utr: dict[str, GatewayRecord],
    ) -> list[dict]:
        """
        Return the top-N bank+gateway rows ordered by amount proximity,
        within ±candidate_window_pct of the ledger amount.
        """
        window = ledger_row.amount * Decimal(str(self.cfg.candidate_window_pct))
        lo = ledger_row.amount - window
        hi = ledger_row.amount + window

        candidates: list[tuple[Decimal, dict]] = []
        for b in bank:
            if lo <= b.amount <= hi:
                gw = gw_by_utr.get(b.utr) if b.utr else None
                diff = abs(b.amount - ledger_row.amount)
                entry: dict = {
                    "bank_utr": b.utr or "(blank)",
                    "bank_amount": str(b.amount),
                    "bank_date": str(b.value_date),
                    "bank_narration": b.narration,
                }
                if gw:
                    entry["gateway_payment_id"] = gw.payment_id
                    entry["gateway_net_amount"] = str(gw.amount)
                    entry["gateway_fee"] = str(gw.fee)
                candidates.append((diff, entry))

        candidates.sort(key=lambda t: t[0])
        return [c for _, c in candidates[: self.cfg.top_n_candidates]]

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        ledger_row: LedgerRecord,
        candidates: list[dict],
    ) -> Tuple[str, str]:
        ledger_block = (
            f"Ledger transaction:\n"
            f"  txn_id    : {ledger_row.txn_id}\n"
            f"  order_id  : {ledger_row.order_id}\n"
            f"  amount    : INR {ledger_row.amount}\n"
            f"  timestamp : {ledger_row.timestamp.isoformat()}\n"
            f"  status    : {ledger_row.status}\n"
        )

        if candidates:
            cand_lines = ["\nCandidates (sorted by amount proximity):"]
            for i, c in enumerate(candidates, 1):
                line = (
                    f"  {i}. Bank UTR={c['bank_utr']}, amount=INR {c['bank_amount']}, "
                    f"date={c['bank_date']}, narration=\"{c['bank_narration']}\""
                )
                if "gateway_payment_id" in c:
                    line += (
                        f"\n     Gateway payment_id={c['gateway_payment_id']}, "
                        f"net=INR {c['gateway_net_amount']}, fee=INR {c['gateway_fee']}"
                    )
                cand_lines.append(line)
            cand_block = "\n".join(cand_lines)
        else:
            cand_block = "\nNo candidates found within the amount window."

        user_msg = (
            ledger_block
            + cand_block
            + "\n\nWhich candidate (if any) matches this ledger transaction? "
            "Return JSON only."
        )
        return _SYSTEM_PROMPT, user_msg

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self, raw: str, txn_id: str = ""
    ) -> Optional[LLMResponse]:
        """
        Parse and validate the LLM JSON response.
        Returns None on any parse or validation failure.
        """
        # Strip markdown code fences if the model added them despite instructions
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                ln for ln in lines if not ln.startswith("```")
            ).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "Malformed JSON from LLM for %s: %s | raw=%r",
                txn_id, exc, raw
            )
            return None

        # Validate required fields
        for field_name in ("proposed_match", "reasoning", "confidence"):
            if field_name not in data:
                logger.warning(
                    "Missing field '%s' in LLM response for %s | raw=%r",
                    field_name, txn_id, raw
                )
                return None

        try:
            confidence = float(data["confidence"])
            if not (0.0 <= confidence <= 1.0):
                raise ValueError(f"confidence {confidence} out of range [0,1]")
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Invalid confidence value in LLM response for %s: %s | raw=%r",
                txn_id, exc, raw
            )
            return None

        proposed = data["proposed_match"]
        if proposed is not None and not isinstance(proposed, str):
            logger.warning(
                "proposed_match is not a string or null for %s | raw=%r",
                txn_id, raw
            )
            return None

        return LLMResponse(
            proposed_match=proposed or None,
            reasoning=str(data.get("reasoning", "")),
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Match resolution
    # ------------------------------------------------------------------

    def _resolve_match(
        self,
        proposed: str,
        bank_utr_set: dict[str, BankRecord],
        gw_pay_set: dict[str, GatewayRecord],
        gw_by_utr: dict[str, GatewayRecord],
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Given the LLM's proposed_match string, return (matched_bank_utr, matched_gw_pay_id).
        Checks bank UTRs first, then gateway payment_ids.
        """
        if proposed in bank_utr_set:
            bank_utr = proposed
            gw = gw_by_utr.get(proposed)
            gw_pay = gw.payment_id if gw else None
            return bank_utr, gw_pay

        if proposed in gw_pay_set:
            gw = gw_pay_set[proposed]
            bank_utr = gw.utr if gw.utr else None
            return bank_utr, gw.payment_id

        # proposed_match is not in either known set — LLM hallucinated
        logger.warning("LLM proposed unknown identifier: %r", proposed)
        return None, proposed  # store raw value in gateway field for audit

    # ------------------------------------------------------------------
    # Unresolved factory
    # ------------------------------------------------------------------

    def _unresolved(
        self,
        ledger_row: LedgerRecord,
        exception_category: str,
        reasoning: Optional[str] = None,
        llm_confidence: Optional[float] = None,
    ) -> MatchRecord:
        return MatchRecord(
            match_id=f"UNRESOLVED_{ledger_row.txn_id}",
            ledger_txn_id=ledger_row.txn_id,
            matched_bank_utr=None,
            matched_gateway_payment_id=None,
            match_type="unresolved",
            confidence=llm_confidence if llm_confidence is not None else 0.0,
            reasoning=reasoning,
            exception_category=exception_category,
        )
