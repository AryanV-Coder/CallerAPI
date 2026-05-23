# Twilio Services

Responsible for initiating the outbound Twilio call flow and bootstrapping the core media streaming context.

## Overview
This service provides the fundamental entry point to the application process loop by authenticating and opening the outbound line using Twilio's Programmable Voice functionality.

## Implementation Details
- **`twilio_call.py`**:
  - Exposes the core `make_call(to_number: str) -> str` method.
  - Generates the initial POST execution pointing the initiated call specifically back to the `<SERVER_URL>/voice` webhook endpoint.
  - Uses the Twilio Python SDK.
  - Securely maps initialization parameters to the keys configured within `.env` logic.
  - Returns the persistent Twilio `CallSid` necessary for tracing session history tracking inside `app.py`.