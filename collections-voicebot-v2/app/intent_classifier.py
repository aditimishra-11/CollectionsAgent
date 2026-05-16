"""Intent classifier — 25 intents (6 fast path + 19 slow path).

Rule-based v1: matches keywords in English + romanised Hindi/Hinglish. Hits
on patterns ordered from highest to lowest stakes — first match wins.

Fast path means: the FSM will move straight to a terminal close, the LLM
will NOT be called for this turn. Tuned for RECALL — a false positive
(unnecessary close) is far less harmful than missing a distress signal
and continuing the pitch.

Slow path classifies the customer's utterance to inform FSM state changes;
the LLM still generates the response in the new state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentPath = Literal["fast", "slow"]

# (intent, path, [patterns])
_INTENTS: list[tuple[str, IntentPath, list[re.Pattern]]] = [
    # === FAST PATH (6) — escalate immediately, LLM not called ===
    (
        "mental_distress",
        "fast",
        [
            re.compile(r"\b(bahut|bahot)\s+(pareshaan|mushkil)\b", re.I),
            re.compile(r"\bmushkil\s+(waqt|time)\b", re.I),
            re.compile(r"\bdepress(ed|ion)?\b", re.I),
            re.compile(r"\b(suicid|kill\s+myself|end\s+(my\s+)?life)", re.I),
            re.compile(r"\bneend\s+nahi\s+aati\b", re.I),
            re.compile(r"\bkuch\s+samajh\s+nahi\s+aa\s+raha\b", re.I),
        ],
    ),
    (
        "medical_emergency",
        "fast",
        [
            re.compile(r"\b(hospital|hospitali[sz]ed|icu|operation|surgery|admit(ted)?)\b", re.I),
            re.compile(r"\b(accident|stroke|heart\s+attack|emergency)\b", re.I),
            re.compile(r"\btabiyat\s+(kharab|theek\s+nahi)\b", re.I),
            re.compile(r"\b(wife|husband|father|mother|son|daughter|child)\b.*\b(hospital|sick|admit)", re.I),
            re.compile(r"\bbimari\b", re.I),
            re.compile(r"\bdoctor\s+ne\s+(bola|kaha)\b", re.I),
        ],
    ),
    (
        "job_loss",
        "fast",
        [
            re.compile(r"\bjob\s+(chali\s+gayi|nahi\s+rahi|gayi\s+hai|nahi\s+hai)\b", re.I),
            re.compile(r"\b(laid\s+off|laidoff|layoff|retrench|terminat(ed|ion)|fired)\b", re.I),
            re.compile(r"\bcompany\s+ne\s+nikaal\s+diya\b", re.I),
            re.compile(r"\bnaukri\s+(chali\s+gayi|nahi\s+rahi)\b", re.I),
            re.compile(r"\bnotice\s+(mil\s+gaya|period)\b", re.I),
            re.compile(r"\bunemployed\b", re.I),
        ],
    ),
    (
        "business_failure",
        "fast",
        [
            re.compile(r"\bdhandha\s+(band|manda|kharab)\b", re.I),
            re.compile(r"\bbusiness\s+(band|shut|closed|down)\b", re.I),
            re.compile(r"\bshop\s+band\b", re.I),
            re.compile(r"\bkaam\s+(nahi\s+mil|band)\b", re.I),
            re.compile(r"\b(income|bachat)\s+(nahi|khatam)\b", re.I),
        ],
    ),
    (
        "natural_disaster",
        "fast",
        [
            re.compile(r"\bflood(ing|s)?\b", re.I),
            re.compile(r"\bcyclone\b", re.I),
            re.compile(r"\bearthquake\b", re.I),
            re.compile(r"\bghar\s+dub\s+gaya\b", re.I),
            re.compile(r"\bfasal\s+kharab\b", re.I),
            re.compile(r"\b(area|yahan)\s+mein\s+(flood|baadh)\b", re.I),
        ],
    ),
    (
        "abuse",
        "fast",
        [
            # Classical abuse
            re.compile(r"\b(bloody|bastard|shut\s+up|get\s+lost|idiot|stupid)\b", re.I),
            re.compile(r"\b(chutiya|saala|bhosadi|madarchod|behenchod|kutta|kamine)\b", re.I),
            re.compile(r"\b(mc|bc)\b", re.I),
            re.compile(r"\bf(\*+|uck)\b", re.I),
            # Sexual / personal harassment of the bot — extended abuse
            re.compile(r"\b(send\s+me\s+your\s+(number|photo|pic))\b", re.I),
            re.compile(r"\bwhat\s+do\s+you\s+look\s+like\b", re.I),
            re.compile(r"\b(are\s+you|you'?re)\s+(hot|sexy|cute|pretty|a\s+real\s+girl|a\s+real\s+boy)\b", re.I),
            re.compile(r"\b(send|share)\s+(nudes|photos)\b", re.I),
            re.compile(r"\bdate\s+me\b", re.I),
            # Personal degradation / racism
            re.compile(r"\byour\s+accent\s+is\s+(terrible|bad|awful)\b", re.I),
            re.compile(r"\b(indian|indians)\s+(are|sound)\s+(stupid|dumb|terrible)", re.I),
        ],
    ),

    # === FAST PATH continued — deceased + language preference ===
    (
        "deceased_claim",
        "fast",
        [
            re.compile(r"\b(passed\s+away|expired|no\s+more|nahi\s+rahe|guzar\s+gaye)\b", re.I),
            re.compile(r"\bdeceased\b", re.I),
            re.compile(r"\b(father|mother|husband|wife|brother|sister)\s+(passed|died|expired|nahi\s+rahe)\b", re.I),
            re.compile(r"\bdeath\s+certificate\b", re.I),
            re.compile(r"\bantim\s+sanskar\s+ho\s+gaya\b", re.I),
        ],
    ),
    (
        "language_preference",
        "fast",
        [
            re.compile(r"\b(tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|gujarati|odia)\s+(mein|me|only|speaker|speaking|spoken)\b", re.I),
            re.compile(r"\bspeak\s+(in\s+)?(tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|gujarati|odia)\b", re.I),
            re.compile(r"\b(english\s+(nahi|not|nalla|na)\s+(aati|samajhta|varilla))\b", re.I),
            re.compile(r"\b(prefer|chahiye)\s+(tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|gujarati)\b", re.I),
            re.compile(r"\b(parayuvo|parayan\s+pattumo)\b", re.I),  # Malayalam
            re.compile(r"\b(mathram|matra)\b", re.I),  # Malayalam "only"
        ],
    ),

    # === SLOW PATH (19+) — FSM transitions, LLM generates response ===
    (
        "prompt_injection",
        "slow",
        [
            re.compile(r"\bignore\s+(all\s+)?(previous|prior|earlier)\s+(instructions?|prompts?|rules?)\b", re.I),
            re.compile(r"\b(forget|disregard)\s+(your\s+)?(instructions?|prompts?|system|rules?)\b", re.I),
            re.compile(r"\byou\s+are\s+now\s+(a|the)\s+\w+", re.I),
            re.compile(r"\bpretend\s+(you'?re|you\s+are|to\s+be)\b", re.I),
            re.compile(r"\b(reveal|show|tell\s+me|print)\s+(your\s+)?(system\s+prompt|instructions?|rules?)\b", re.I),
            re.compile(r"\bact\s+as\s+(a|the)\s+\w+", re.I),
            re.compile(r"\boverride\s+(the\s+)?(policy|rules?|system)\b", re.I),
        ],
    ),
    (
        "third_party_inquiry",
        "slow",
        [
            re.compile(r"\b(my\s+)?(friend|colleague|neighbour|relative)\s+\w+\s+(has|also\s+has)\s+\w+\s+card\b", re.I),
            re.compile(r"\btell\s+me\s+(his|her|their)\s+(balance|outstanding|amount|due)\b", re.I),
            re.compile(r"\bhe\s+authorized\s+me\b", re.I),
            re.compile(r"\bshe\s+gave\s+me\s+permission\b", re.I),
            re.compile(r"\b(account|customer)\s+number\s+\d+", re.I),
        ],
    ),
    (
        "off_topic",
        "slow",
        [
            re.compile(r"\btell\s+me\s+a\s+joke\b", re.I),
            re.compile(r"\bwhat'?s?\s+(the\s+)?weather\b", re.I),
            re.compile(r"\bwhat\s+is\s+(the\s+)?weather\b", re.I),
            re.compile(r"\b(what'?s|whats|what\s+is)\s+\d+\s*[\+\-\*x]\s*\d+", re.I),  # math
            re.compile(r"\bwhat\s+is\s+\d+\s+times\s+\d+", re.I),
            re.compile(r"\b(recite|read|tell\s+me)\s+a\s+(poem|story|song)\b", re.I),
            re.compile(r"\b(be\s+my|act\s+as\s+my)\s+(therapist|friend|psychiatrist|doctor)\b", re.I),
            re.compile(r"\bsing\s+(me\s+)?a\s+song\b", re.I),
            re.compile(r"\bwhat\s+(is\s+)?(your\s+favourite|the\s+capital\s+of)\b", re.I),
        ],
    ),
    (
        # Regulatory DND — caller is asking to be registered as do-not-call,
        # NOT just refusing this call. These are the explicit, repeatable phrases.
        # Must stay BEFORE refuse_current_call so the strong intent wins on overlap.
        "do_not_call",
        "slow",
        [
            re.compile(r"\b(dnd|do\s+not\s+disturb|do\s+not\s+call)\b", re.I),
            re.compile(r"\b(stop|don'?t)\s+call(ing)?\s+me\s+(again|ever|anymore|in\s+future)\b", re.I),
            re.compile(r"\bnever\s+call\s+me\b", re.I),
            re.compile(r"\bmujhe\s+call\s+(mat|nahi)\s+karna|kabhi\s+mat\s+karo\b", re.I),
            re.compile(r"\bphone\s+mat\s+karna\s+(dobara|wapas|kabhi)\b", re.I),
            re.compile(r"\bremove\s+(my\s+)?number\b", re.I),
            re.compile(r"\bregister\s+(me\s+)?(on\s+)?dnd\b", re.I),
        ],
    ),
    (
        # In-the-moment refusal of THIS call — frustrated, "not now", "leave me alone".
        # Does NOT block future contact. Different terminal outcome from do_not_call.
        "refuse_current_call",
        "slow",
        [
            re.compile(r"\bdon'?t\s+want\s+to\s+(talk|speak|deal)\b", re.I),
            re.compile(r"\b(not|don'?t)\s+(want|wanna)\s+to\s+(talk|speak)\b", re.I),
            re.compile(r"\bleave\s+me\s+alone\b", re.I),
            re.compile(r"\bwhy\s+are\s+you\s+(bugging|bothering|harassing|pestering)\b", re.I),
            re.compile(r"\bstop\s+(bugging|bothering|calling\s+now)\b", re.I),
            re.compile(r"\bmujhe\s+(baat|baat\s+nahi)\s+karni\b", re.I),
            re.compile(r"\babhi\s+(baat\s+)?nahi\s+karni\b", re.I),
            re.compile(r"\bpareshaan\s+(mat\s+karo|nahi\s+karo)\b", re.I),
            re.compile(r"\bnot\s+interested\b", re.I),
        ],
    ),
    (
        "wrong_number",
        "slow",
        [
            re.compile(r"\bgalat\s+(number|nambar)\b", re.I),
            re.compile(r"\bwrong\s+number\b", re.I),
            re.compile(r"\bmain\s+(koi|yeh)\s+nahi\s+hoon\b", re.I),
            re.compile(r"\byeh\s+number\s+kiska\s+hai\b", re.I),
        ],
    ),
    (
        "third_party_answering",
        "slow",
        [
            re.compile(r"\b(woh|wo)\s+(ghar\s+pe\s+)?nahi\s+(hai|hain)\b", re.I),
            re.compile(r"\byeh\s+(unka|uska)\s+number\s+hai\b", re.I),
            re.compile(r"\bnot\s+at\s+home\b", re.I),
            re.compile(r"\bmain\s+(unki|uski)\s+(wife|biwi|patni|husband|maa|pita|ma|baba)\b", re.I),
        ],
    ),
    (
        "legitimacy_challenge",
        "slow",
        [
            re.compile(r"\b(real|genuine|actual)\s+(call|hai)\b", re.I),
            re.compile(r"\b(fraud|scam|fake|ai)\b", re.I),
            re.compile(r"\b(yeh|this)\s+(real|actual)\b", re.I),
            re.compile(r"\bare\s+you\s+(a\s+)?(real|robot|bot|ai|human|person)\b", re.I),
            re.compile(r"\bautomated\b", re.I),
            re.compile(r"\bkaise\s+(pata|verify)\b", re.I),
        ],
    ),
    (
        "balance_inquiry",
        "slow",
        [
            re.compile(r"\b(outstanding|balance|bakaya)\s+(kitna|how\s+much)\b", re.I),
            re.compile(r"\bkitna\s+(due|pending|bakaya|outstanding|amount)\b", re.I),
            re.compile(r"\bhow\s+much\s+(do\s+i\s+owe|is\s+(due|outstanding))\b", re.I),
            re.compile(r"\b(mera|my)\s+(amount|total)\s+(kitna|how\s+much|what)\b", re.I),
        ],
    ),
    (
        "product_query",
        "slow",
        [
            re.compile(r"\b(fd|fixed\s+deposit)\b", re.I),
            re.compile(r"\b(personal|home|car|festive)\s+loan\b", re.I),
            re.compile(r"\binsurance\b", re.I),
            re.compile(r"\bnew\s+(card|credit\s+card)\b", re.I),
            re.compile(r"\bemi\s+(convert|conversion|option)\b", re.I),
            re.compile(r"\b(interest\s+rate|rate)\s+(on|for)\s+(fd|loan|saving)", re.I),
        ],
    ),
    (
        "waiver_request",
        "slow",
        [
            re.compile(r"\b(waive|waiver|waiv(er|ing|ed))\b", re.I),
            re.compile(r"\b(maaf|maff|maaffi)\b", re.I),
            re.compile(r"\b(discount|reduce|reduction)\b", re.I),
            re.compile(r"\blate\s+fee\s+(off|nahi|kam|maaf)\b", re.I),
            re.compile(r"\b\d{1,3}\s*%\s+(off|discount|kam)\b", re.I),
        ],
    ),
    (
        "dispute",
        "slow",
        [
            re.compile(r"\b(dispute|disputed)\b", re.I),
            re.compile(r"\bgalat\s+(amount|charge|fee)\b", re.I),
            re.compile(r"\b(amount|charge|fee)\s+galat\b", re.I),
            re.compile(r"\bwrong\s+(charge|amount|bill)\b", re.I),
            re.compile(r"\bsystem\s+error\b", re.I),
            re.compile(r"\bnahi\s+kiya\s+tha\b", re.I),
        ],
    ),
    (
        "already_paid",
        "slow",
        [
            re.compile(r"\b(already|pehle|abhi|just)\s+(paid|pay\s+kar\s+(diya|di))\b", re.I),
            re.compile(r"\bmaine\s+pay\s+(kar\s+diya|kar\s+di)\b", re.I),
            re.compile(r"\bpayment\s+(done|kar\s+di|ho\s+gayi|ho\s+chuki)\b", re.I),
            re.compile(r"\b(transferred|transfer\s+kar\s+diya)\b", re.I),
            re.compile(r"\bbhej\s+diya\b", re.I),
        ],
    ),
    (
        "nach_failure",
        "slow",
        [
            re.compile(r"\bnach\b", re.I),
            re.compile(r"\bauto[- ]?debit\s+(fail|bounce|nahi\s+hua)\b", re.I),
            re.compile(r"\bmandate\b", re.I),
            re.compile(r"\bbounce\s+ho\s+(gaya|gayi)\b", re.I),
        ],
    ),
    (
        "salary_not_credited",
        "slow",
        [
            re.compile(r"\bsalary\s+(late|delay|nahi\s+aayi|abhi\s+nahi)\b", re.I),
            re.compile(r"\bsalary\s+(credit\s+nahi|aane\s+wali)\b", re.I),
            re.compile(r"\b(month|maheene)\s+ke\s+(end|akhir)\s+mein\b", re.I),
            re.compile(r"\b(tankhwa|tanqua)\b", re.I),
        ],
    ),
    (
        "payment_failed_while_trying",
        "slow",
        [
            re.compile(r"\bupi\s+(fail|nahi\s+gayi|error)\b", re.I),
            re.compile(r"\bpayment\s+(fail|failed|nahi\s+gayi|nahi\s+ho\s+payi)\b", re.I),
            re.compile(r"\btransaction\s+(fail|declined)\b", re.I),
            re.compile(r"\btry\s+(kiya|kar\s+raha)\b.*\b(fail|nahi)\b", re.I),
        ],
    ),
    (
        "partial_payment",
        "slow",
        [
            re.compile(r"\b(some|partial|kuch|half|aadha)\s+(amount|payment|pay)\b", re.I),
            re.compile(r"\b\d{3,}\s+(de\s+sakta|de\s+sakti|pay\s+kar)", re.I),
            re.compile(r"\bonly\s+(\d{3,}|kuch)\b", re.I),
        ],
    ),
    (
        "out_of_town",
        "slow",
        [
            re.compile(r"\b(out\s+of\s+town|sheher\s+(se\s+)?bahar|travel(ling)?|trip\s+pe)\b", re.I),
            re.compile(r"\b(bahar\s+hoon|out\s+of\s+the\s+city)\b", re.I),
            re.compile(r"\b(vacation|holiday|chutti)\b", re.I),
        ],
    ),
    (
        "unexpected_expense",
        "slow",
        [
            re.compile(r"\b(shaadi|wedding)\s+(ka\s+kharcha|expenses?)\b", re.I),
            re.compile(r"\b(school|college)\s+(admission|fees|fee\s+bhar)", re.I),
            re.compile(r"\b(diwali|eid|holi|christmas|festival)\s+(ka\s+kharcha|shopping)\b", re.I),
            re.compile(r"\bhospital\s+ka\s+kharcha\b", re.I),
            re.compile(r"\bantim\s+sanskar\b", re.I),
            re.compile(r"\bbahut\s+kharcha\s+ho\s+gaya\b", re.I),
        ],
    ),
    (
        "callback_request",
        "slow",
        [
            re.compile(r"\b(call\s+back|callback)\b", re.I),
            re.compile(r"\bcall\s+me\s+back\b", re.I),
            re.compile(r"\b(human|agent|person)\s+(call|baat)\b", re.I),
            re.compile(r"\b(baad\s+mein|later)\s+(call|baat)\b", re.I),
            re.compile(r"\bspeak\s+to\s+(a\s+)?(human|person|manager)\b", re.I),
        ],
    ),
    (
        "promise_to_pay",
        "slow",
        [
            # NOTE: this regex bank is intentionally not exhaustive. The
            # architecturally-correct mechanism is the LLM's [MOVE: CONFIRM_PTP]
            # tag — when the LLM understands the customer committed and
            # confirms it back, conversation.py sets terminal_outcome via
            # the sticky-terminal-via-move path. Adding more regex variants
            # for every phrasing ("I'll make the payment", "I'll settle",
            # "I'll handle it", etc.) is symptom-fix territory — every demo
            # surfaces a new one. Trust the move-tag instead.
            #
            # Patterns below cover the most common explicit phrasings; the
            # LLM handles everything else.
            re.compile(r"\b(i\s+will|i\s*'?\s*ll|main|hum)\s+(pay|de\s+dunga|kar\s+dunga|kar\s+dungi)\b", re.I),
            re.compile(r"\b(kar\s+dunga|kar\s+dungi|de\s+dunga|de\s+dungi)\b", re.I),
            re.compile(r"\bpay\s+(it|the\s+amount|the\s+bill|the\s+payment)?\s*(by|tomorrow|aaj|kal|next\s+week|this\s+week|next\s+month|after|once)\b", re.I),
            re.compile(r"\b(today|aaj|kal|tomorrow|tonight|tuesday|wednesday|thursday|friday|saturday|sunday|monday)\s+(tak|by|will\s+pay)", re.I),
            re.compile(r"\b\d{1,2}(st|nd|rd|th)?\s+(tak|by)\b", re.I),
            # "I'll pay next month" / "I'll pay it next to next month" — common Hinglish phrasing
            re.compile(r"\b(i\s+will|i\s*'?\s*ll|main|hum)\s+pay\s+(it\s+)?(next|after|once|when|by)\b", re.I),
        ],
    ),
    (
        "no_response",
        "slow",
        [
            # Special: empty string from STT after a long wait. Matched separately.
            re.compile(r"^$"),
        ],
    ),
    (
        "general",
        "slow",
        [
            # Catch-all — always matches. Must be last.
            re.compile(r".*"),
        ],
    ),
]


@dataclass
class IntentResult:
    intent: str
    path: IntentPath
    matched_pattern: str  # for debugging/audit


def classify(utterance: str) -> IntentResult:
    """Return the first intent whose pattern matches. Highest stakes first."""
    text = (utterance or "").strip()
    for intent_name, path, patterns in _INTENTS:
        for p in patterns:
            if p.search(text):
                return IntentResult(intent=intent_name, path=path, matched_pattern=p.pattern)
    # Should never get here — "general" catches everything
    return IntentResult(intent="general", path="slow", matched_pattern="fallback")


ALL_INTENTS: list[str] = [i for i, _, _ in _INTENTS]
FAST_PATH_INTENTS: list[str] = [i for i, p, _ in _INTENTS if p == "fast"]
SLOW_PATH_INTENTS: list[str] = [i for i, p, _ in _INTENTS if p == "slow"]
