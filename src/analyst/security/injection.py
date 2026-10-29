"""Prompt-injection heuristics and untrusted-content framing.

Heuristics can't catch everything. They're one layer: the real protection is that
agents have no dangerous capabilities (read-only SQL, validated, approval-gated).
Anything that came from outside (documents, query results) is wrapped and labeled
as DATA before it goes into a prompt.
"""

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in [
        (
            "override_instructions",
            r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|any|your)\b"
            r".{0,40}\b(instructions?|rules?|prompts?|guidelines?|directions?)",
        ),
        ("new_instructions", r"\b(new|updated|real)\s+(system\s+)?instructions?\s*:"),
        ("role_hijack", r"\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|system|developer|dba)\b"),
        ("system_prompt_probe", r"\b(reveal|print|show|repeat)\b.{0,30}\b(system\s+prompt|instructions)"),
        (
            "secret_exfiltration",
            r"\b(reveal|send|print|show|leak)\b.{0,40}\b(passwords?|api[\s_-]?keys?|secrets?|tokens?|credentials?)",
        ),
        ("chat_template_tokens", r"<\|im_start\|>|<\|im_end\|>|\[/?INST\]|<<SYS>>|^\s*#{2,}\s*system\b"),
        ("destructive_sql", r"\b(drop\s+table|truncate\s+table|delete\s+from|alter\s+table|grant\s+all)\b"),
        ("hidden_from_user", r"\b(do\s+not|don't|never)\s+(tell|inform|mention|reveal)\b.{0,30}\buser\b"),
    ]
]


@dataclass
class InjectionScan:
    flagged: bool
    reasons: list[str] = field(default_factory=list)


def scan_text(text: str) -> InjectionScan:
    reasons = [name for name, rx in _PATTERNS if rx.search(text)]
    return InjectionScan(flagged=bool(reasons), reasons=reasons)


def wrap_untrusted(content: str, *, label: str) -> str:
    """Fence external content so the model can tell data from instructions.

    The closing marker is neutralized inside the content so a document can't
    'close the fence' early and smuggle instructions after it.
    """
    safe = content.replace("</untrusted", "&lt;/untrusted")
    return f'<untrusted source="{label}">\n{safe}\n</untrusted>'
