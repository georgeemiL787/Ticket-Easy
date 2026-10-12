/* A small translator for the chat and the inbox pages (plain JavaScript, no build step).
   Texts are written in English in the page and looked up here by their English words; a missing translation shows the English.
   The language is ?lang=en|ar, else the one chosen last (localStorage), else English. Arabic sets dir="rtl" on the page.
   Elements marked data-i18n="text", data-i18n-placeholder, data-i18n-aria or data-i18n-title are translated by TE.apply(). */
(function (root) {
  "use strict";

  var AR = {
    // chat
    "Chat": "المحادثة",
    "Type a message": "اكتب رسالة",
    "Message": "الرسالة",
    "Send": "إرسال",
    "developer": "المطوّر",
    "Show how the agent decided": "اعرض كيف قرّر المساعد",
    "Customer care": "خدمة العملاء",
    "This business is not available.": "هذا النشاط غير متاح.",
    "This business is not live yet.": "هذا النشاط لم يبدأ العمل بعد.",
    "Could not reach the server. Please try again.": "تعذّر الوصول إلى الخادم. حاول مرة أخرى.",
    "Something went wrong ({status}). Please try again.": "حدث خطأ ({status}). حاول مرة أخرى.",
    "waiting for: ": "في انتظار: ",
    "trace": "التتبّع",
    "Language": "اللغة",
    // inbox
    "Inbox": "صندوق الوارد",
    "Your name": "اسمك",
    "Business": "النشاط",
    "Sign out": "تسجيل الخروج",
    "Sign in": "تسجيل الدخول",
    "Email": "البريد الإلكتروني",
    "Password": "كلمة المرور",
    "Cases": "الحالات",
    "Case": "الحالة",
    "Status": "الحالة",
    "Priority": "الأولوية",
    "Reason": "السبب",
    "Lang": "اللغة",
    "Age": "العمر",
    "Summary": "الملخص",
    "new (just arrived)": "جديدة (وصلت للتو)",
    "open + claimed": "مفتوحة + مستلمة",
    "any status": "أي حالة",
    "any priority": "أي أولوية",
    "any reason": "أي سبب",
    "open": "مفتوحة",
    "claimed": "مستلمة",
    "resolved": "محلولة",
    "returned to agent": "أُعيدت للمساعد",
    "urgent": "عاجلة",
    "high": "مرتفعة",
    "normal": "عادية",
    "low": "منخفضة",
    "mine": "الخاصة بي",
    "No cases match.": "لا توجد حالات مطابقة.",
    "Load more": "عرض المزيد",
    "Write to the customer": "اكتب للعميل",
    "Reply": "الرد",
    "Send reply": "إرسال الرد",
    "e.g. Sara": "مثال: سارة",
    "updated": "تم التحديث",
    "Type your name first.": "اكتب اسمك أولاً.",
    "Cannot reach the server. Check your connection.": "تعذّر الوصول إلى الخادم. تحقق من الاتصال.",
    "Something went wrong (HTTP {status}).": "حدث خطأ (HTTP {status}).",
    "Claim": "استلام",
    "Release": "تحرير",
    "Approve": "موافقة",
    "Reject": "رفض",
    "Resolve": "إغلاق",
    "Give back to assistant": "إعادة إلى المساعد",
    "note (optional)": "ملاحظة (اختيارية)",
    "Decision note": "ملاحظة القرار",
    "AI-written summary": "ملخص كتبه الذكاء الاصطناعي",
    "Suggested next step: ": "الخطوة التالية المقترحة: ",
    "Why: ": "السبب: ",
    "Already tried (do not ask the customer again)": "جُرّب بالفعل (لا تسأل العميل مرة أخرى)",
    "Waiting for approval": "في انتظار الموافقة",
    "Customer": "العميل",
    "Details the customer gave": "ما ذكره العميل",
    "Order facts": "بيانات الطلب",
    "Safety flags": "تنبيهات السلامة",
    "Policy evidence": "سند السياسة",
    "Rule checker answers": "إجابات فاحص القواعد",
    "Actions tried": "الإجراءات المجرَّبة",
    "Failures": "الأعطال",
    "Similar past tickets": "تذاكر سابقة مشابهة",
    "Conversation": "المحادثة",
    "History": "السجل",
    "no policy passage was used": "لم تُستخدم أي فقرة من السياسة",
    "none": "لا شيء",
    "AI suggestion, not approved: ": "اقتراح من الذكاء الاصطناعي، غير معتمد: ",
    "Already tried": "جُرّب بالفعل",
    "Back to list": "العودة إلى القائمة"
  };

  function readLang() {
    var wanted = null;
    try { wanted = new URLSearchParams(root.location.search).get("lang"); } catch (e) { /* no window */ }
    if (wanted !== "en" && wanted !== "ar") {
      try { wanted = root.localStorage.getItem("te.lang"); } catch (e) { wanted = null; }
    }
    return wanted === "ar" ? "ar" : "en";
  }

  var lang = readLang();

  function tr(text, vars) {
    var found = lang === "ar" && Object.prototype.hasOwnProperty.call(AR, text) ? AR[text] : text;
    if (!vars) { return found; }
    return found.replace(/\{(\w+)\}/g, function (whole, name) { return name in vars ? String(vars[name]) : whole; });
  }

  function apply(scope) {
    var base = scope || root.document;
    [["data-i18n", null], ["data-i18n-placeholder", "placeholder"], ["data-i18n-aria", "aria-label"], ["data-i18n-title", "title"]].forEach(function (pair) {
      base.querySelectorAll("[" + pair[0] + "]").forEach(function (node) {
        var text = tr(node.getAttribute(pair[0]));
        if (pair[1]) { node.setAttribute(pair[1], text); } else { node.textContent = text; }
      });
    });
  }

  function setLang(next) {
    try { root.localStorage.setItem("te.lang", next === "ar" ? "ar" : "en"); } catch (e) { /* private mode */ }
    var url = new URL(root.location.href);
    url.searchParams.set("lang", next === "ar" ? "ar" : "en");
    root.location.href = url.toString();
  }

  if (root.document) {
    root.document.documentElement.lang = lang;
    root.document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () { apply(); });
    } else {
      apply();
    }
  }

  root.TE = { lang: lang, tr: tr, apply: apply, setLang: setLang, dictionary: AR };
})(typeof window !== "undefined" ? window : globalThis);
