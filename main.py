from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os, re, datetime, tempfile, fitz, docx
from ppt_generator import create_ppt
from doc_generator import create_doc
from google import genai

# ---------------- CONFIG ----------------
API_KEY = os.getenv("GEMINI_API_KEY", "your-api-key-here")  # set in env or replace here
MODEL_NAME = "gemini-2.0-flash"  # or gemini-1.5-pro, etc.

client = genai.Client(api_key=API_KEY)

# ---------------- FASTAPI ----------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- MODELS ----------------
class ChatRequest(BaseModel):
    message: str

class ChatDocRequest(BaseModel):
    message: str
    document_text: str

class Slide(BaseModel):
    title: str
    description: str

class Section(BaseModel):
    title: str
    description: str

class Outline(BaseModel):
    title: str
    slides: List[Slide]

class DocOutline(BaseModel):
    title: str
    sections: List[Section]

class EditRequest(BaseModel):
    outline: Outline
    feedback: str

class EditDocRequest(BaseModel):
    outline: DocOutline
    feedback: str

class GeneratePPTRequest(BaseModel):
    description: str = ""
    outline: Optional[Outline] = None

class GenerateDocRequest(BaseModel):
    description: str = ""
    outline: Optional[DocOutline] = None


# ---------------- HELPERS ----------------
def extract_slide_count(description: str, default: int = 5) -> int:
    m = re.search(r"(\d+)\s*(slides?|sections?|pages?)", description, re.IGNORECASE)
    if m:
        total = int(m.group(1))
        return max(1, total - 1)
    return default - 1

def generate_title(summary: str) -> str:
    prompt = f"""Read the following summary and create a short, clear, presentation-style title.
- Keep it under 12 words
- Do not include birth dates, long sentences, or excessive details
- Just give a clean title, like a presentation heading

Summary:
{summary}
"""
    return call_gemini(prompt).strip()

def infer_title(description: str) -> str:
    description = description.strip()
    m = re.search(
        r"(?:ppt|presentation|doc|document|report)\s+on\s+([A-Za-z0-9\s.&'\-]{2,80})",
        description,
        re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+in\s+\d+\s+(slides?|sections?|pages?)$", "", m.group(1).strip(), flags=re.IGNORECASE)
    return description.title() or "Untitled"

def parse_points(points_text: str):
    points = []
    current_title, current_content = None, []
    lines = [re.sub(r"[#*>`]", "", ln).rstrip() for ln in points_text.splitlines()]

    for line in lines:
        if not line or "Would you like" in line:
            continue

        # Detect Slide or Section header
        m = re.match(r"^\s*(Slide|Section)\s*(\d+)\s*:\s*(.+)$", line, re.IGNORECASE)
        if m:
            if current_title:
                points.append({"title": current_title, "description": "\n".join(current_content)})
            current_title, current_content = m.group(3).strip(), []
            continue

        # Main points → start with "-"
        if line.strip().startswith("-"):
            text = line.lstrip("-").strip()
            if text:
                current_content.append(f"• {text}")   # dot for main points

        # Sub-points → start with "•", "*", or indentation
        elif line.strip().startswith(("•", "*")) or line.startswith("  "):
            text = line.lstrip("•*").strip()
            if text:
                current_content.append(f"- {text}")   # dash for sub-points

        else:
            # Treat plain lines as normal text
            if line.strip():
                current_content.append(line.strip())

    if current_title:
        points.append({"title": current_title, "description": "\n".join(current_content)})

    return points



def extract_text(path: str, filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        text_parts: List[str] = []
        doc = fitz.open(path)
        try:
            for page in doc:
                text_parts.append(page.get_text("text"))
        finally:
            doc.close()
        return "\n".join(text_parts)

    if name.endswith(".docx"):
        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)

    if name.endswith(".txt"):
        for enc in ("utf-8", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    return ""

def split_text(text: str, chunk_size: int = 8000, overlap: int = 300) -> List[str]:
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks

# ---------------- Gemini Calls ----------------
def call_gemini(prompt: str) -> str:
    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt
    )
    return resp.text.strip()

def generate_outline_from_desc(description: str, num_items: int, mode: str = "ppt"):
    if mode == "ppt":
        prompt = f"""Create a PowerPoint outline on: {description}.
Generate exactly {num_items} content slides (⚠️ excluding the title slide).
Do NOT include a title slide — I will handle it separately.
Start from Slide 1 as the first *content slide*.
Format strictly like this:
Slide 1: <Title>
- Bullet
- Bullet
- Bullet
"""
    else:  # DOC mode
        prompt = f"""Create a detailed Document outline on: {description}.
Generate exactly {num_items} sections (treat each section as roughly one page).
Each section should have:
- A section title
- 2–3 descriptive paragraphs (5–7 sentences each) of full prose, not bullets.
Do NOT use bullet points.
Format strictly like this:
Section 1: <Title>
<Paragraph 1>
<Paragraph 2>
<Paragraph 3>
"""

    points_text = call_gemini(prompt)
    return parse_points(points_text)

def summarize_long_text(full_text: str) -> str:
    chunks = split_text(full_text)
    if len(chunks) <= 1:
        return call_gemini(f"Summarize the following text in detail:\n\n{full_text}")
    partial_summaries = []
    for idx, ch in enumerate(chunks, start=1):
        mapped = call_gemini(f"Summarize this part of a longer document:\n\n{ch}")
        partial_summaries.append(f"Chunk {idx}:\n{mapped.strip()}")
    combined = "\n\n".join(partial_summaries)
    return call_gemini(f"Combine these summaries into one clean, well-structured summary:\n\n{combined}")

def sanitize_filename(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)

def clean_title(title: str) -> str:
    return re.sub(r"\s*\(.*?\)", "", title).strip()

# ---------------- ROUTES ----------------
@app.post("/chat")
def chat(req: ChatRequest):
    if "ppt" in req.message.lower() or "presentation" in req.message.lower():
        return {"response": "📑 I can help you create a PPT! Tell me more details (topic, slides, etc.)."}
    if "doc" in req.message.lower() or "document" in req.message.lower():
        return {"response": "📄 I can help you create a Document! Tell me more details (topic, pages, etc.)."}
    reply = call_gemini(req.message)
    return {"response": reply}

# ---------------- UPLOAD ----------------
@app.post("/upload/")
async def upload(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        text = extract_text(tmp_path, file.filename)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Unsupported, empty, or unreadable file content.")

    try:
        summary = summarize_long_text(text)
        title = generate_title(summary) or os.path.splitext(file.filename)[0]  # ✅ Generate clean title here
        return {
            "filename": file.filename,
            "chars": len(text),
            "chunks": len(split_text(text)),
            "title": title,
            "summary": summary,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Summarization failed: {e}")

# ---------------- PPT ENDPOINTS ----------------
@app.post("/generate-ppt-outline")
def generate_ppt_outline(request: GeneratePPTRequest):
    title = generate_title(request.description)  # ✅ Generate clean title for PPT
    num_content_slides = extract_slide_count(request.description, default=5)
    points = generate_outline_from_desc(request.description, num_content_slides, mode="ppt")
    return {"title": title, "slides": points}

@app.post("/edit-ppt-outline")
def edit_ppt_outline(request: EditRequest):
    outline_text = ""
    for idx, slide in enumerate(request.outline.slides, start=1):
        outline_text += f"Slide {idx}: {slide.title}\n"
        for bullet in slide.description.split("\n"):
            outline_text += f"- {bullet}\n"
    prompt = f"""You are editing a PowerPoint outline.
Here is the outline:
{outline_text}
Feedback: "{request.feedback}"
Update the outline according to the feedback and return in the same format.
"""
    points_text = call_gemini(prompt)
    points = parse_points(points_text)
    return {"title": request.outline.title, "slides": points}

@app.post("/generate-ppt")
def generate_ppt(req: GeneratePPTRequest):
    if req.outline:
        title = clean_title(req.outline.title) or "Presentation"
        points = [{"title": clean_title(s.title), "description": s.description} for s in req.outline.slides]
    else:
        title = clean_title(generate_title(req.description))
        num_content_slides = extract_slide_count(req.description, default=5)
        points = generate_outline_from_desc(req.description, num_content_slides, mode="ppt")

    
    output_dir = os.path.join(os.path.dirname(__file__), "generated_files")
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"{sanitize_filename(title)}.pptx")

    create_ppt(title, points, filename=filename)

    return FileResponse(
        filename,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=os.path.basename(filename)
    )


@app.post("/generate-doc-outline")
def generate_doc_outline(request: GenerateDocRequest):
    title = generate_title(request.description)  # ✅ Generate clean title for DOC
    num_sections = extract_slide_count(request.description, default=5)
    points = generate_outline_from_desc(request.description, num_sections, mode="doc")
    return {"title": title, "sections": points}

@app.post("/edit-doc-outline")
def edit_doc_outline(request: EditDocRequest):
    outline_text = ""
    for idx, section in enumerate(request.outline.sections, start=1):
        outline_text += f"Section {idx}: {section.title}\n"
        for paragraph in section.description.split("\n"):
            outline_text += f"{paragraph}\n"
    prompt = f"""You are editing a Document outline.
Here is the outline:
{outline_text}
Feedback: "{request.feedback}"
Update the outline according to the feedback and return in the same format.
"""
    points_text = call_gemini(prompt)
    points = parse_points(points_text)
    return {"title": request.outline.title, "sections": points}

@app.post("/generate-doc")
def generate_doc(req: GenerateDocRequest):
    if req.outline:
        title = clean_title(req.outline.title) or "Document"
        points = [{"title": clean_title(s.title), "description": s.description} for s in req.outline.sections]
    else:
        title = clean_title(generate_title(req.description))
        num_sections = extract_slide_count(req.description, default=5)
        points = generate_outline_from_desc(req.description, num_sections, mode="doc")


    output_dir = os.path.join(os.path.dirname(__file__), "generated_files")
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.join(output_dir, f"{sanitize_filename(title)}.docx")

    create_doc(title, points, filename=filename)

    return FileResponse(
        filename,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(filename)
    )


@app.post("/chat-doc")
def chat_with_doc(req: ChatDocRequest):
    prompt = f"""
    You are an assistant answering based only on the provided document.
    Document:
    {req.document_text}

    Question:
    {req.message}

    Answer clearly and concisely using only the document content.
    """
    try:
        reply = call_gemini(prompt)
        return {"response": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat-with-doc failed: {e}")

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}
