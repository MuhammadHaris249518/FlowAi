import os
import sys
import time
import json
from dotenv import load_dotenv
from groq import Groq
from google import genai
from google.genai import types
from deepgram import DeepgramClient

def transcribe(audio_path: str) -> tuple[str, float]:
    """Transcribes an audio file using Groq's whisper-large-v3-turbo model."""
    with open(audio_path, "rb") as file:
        return transcribe_bytes(file.read(), os.path.basename(audio_path))

def transcribe_bytes(audio_bytes: bytes, filename: str) -> tuple[str, float]:
    """Transcribes in-memory audio (e.g. a browser webm upload). The filename extension tells Groq the format."""
    start_time = time.perf_counter()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY missing in environment variables.")

    client = Groq(api_key=api_key)
    transcription = client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model="whisper-large-v3-turbo",
        response_format="json"
    )

    transcript = transcription.text if hasattr(transcription, "text") else str(transcription)
    elapsed = time.perf_counter() - start_time
    print(f"[STT Stage] Groq Whisper completed in {elapsed:.3f}s")
    print(f"Transcript: '{transcript}'\n")
    return transcript, elapsed

def merge_state(current_state: dict, new_extracted: dict) -> dict:
    """Merges new extracted fields into current state without overwriting non-null values with nulls."""
    new_intent = new_extracted.get("intent")
    if new_intent == "book_appointment":
        new_intent = "book"

    if new_intent in ("book", "reschedule", "cancel") and current_state.get("intent") in (None, "unclear"):
        current_state["intent"] = new_intent

    for field in ("service", "issue", "area", "preferred_time", "caller_name", "existing_appointment_ref"):
        val = new_extracted.get(field)
        if val is not None and str(val).strip() != "" and str(val).lower() != "null":
            current_state[field] = val

    return current_state

def extract_intent(transcript: str) -> tuple[dict, float]:
    """Extracts structured intent from transcript using Gemini/Groq LLM."""
    start_time = time.perf_counter()

    system_prompt = (
        "You are an AI receptionist extracting structured conversation details from one caller turn.\n"
        "Return ONLY a JSON object matching this exact schema with no extra text or markdown formatting:\n"
        "{\n"
        '  "intent": "book" | "reschedule" | "cancel" | "question" | "unclear",\n'
        '  "service": null,\n'
        '  "preferred_time": null,\n'
        '  "issue": null,\n'
        '  "area": null,\n'
        '  "caller_name": null,\n'
        '  "existing_appointment_ref": null,\n'
        '  "question": null,\n'
        '  "confidence": 0.0\n'
        "}\n"
        "Rules:\n"
        "- 'book': wants a technician visit, repair, installation, estimate or appointment, OR describes a problem\n"
        "  that needs one (e.g. 'my AC stopped working', 'my sink is leaking'). 'question': asks for\n"
        "  information (prices, hours, areas, warranty, how something works) without asking to book in this turn.\n"
        "- 'service': the kind of job in a few words (e.g. 'AC repair', 'water heater', 'new AC installation').\n"
        "- 'issue': the problem as described (e.g. 'AC blowing warm air', 'kitchen sink leaking').\n"
        "- 'area': the city, neighborhood or ZIP code where the job is (e.g. 'Round Rock', '78704').\n"
        "- 'preferred_time': the day and time exactly as said (e.g. 'Tuesday at 2 PM', 'tomorrow morning').\n"
        "- 'question': the caller's question rephrased as one short sentence, only when they asked one.\n"
        "- ONLY fill in fields the caller explicitly stated in this transcript turn.\n"
        "- Leave unstated fields as null (do NOT invent or guess values)."
    )

    fallback = {
        "intent": "unclear",
        "service": None,
        "preferred_time": None,
        "issue": None,
        "area": None,
        "caller_name": None,
        "existing_appointment_ref": None,
        "question": None,
        "confidence": 0.0
    }

    # llama-3.3-70b-versatile was retired by Groq; the model is configurable so the next retirement is an env change
    groq_model = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-20b")
    model_name = f"groq/{groq_model}"
    try:
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            raise ValueError("GROQ_API_KEY missing in environment variables.")
        groq_client = Groq(api_key=groq_key)
        extra = {"reasoning_effort": "low"} if "gpt-oss" in groq_model else {}
        groq_res = groq_client.chat.completions.create(
            model=groq_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            **extra
        )
        raw_text = groq_res.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM Stage Warning] Groq LLM ({model_name}) failed: {e}. Falling back to Gemini 2.0 Flash...")
        try:
            model_name = "gemini-2.0-flash"
            # Gemini is only the fallback, so its key is only required when Groq fails
            gemini_key = os.getenv("GEMINI_API_KEY")
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY missing in environment variables.")
            client = genai.Client(api_key=gemini_key)
            response = client.models.generate_content(
                model=model_name,
                contents=transcript,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            raw_text = response.text.strip() if response.text else ""
        except Exception as fallback_err:
            print(f"[LLM Stage Error] All LLM providers failed: {fallback_err}. Returning fallback dict.")
            elapsed = time.perf_counter() - start_time
            return fallback, elapsed

    # Clean potential markdown fences
    clean_text = raw_text
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    try:
        extracted_data = json.loads(clean_text)
        if extracted_data.get("intent") == "book_appointment":
            extracted_data["intent"] = "book"
    except Exception as parse_err:
        print(f"[LLM Stage Warning] JSON parse failed: {parse_err}")
        print(f"Raw response was: {raw_text}")
        extracted_data = fallback

    elapsed = time.perf_counter() - start_time
    print(f"[LLM Stage] Intent extraction completed in {elapsed:.3f}s using {model_name}")
    print(f"Extracted Intent Dict: {json.dumps(extracted_data, indent=2)}\n")
    return extracted_data, elapsed

def synthesize_bytes(reply_text: str) -> bytes:
    """Synthesizes speech with Deepgram Aura (aura-asteria-en) and returns the MP3 bytes."""
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY missing in environment variables.")

    client = DeepgramClient(api_key=api_key)

    audio_stream = client.speak.v1.audio.generate(
        text=reply_text,
        model="aura-asteria-en"
    )
    return b"".join(audio_stream)

def synthesize(reply_text: str, out_path: str = "reply.wav") -> float:
    """Synthesizes speech from reply_text using Deepgram Aura (aura-asteria-en)."""
    start_time = time.perf_counter()
    audio = synthesize_bytes(reply_text)

    with open(out_path, "wb") as f:
        f.write(audio)

    elapsed = time.perf_counter() - start_time
    print(f"[TTS Stage] Deepgram Aura synthesis completed in {elapsed:.3f}s")
    print(f"Saved audio to: {out_path}\n")
    return elapsed

def main():
    load_dotenv()
    
    audio_input = "sample_input.wav"
    if len(sys.argv) > 1:
        audio_input = sys.argv[1]
        
    if not os.path.exists(audio_input):
        print(f"Error: Sample audio file '{audio_input}' not found!")
        print("Please place 'sample_input.wav' in the workspace directory or pass a path to a wav/mp3 file.")
        sys.exit(1)
        
    print(f"=== Starting Talkse POC Pipeline for '{audio_input}' ===")
    total_start = time.perf_counter()
    
    # Stage 1: STT
    transcript, stt_time = transcribe(audio_input)
    
    # Stage 2: LLM
    extracted, llm_time = extract_intent(transcript)
    
    # Formulate confirmation reply string based on extracted intent
    if extracted.get("intent") in ("book", "book_appointment"):
        service = extracted.get("service") or "an appointment"
        caller = extracted.get("caller_name") or "there"
        time_pref = extracted.get("preferred_time") or "your requested time"
        reply_text = f"Got it, booking {service} for {caller} at {time_pref}. Does that sound right?"
    else:
        reply_text = "I'm sorry, I couldn't quite capture your booking details. Could you please repeat the service and time you prefer?"
        
    print(f"Confirmation Reply Text: '{reply_text}'")
    
    # Stage 3: TTS
    out_audio = "reply.wav"
    tts_time = synthesize(reply_text, out_audio)
    
    total_time = time.perf_counter() - total_start
    
    # Print Timing Summary Table
    print("\n" + "="*50)
    print("           TALKSE POC LATENCY SUMMARY           ")
    print("="*50)
    print(f" STT Stage (Groq Whisper):     {stt_time:6.3f} s")
    print(f" LLM Stage (Gemini Flash):     {llm_time:6.3f} s")
    print(f" TTS Stage (Deepgram Aura):    {tts_time:6.3f} s")
    print("-" * 50)
    print(f" TOTAL ROUND-TRIP TIME:        {total_time:6.3f} s")
    print("="*50)
    print(f"Generated spoken reply saved to '{out_audio}'.")
    
if __name__ == "__main__":
    main()
