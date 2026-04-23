import aiohttp
from config import GROQ_API_KEY

async def groq_chat(messages):
    url = "https://api.groq.com/openai/v1/chat/completions"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama3-8b-8192",
                "messages": messages
            }
        ) as res:
            data = await res.json()

            if "choices" not in data:
                raise Exception(data)

            return data["choices"][0]["message"]["content"]