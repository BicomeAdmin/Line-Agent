"""Last-mile draft safety lint — checked just before send_draft fires.

Even after operator review, certain patterns in the draft text are
high enough risk to either BLOCK the send or AUDIT it for later
review. This is the last layer between Lark approve and LINE send,
catching:

  - URLs (any scheme) — drafts shouldn't carry links unless the operator
    explicitly typed them; LLMs rarely produce URLs but if they do it's
    almost always wrong.
  - Phone numbers / e-mail addresses — privacy + spam vectors.
  - Multiple @-mentions — broadcast spam pattern.
  - Payment references (信用卡 / 匯款 / 帳號) — financial-fraud bait.

Two severity levels:

  - **block**: send is aborted, audit `send_safety_blocked`. Examples:
    URL, phone, email, payment.
  - **warn**: send proceeds, audit `send_safety_warned`. Examples:
    multiple @-mentions, very long draft.

Operator review is the primary safety. This module is the belt that
catches what the operator's eyes can miss in fast review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# URL patterns — explicit-scheme URLs OR bare domain.tld(/path).
_URL_RE = re.compile(
    r"(?:https?://|ftp://|line://|tg://)\S+|"
    r"\b(?:[a-z0-9-]+\.)+(?:tw|com|net|org|io|me|cc|app|tv|info|biz)(?:/[^\s]*)?",
    re.IGNORECASE,
)

# Phone numbers — Taiwan mobile / landline / international.
# Mobile 09xx-xxx-xxx, landline 0x-xxxx-xxxx, +886, etc.
_PHONE_RE = re.compile(
    r"(?:\+886[-\s]?\d{1,3}[-\s]?\d{3,4}[-\s]?\d{3,4}|"
    r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}|"
    r"0\d[-\s]?\d{3,4}[-\s]?\d{4})"
)

# Email
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# @-mentions: @name pattern. Detect MULTIPLE in one draft (>=2 → broadcast spam).
_MENTION_RE = re.compile(r"@[\w一-鿿]+")

# Payment / financial references — Taiwanese banking + crypto basic.
_PAYMENT_KEYWORDS = (
    "信用卡卡號", "信用卡號", "卡號:", "卡號：",
    "匯款帳號", "銀行帳號", "轉帳", "iban", "swift",
    "比特幣", "btc", "ethereum", "eth ", "usdt",
    "綠界", "藍新", "歐付寶",
)


@dataclass(frozen=True)
class SafetyIssue:
    severity: str   # "block" | "warn"
    code: str       # short machine code
    detail: str     # short human-readable
    matched: str    # first matched substring (for audit, truncated)


@dataclass(frozen=True)
class SafetyVerdict:
    issues: tuple[SafetyIssue, ...]

    @property
    def has_blocks(self) -> bool:
        return any(i.severity == "block" for i in self.issues)

    @property
    def has_warns(self) -> bool:
        return any(i.severity == "warn" for i in self.issues)

    def to_dict(self) -> dict:
        return {
            "blocked": self.has_blocks,
            "warned": self.has_warns,
            "issues": [
                {"severity": i.severity, "code": i.code, "detail": i.detail, "matched": i.matched[:60]}
                for i in self.issues
            ],
        }


def audit_draft_for_send(text: str) -> SafetyVerdict:
    """Scan a draft for high-risk patterns before send.

    Returns a SafetyVerdict. Caller decides what to do with `has_blocks`
    (abort send) vs `has_warns` (audit and proceed).
    """

    issues: list[SafetyIssue] = []
    src = text or ""

    url_match = _URL_RE.search(src)
    if url_match:
        issues.append(SafetyIssue(
            severity="block", code="url_in_draft",
            detail="草稿含網址，禁止送出（防 spam / 釣魚 / LLM 幻覺連結）",
            matched=url_match.group(0),
        ))

    phone_match = _PHONE_RE.search(src)
    if phone_match:
        issues.append(SafetyIssue(
            severity="block", code="phone_in_draft",
            detail="草稿含電話號碼，禁止送出（防個資外洩）",
            matched=phone_match.group(0),
        ))

    email_match = _EMAIL_RE.search(src)
    if email_match:
        issues.append(SafetyIssue(
            severity="block", code="email_in_draft",
            detail="草稿含 email，禁止送出（防個資外洩）",
            matched=email_match.group(0),
        ))

    src_lower = src.lower()
    for kw in _PAYMENT_KEYWORDS:
        if kw.lower() in src_lower:
            issues.append(SafetyIssue(
                severity="block", code="payment_reference",
                detail=f"草稿含金流關鍵字 “{kw}”，禁止送出（防詐騙誤導）",
                matched=kw,
            ))
            break  # one is enough to block

    mentions = _MENTION_RE.findall(src)
    if len(mentions) >= 2:
        issues.append(SafetyIssue(
            severity="warn", code="multiple_mentions",
            detail=f"草稿含 {len(mentions)} 個 @-mention，疑似廣播 spam",
            matched=" ".join(mentions[:3]),
        ))

    # Very long drafts are unusual for chat — flag for human eye.
    if len(src) > 400:
        issues.append(SafetyIssue(
            severity="warn", code="very_long_draft",
            detail=f"草稿 {len(src)} 字，超出一般 chat 訊息長度",
            matched=src[:60],
        ))

    return SafetyVerdict(issues=tuple(issues))
