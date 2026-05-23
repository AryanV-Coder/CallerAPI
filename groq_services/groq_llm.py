from groq import Groq
import json
import config


client = Groq(api_key=config.GROQ_API_KEY)


def chat(user_message: str, chat_history: list[dict] | None = None) -> str:
    """
    Send a message to Groq LLM and get a response (non-streaming).

    Args:
        user_message: The user's transcribed speech text.
        chat_history: List of previous messages for context.
                      Must already contain a system prompt.

    Returns:
        The AI's response text.
    """
    if chat_history is None:
        chat_history = []

    chat_history.append({"role": "user", "content": user_message})

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=chat_history,
            stream=False,
            temperature=0.7,
            max_tokens=200,
        )

        ai_reply = response.choices[0].message.content
        chat_history.append({"role": "assistant", "content": ai_reply})

        print(f"✅ [Groq LLM] Response: {ai_reply}")
        return ai_reply

    except Exception as e:
        print(f"❌ Groq LLM Error: {e}")
        return "Sorry, I am having trouble processing your request right now."


def summarize_call(transcript: list[dict]) -> dict:
    """
    Generate a summary and sentiment analysis from a completed call transcript.

    Args:
        transcript: The full conversation history (list of role/content dicts).

    Returns:
        Dict with keys: summary, user_sentiment, sentiment_detail
    """
    # Build a clean transcript string (skip system messages)
    conversation_lines = []
    for msg in transcript:
        if msg["role"] == "system":
            continue
        role_label = "Bot" if msg["role"] == "assistant" else "User"
        conversation_lines.append(f"{role_label}: {msg['content']}")

    conversation_text = "\n".join(conversation_lines)

    summarize_prompt = [
        {
            "role": "system",
            "content": """You are a call analysis assistant. Given a phone call transcript between a Bot and a User, produce a JSON object with exactly these three fields:

1. "summary": A concise 2-4 sentence summary of what happened in the call — key topics discussed, decisions made, and outcomes.
2. "user_sentiment": One of: "positive", "neutral", "negative", or "mixed".
3. "sentiment_detail": A 1-2 sentence explanation of how the user felt during the call and why.

Respond with ONLY the JSON object, no markdown, no code fences.""",
        },
        {
            "role": "user",
            "content": f"Here is the call transcript:\n\n{conversation_text}",
        },
    ]

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=summarize_prompt,
            stream=False,
            temperature=0.3,
            max_tokens=400,
        )

        raw = response.choices[0].message.content.strip()

        # Try to parse JSON from the response
        result = json.loads(raw)
        print(f"✅ [Groq] Call summary generated")
        return {
            "summary": result.get("summary", "No summary available."),
            "user_sentiment": result.get("user_sentiment", "neutral"),
            "sentiment_detail": result.get("sentiment_detail", ""),
        }

    except json.JSONDecodeError:
        print(f"⚠ [Groq] Could not parse summary JSON, using raw text")
        return {
            "summary": raw,
            "user_sentiment": "neutral",
            "sentiment_detail": "Could not analyze sentiment.",
        }

    except Exception as e:
        print(f"❌ Groq summarization error: {e}")
        return {
            "summary": "Summary generation failed.",
            "user_sentiment": "neutral",
            "sentiment_detail": "Sentiment analysis unavailable.",
        }