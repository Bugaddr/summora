import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, conint
from readability import Document
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
from urllib.parse import unquote, urlparse

app = FastAPI()

class SummaryRequest(BaseModel):
    url: str
    level: conint(ge=1, le=5) = 3

def validate_url(url: str) -> None:
    """Validate URL format and scheme"""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL format")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Unsupported URL scheme")

async def fetch_content(url: str) -> str:
    """Fetch and process content from URL"""
    try:
        if "youtube.com" in url or "youtu.be" in url:
            return await get_youtube_transcript(url)
            
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                url,
                headers={'User-Agent': 'Mozilla/5.0'},
                follow_redirects=True
            )
            response.raise_for_status()
            doc = Document(response.text)
            return BeautifulSoup(doc.summary(), 'html.parser').get_text(separator='\n', strip=True)
            
    except Exception as e:
        raise RuntimeError(f"Content extraction failed: {str(e)}")

async def get_youtube_transcript(url: str) -> str:
    """Get YouTube transcript with fallback languages"""
    try:
        video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]
        languages = ['en', 'en-US', 'en-GB', 'en-IN']
        
        for lang in languages:
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                return " ".join(entry['text'] for entry in transcript)
            except NoTranscriptFound:
                continue

        # If no transcripts found in fallback languages
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        available_langs = [t.language_code for t in transcripts]
        raise RuntimeError(f"No English transcript found. Available languages: {available_langs}")
        
    except TranscriptsDisabled:
        raise RuntimeError("Subtitles are disabled for this video")
    except Exception as e:
        raise RuntimeError(f"YouTube error: {str(e)}")

async def generate_summary(text: str, level: int) -> str:
    """Generate summary using Ollama"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "gemma3",
                    "prompt": f"Create {3 + level} bullet points summarizing this:\n{text[:3000]}",
                    "stream": False,
                    "options": {"temperature": 0.3}
                }
            )
            response.raise_for_status()
            return response.json().get("response", "")
            
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Ollama API error: {e.response.text}")
    except Exception as e:
        raise RuntimeError(f"Summarization failed: {str(e)}")

@app.post("/summarize")
async def summarize(request: SummaryRequest):
    try:
        validate_url(request.url)
        clean_url = unquote(request.url).rstrip('/')
        content = await fetch_content(clean_url)
        summary = await generate_summary(content, request.level)
        return {"summary": summary}
    
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        raise HTTPException(500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)