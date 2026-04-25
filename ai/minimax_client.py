import aiohttp
from session import SharedSession

async def call_minimax_api(messages, api_key):
    url = "https://api.minimax.io/v1/chat/completions"
    session = await SharedSession.get_session()
    async with session.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "MiniMax-Text-01", "messages": messages}
    ) as res:
        data = await res.json()
        return data["choices"][0]["message"]["content"]
