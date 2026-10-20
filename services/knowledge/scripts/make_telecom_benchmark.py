"""Write data/telecom/telco_001/benchmark/retrieval.{dev,test}.jsonl from the telecom FAQ plus off-topic questions.

Each FAQ item has the same question in English, Egyptian Arabic and Arabizi and cites the policy section it is
answered by; that citation is the gold passage. The split is by FAQ item (alternating), so the three wordings of
one question always land in the same split and the held-out test is not seen in another language. The FAQ is
not part of the corpus (every answer restates a policy section that is), so the questions do not match
themselves. The off-topic questions are about things the policies do not cover (checked against the text).

Run from services/knowledge/:  python scripts/make_telecom_benchmark.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from knowledge_service.config import settings  # noqa: E402
from knowledge_service.search.index import build_passages  # noqa: E402

TENANT = "telco_001"
STYLES = (("en", "question_en"), ("ar", "question_ar"), ("arabizi", "question_arabizi"))
OFF_TOPIC = {
    "en": ["Do you sell smart TVs in your stores?", "How can I apply for a job at Masr Connect?",
           "Can I pay my electricity bill through your app?", "Do you have a 5G network in Cairo?",
           "What is Masr Connect's share price today?", "Do you offer satellite TV packages?",
           "How do I become an authorised dealer?", "Does my phone plan include travel insurance?"],
    "ar": ["عندكم شاشات تلفزيون في الفروع؟", "إزاي أقدم على وظيفة في مصر كونكت؟",
           "ينفع أدفع فاتورة الكهربا من التطبيق بتاعكم؟", "فيه شبكة 5G في القاهرة؟",
           "سعر سهم الشركة النهارده كام؟", "عندكم باقات قنوات دش؟",
           "إزاي أبقى وكيل معتمد ليكم؟", "الباقة فيها تأمين سفر؟"],
    "arabizi": ["3andoko shashat TV fel fro3?", "ezay a2adem 3ala wazifa 3andoko?",
                "momken adfa3 fatoret el kahraba men el app?", "fe 5G fel Cairo?",
                "se3r sahm el shereka kam enaharda?", "3andoko ba2at 2anawat dish?",
                "ezay ab2a wakeel mo3tamad?", "el ba2a fiha ta2meen safar?"],
}


def main() -> None:
    tenant_dir = settings.tenant_dir(TENANT)
    faq = json.loads((tenant_dir / "faq.json").read_text(encoding="utf-8"))
    current = {p["citation"] for p in build_passages(TENANT)[0] if p["current"]}
    out = {"dev": [], "test": []}
    for i, item in enumerate(faq):
        if item["citation"] not in current:
            sys.exit(f"{item['faq_id']} cites {item['citation']}, which is not a current passage")
        split = "dev" if i % 2 == 0 else "test"
        for style, field in STYLES:
            out[split].append({"id": f"{item['faq_id']}-{style}", "style": style, "question": item[field],
                               "expected": [item["citation"]]})
    for style, questions in OFF_TOPIC.items():
        for j, q in enumerate(questions):
            split = "dev" if j % 2 == 0 else "test"
            out[split].append({"id": f"OFF-{style}-{j + 1:02d}", "style": style, "question": q, "expected": []})
    folder = tenant_dir / "benchmark"
    folder.mkdir(exist_ok=True)
    for split, rows in out.items():
        (folder / f"retrieval.{split}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
        print(f"{split}: {len(rows)} questions ({sum(not r['expected'] for r in rows)} off-topic)")


if __name__ == "__main__":
    main()
