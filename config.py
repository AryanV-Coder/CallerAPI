import os
from dotenv import load_dotenv

load_dotenv()

# Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Sarvam AI
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

# Groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Server
SERVER_URL = os.getenv("SERVER_URL")  # ngrok or public URL
