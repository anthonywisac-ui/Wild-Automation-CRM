from .groq_client import groq_chat
from .gemini_client import gemini_chat
from .memory import build_messages
from .logger import log_ai

async def get_ai_response(sender, message, session=None):
    messages = build_messages(message, session)

    try:
        response = await groq_chat(messages)
        log_ai("GROQ SUCCESS", response)
        return response

    except Exception as e:
        log_ai("GROQ FAIL", str(e))

        try:
            response = await gemini_chat(message)
            log_ai("GEMINI SUCCESS", response)
            return response

        except Exception as e2:
            log_ai("GEMINI FAIL", str(e2))
            return "AI system unavailable."