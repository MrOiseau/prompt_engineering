"""
PII Redactor Module

Uses an LLM to extract PII entities (type, text, start, end) and then applies
deterministic regex-based replacement to redact them from the original text.
The LLM-provided positions are used for verification.

Supports:
- Chunking for large files
- Vault tokenization (reversible anonymization)
- Confidence scoring
"""
import json
import re
import uuid
from pathlib import Path
from typing import Optional, Dict

import yaml
from pydantic import BaseModel, Field

from src.core.llm_client import LLMClient
from src.core.log_setup import setup_logging
from src.tasks.pii_redaction import chunking

logger = setup_logging(__name__)


class PIIEntity(BaseModel):
    """Represents a detected PII entity."""
    type: str
    text: str
    start: int
    end: int
    confidence: float = 1.0
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class RedactionResult(BaseModel):
    """Result of a PII redaction operation."""
    original_text: str
    redacted_text: str
    entities: list[PIIEntity]
    vault: Dict[str, str] # map of ID -> Original Text
    redaction_count: int
    model: str
    tokens_used: int


class PIIRedactor:
    """
    Handles PII redaction from text using an LLM for entity extraction
    and deterministic regex-based replacement.
    
    The LLM identifies WHAT is PII (entity type, text, and position).
    Python uses regex to find the entity and verify against LLM-provided positions.
    This separation ensures auditability and avoids hallucination in output.
    """
    
    def __init__(self, prompts_path: Optional[Path] = None, model_name: Optional[str] = None):
        """
        Initialize the PIIRedactor.

        Args:
            prompts_path: Optional path to the prompts YAML file.
                          Defaults to 'prompts.yaml' in the same directory.
            model_name: Optional model to use (overrides config).
        """
        # Resolve paths
        base_dir = Path(__file__).parent
        if not prompts_path:
            prompts_path = base_dir / "prompts.yaml"
        config_path = base_dir / "config.yaml"
        
        # Load Prompts
        with open(prompts_path, encoding="utf-8") as f:
            self.prompts = yaml.safe_load(f)

        # Load Config
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
        else:
            logger.warning(f"Config file not found at {config_path}. Using defaults.")
            self.config = {}

        # Determine Model
        # CLI override > Config > Fallback
        self.model = model_name or self.config.get("model")
        self.temperature = self.config.get("temperature")
        self.timeout = self.config.get("timeout")
        self.chunk_size = self.config.get("chunk_size", 4000) # heuristic char limit
            
        self.llm = LLMClient(model=self.model)

    def run(self, text: str) -> RedactionResult:
        """
        Executes the full PII redaction pipeline.

        Pipeline:
        1. Chunk text if too long.
        2. LLM extracts PII entities from each chunk.
        3. Merge entities and adjust offsets.
        4. Python verifies string match and replaces with Vault Tokens.

        Args:
            text: The input text to be redacted.

        Returns:
            RedactionResult: Contains original text, redacted text, 
                             entities found, vault map, and statistics.
        """
        # 1. Chunking Strategy
        if len(text) > self.chunk_size:
            logger.info(f"Text length {len(text)} exceeds specific chunk size. Splitting...")
            segments = chunking.make_atomic_segments(text)
            
            # Simple heuristic: merge small segments to fill context window
            # Real implementation might need token counting, but char count is a safe proxy
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

        # 2. Process Chunks
        all_entities: list[PIIEntity] = []
        offset = 0
        total_tokens = 0

        for i, chunk in enumerate(chunks):
            # Extract
            chunk_entities = self._extract_entities(chunk)
            
            # Adjust offsets
            for entity in chunk_entities:
                entity.start += offset
                entity.end += offset
                all_entities.append(entity)
            
            offset += len(chunk)

        # 3. Sort by start position descending (so replacements don't shift indices)
        all_entities.sort(key=lambda e: e.start, reverse=True)
        
        # 4. Vault Tokenization & Verification
        redacted_text = text
        total_replacements = 0
        valid_entities: list[PIIEntity] = []
        vault: Dict[str, str] = {}
        
        for entity in all_entities:
            # Use regex to find the entity text
            pattern = re.escape(entity.text)
            
            # Find all occurrences
            matches = list(re.finditer(pattern, redacted_text))
            
            if not matches:
                logger.warning(f"Entity '{entity.text}' not found in text. Skipping.")
                continue
            
            # Find the match closest to LLM-provided position
            best_match = None
            best_distance = float('inf')
            
            for match in matches:
                distance = abs(match.start() - entity.start)
                if distance < best_distance:
                    best_distance = distance
                    best_match = match
            
            if best_match:
                # Verify position (allow some tolerance for drift due to chunk context or spacing)
                if best_distance > 20: 
                    logger.warning(f"Large position drift ({best_distance} chars) for '{entity.text}'. Redacting anyway if confident.")
                    # In production, we might skip if drift is huge, but here we prioritize recall.

                # Update entity with verified position
                entity.start = best_match.start()
                entity.end = best_match.end()
                
                # Vault Token Construction
                # [PII_ID_<uuid>_<TYPE>]
                # Add review flag if confidence is low
                review_flag = "_REVIEW" if entity.confidence < 0.8 else ""
                token = f"[PII_ID_{entity.id}_{entity.type}{review_flag}]"
                
                # Sanity check: ensure ID is unique (it is UUID)
                vault[entity.id] = entity.text
                
                # Replace at the verified position
                redacted_text = (
                    redacted_text[:best_match.start()] + 
                    token + 
                    redacted_text[best_match.end():]
                )
                total_replacements += 1
                valid_entities.append(entity)
        
        # Reverse to restore reading order
        valid_entities.reverse()
        
        return RedactionResult(
            original_text=text,
            redacted_text=redacted_text,
            entities=valid_entities,
            vault=vault,
            redaction_count=total_replacements,
            model=self.llm.model,
            tokens_used=self.llm.total_tokens + total_tokens # simplified tracking
        )

    def _extract_entities(self, text: str) -> list[PIIEntity]:
        """
        Calls LLM to extract PII entities as {type, text, start, end}.

        Args:
            text: The text to analyze.

        Returns:
            list[PIIEntity]: A list of detected PII entities.
        """
        system_prompt = self.prompts["system"]
        user_prompt = self.prompts["user"].format(text=text)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
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
                except Exception as parse_error:
                    logger.warning(f"Failed to parse entity {e}: {parse_error}")
            
            logger.info(f"LLM extracted {len(entities)} PII entities")
            return entities
            
        except json.JSONDecodeError:
            logger.error("Failed to parse LLM JSON response")
            return []
        except Exception as e:
            logger.error(f"Error during PII extraction: {e}")
            return []
