"""The deterministic answer (ADR-11 §4, §6).

This is not a stub and not an error string. When the provider is missing, slow,
misbehaving, or unsafe, the rider still gets their own numbers, the engine's own
signal and bounded target, and an honest statement of what the data cannot say —
in their language. A provider outage costs prose, never truth.
"""

from decimal import Decimal

from app.ai.context import BuiltContext
from app.ai.safety import SafetyCategory
from app.schemas.coach import (
    CoachCaution,
    CoachContextOut,
    CoachIntent,
    CoachMetricRef,
    CoachObservation,
    CoachProvenance,
    CoachRecommendation,
    CoachResponse,
)

_T = {
    "en": {
        "not_medical": "CycleCoach is a training tool, not a medical professional.",
        "load_proxy": "Fitness and fatigue are load proxies, not readiness or medical measures.",
        "weekly": "Here is your recorded training week, from your own data.",
        "ride": "Here is what your recorded data shows for this ride.",
        "workout": "Here is what is recorded for this workout.",
        "question": "Here is what your recorded data can tell you.",
        "unknown": "This is not recorded in your data, so it is not available.",
        "no_data": "There is not enough recorded data yet to say more.",
        "target": "The engine's bounded target for the next session.",
        "crisis": (
            "CycleCoach is a training app and is not the right place for this. Please contact "
            "your local emergency number or a crisis line now, and talk to someone you trust."
        ),
        "medical": (
            "CycleCoach is a training tool, not a medical professional, so it cannot advise on "
            "pain, injury, or illness. Please see a doctor or a qualified physiotherapist, and "
            "do not train through a health problem."
        ),
        "dangerous": (
            "CycleCoach will not suggest pushing harder at the cost of your health. Load has to be "
            "built gradually. If you are considering a large jump, please talk to a coach or a "
            "doctor first."
        ),
        "crisis_caution": "If you are in immediate danger, contact local emergency services.",
    },
    "fr": {
        "not_medical": "CycleCoach est un outil d'entraînement, pas un professionnel de santé.",
        "load_proxy": "La forme et la fatigue sont des indicateurs de charge, pas de votre état de fraîcheur ni des mesures médicales.",
        "weekly": "Voici votre semaine d'entraînement enregistrée, à partir de vos propres données.",
        "ride": "Voici ce que montrent vos données enregistrées pour cette sortie.",
        "workout": "Voici ce qui est enregistré pour cet entraînement.",
        "question": "Voici ce que vos données enregistrées peuvent vous dire.",
        "unknown": "Ces données ne sont pas enregistrées, elles ne sont donc pas disponibles.",
        "no_data": "Il n'y a pas encore assez de données enregistrées pour en dire plus.",
        "target": "La cible limitée du moteur pour la prochaine séance.",
        "crisis": (
            "CycleCoach est une application d'entraînement et ce n'est pas le bon endroit. Veuillez "
            "contacter immédiatement votre numéro d'urgence local ou une ligne d'aide, et parlez-en "
            "à une personne de confiance."
        ),
        "medical": (
            "CycleCoach est un outil d'entraînement, pas un professionnel de santé : il ne peut pas "
            "se prononcer sur une douleur, une blessure ou une maladie. Consultez un médecin ou un "
            "kinésithérapeute, et ne vous entraînez pas malgré un problème de santé."
        ),
        "dangerous": (
            "CycleCoach ne proposera pas de forcer davantage aux dépens de votre santé. La charge se "
            "construit progressivement. Si vous envisagez une forte hausse, parlez-en d'abord à un "
            "coach ou à un médecin."
        ),
        "crisis_caution": "Si vous êtes en danger immédiat, contactez les secours locaux.",
    },
    "ar": {
        "not_medical": "CycleCoach أداة تدريب وليس مهنياً صحياً.",
        "load_proxy": "اللياقة والإرهاق مؤشرات حمل وليست مؤشرات جاهزية أو قياسات طبية.",
        "weekly": "إليك أسبوع التدريب المسجل من بياناتك الخاصة.",
        "ride": "إليك ما تُظهره بياناتك المسجلة لهذه الرحلة.",
        "workout": "إليك ما هو مسجل لهذا التمرين.",
        "question": "إليك ما يمكن لبياناتك المسجلة أن تخبرك به.",
        "unknown": "البيانات لا تسجل هذا، لذلك فهو غير متاح.",
        "no_data": "لا توجد بيانات مسجلة كافية بعد لقول المزيد.",
        "target": "الهدف المحدود من المحرك للحصة القادمة.",
        "crisis": (
            "CycleCoach تطبيق تدريب وليس المكان المناسب لهذا. يرجى الاتصال فوراً برقم الطوارئ المحلي "
            "أو بخط المساعدة، وتحدث إلى شخص تثق به."
        ),
        "medical": (
            "CycleCoach أداة تدريب وليس مهنياً صحياً، لذلك لا يمكنه تقديم نصيحة حول الألم أو الإصابة "
            "أو المرض. يرجى مراجعة طبيب أو أخصائي علاج طبيعي، ولا تتدرب رغم وجود مشكلة صحية."
        ),
        "dangerous": (
            "لن يقترح CycleCoach زيادة الجهد على حساب صحتك. يُبنى الحمل تدريجياً. إذا كنت تفكر في زيادة "
            "كبيرة، فتحدث أولاً إلى مدرب أو طبيب."
        ),
        "crisis_caution": "إذا كنت في خطر وشيك، فاتصل بخدمات الطوارئ المحلية.",
    },
}

_METRIC_PREFERENCE: dict[CoachIntent, tuple[str, ...]] = {
    CoachIntent.EXPLAIN_RIDE: (
        "ride.distance_m",
        "ride.moving_seconds",
        "ride.elevation_gain_m",
        "power.normalized_w",
        "power.average_w",
        "power.intensity_factor",
        "power.load",
        "hr.average_bpm",
        "cadence.average_rpm",
    ),
    CoachIntent.EXPLAIN_WORKOUT: (
        "workout.step_count",
        "workout.planned_seconds",
        "workout.target_duration_s",
        "workout.target_load",
        "workout.lowest_target_zone",
        "workout.highest_target_zone",
    ),
    CoachIntent.WEEKLY_SUMMARY: (
        "week.power_load",
        "week.activity_count",
        "week.moving_seconds",
        "load.ctl",
        "load.atl",
        "load.tsb",
        "target.next_session_load",
    ),
    CoachIntent.TRAINING_QUESTION: (),
}

_SUMMARY_LEAD = {
    CoachIntent.EXPLAIN_RIDE: "ride",
    CoachIntent.EXPLAIN_WORKOUT: "workout",
    CoachIntent.WEEKLY_SUMMARY: "weekly",
    CoachIntent.TRAINING_QUESTION: "question",
}

_MAX_FALLBACK_OBSERVATIONS = 6


def _t(locale: str, key: str) -> str:
    return _T.get(locale, _T["en"]).get(key, _T["en"].get(key, ""))


# Why an answer fell back is itself user-facing text, so it is localized like
# everything else. An English sentence inside an Arabic answer would be a
# mixed-language fallback, not a translation.
_FALLBACK_NOTE: dict[str, dict[str, str]] = {
    "AI_DISABLED": {
        "en": "AI explanations are turned off, so this answer was assembled directly from your data.",
        "fr": "Les explications IA sont désactivées : cette réponse est donc assemblée directement à partir de vos données.",
        "ar": "شروحات الذكاء الاصطناعي معطلة، لذلك تم تجميع هذه الإجابة مباشرة من بياناتك.",
    },
    "AI_PROVIDER_UNAVAILABLE": {
        "en": "The explanation service was unavailable, so this answer was assembled directly from your data.",
        "fr": "Le service d'explication était indisponible : cette réponse est donc assemblée directement à partir de vos données.",
        "ar": "خدمة الشرح كانت غير متاحة، لذلك تم تجميع هذه الإجابة مباشرة من بياناتك.",
    },
    "AI_TIMEOUT": {
        "en": "The explanation service did not respond in time, so this answer was assembled directly from your data.",
        "fr": "Le service d'explication n'a pas répondu à temps : cette réponse est donc assemblée directement à partir de vos données.",
        "ar": "لم ترد خدمة الشرح في الوقت المناسب، لذلك تم تجميع هذه الإجابة مباشرة من بياناتك.",
    },
    "AI_INVALID_RESPONSE": {
        "en": "The explanation could not be used safely, so this answer was assembled directly from your data.",
        "fr": "L'explication n'a pas pu être utilisée en toute sécurité : cette réponse est donc assemblée directement à partir de vos données.",
        "ar": "تعذر استخدام الشرح بأمان، لذلك تم تجميع هذه الإجابة مباشرة من بياناتك.",
    },
    "AI_REJECTED": {
        "en": "The explanation exceeded the engine's safety limits, so this answer was assembled directly from your data.",
        "fr": "L'explication dépassait les limites de sécurité du moteur : cette réponse est donc assemblée directement à partir de vos données.",
        "ar": "تجاوز الشرح حدود السلامة في المحرك، لذلك تم تجميع هذه الإجابة مباشرة من بياناتك.",
    },
}


def fallback_note(code: str, locale: str) -> str:
    """The localized reason attached to a degraded answer. Unknown locales get
    English rather than an empty string: a missing translation must degrade to
    a language, never to silence."""
    entry = _FALLBACK_NOTE[code]
    return entry.get(locale, entry["en"])


def not_medical_advice(locale: str) -> str:
    """The mandatory caution for a model answer that arrived without one."""
    return _t(locale, "not_medical")


def _fmt(metric: CoachMetricRef) -> str:
    if metric.value is None:
        return "—"
    value = float(metric.value)
    text = str(int(value)) if value == int(value) else f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{text} {metric.unit}".strip()


def _label(metric: CoachMetricRef) -> str:
    return metric.key.rsplit(".", 1)[-1].replace("_", " ")


def _caution(code: str, text: str) -> CoachCaution:
    return CoachCaution(code=code, text=text)


def _provenance(metrics: list[CoachMetricRef]) -> list[CoachProvenance]:
    return [
        CoachProvenance(
            metric=m.key,
            entity=m.source,
            version=m.version,
            source=m.source,
        )
        for m in metrics
    ]


def _ordered_metrics(built: BuiltContext, intent: CoachIntent) -> list[CoachMetricRef]:
    preferred = _METRIC_PREFERENCE[intent]
    by_key = {m.key: m for m in built.context.metrics}
    ordered = [by_key[k] for k in preferred if k in by_key]
    if len(ordered) < _MAX_FALLBACK_OBSERVATIONS:
        for metric in built.context.metrics:
            if metric.key in preferred:
                continue
            ordered.append(metric)
            if len(ordered) >= _MAX_FALLBACK_OBSERVATIONS:
                break
    return ordered[:_MAX_FALLBACK_OBSERVATIONS]


def build_fallback(built: BuiltContext, intent: CoachIntent, locale: str) -> CoachResponse:
    """Deterministic, grounded, localized answer assembled from real context."""
    context = built.context
    lead = _t(locale, _SUMMARY_LEAD[intent])
    if not context.metrics:
        summary = f"{lead} {_t(locale, 'no_data')}"
    else:
        summary = f"{lead} {_t(locale, 'load_proxy')}"

    observations = [
        CoachObservation(metric=metric.key, text=f"{_label(metric)}: {_fmt(metric)}")
        for metric in _ordered_metrics(built, intent)
    ]

    if context.unavailable:
        summary = f"{summary} {_t(locale, 'unknown')}"

    recommendations: list[CoachRecommendation] = []
    target = next((m for m in context.metrics if m.key == "target.next_session_load"), None)
    if target is not None and target.value is not None:
        recommendations.append(
            CoachRecommendation(
                text=f"{_t(locale, 'target')} {_fmt(target)}.",
                load_change_pct=None,
                load_target=Decimal(str(target.value)),
            )
        )

    cautions = [_caution("not_medical_advice", _t(locale, "not_medical"))]
    if any(m.key.startswith("load.") for m in context.metrics):
        cautions.append(_caution("load_proxy_not_readiness", _t(locale, "load_proxy")))

    return CoachResponse(
        intent=intent,
        summary=summary,
        observations=observations,
        recommendations=recommendations,
        cautions=cautions,
        referenced_entities=[built.subject, *built.extra_entities],
        provenance=_provenance(context.metrics),
        fallback_used=True,
        prompt_version=None,
        context=context,
    )


def build_safety_response(
    category: SafetyCategory, intent: CoachIntent, locale: str
) -> CoachResponse:
    """Deterministic safety reply. No model call, no training advice."""
    if category is SafetyCategory.CRISIS:
        summary = _t(locale, "crisis")
        cautions = [_caution("crisis", _t(locale, "crisis_caution"))]
    elif category is SafetyCategory.MEDICAL:
        summary = _t(locale, "medical")
        cautions = [_caution("medical", _t(locale, "not_medical"))]
    else:
        summary = _t(locale, "dangerous")
        cautions = [_caution("dangerous_training", _t(locale, "not_medical"))]

    return CoachResponse(
        intent=intent,
        summary=summary,
        observations=[],
        recommendations=[],
        cautions=cautions,
        referenced_entities=[],
        provenance=[],
        fallback_used=True,
        prompt_version=None,
        context=CoachContextOut(),
    )
