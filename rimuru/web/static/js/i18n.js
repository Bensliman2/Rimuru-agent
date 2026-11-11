/* The interface in English, French or Arabic (Arabic right to left).

   The pages are written in English. Every piece of interface text that is
   exactly a key below is shown in the chosen language, including text the
   scripts put on the page later (a MutationObserver watches for it). What
   people and AI models write - messages, file names, titles - is never touched.
   The language comes from the picker in the top bar, or the browser's. */

export const LANGS = { en: "English", fr: "Français", ar: "العربية" };

const FR = {
  "Your assistant": "Votre assistant",
  "Connecting": "Connexion…",
  "Reconnecting": "Reconnexion…",
  "Online": "En ligne",
  "Demo mode": "Mode démo",
  "Demo mode.": "Mode démo.",
  "Free limit nearly used": "Limite gratuite presque atteinte",
  "Out of credit": "Plus de crédit",
  "Key not accepted": "Clé refusée",
  "More": "Plus",
  "Under the hood": "Sous le capot",
  "Memory": "Mémoire",
  "Connections": "Connexions",
  "Skills": "Savoir-faire",
  "Models": "Modèles",
  "Stop": "Arrêter",
  "Language": "Langue",
  "New session": "Nouvelle session",
  "Sessions": "Sessions",
  "Your sessions": "Vos sessions",
  "What are sessions?": "Qu’est-ce qu’une session ?",
  "Start a new session for each new piece of work. Each session keeps its own conversation, its own clones and its own folder of files - so nothing gets mixed up. Rimuru still remembers what you told it in other sessions.":
    "Ouvrez une nouvelle session pour chaque nouveau travail. Chaque session a sa propre conversation, ses clones et son dossier de fichiers : rien ne se mélange. Rimuru se souvient de ce que vous lui avez dit dans les autres sessions.",
  "Not started yet": "Pas encore commencée",
  "No AI model is set up yet, so nothing real is built.": "Aucun modèle d’IA n’est configuré : rien n’est vraiment construit.",
  "Choose models": "Choisir les modèles",
  "New folder for this session": "Nouveau dossier pour cette session",
  "View it": "Voir",
  "View what it made": "Voir ce qu’il a fait",
  "Download": "Télécharger",
  "Back to the chat": "Retour à la discussion",
  "Report a problem": "Signaler un problème",
  "Open": "Ouvrir",
  "Change": "Changer",
  "Hi, I’m Rimuru.": "Bonjour, je suis Rimuru.",
  "Tell me what you need. I split into clones so several jobs can run at once, and I’ll keep you posted right here.":
    "Dites-moi ce dont vous avez besoin. Je me divise en clones pour mener plusieurs travaux à la fois, et je vous tiens au courant ici.",
  "Build a small Python calculator with tests": "Créer une petite calculatrice en Python avec des tests",
  "Write a script that renames photos by the date they were taken": "Écrire un script qui renomme les photos selon leur date",
  "Make a simple to-do list page in a single HTML file": "Faire une page de liste de tâches dans un seul fichier HTML",
  "Tell Rimuru what you need…": "Dites à Rimuru ce dont vous avez besoin…",
  "Message Rimuru": "Écrire à Rimuru",
  "Send": "Envoyer",
  "Clones at work": "Clones au travail",
  "What are clones?": "Que sont les clones ?",
  "When you ask for something, Rimuru hands it to a clone of itself. Each clone works on one job, so several jobs can run side by side. Only this session's clones are shown here. Click a clone to see exactly what it did.":
    "Quand vous demandez quelque chose, Rimuru le confie à un clone de lui-même. Chaque clone fait un seul travail, et plusieurs peuvent avancer en même temps. Seuls les clones de cette session sont montrés ici. Cliquez sur un clone pour voir exactement ce qu’il a fait.",
  "No clones are working right now. When you ask for something, Rimuru starts one here.":
    "Aucun clone ne travaille pour l’instant. Quand vous demandez quelque chose, Rimuru en lance un ici.",
  "Finished": "Terminé",
  "Thinking": "Réfléchit",
  "Reading the code": "Lit le code",
  "Writing code": "Écrit le code",
  "Running the tests": "Lance les tests",
  "Running a command": "Lance une commande",
  "Waiting for a free spot": "Attend une place libre",
  "Couldn't finish": "N’a pas pu finir",
  "Stopped": "Arrêté",
  "Interrupted by a restart": "Interrompu par un redémarrage",
  "Stop this job": "Arrêter ce travail",
  "Close": "Fermer",
  "Choose which AI does each job.": "Choisissez quelle IA fait chaque travail.",
  "Save changes": "Enregistrer",
  "Where this session works": "Où travaille cette session",
  "Its clones read and write files only in this folder.": "Ses clones ne lisent et n’écrivent que dans ce dossier.",
  "Rimuru's own folder for this session": "Le dossier de Rimuru pour cette session",
  "Made for you and named after the first job. Best for something new.": "Créé pour vous et nommé d’après le premier travail. Idéal pour quelque chose de nouveau.",
  "A folder on this computer": "Un dossier sur cet ordinateur",
  "Make it here": "Le créer ici",
  "Use this": "Utiliser",
  "Talk to Rimuru from Telegram, and let it work on GitHub. Only people you allow can give it work.":
    "Parlez à Rimuru depuis Telegram et laissez-le travailler sur GitHub. Seules les personnes autorisées peuvent lui confier du travail.",
  "Loading…": "Chargement…",
  "Tokens are kept in your .env file and never shown again.": "Les jetons sont gardés dans votre fichier .env et ne sont plus jamais affichés.",
  "New skill": "Nouveau savoir-faire",
  "Name": "Nom",
  "When to use it": "Quand s’en servir",
  "What the clones should do": "Ce que les clones doivent faire",
  "Add skill": "Ajouter",
  "Cancel": "Annuler",
  "Open the folder": "Ouvrir le dossier",
  "What Rimuru remembers": "Ce dont Rimuru se souvient",
  "Lasting facts about you and your work, used by itself in later sessions. Change or forget any of them. Passwords and keys are never kept.":
    "Des faits durables sur vous et votre travail, réutilisés d’eux-mêmes dans les sessions suivantes. Modifiez-les ou oubliez-les. Les mots de passe et les clés ne sont jamais gardés.",
  "About you": "Sur vous",
  "About me": "Sur moi",
  "General": "Général",
  "Remember": "Retenir",
  "Remembered.": "Retenu.",
  "Changed.": "Modifié.",
  "Forgotten.": "Oublié.",
  "Forget": "Oublier",
  "Save": "Enregistrer",
  "Nothing yet. As you work together, Rimuru keeps lasting facts here - and you can add your own.":
    "Rien pour l’instant. Au fil du travail, Rimuru garde ici des faits durables, et vous pouvez ajouter les vôtres.",
  "added by you": "ajouté par vous",
  "Rimuru chose to remember this": "Rimuru a choisi de s’en souvenir",
  "Delete this session?": "Supprimer cette session ?",
  "Its conversation, its jobs and what Rimuru remembers about it are deleted for good.":
    "Sa conversation, ses travaux et ce dont Rimuru se souvient à son sujet sont supprimés définitivement.",
  "Delete": "Supprimer",
  "Delete this session": "Supprimer cette session",
  "Hi, I'm Rimuru.": "Bonjour, je suis Rimuru.",
  "Free and fast": "Gratuit et rapide",
  "I have a key": "J’ai une clé",
  "Paste the key": "Collez la clé",
  "Connect": "Connecter",
  "A model on this computer": "Un modèle sur cet ordinateur",
  "Ollama is running here. Nothing you send leaves this computer.": "Ollama tourne ici. Rien de ce que vous envoyez ne quitte cet ordinateur.",
  "Just look around first - a demo that only pretends to work": "Juste regarder d’abord : une démo qui fait semblant de travailler",
  "Trying it out…": "Essai en cours…",
  "Asking the model to say hello. This takes a few seconds.": "Le modèle doit dire bonjour. Cela prend quelques secondes.",
  "It works.": "Ça marche.",
  "Start": "Commencer",
  "That did not work.": "Ça n’a pas marché.",
  "Try again": "Réessayer",
  "Back": "Retour",
  "Try this one": "Essayer celui-ci",
};

const AR = {
  "Your assistant": "مساعدك",
  "Connecting": "جارٍ الاتصال…",
  "Reconnecting": "جارٍ إعادة الاتصال…",
  "Online": "متصل",
  "Demo mode": "وضع تجريبي",
  "Demo mode.": "وضع تجريبي.",
  "Free limit nearly used": "الحد المجاني على وشك النفاد",
  "Out of credit": "نفد الرصيد",
  "Key not accepted": "المفتاح مرفوض",
  "More": "المزيد",
  "Under the hood": "ما وراء الكواليس",
  "Memory": "الذاكرة",
  "Connections": "الاتصالات",
  "Skills": "المهارات",
  "Models": "النماذج",
  "Stop": "إيقاف",
  "Language": "اللغة",
  "New session": "جلسة جديدة",
  "Sessions": "الجلسات",
  "Your sessions": "جلساتك",
  "What are sessions?": "ما هي الجلسات؟",
  "Start a new session for each new piece of work. Each session keeps its own conversation, its own clones and its own folder of files - so nothing gets mixed up. Rimuru still remembers what you told it in other sessions.":
    "ابدأ جلسة جديدة لكل عمل جديد. لكل جلسة محادثتها ونسخها ومجلد ملفاتها، فلا يختلط شيء. ويبقى ريمورو يتذكر ما قلته له في الجلسات الأخرى.",
  "Not started yet": "لم تبدأ بعد",
  "No AI model is set up yet, so nothing real is built.": "لم يُضبط أي نموذج ذكاء اصطناعي بعد، لذلك لا يُبنى شيء حقيقي.",
  "Choose models": "اختر النماذج",
  "New folder for this session": "مجلد جديد لهذه الجلسة",
  "View it": "عرض",
  "View what it made": "عرض ما صنعه",
  "Download": "تنزيل",
  "Back to the chat": "العودة إلى المحادثة",
  "Report a problem": "الإبلاغ عن مشكلة",
  "Open": "فتح",
  "Change": "تغيير",
  "Hi, I’m Rimuru.": "مرحبًا، أنا ريمورو.",
  "Tell me what you need. I split into clones so several jobs can run at once, and I’ll keep you posted right here.":
    "أخبرني بما تحتاجه. أنقسم إلى نسخ لتنجز عدة أعمال في وقت واحد، وأطلعك على كل شيء هنا.",
  "Build a small Python calculator with tests": "أنشئ آلة حاسبة صغيرة بلغة بايثون مع اختبارات",
  "Write a script that renames photos by the date they were taken": "اكتب برنامجًا يعيد تسمية الصور حسب تاريخ التقاطها",
  "Make a simple to-do list page in a single HTML file": "اصنع صفحة قائمة مهام بسيطة في ملف HTML واحد",
  "Tell Rimuru what you need…": "أخبر ريمورو بما تحتاجه…",
  "Message Rimuru": "راسل ريمورو",
  "Send": "إرسال",
  "Clones at work": "النسخ أثناء العمل",
  "What are clones?": "ما هي النسخ؟",
  "When you ask for something, Rimuru hands it to a clone of itself. Each clone works on one job, so several jobs can run side by side. Only this session's clones are shown here. Click a clone to see exactly what it did.":
    "عندما تطلب شيئًا، يسلّمه ريمورو إلى نسخة منه. كل نسخة تعمل على مهمة واحدة، فتتقدم عدة مهام معًا. تظهر هنا نسخ هذه الجلسة فقط. انقر على نسخة لترى ما فعلته بالضبط.",
  "No clones are working right now. When you ask for something, Rimuru starts one here.":
    "لا توجد نسخ تعمل الآن. عندما تطلب شيئًا، يبدأ ريمورو نسخة هنا.",
  "Finished": "انتهت",
  "Thinking": "تفكر",
  "Reading the code": "تقرأ الشيفرة",
  "Writing code": "تكتب الشيفرة",
  "Running the tests": "تشغّل الاختبارات",
  "Running a command": "تشغّل أمرًا",
  "Waiting for a free spot": "تنتظر مكانًا شاغرًا",
  "Couldn't finish": "لم تتمكن من الإنهاء",
  "Stopped": "متوقفة",
  "Interrupted by a restart": "قُطعت بإعادة تشغيل",
  "Stop this job": "أوقف هذه المهمة",
  "Close": "إغلاق",
  "Choose which AI does each job.": "اختر أي ذكاء اصطناعي يقوم بكل مهمة.",
  "Save changes": "حفظ التغييرات",
  "Where this session works": "أين تعمل هذه الجلسة",
  "Its clones read and write files only in this folder.": "نسخها تقرأ وتكتب الملفات في هذا المجلد فقط.",
  "Rimuru's own folder for this session": "مجلد ريمورو الخاص بهذه الجلسة",
  "Made for you and named after the first job. Best for something new.": "يُنشأ لك ويُسمّى باسم أول مهمة. الأفضل لشيء جديد.",
  "A folder on this computer": "مجلد على هذا الحاسوب",
  "Make it here": "أنشئه هنا",
  "Use this": "استخدم هذا",
  "Talk to Rimuru from Telegram, and let it work on GitHub. Only people you allow can give it work.":
    "تحدث مع ريمورو من تيليغرام، ودعه يعمل على GitHub. فقط من تسمح لهم يمكنهم إعطاؤه عملًا.",
  "Loading…": "جارٍ التحميل…",
  "Tokens are kept in your .env file and never shown again.": "تُحفظ الرموز في ملف ‎.env لديك ولا تُعرض مرة أخرى.",
  "New skill": "مهارة جديدة",
  "Name": "الاسم",
  "When to use it": "متى تُستخدم",
  "What the clones should do": "ما يجب أن تفعله النسخ",
  "Add skill": "إضافة",
  "Cancel": "إلغاء",
  "Open the folder": "فتح المجلد",
  "What Rimuru remembers": "ما يتذكره ريمورو",
  "Lasting facts about you and your work, used by itself in later sessions. Change or forget any of them. Passwords and keys are never kept.":
    "حقائق دائمة عنك وعن عملك، يستعملها من تلقاء نفسه في الجلسات اللاحقة. يمكنك تغيير أي منها أو نسيانها. لا تُحفظ كلمات المرور ولا المفاتيح أبدًا.",
  "About you": "عنك",
  "About me": "عني",
  "General": "عام",
  "Remember": "تذكّر",
  "Remembered.": "تم الحفظ.",
  "Changed.": "تم التغيير.",
  "Forgotten.": "تم النسيان.",
  "Forget": "انسَ",
  "Save": "حفظ",
  "Nothing yet. As you work together, Rimuru keeps lasting facts here - and you can add your own.":
    "لا شيء بعد. مع العمل معًا، يحفظ ريمورو هنا حقائق دائمة، ويمكنك إضافة ما تريد.",
  "added by you": "أضفته أنت",
  "Rimuru chose to remember this": "اختار ريمورو أن يتذكر هذا",
  "Delete this session?": "حذف هذه الجلسة؟",
  "Its conversation, its jobs and what Rimuru remembers about it are deleted for good.":
    "تُحذف محادثتها ومهامها وما يتذكره ريمورو عنها نهائيًا.",
  "Delete": "حذف",
  "Delete this session": "احذف هذه الجلسة",
  "Hi, I'm Rimuru.": "مرحبًا، أنا ريمورو.",
  "Free and fast": "مجاني وسريع",
  "I have a key": "لدي مفتاح",
  "Paste the key": "الصق المفتاح",
  "Connect": "اتصال",
  "A model on this computer": "نموذج على هذا الحاسوب",
  "Ollama is running here. Nothing you send leaves this computer.": "Ollama يعمل هنا. لا يغادر شيء مما ترسله هذا الحاسوب.",
  "Just look around first - a demo that only pretends to work": "أريد التجول أولًا: عرض تجريبي يتظاهر بالعمل فقط",
  "Trying it out…": "جارٍ التجربة…",
  "Asking the model to say hello. This takes a few seconds.": "نطلب من النموذج أن يلقي التحية. يستغرق ذلك بضع ثوانٍ.",
  "It works.": "يعمل.",
  "Start": "ابدأ",
  "That did not work.": "لم ينجح ذلك.",
  "Try again": "حاول مجددًا",
  "Back": "رجوع",
  "Try this one": "جرّب هذا",
};

// Interface lines with a name or a title in them.
const PATTERNS = {
  fr: [
    [/^(.+) started: (.+)$/, "$1 a commencé : $2"],
    [/^(.+) finished: (.+)$/, "$1 a terminé : $2"],
    [/^Finished: (.+)$/, "Terminé : $1"],
    [/^(.+) couldn't finish: (.+)$/, "$1 n’a pas pu finir : $2"],
    [/^Stopped: (.+)$/, "Arrêté : $1"],
    [/^Folder: (.+)$/, "Dossier : $1"],
    [/^Your folder: (.+)$/, "Votre dossier : $1"],
    [/^(\d+) min ago$/, "il y a $1 min"],
    [/^just now$/, "à l’instant"],
  ],
  ar: [
    [/^(.+) started: (.+)$/, "بدأت $1: $2"],
    [/^(.+) finished: (.+)$/, "أنهت $1: $2"],
    [/^Finished: (.+)$/, "انتهت: $1"],
    [/^(.+) couldn't finish: (.+)$/, "لم تتمكن $1 من الإنهاء: $2"],
    [/^Stopped: (.+)$/, "أوقفت: $1"],
    [/^Folder: (.+)$/, "المجلد: $1"],
    [/^Your folder: (.+)$/, "مجلدك: $1"],
    [/^(\d+) min ago$/, "قبل $1 دقيقة"],
    [/^just now$/, "الآن"],
  ],
};

export function chosen() {
  try { return localStorage.getItem("rimuru.lang") || "auto"; } catch { return "auto"; }
}
export function choose(value) {
  try { localStorage.setItem("rimuru.lang", value); } catch { /* private window */ }
}
export const lang = (() => {
  const picked = chosen();
  if (picked !== "auto" && LANGS[picked]) return picked;
  const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
  return LANGS[browser] ? browser : "en";
})();
const DICT = lang === "fr" ? FR : lang === "ar" ? AR : null;

export function t(text) {
  if (!DICT || !text) return text;
  const key = String(text).replace(/\s+/g, " ").trim();
  if (DICT[key]) return DICT[key];
  for (const [pattern, out] of PATTERNS[lang]) if (pattern.test(key)) return key.replace(pattern, out);
  return text;
}

// Never translated: what people and models wrote, names of things.
const KEEP = ".bubble, .session-title, .mem-text, .ctitle, .msg-text, code, pre, textarea, .fb-path, "
  + ".skill-name, .skill-when, .row-title, .fe-text, .model-name, .preset-models, .box, [data-no-i18n]";
const ATTRS = ["title", "placeholder", "aria-label"];

function textNode(node) {
  const parent = node.parentElement;
  if (!parent || parent.closest(KEEP)) return;
  const raw = node.nodeValue;
  const core = raw.trim();
  if (!core || !/[A-Za-z]/.test(core)) return;
  const done = t(core);
  if (done !== core) node.nodeValue = raw.replace(core, done);
}
// Labels on a typing box are interface text; what is typed in it is not touched.
const KEEP_LABELS = KEEP.replace("textarea, ", "");

function element(el) {
  if (el.closest && el.closest(KEEP_LABELS)) return;
  for (const name of ATTRS) {
    const value = el.getAttribute && el.getAttribute(name);
    if (value) {
      const done = t(value);
      if (done !== value) el.setAttribute(name, done);
    }
  }
}
function walk(root) {
  if (root.nodeType === Node.TEXT_NODE) { textNode(root); return; }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  element(root);
  const all = root.querySelectorAll("*");
  for (const el of all) element(el);
  const texts = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (texts.nextNode()) textNode(texts.currentNode);
}

export function startTranslating() {
  // No slide while the direction flips: the drawer would cross the screen.
  document.documentElement.classList.add("no-motion");
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  requestAnimationFrame(() => requestAnimationFrame(() => document.documentElement.classList.remove("no-motion")));
  if (!DICT) return;
  walk(document.body);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "characterData") textNode(record.target);
      else if (record.type === "attributes") element(record.target);
      else record.addedNodes.forEach(walk);
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true,
                              attributes: true, attributeFilter: ATTRS });
}
