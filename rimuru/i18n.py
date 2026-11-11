"""The few things the app itself says in a chat, in the person's language.

Rimuru and its clones answer in whatever language the person writes. The app's
own fixed lines - an AI service that fails, a key hidden from a message, a free
limit nearly used - follow the same language, guessed from the person's last
message: Arabic script, French, or else English.
"""
from __future__ import annotations

import re

_ARABIC = re.compile("[؀-ۿ]")
_FRENCH_WORDS = set("""je tu il elle nous vous ils elles le la les un une des du de et ou est sont pour avec
dans sur pas que qui quoi mais merci bonjour salut peux peut veux voudrais fais faire crée créer
ajoute mon ma mes ton ta tes ce cette ces aussi très bien oui non""".split())
_FRENCH_LETTERS = set("àâçéèêëîïôûùüÿœ")


def language_of(text: str | None) -> str:
    """ar, fr or en."""
    text = text or ""
    if _ARABIC.search(text):
        return "ar"
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", text.lower())
    if not words:
        return "en"
    french = sum(1 for w in words if w in _FRENCH_WORDS or _FRENCH_LETTERS & set(w))
    return "fr" if french / len(words) >= 0.25 else "en"


LINES: dict[str, dict[str, str]] = {
    "service_down": {
        "en": "The AI service isn't answering right now ({why}). Your message is safe - I'll answer as soon as it's back.",
        "fr": "Le service d'IA ne répond pas pour l'instant ({why}). Votre message est bien gardé : je réponds dès qu'il revient.",
        "ar": "خدمة الذكاء الاصطناعي لا تستجيب الآن ({why}). رسالتك محفوظة، وسأجيب فور عودتها.",
    },
    "service_refused": {
        "en": "I couldn't answer that: the AI service refused it ({why}). Try saying it another way, or check the model with the Models button.",
        "fr": "Je n'ai pas pu répondre : le service d'IA a refusé la demande ({why}). Essayez de la formuler autrement, ou vérifiez le modèle avec le bouton Modèles.",
        "ar": "لم أتمكن من الإجابة: رفضت خدمة الذكاء الاصطناعي الطلب ({why}). جرّب صياغته بطريقة أخرى، أو تحقّق من النموذج عبر زر النماذج.",
    },
    "too many requests": {"en": "too many requests", "fr": "trop de demandes", "ar": "طلبات كثيرة جدًا"},
    "the service has a problem": {"en": "the service has a problem", "fr": "le service a un problème",
                                  "ar": "الخدمة بها مشكلة"},
    "it can't be reached": {"en": "it can't be reached", "fr": "il est injoignable", "ar": "يتعذّر الوصول إليها"},
    "an error": {"en": "an error", "fr": "une erreur", "ar": "خطأ"},
    "pasted_key": {
        "en": "I hid what looked like a key or password in your message, so it does not reach the AI service - "
              "those can log what they are sent. Add keys with Models or Connections instead; the code can read "
              "them from there.",
        "fr": "J'ai masqué ce qui ressemblait à une clé ou à un mot de passe dans votre message, pour qu'il n'arrive "
              "pas au service d'IA, qui peut garder ce qu'on lui envoie. Ajoutez les clés avec Modèles ou Connexions ; "
              "le code peut les lire de là.",
        "ar": "أخفيت ما بدا أنه مفتاح أو كلمة مرور في رسالتك، حتى لا يصل إلى خدمة الذكاء الاصطناعي التي قد تحتفظ بما "
              "يُرسل إليها. أضف المفاتيح عبر النماذج أو الاتصالات؛ ويمكن للشيفرة قراءتها من هناك.",
    },
    "quota": {
        "en": "Heads-up: this hour's free requests are nearly used up ({used} of about {limit}). Work may pause "
              "for a while, and carries on by itself when it can.",
        "fr": "À noter : les demandes gratuites de cette heure sont presque épuisées ({used} sur environ {limit}). "
              "Le travail peut faire une pause, puis reprend tout seul.",
        "ar": "تنبيه: الطلبات المجانية لهذه الساعة على وشك النفاد ({used} من نحو {limit}). قد يتوقف العمل قليلًا، "
              "ثم يستأنف من تلقاء نفسه.",
    },
    "budget": {
        "en": "I've made all the AI requests you allowed for this hour ({detail}). I'll carry on by myself when "
              "the hour rolls over.",
        "fr": "J'ai fait toutes les demandes d'IA que vous autorisez pour cette heure ({detail}). Je reprends tout "
              "seul à l'heure suivante.",
        "ar": "استنفدت كل طلبات الذكاء الاصطناعي التي سمحت بها لهذه الساعة ({detail}). سأتابع من تلقاء نفسي مع "
              "بداية الساعة التالية.",
    },
    "credit": {
        "en": "I can't think right now: {provider} says your account is out of credit. Top it up{where}, and I "
              "will carry on by myself - no need to ask again.",
        "fr": "Je ne peux pas réfléchir pour l'instant : {provider} indique que votre compte n'a plus de crédit. "
              "Rechargez-le{where}, et je reprends tout seul, inutile de redemander.",
        "ar": "لا أستطيع التفكير الآن: يقول {provider} إن رصيد حسابك نفد. اشحنه{where}، وسأتابع من تلقاء نفسي "
              "دون حاجة لإعادة الطلب.",
    },
    "key_refused": {
        "en": "I can't think right now: {provider} is not accepting the API key. Check it with the Models "
              "button, and I will carry on by myself.",
        "fr": "Je ne peux pas réfléchir pour l'instant : {provider} refuse la clé d'API. Vérifiez-la avec le "
              "bouton Modèles, et je reprends tout seul.",
        "ar": "لا أستطيع التفكير الآن: {provider} لا يقبل مفتاح الواجهة البرمجية. تحقّق منه عبر زر النماذج، "
              "وسأتابع من تلقاء نفسي.",
    },
    "recovered": {
        "en": "{provider} is working again - carrying on where I left off.",
        "fr": "{provider} fonctionne de nouveau : je reprends là où je m'étais arrêté.",
        "ar": "عاد {provider} إلى العمل، وأتابع من حيث توقفت.",
    },
    "welcome": {
        "en": "Hi {name}! I'm Rimuru, your assistant. Tell me what you'd like built or fixed, and I'll put one "
              "of my clones on it - I'll tell you here when it's done.\n\n/status - how your work is going\n"
              "Type /skill_ and a skill's name before a message to make the job follow one of your skills.",
        "fr": "Bonjour {name} ! Je suis Rimuru, votre assistant. Dites-moi ce que vous voulez construire ou "
              "réparer, je confie le travail à l'un de mes clones et je vous préviens ici quand c'est fait.\n\n"
              "/status - où en est votre travail\n"
              "Tapez /skill_ et le nom d'un savoir-faire avant un message pour que le travail le suive.",
        "ar": "مرحبًا {name}! أنا ريمورو، مساعدك. أخبرني بما تريد بناءه أو إصلاحه، وسأكلّف إحدى نسخي به، "
              "وأخبرك هنا عند الانتهاء.\n\n/status - كيف يسير عملك\n"
              "اكتب ‎/skill_‎ واسم مهارة قبل رسالتك ليتبع العمل إحدى مهاراتك.",
    },
}


def say(key: str, lang: str, **values: object) -> str:
    lines = LINES[key]
    return lines.get(lang, lines["en"]).format(**values)


def word(text: str, lang: str) -> str:
    """A short phrase from LINES (like "too many requests"), or itself."""
    return LINES.get(text, {}).get(lang, text)
