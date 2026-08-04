from fastapi import FastAPI,Depends,HTTPException,Header
from dotenv import load_dotenv
import os
import ollama
from src.main import executar_fluxo_assessor

app = FastAPI()
load_dotenv()
ENDPOINT = 'generate'
x_api_key = os.getenv("GEMINI_API_KEY")

API_KEYS = {x_api_key:  5}

def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    
    credits = API_KEYS.get(x_api_key)
    
    if credits <= 0:
        raise HTTPException(status_code=403, detail="Insufficient credits")
    
    return x_api_key

@app.post(f"/{ENDPOINT}")
def generate(prompt: str, api_key: str = Depends(verify_api_key)):
    API_KEYS[api_key] -= 1
    
    try:
        resposta_final = executar_fluxo_assessor(pergunta_usuario=prompt, session_id="api_session_1")
        
        return {"response": resposta_final}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no fluxo de agentes: {str(e)}")