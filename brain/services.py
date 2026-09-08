import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any

# Add study_lib to path
sys.path.append(str(Path(__file__).resolve().parents[2] / "study" / "study_lib"))

from study_lib.parse import parse_source
from study_lib.embed import StubEmbedder
from study_lib.store import IndexStore, JsonDurableSink
from study_lib.retrieve import retrieve
from study_lib.factory import generate_one
from study_lib.protocol import load_schema, validate
from study_lib.registry import Library, JsonFileStore

app = FastAPI(title="Local AI Service Suite")

# ---------- Pydantic models ----------
class ParseRequest(BaseModel):
    path: str = Field(..., description="Absolute path to source file")
    profile: str = Field(..., description="Chunking profile")
    book_id: str = Field(..., description="Book identifier")

class ParseResponse(BaseModel):
    book_id: str
    markdown: str
    metadata: Dict[str, Any] | None = None

class EmbedRequest(BaseModel):
    texts: List[str]

class EmbedResponse(BaseModel):
    vectors: List[List[float]]

class RetrieveRequest(BaseModel):
    vector: List[float]
    k: int = 5

class RetrieveResponse(BaseModel):
    hits: List[Dict[str, Any]]

class GenerateRequest(BaseModel):
    topic: Dict[str, Any]
    context: List[Dict[str, Any]]
    schema: Dict[str, Any]
    json_object: bool = True

class GenerateResponse(BaseModel):
    __root__: Dict[str, Any]

class ErrorResponse(BaseModel):
    error: str
    code: int | None = None
    details: Dict[str, Any] | None = None

# Global in‑memory store for demo purposes
store_path = Path(__file__).resolve().parents[2] / "brain" / "store"
store_path.mkdir(parents=True, exist_ok=True)
index_store = IndexStore(JsonDurableSink(store_path))

# Global embedder (Stub for demo)
embedder = StubEmbedder()

# Global library (JSON file for demo)
registry_path = Path(__file__).resolve().parents[2] / "brain" / "registry.json"
library = Library(JsonFileStore(registry_path))

@app.post("/parse", response_model=ParseResponse, responses={default: {"model": ErrorResponse}})
async def parse_endpoint(req: ParseRequest):
    try:
        parsed = parse_source(req.path, profile=req.profile, book_id=req.book_id)
        return ParseResponse(book_id=parsed["book_id"], markdown=parsed["markdown"], metadata=parsed.get("metadata"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/embed", response_model=EmbedResponse, responses={default: {"model": ErrorResponse}})
async def embed_endpoint(req: EmbedRequest):
    try:
        vectors = embedder.embed_texts(req.texts)
        return EmbedResponse(vectors=vectors)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/retrieve", response_model=RetrieveResponse, responses={default: {"model": ErrorResponse}})
async def retrieve_endpoint(req: RetrieveRequest):
    try:
        hits = retrieve(req.vector, index_store, k=req.k)
        # Convert SearchHit objects to dicts
        hits_dicts = [h._asdict() if hasattr(h, "_asdict") else h.dict() for h in hits]
        return RetrieveResponse(hits=hits_dicts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/generate", response_model=GenerateResponse, responses={default: {"model": ErrorResponse}})
async def generate_endpoint(req: GenerateRequest):
    try:
        # Load schema for validation
        schema = load_schema()
        # Use dummy LLM client that returns static payload
        class DummyLLM:
            def complete(self, *, system: str, user: str, max_tokens: int = 1000, json_object: bool = True):
                # Minimal payload that satisfies the schema (example)
                return {"lecture": "Demo lecture", "cs": [{"c": "Concept", "d": "Definition", "f": "$x=y$", "k": "Intuition", "m": "Mistake"}], "r": "Recipe"}
        llm = DummyLLM()
        # generate_one expects many arguments; we pass minimal ones
        result = generate_one(
            topic=req.topic,
            library=library,
            store=index_store,
            embedder=embedder,
            llm=llm,
            schema=req.schema,
            guide="",
            notes_dir=Path(__file__).resolve().parents[2] / "brain" / "notes"
        )
        # Validate against provided schema
        validate(result.payload, schema)
        return GenerateResponse(__root__=result.payload)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
