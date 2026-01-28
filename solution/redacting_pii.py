"""
PII Redactor Script (Standalone)

Usage:
    python solution/redacting_pii.py <input_file>.txt
    e.g. python solution/redacting_pii.py "solution/test_examples/input/pii_redaction_example_long_intervew.txt"
"""
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Any

from pydantic import BaseModel, Field
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

try:
    from openai import APIError, APITimeoutError, OpenAI, RateLimitError
except ImportError:
    print("Error: 'openai', 'pydantic', 'tenacity' are required.")
    sys.exit(1)

# -----------------------------------------------------------------------------
# Configuration & Logging
# -----------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def setup_logging(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

logger = setup_logging(__name__)

# -----------------------------------------------------------------------------
# LLM Client
# -----------------------------------------------------------------------------
class LLMClient:
    def __init__(self, model: str):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set.")
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.total_tokens = 0

    @retry(
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def call(
        self, 
        messages: list[dict[str, str]], 
        temperature: float = 0.0,
        response_format: Optional[dict[str, Any]] = None,
        timeout: float = 60.0
    ) -> str:
        start_time = time.time()
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "response_format": response_format,
                "timeout": timeout,
                "temperature": temperature
            }
            
            logger.debug(f"Sending request to {self.model}")
            response = self.client.chat.completions.create(**kwargs)
            
            if response.usage:
                self.total_tokens += response.usage.total_tokens
            
            content = response.choices[0].message.content
            duration = time.time() - start_time
            logger.debug(f"Request completed in {duration:.2f}s")
            
            return content or ""
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

# -----------------------------------------------------------------------------
# Chunking Logic
# -----------------------------------------------------------------------------
Span = Tuple[int, int]

@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    kind: str
    text: str

def normalize_spans(spans: Iterable[Span], text_len: int) -> List[Span]:
    cleaned: List[Span] = []
    for s, e in spans:
        if s is None or e is None: continue
        s = max(0, min(int(s), text_len)); e = max(0, min(int(e), text_len))
        if e <= s: continue
        cleaned.append((s, e))
    if not cleaned: return []
    cleaned.sort(key=lambda x: (x[0], x[1]))
    merged: List[Span] = [cleaned[0]]
    for s, e in cleaned[1:]:
        ps, pe = merged[-1]
        if s <= pe: merged[-1] = (ps, max(pe, e))
        else: merged.append((s, e))
    return merged

def boundary_inside_any_entity(boundary: int, spans: Sequence[Span]) -> bool:
    for s, e in spans:
        if boundary <= s: return False
        if s < boundary < e: return True
    return False

def filter_boundaries(boundaries: Iterable[int], spans: Sequence[Span]) -> List[int]:
    out: List[int] = []
    for b in boundaries:
        if not boundary_inside_any_entity(b, spans): out.append(b)
    return out

def split_by_boundaries(text: str, boundaries: Sequence[int], kind: str) -> List[Segment]:
    if not boundaries: return [Segment(0, len(text), kind, text)]
    segs: List[Segment] = []
    last = 0
    for b in boundaries:
        if b <= last: continue
        segs.append(Segment(last, b, kind, text[last:b]))
        last = b
    if last < len(text): segs.append(Segment(last, len(text), kind, text[last:]))
    return segs

TS_TURN_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+\w+:\s*", re.MULTILINE)
SPEAKER_TURN_RE = re.compile(r"^[A-Za-z][\w\s'.-]{0,30}:\s+", re.MULTILINE)

def find_turn_starts(text: str) -> Tuple[List[int], str]:
    ts = [m.start() for m in TS_TURN_RE.finditer(text)]
    if ts: return ts, "turn:timestamp"
    sp = [m.start() for m in SPEAKER_TURN_RE.finditer(text)]
    if sp: return sp, "turn:speaker"
    return [], "fallback"

DEFAULT_ABBREVIATIONS = {
    "dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "jr.", "st.", "no.", "etc.", "e.g.", "i.e.",
    "npr.", "itd.", "itp.", "tj.", "god.", "br.",
}
SENT_END_RE = re.compile(r"([.!?])(\s+)")

def protect_dots(text: str, abbreviations: Optional[set[str]] = None) -> str:
    if abbreviations is None: abbreviations = DEFAULT_ABBREVIATIONS
    sentinel = "∯"
    text = re.sub(r"(?<=\d)\.(?=\d)", sentinel, text)
    text = re.sub(r"\b([A-Za-zА-Яа-я])\.(?=\s)", r"\1" + sentinel, text)
    abbr_sorted = sorted(abbreviations, key=len, reverse=True)
    abbr_pat = r"\b(?:" + "|".join(re.escape(a) for a in abbr_sorted) + r")"
    abbr_re = re.compile(abbr_pat, re.IGNORECASE)
    def _abbr_repl(m: re.Match) -> str: return m.group(0).replace(".", sentinel)
    return abbr_re.sub(_abbr_repl, text)

def sentence_and_newline_boundaries(text: str) -> List[int]:
    protected = protect_dots(text)
    boundaries: List[int] = []
    i = 0; n = len(protected)
    while i < n:
        if protected.startswith("\n\n", i):
            if i > 0: boundaries.append(i)
            i += 2; continue
        if protected[i] == "\n":
            boundaries.append(i + 1); i += 1; continue
        m = SENT_END_RE.match(protected, i)
        if m:
            boundaries.append(i + 1); i = i + 1 + len(m.group(2)); continue
        i += 1
    return sorted({b for b in boundaries if 0 < b < len(text)})

def make_atomic_segments(text: str) -> List[Segment]:
    spans = normalize_spans([], len(text))
    starts, kind_base = find_turn_starts(text)
    if starts:
        uniq = sorted({s for s in starts if 0 <= s < len(text)})
        if not uniq: return [Segment(0, len(text), kind_base, text)]
        if uniq[0] != 0: uniq = [0] + uniq
        boundaries = filter_boundaries(uniq[1:], spans)
        return split_by_boundaries(text, boundaries, kind_base)
    
    boundaries = sentence_and_newline_boundaries(text)
    boundaries = filter_boundaries(boundaries, spans)
    return split_by_boundaries(text, boundaries, "sentence") if boundaries else [Segment(0, len(text), "fallback", text)]

# -----------------------------------------------------------------------------
# Redactor Logic
# -----------------------------------------------------------------------------
PROMPTS = {
    "system": """You are a PII extraction system for speech-to-text transcripts.

# OUTPUT SCHEMA (strict JSON, no markdown):
{"reasoning": "<brief analysis>", "entities": [{"type": "<TYPE>", "text": "<exact_substring>", "start": <int>, "end": <int>, "confidence": <float 0.0-1.0>}]}

# FIELD DEFINITIONS:
- type: One of the PII types below
- text: The EXACT substring as it appears in the input (copy-paste accuracy)
- start: 0-indexed position of the first character of this entity
- end: 0-indexed position AFTER the last character (i.e., text = input[start:end])

# PII TYPES (Categorized):
PERSON_NAME | EMAIL | PHONE_NUMBER | SSN | PASSPORT_NUMBER | DRIVER_LICENSE | TAX_ID | ID_NUMBER | BIOMETRIC_DATA | MEDICAL_RECORD_NUM | BANK_ACCOUNT | CREDIT_CARD | PASSWORD_PIN
RELIGION | DATE_OF_BIRTH | PLACE_OF_BIRTH | ADDRESS | ZIP_CODE | GPS_LOCATION | JOB_TITLE | EMPLOYER | SALARY | FAMILY_MEMBER_NAME
IP_ADDRESS | MAC_ADDRESS | DEVICE_ID | SOCIAL_HANDLE | COOKIE_ID

# CRITICAL RULES:
1. "text" must be the EXACT substring as it appears in the input
2. Return {"entities": []} if no PII or uncertain
3. Precision > Recall, BUT include "old" or "incorrect" PII if it identifies the person.
4. LOOK FOR SPELLED OUT CHARACTERS: "X Y Z", "L-E-O" are strictly PII. Capturing them is mandatory.
5. Assign a confidence score (0.0 to 1.0) based on how certain you are this is PII.
""",
    "user": """Extract ALL PII from this transcript.
Pay special attention to:
- Spelled-out names/IDs (e.g. "a b c one two three")
- Redundant date/phone mentions

Transcript:
{text}
"""
}

class PIIEntity(BaseModel):
    type: str
    text: str
    start: int
    end: int
    confidence: float = 1.0
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))

class RedactionResult(BaseModel):
    original_text: str
    redacted_text: str
    entities: list[PIIEntity]
    vault: Dict[str, str]
    redaction_count: int
    model: str
    tokens_used: int

class PIIRedactor:
    def __init__(self, model_name: str = "gpt-4o"):
        self.model = model_name
        self.temperature = 0.0
        self.timeout = 60.0
        self.chunk_size = 4000
        self.llm = LLMClient(model=self.model)

    def run(self, text: str) -> RedactionResult:
        # 1. Chunking
        if len(text) > self.chunk_size:
            logger.info(f"Text length {len(text)} exceeds specific chunk size. Splitting...")
            segments = make_atomic_segments(text)
            chunks = []
            current_buffer = []
            current_len = 0
            for seg in segments:
                if current_len + len(seg.text) > self.chunk_size:
                    chunks.append("".join([s.text for s in current_buffer]))
                    current_buffer = [seg]
                    current_len = len(seg.text)
                else:
                    current_buffer.append(seg)
                    current_len += len(seg.text)
            if current_buffer:
                chunks.append("".join([s.text for s in current_buffer]))
        else:
            chunks = [text]

        logger.info(f"Processing {len(chunks)} chunks")

        # 2. Process
        all_entities: list[PIIEntity] = []
        offset = 0
        
        for chunk in chunks:
            chunk_entities = self._extract_entities(chunk)
            for entity in chunk_entities:
                entity.start += offset
                entity.end += offset
                all_entities.append(entity)
            offset += len(chunk)

        all_entities.sort(key=lambda e: e.start, reverse=True)
        
        # 4. Redact
        redacted_text = text
        total_replacements = 0
        valid_entities: list[PIIEntity] = []
        vault: Dict[str, str] = {}
        
        for entity in all_entities:
            pattern = re.escape(entity.text)
            matches = list(re.finditer(pattern, redacted_text))
            
            if not matches:
                logger.warning(f"Entity '{entity.text}' not found. Skipping.")
                continue
            
            best_match = None
            best_distance = float('inf')
            
            for match in matches:
                distance = abs(match.start() - entity.start)
                if distance < best_distance:
                    best_distance = distance
                    best_match = match
            
            if best_match:
                if best_distance > 20: 
                    logger.warning(f"Large position drift ({best_distance}). Redacting anyway.")

                entity.start = best_match.start()
                entity.end = best_match.end()
                
                review_flag = "_REVIEW" if entity.confidence < 0.8 else ""
                token = f"[PII_ID_{entity.id}_{entity.type}{review_flag}]"
                vault[entity.id] = entity.text
                
                redacted_text = (
                    redacted_text[:best_match.start()] + 
                    token + 
                    redacted_text[best_match.end():]
                )
                total_replacements += 1
                valid_entities.append(entity)
        
        valid_entities.reverse()
        return RedactionResult(
            original_text=text,
            redacted_text=redacted_text,
            entities=valid_entities,
            vault=vault,
            redaction_count=total_replacements,
            model=self.llm.model,
            tokens_used=self.llm.total_tokens
        )

    def _extract_entities(self, text: str) -> list[PIIEntity]:
        messages = [
            {"role": "system", "content": PROMPTS["system"]},
            {"role": "user", "content": PROMPTS["user"].format(text=text)}
        ]
        try:
            response_text = self.llm.call(
                messages, 
                temperature=self.temperature,
                timeout=self.timeout,
                response_format={"type": "json_object"}
            )
            data = json.loads(response_text)
            raw_entities = data.get("entities", [])
            entities = []
            for e in raw_entities:
                try:
                    entities.append(PIIEntity(**e))
                except Exception:
                    pass
            return entities
        except Exception as e:
            logger.error(f"Error during PII extraction: {e}")
            return []

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python redacting_pii.py <input_file>")
        sys.exit(1)
        
    input_file = sys.argv[1]
    
    if not os.path.exists(input_file):
        print(f"File not found: {input_file}")
        sys.exit(1)

    # Setup API Key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY environment variable not found.")
        api_key = input("Please enter your OpenAI API Key: ").strip()
        if api_key:
            os.environ["OPENAI_API_KEY"] = api_key
        else:
            print("Error: API Key is required.")
            sys.exit(1)
        
    with open(input_file, "r", encoding="utf-8") as f:
        text = f.read()
        
    redactor = PIIRedactor()
    try:
        result = redactor.run(text)
        
        print("REDACTED TEXT:")
        print("-" * 40)
        print(result.redacted_text)
        print("-" * 40)
        print(f"Stats: {result.redaction_count} entities redacted. Model: {result.model}")
        
        # Output paths
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        extension = os.path.splitext(os.path.basename(input_file))[1]
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.dirname(os.path.abspath(input_file)) # fallback
        
        # Try to use a specific output folder if "input" in path -> "output"
        if "input" in os.path.dirname(os.path.abspath(input_file)):
            output_dir = os.path.dirname(os.path.abspath(input_file)).replace("input", "output")
        else:
            output_dir = os.path.join(output_dir, "output")
            
        os.makedirs(output_dir, exist_ok=True)

        # 1. Redacted Text Output (Original Extension)
        # User requested: same name - extension + timestamp + .<<same_extension>>
        redacted_output_path = os.path.join(output_dir, f"{base_name}_{timestamp}{extension}")
        
        with open(redacted_output_path, "w", encoding="utf-8") as f:
            f.write(result.redacted_text)
        print(f"Saved redacted text to {redacted_output_path}")

        # 2. Main JSON Output (Redacted Text + Metadata)
        json_output_path = os.path.join(output_dir, f"{base_name}_{timestamp}.json")
        
        # Convert entities to list of dicts
        entities_data = [e.model_dump() for e in result.entities]
        
        output_data = {
            "original_file": input_file,
            "redacted_text": result.redacted_text,
            "entities": entities_data,
            "redaction_count": result.redaction_count,
            "model": result.model,
            "tokens_used": result.tokens_used
        }
        with open(json_output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2)
        print(f"Saved JSON output to {json_output_path}")

        # 3. Vault Output (ID -> Original Mapping)
        vault_output_path = os.path.join(output_dir, f"{base_name}_{timestamp}.vault.json")
        with open(vault_output_path, "w", encoding="utf-8") as f:
            json.dump(result.vault, f, indent=2)
        print(f"Saved Vault output to {vault_output_path}")
        
    except Exception as e:
        print(f"Error executing redaction: {e}")
        sys.exit(1)
