"""Answers caller questions from known FAQs: the data file's FAQ plus staff-saved answers in talkse.faqs.

The LLM matches the caller's question to an FAQ by meaning (callers rarely use the
FAQ's words); a keyword match is the fallback if the LLM call fails. No match means
a human has to answer (the handoff flow).
"""

import json
import os

from groq import Groq

import clinic_config as config
import db


def _all_faqs() -> list[dict]:
    """Data-file FAQs plus staff-saved FAQs, as {id, question, answer}."""
    faqs = [{"id": f["id"], "question": f["question"], "answer": f["answer"]} for f in config.FAQ]
    try:
        for row in db.get_faqs():
            faqs.append({"id": f"staff_{row['id']}", "question": row["question"], "answer": row["answer"]})
    except Exception as e:
        print(f"[FAQ] Could not load staff FAQs: {e}")
    return faqs


def _llm_match(question: str, faqs: list[dict]) -> str | None:
    """Returns the id of the FAQ that answers the question, or None."""
    catalog = "\n".join(f"{f['id']}: Q: {f['question']} A: {f['answer']}" for f in faqs)
    system_prompt = (
        "You match a caller's question to a list of FAQs (question and answer) for a business.\n"
        'Return ONLY JSON: {"match": "<faq id>"} if one FAQ\'s ANSWER directly answers the caller\'s question, '
        'otherwise {"match": null}.\n'
        "Be strict: if the caller asks about something specific (a brand, product, place, price or service) "
        "that the answer does not explicitly mention, return null. A related topic is not a match. "
        "When unsure, return null; a person will answer instead.\n\n"
        f"FAQs:\n{catalog}"
    )
    model = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-20b")
    extra = {"reasoning_effort": "low"} if "gpt-oss" in model else {}
    res = Groq(api_key=os.getenv("GROQ_API_KEY")).chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": question}],
        response_format={"type": "json_object"},
        temperature=0,
        **extra,
    )
    match = json.loads(res.choices[0].message.content).get("match")
    return match if match and any(f["id"] == match for f in faqs) else None


def answer_question(question: str) -> str | None:
    """Returns the FAQ answer for the caller's question, or None if no known FAQ answers it."""
    if not question or not question.strip():
        return None
    faqs = _all_faqs()
    try:
        match_id = _llm_match(question, faqs)
        if match_id:
            return next(f["answer"] for f in faqs if f["id"] == match_id)
        return None
    except Exception as e:
        print(f"[FAQ] LLM match failed ({e}); using keyword match")
        hit = config.find_faq(question)
        return hit["answer"] if hit else None
