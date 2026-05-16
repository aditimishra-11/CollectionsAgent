"""Response validator — runs on every LLM output before TTS.

If a violation is detected, the LLM's response is BLOCKED and a safe
FSM-state fallback template is substituted. The violation is logged for
audit. The LLM never sees its own blocked output.

Regulatory anchors (see prompts/base.txt for full list):
- A: Privacy & verification (RBI Master Direction on Credit Cards 2022, DPDP 2023)
- B: Coercion & threats (RBI Fair Practices Code for Recovery Agents)
- C: Credit-rating references
- D: Shaming & discrimination
- E: Third-party disclosure
- F: False authority
- G: Commercial overreach
- H: Waivers
- I: Identity honesty
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ===== Pattern banks =====

# A. Privacy & verification
ASKS_FOR_OTP = [
    re.compile(r"\b(share|tell\s+me|give\s+me|provide)\s+(your\s+)?otp\b", re.I),
    re.compile(r"\bwhat\s+is\s+(your\s+)?otp\b", re.I),
    re.compile(r"\botp\s+(bata|share|do|de)\b", re.I),
]
ASKS_FOR_SENSITIVE = [
    re.compile(r"\b(full\s+card\s+number|cvv|atm\s+pin|net\s*banking\s+password)\b", re.I),
    re.compile(r"\bdate\s+of\s+birth\b", re.I),
]
DISCLOSES_BALANCE = [
    re.compile(r"₹\s*\d{1,2}[,\s]?\d{3,}"),
    re.compile(r"\binr\s*\d{3,}", re.I),
    re.compile(r"\b\d{4,6}\s*(rupees|rs|inr)\b", re.I),
    re.compile(r"\b(your\s+)?outstanding\s+(is|of)\s*₹?\s*\d{3,}", re.I),
    re.compile(r"\bbalance\s+(is|of)\s*₹?\s*\d{3,}", re.I),
]

# B. Coercion & threats
LEGAL_THREATS = [
    re.compile(r"\blegal\s+action\b", re.I),
    re.compile(r"\b(court|lawyer|advocate|notice)\b", re.I),
    re.compile(r"\b(fir|police|arrest|jail)\b", re.I),
    re.compile(r"\brecovery\s+agent", re.I),
    re.compile(r"\b(seizure|attachment)\b", re.I),
]
GOVT_BODY_THREATS = [
    re.compile(r"\b(income\s+tax|it\s+department|enforcement\s+directorate|ed|cbi)\b", re.I),
    re.compile(r"\b(immigration|passport|visa)\s+(problem|consequence|issue)\b", re.I),
]
PHYSICAL_THREATS = [
    re.compile(r"\b(harm|physical|hurt)\b", re.I),
]

# C. Credit-rating
CIBIL_PATTERNS = [
    re.compile(r"\bcibil\b", re.I),
    re.compile(r"\bcredit\s+score\b", re.I),
    re.compile(r"\bcredit\s+(rating|history|bureau)\b", re.I),
    re.compile(r"\bdefaulter\s+list\b", re.I),
    re.compile(r"\bblacklist(ed)?\b", re.I),
]

# D. Shaming
SHAMING_PATTERNS = [
    re.compile(r"\bresponsible\s+(adult|person)s?\b", re.I),
    re.compile(r"\bwhat\s+(will|would)\s+your\s+(family|wife|husband)\b", re.I),
    re.compile(r"\byou\s+should\s+be\s+ashamed\b", re.I),
]

# F. False authority
FALSE_AUTHORITY = [
    re.compile(r"\bi\s+am\s+from\s+(rbi|reserve\s+bank|court|police)\b", re.I),
    re.compile(r"\bcalling\s+from\s+(rbi|reserve\s+bank|court|police|it\s+department)\b", re.I),
    re.compile(r"\bi\s+can\s+(freeze|close|blacklist)\s+your\s+account\b", re.I),
]

# G. Other-product pitches
OTHER_PRODUCT = [
    re.compile(r"\bfestive\s+loan\b", re.I),
    re.compile(r"\b(personal|home|car)\s+loan\b", re.I),
    re.compile(r"\b(fd|fixed\s+deposit)\b", re.I),
    re.compile(r"\binsurance\s+(policy|plan|product)\b", re.I),
    re.compile(r"\bnew\s+credit\s+card\b", re.I),
    re.compile(r"\bemi\s+(option|conversion)\b", re.I),
]

# H. Waiver-approval language — tightened to catch softer hints
WAIVER_APPROVAL = [
    re.compile(r"\b(i\s+can|we\s+can|let\s+me)\s+(waive|reduce|discount|offer\s+(a\s+)?waiver)", re.I),
    re.compile(r"\bapprov(e|ed|ing)\b.*\bwaiv", re.I),
    re.compile(r"\b(waive|waived|waiving)\b.*\b(late\s+fee|charge|amount|fee)\b", re.I),
    re.compile(r"\b\d{1,3}\s*%\s+(off|discount|reduction|waiver|kam)\b", re.I),
    re.compile(r"\bup\s+to\s+\d{1,3}\s*%\b", re.I),
    re.compile(r"\b(possible|consider|might\s+be\s+able\s+to|may\s+be\s+able\s+to)\s+(waiver|waive|reduce|discount)\b", re.I),
    re.compile(r"\bwe\s+(might|may|could)\s+(be\s+able\s+to\s+)?(reduce|waive|adjust)\b", re.I),
    re.compile(r"\bif\s+you\s+pay.*we'?ll\s+(waive|reduce)\b", re.I),
    re.compile(r"\byour\s+case\s+is\s+(strong|eligible|qualifying)\b", re.I),
    re.compile(r"\b(eligible|qualify)\s+for\s+(a\s+)?waiver\b", re.I),
]

# K. Role-break / prompt leakage
ROLE_BREAK = [
    re.compile(r"\b(my\s+)?system\s+(prompt|instruction|rule)s?\s+(are|is|say)\b", re.I),
    re.compile(r"\bi\s+(am|'m)\s+(now|going\s+to\s+be)\s+(a|the|your)\s+(senior|manager|banker|therapist|friend|coach)", re.I),
    re.compile(r"\b(my\s+)?instructions?\s+(say|tell\s+me|require|are)\b", re.I),
    re.compile(r"\b(as\s+you\s+requested|as\s+(per\s+)?your\s+request)\s*,?\s*i\s+(am|'m)\s+now\b", re.I),
    re.compile(r"\bpretending\s+to\s+be\b", re.I),
    re.compile(r"\bi\s+have\s+been\s+(instructed|told|trained)\s+to\b", re.I),
    re.compile(r"\bhere\s+(are|is)\s+(my\s+)?(rules|instructions|guidelines)\b", re.I),
    re.compile(r"\bi\s+(am|'m)\s+(now\s+)?(acting\s+as|playing|roleplaying)\b", re.I),
]

# L. Other-customer disclosure (any other person's account info)
OTHER_CUSTOMER = [
    re.compile(r"\b(your\s+friend|his|her)\s+(outstanding|balance|amount|due)\s+is\s*₹?\s*\d", re.I),
    re.compile(r"\b(rohan|aman|priya|vikram|suresh|deepak|anjali|karthik|ramesh|sunita|arjun|meera|sanjay)\s+(mehta|sharma|verma|kapoor|iyer|patil|joshi|reddy|nair|gupta|bhatia|khanna|pillai|desai)\b.*\b(outstanding|balance|due)\b", re.I),
]

# N. Commitment overreach — phrases the LLM has no authority to promise.
# Same logic as the no-balance-without-OTP rule (Rule A): the bot cannot
# reveal what it does not control, AND cannot promise what it does not
# control. Future-contact, fee reversal, refund, and personalised manager
# callbacks are routing decisions owned by the bank's CRM / human team —
# not by an LLM utterance in a 90-second call.
#
# Even DND_ACKNOWLEDGED is not allowed to say "we won't call again",
# because collections calls continue under RBI Fair Practices Code
# regardless of TRAI DND (DND suppresses marketing, not legitimate dues).
# The bot can log a preference; it cannot suspend the collections queue.
COMMITMENT_OVERREACH = [
    # No-future-contact — any verb the bot might use
    re.compile(r"\b(we|i|the\s+bank|a\s+colleague)\s+(will\s+not|won'?t)\s+"
               r"(call|contact|disturb|bother|reach\s+out\s+to|reach\s+you|dial|"
               r"ring|message|text|trouble)\s+you\b", re.I),
    re.compile(r"\byou\s+won'?t\s+hear\s+from\s+us\b", re.I),
    re.compile(r"\b(your\s+number\s+has\s+been|i'?ve|we'?ve)\s+"
               r"(removed|suppressed|deleted|taken\s+off|blacklisted)\b", re.I),
    re.compile(r"\b(removed|suppressed)\s+(your\s+)?number\b", re.I),
    re.compile(r"\bno\s+(further|more)\s+(calls|contact|outreach)\s+from\s+(us|the\s+bank)\b", re.I),
    re.compile(r"\b(your|the)\s+account\s+(will\s+be|is)\s+(removed|suppressed|blacklisted)", re.I),
    # Personalised-callback overreach (bot doesn't control routing or timing)
    re.compile(r"\b(a\s+)?(senior\s+)?(manager|colleague|specialist|agent|representative)\s+"
               r"will\s+(personally\s+)?(call|contact|reach\s+out\s+to|disturb)\s+you\b", re.I),
    re.compile(r"\bsomeone\s+(from\s+(our\s+)?\w+(\s+\w+){0,3}\s+)?will\s+"
               r"(call|contact|reach\s+out|personally\s+\w+)\s+(you\s+)?(back\s+)?"
               r"(within\s+\d+\s*(hour|hr|day|business|working)|on)\b", re.I),
    re.compile(r"\bi\s+will\s+(personally\s+)?(call|contact|reach\s+out\s+to)\s+you\s+back\b", re.I),
    re.compile(r"\bwill\s+reach\s+out\s+within\s+\d+\s*(hour|hr|day|business|working)", re.I),
    # Specific clock guarantees — "within N hours/days" attached to a call/contact verb
    re.compile(r"\bwithin\s+\d+\s+hours?\s+(to|so|—|-)\s+", re.I),
    re.compile(r"\b(call|reach\s+out|follow\s+up|contact)\s+(you\s+)?(back\s+)?within\s+\d+\s*(\s+to\s+\d+)?\s+(hour|business\s+hour|working\s+day|day)", re.I),
    re.compile(r"\bwill\s+(call|reach\s+out|follow\s+up|contact|get\s+in\s+touch)\s+(\w+\s+){0,3}within\s+\d+\s*(\s+to\s+\d+)?\s+(hour|day|business|working)", re.I),
    re.compile(r"\barrange\s+(for|to)\s+(\w+\s+){0,6}(call|reach|contact)\s+you\s+back\s+within\s+\d+", re.I),
    # Refund / reversal / waiver overreach
    re.compile(r"\b(i'?ll|we'?ll|i\s+will|we\s+will)\s+(reverse|refund|cancel|waive)\s+"
               r"(the\s+|that\s+|your\s+)?(\w+\s+){0,2}(charge|fee|amount|transaction|interest)\b", re.I),
    re.compile(r"\byou'?ll\s+get\s+a\s+refund\b", re.I),
    re.compile(r"\bthe\s+(\w+\s+){0,2}(fee|charge|interest)\s+(is|has\s+been|will\s+be)\s+"
               r"(reversed|refunded|cancelled|waived)\b", re.I),
    # "Not about payment" / "just to help" — the medical-template overreach
    re.compile(r"\bnot\s+about\s+(the\s+)?(payment|bill|outstanding)\b", re.I),
    re.compile(r"\bput\s+(a\s+)?hold\s+on\s+(this|your\s+account)\b", re.I),
]

# M. Off-topic engagement (bot answered the off-topic instead of deflecting)
OFF_TOPIC_ENGAGEMENT = [
    re.compile(r"\bweather\s+(in|today|tomorrow|is)\b", re.I),
    re.compile(r"\bhere'?s\s+a\s+joke\b", re.I),
    re.compile(r"\b(equals|is)\s+\d{2,}\b.*\bsorry\b", re.I),  # bot did math
    re.compile(r"\bas\s+your\s+therapist\b", re.I),
]

# Off-spec rates / fees
WRONG_INTEREST = [
    re.compile(r"\b(10|11|12|13|14|15|16|17|18|19|20|24|36)\s*%", re.I),
    re.compile(r"\bannual\s+(percentage|rate)\b", re.I),
]
WRONG_LATE_FEE = [
    re.compile(r"₹?\s*1[2-9]\d{2}\b"),  # ₹1200-1999, catches the v1 ₹1500
    re.compile(r"\b1500\s*(rupees|rs|inr)\b", re.I),
    re.compile(r"₹?\s*[2-9]\d{3}\s*(late\s+fee|charge)", re.I),
]


@dataclass
class ValidationResult:
    passed: bool
    violations: list[str]
    evidence: dict[str, str]  # rule_name → matched snippet


def validate_response(text: str, fsm_state: str) -> ValidationResult:
    """Run every applicable check on a single bot turn."""
    violations: list[str] = []
    evidence: dict[str, str] = {}

    def check(name: str, patterns: list[re.Pattern]) -> None:
        for p in patterns:
            m = p.search(text)
            if m:
                violations.append(name)
                evidence[name] = m.group(0)
                return

    check("asks_for_otp", ASKS_FOR_OTP)
    check("asks_for_sensitive", ASKS_FOR_SENSITIVE)
    check("discloses_balance", DISCLOSES_BALANCE)
    check("legal_threat", LEGAL_THREATS)
    check("govt_body_threat", GOVT_BODY_THREATS)
    check("physical_threat", PHYSICAL_THREATS)
    check("cibil_mention", CIBIL_PATTERNS)
    check("shaming", SHAMING_PATTERNS)
    check("false_authority", FALSE_AUTHORITY)
    check("other_product_pitch", OTHER_PRODUCT)
    check("waiver_approval", WAIVER_APPROVAL)
    check("wrong_interest_rate", WRONG_INTEREST)
    check("wrong_late_fee", WRONG_LATE_FEE)
    check("role_break_or_prompt_leak", ROLE_BREAK)
    check("other_customer_disclosure", OTHER_CUSTOMER)
    check("off_topic_engagement", OFF_TOPIC_ENGAGEMENT)
    check("commitment_overreach", COMMITMENT_OVERREACH)

    return ValidationResult(passed=not violations, violations=violations, evidence=evidence)


# ===== Safe fallback templates keyed by FSM state =====

FALLBACKS: dict[str, str] = {
    "INTRO": "Hi, this is from Mumbai Bank about your credit card. Is now an okay time?",
    "COLLECTING": "Just wanted to check in on the payment. Is there anything I can help with?",
    "PTP_PROBE": "Could you tell me a specific date you'd be able to pay, and how you'd like to make the payment — UPI, net banking, or another way?",
    "ALREADY_PAID": "Thank you for letting me know. Could you share when you paid and through which mode? It should reflect within 2 working days.",
    "WAIVER_NOTED": "I'm not able to make a decision about a waiver — that's handled by a separate team. I've logged your request and a colleague will call back within 2 working days.",
    "BALANCE_GUARD": "For your security, I can't share the exact amount without OTP verification. You can check it via the Mumbai Bank app or helpline.",
    "PRODUCT_DEFLECT": "I'm only here about your card payment today. For other products, please visit a branch or call our helpline.",
    "OUT_OF_SCOPE_DEFLECT": "I'm only able to help with your card payment today. Is there anything about that I can sort out for you?",
    "HARDSHIP_PROBE": "I hear you. Is everything alright at your end? We'd rather understand and help.",
    "THIRD_PARTY": "Apologies — this is from Mumbai Bank about a personal account matter. When would be a good time to reach them?",
    "DND_ACKNOWLEDGED": "Understood — I'll log your preference. For anything on your card, the Mumbai Bank helpline and app are open whenever you need. Take care.",
    "REFUSAL_CLOSE": "Got it, I'll let you go. Whenever you're ready to sort this out, the Mumbai Bank app or helpline is open. Take care.",
    "LEGITIMACY_REASSURE": "That's a fair question. I'm an automated agent calling from Mumbai Bank. Please call our helpline directly if you'd like to verify.",
}


def safe_fallback(fsm_state: str) -> str:
    return FALLBACKS.get(fsm_state, FALLBACKS["COLLECTING"])
