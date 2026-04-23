def build_messages(user_message, session=None):
    messages = [
        {"role": "system", "content": "You are a smart AI assistant."},
        {"role": "user", "content": user_message}
    ]

    if session and "conversation" in session:
        messages = session["conversation"][-5:] + messages

    return messages