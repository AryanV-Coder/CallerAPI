# Groq Services

Handles interactions with the Groq API to serve as the core conversational logic engine for the application.

## Overview
This service maintains the conversation logic and contextual history, generating dynamic, near-instant query responses for the real-time voice pipeline.

## Implementation Details
- **`groq_llm.py`**:
  - Exposes the `chat(user_input: str, chat_history: list[dict]) -> str` method.
  - Automatically initializes context for new interactions by prepending a pre-defined system prompt to the `chat_history`.
  - Maintains conversation history specific to each individual `CallSid` to ensure accurate and contextual multi-turn conversations.
  - Currently hardcoded to leverage the `llama-3.3-70b-versatile` model to balance intelligence with response velocity.