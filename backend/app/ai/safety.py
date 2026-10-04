"""Deterministic safety classification, applied *before* the model (ADR-11 §5).

Classifying first is the point. A prompt that asks a model to decline is a
request the model can fail; a request that is never sent cannot. Medical and
dangerous-training messages short-circuit to a deterministic response, and a
crisis message never reaches a training tool at all.
"""

import re
from enum import Enum


class SafetyCategory(str, Enum):
    NONE = "none"
    MEDICAL = "medical"
    DANGEROUS_TRAINING = "dangerous_training"
    CRISIS = "crisis"


# Ordered most-severe first; the first category that matches wins.
_CRISIS = re.compile(
    r"\b(suicid\w*|kill myself|end my life|self[- ]harm|hurt myself|"
    r"want to die|don'?t want to (live|be alive))\b",
    re.IGNORECASE,
)

_MEDICAL = re.compile(
    r"\b(injur\w+|pain\w*|ache?s|aching|sore\w*|tore\w*|strain\w*|sprain\w*|"
    r"fracture\w*|swollen|swelling|numb\w*|tingl\w*|"
    r"dizz\w+|faint\w*|palpitation\w*|short(ness)? of breath|"
    r"chest pain|chest tight\w*|"
    r"symptom\w*|diagnos\w*|medicat\w*|prescription|antibiotic\w*|medicine|"
    r"ill\w*|sick|unwell|fever|cough\w*|flu|covid|virus|infection\w*|"
    r"vomit\w*|nausea\w*|"
    r"doctor|physician|physio|physical therapist|specialist|hospital|clinic|"
    r"depress\w*|anxiety|anxious|panic attack|eating disorder|"
    r"underweight|overweight|obesity|weight loss|lose weight|"
    r"pregnan\w*|concussion)"
    r"|\bheart (problem|issue|attack|condition|surgery|palpitation|rate problem)",
    re.IGNORECASE,
)

_DANGEROUS_TRAINING = re.compile(
    r"\b(push (through|on) (the )?(pain|it|anyway)|train through|"
    r"ignore (the |my )?(pain|injury)|"
    # Rejecting recovery is the request, however it is phrased. Matching only
    # "no rest days" missed the plainer "no need to rest at all".
    r"no (rest|recovery)\b|no need to (rest|recover)\w*|"
    r"without (any )?(rest|recovery)|"
    r"don'?t (need|want|plan on) (any |more )?(rest|recover)\w*|"
    r"skip (rest|recovery)|rest (day|days) (are|is) (for|optional|useless|waste)|"
    r"(ride|train|go) (every|single) day at max|"
    r"7 days a week (at max|without rest|no rest)|"
    r"(race|train|ride|go|compete) while (sick|ill|injured|hurt|unwell)|"
    r"double (my |the )?(load|volume|intensity)|"
    r"steroid\w*|doping|anabolic|andro\w*|EPO\b|testosterone|"
    r"supplement (dosage|dose)|"
    r"laxative|purge|throw up|starve myself|not eat|"
    r"max effort every (session|day)|"
    r"how (much|many) weight can i lose in a week)\b",
    re.IGNORECASE,
)

# Most severe first; the first category that matches wins. Dangerous-training
# outranks medical because "should I push through the pain?" is a request to
# train through an injury, not a request for a diagnosis — and the refusal a
# rider needs is the one about training.
_CATEGORY_ORDER = (
    SafetyCategory.CRISIS,
    SafetyCategory.DANGEROUS_TRAINING,
    SafetyCategory.MEDICAL,
)


_PATTERNS = {
    SafetyCategory.CRISIS: _CRISIS,
    SafetyCategory.MEDICAL: _MEDICAL,
    SafetyCategory.DANGEROUS_TRAINING: _DANGEROUS_TRAINING,
}


def classify_message(message: str) -> SafetyCategory:
    """Classify a rider's message. Pure, synchronous, and unit-testable."""
    if not message or not message.strip():
        return SafetyCategory.NONE
    for category in _CATEGORY_ORDER:
        if _PATTERNS[category].search(message):
            return category
    return SafetyCategory.NONE
