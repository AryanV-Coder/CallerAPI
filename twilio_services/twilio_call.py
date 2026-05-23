from twilio.rest import Client
import config


client = Client(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def make_call(phone_number: str) -> str:
    """
    Initiate an outbound call to a phone number.

    Args:
        phone_number: Full E.164 phone number (e.g. +919876543210).

    Returns:
        The Twilio Call SID.
    """
    call = client.calls.create(
        to=phone_number,
        from_=config.TWILIO_PHONE_NUMBER,
        url=f"{config.SERVER_URL}/voice",
        status_callback=f"{config.SERVER_URL}/call-status",
        status_callback_event=["completed"],
        status_callback_method="POST",
    )
    print(f"✅ [Twilio] Call initiated to {phone_number} | SID: {call.sid}")
    return call.sid