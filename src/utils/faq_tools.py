from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from dotenv import load_dotenv
from langchain.tools import tool
from langchain_community.vectorstores import FAISS

import os
from .prompts import(
    ROUTER_PROMPT_COMPLETO,
    FINANCEIRO_PROMPT_COMPLETO,
    AGENDA_PROMPT_COMPLETO,
    ORQUESTRADOR_PROMPT_COMPLETO,
    FAQ_PROMPT_COMPLETO
)

load_dotenv()
PDF_PATH = os.getenv("PDF_PATH", "FAQ_assessor_v1.1.pdf")

@tool
def faq_retriver(question: str):
    """Use esta ferramenta para buscar informações e responder dúvidas no FAQ da assessoria."""
    #leitura do doc
    google_api_key = os.getenv("GOOGLE_API_KEY")
    model = os.getenv("GEMINI_EMBEDDING")

    loader = PyPDFLoader(PDF_PATH)
    docs = loader.load()

    #pre processamento dos dados, normaliza, remove ruidos e prepara o texto
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=150)
    chunks = splitter.split_documents(docs)

    embeddings = GoogleGenerativeAIEmbeddings(
        model=model,
        google_api_key=google_api_key
    )

    #FAIIS VECTOR DB
    db = FAISS.from_documents(chunks, embeddings)

    #RECUPERACAO DE CONTEXTO
    results = db.similarity_search(question, k=6) 
    
    return results