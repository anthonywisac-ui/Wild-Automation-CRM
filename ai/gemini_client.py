import google.generativeai as genai
from config import GEMINI_API_KEY

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-pro")

async def gemini_chat(prompt):
    response = model.generate_content(prompt)
    return response.text