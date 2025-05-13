import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, conint
from readability import Document
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled
from urllib.parse import unquote, urlparse
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class SummaryRequest(BaseModel):
    url: str
    level: conint(ge=1, le=5) = 3

def validate_url(url: str) -> None:
    """Validate URL format and scheme with enhanced parsing"""
    parsed = urlparse(url)
    logger.info(f"Parsing URL: {url}")
    logger.info(f"Scheme: {parsed.scheme}, Netloc: {parsed.netloc}")
    
    if not parsed.scheme:
        raise ValueError("URL must include http:// or https://")
    if not parsed.netloc:
        raise ValueError("Invalid domain format")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only HTTP/HTTPS URLs are supported")

async def fetch_content(url: str) -> str:
    """Fetch and process content from URL"""
    logger.info(f"Starting content fetch for: {url}")
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
            content = BeautifulSoup(doc.summary(), 'html.parser').get_text(separator='\n', strip=True)
            logger.info(f"Fetched {len(content)} characters from webpage")
            return content
            
    except Exception as e:
        logger.error(f"Content extraction failed: {str(e)}")
        raise RuntimeError(f"Could not process content: {str(e)}")

async def get_youtube_transcript(url: str) -> str:
    """Get YouTube transcript with fallback languages"""
    logger.info(f"Processing YouTube URL: {url}")
    try:
        video_id = url.split("v=")[-1].split("&")[0] if "v=" in url else url.split("/")[-1]
        languages = ['en', 'en-US', 'en-GB', 'en-IN']
        
        for lang in languages:
            try:
                transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=[lang])
                content = " ".join(entry['text'] for entry in transcript)
                logger.info(f"Found transcript in {lang}: {len(content)} characters")
                return content
            except NoTranscriptFound:
                continue

        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        available_langs = [t.language_code for t in transcripts]
        error_msg = f"No English transcript found. Available languages: {available_langs}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
        
    except TranscriptsDisabled:
        error_msg = "Subtitles disabled for this video"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        logger.error(f"YouTube error: {str(e)}")
        raise RuntimeError(f"Could not process YouTube video: {str(e)}")

async def generate_summary(text: str, level: int) -> str:
    """Generate summary using Ollama"""
    logger.info(f"Generating summary (level {level}) for {len(text)} characters")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": "gemma3",
                    "prompt": f"Create {3 + level} bullet points summarizing this, no header or footer:\n{text[:3000]}",
                    "stream": False,
                    "options": {"temperature": 0.3}
                }
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
            
    except httpx.HTTPStatusError as e:
        error_msg = f"AI service error ({e.response.status_code}): {e.response.text}"
        logger.error(error_msg)
        raise RuntimeError("AI service unavailable")
    except Exception as e:
        logger.error(f"Summarization failed: {str(e)}")
        raise RuntimeError("Could not generate summary")

@app.post("/summarize")
async def summarize(request: SummaryRequest):
    try:
        # Decode and clean URL first
        decoded_url = unquote(request.url)
        logger.info(f"Received request for URL: {decoded_url}")
        
        validate_url(decoded_url)
        clean_url = decoded_url.rstrip('/')
        
        content = await fetch_content(clean_url)
        summary = await generate_summary(content, request.level)
        return {"summary": summary}
    
    except ValueError as e:
        logger.warning(f"Validation error: {str(e)}")
        raise HTTPException(400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Processing error: {str(e)}")
        raise HTTPException(422, detail=str(e))
    except Exception as e:
        logger.critical(f"Unexpected error: {str(e)}")
        raise HTTPException(500, detail="Internal server error")

# Serve static files
app.mount("/web_ui", StaticFiles(directory="web_ui"), name="web_ui")

@app.get("/")
async def read_root():
    return FileResponse("web_ui/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
