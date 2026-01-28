"""
Unified CLI entry point for the tasks_prompt_eng package.

This module provides a command-line interface using Typer to execute
various processing tasks such as PII redaction and Menu Sweep decisions.
"""

import datetime
import json
import sys
import logging
from pathlib import Path
from typing import Optional

import typer

from src.core.log_setup import setup_logging
from src.tasks.menu_sweep.sweep_decider import SweepDecider
from src.tasks.pii_redaction.redactor import PIIRedactor

# Setup global logger
logger = setup_logging("src.cli")

app = typer.Typer(
    name="src",
    help="Take-home test CLI for PII redaction and Menu Sweep tasks.",
    no_args_is_help=True
)

def _generate_output_path(input_path: Path, ext: str = ".json") -> Path:
    """
    Generates an output path mirroring the input directory structure, but inside 'output' folder.
    Appends a timestamp to the filename.
    
    Args:
        input_path: The path to the input file.
        ext: The extension for the output file (default: .json).
        
    Returns:
        The generated output path.
        
    Example:
        data/input/sub/file.txt -> data/output/sub/file_<timestamp>.json
    """
    # resolve absolute path to handle relative inputs correctly
    abs_input = input_path.resolve()
    
    # Try to mirror structure by replacing the last 'input' segment with 'output'
    parts = list(abs_input.parts)
    try:
        # Iterate backwards to find the last 'input' directory
        idx = -1
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] == "input":
                idx = i
                break
        
        if idx != -1:
            parts[idx] = "output"
        else:
            # Fallback: just put it in a 'output' sibling of the parent dir
            parts.insert(-1, "output")
             
    except Exception:
        # Fallback to current directory/output if path manipulation fails
        pass
        
    # Timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = abs_input.stem
    new_filename = f"{stem}_{timestamp}{ext}"
    
    parts[-1] = new_filename
    return Path(*parts)


@app.command()
def pii(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Path to input text file", exists=True
    ),
    output_path: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to output file. If not provided, generates one automatically."),
    model_name: Optional[str] = typer.Option(None, "--model", "-m", help="OpenAI model to use (overrides config)."),
):
    """
    Run PII redaction on input file.

    This command reads the input text file, uses an LLM to identify Personally Identifiable Information (PII),
    and replaces the detected entities with placeholders (e.g., [PERSON_NAME], [EMAIL]).
    The output is a JSON file containing the original text, redacted text, and a list of entities found.
    """
    try:
        if output_path is None:
            output_path = _generate_output_path(input_path, ".json")

        logger.info(f"Starting PII redaction task for {input_path}")
        with open(input_path, encoding="utf-8") as f:
            text = f.read()
        
        redactor = PIIRedactor(model_name=model_name)
        result = redactor.run(text)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Separate vault for security
        output_data = result.model_dump()
        vault_data = output_data.pop("vault", {})
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
        if vault_data:
            vault_path = output_path.with_suffix(".vault.json")
            with open(vault_path, "w", encoding="utf-8") as f:
                json.dump(vault_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Vault secrets saved to {vault_path}")
            
        logger.info(f"PII redaction complete. Output saved to {output_path}")
        logger.info(f"Entities found: {len(result.entities)}")
        
    except Exception as e:
        logger.error(f"PII task failed: {e}")
        sys.exit(1)

@app.command()
def menu(
    input_path: Path = typer.Option(
        ..., "--input", "-i", help="Path to input menu text/HTML file", exists=True
    ),
    output_path: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to output file. If not provided, generates one automatically."),
    model_name: Optional[str] = typer.Option(None, "--model", "-m", help="OpenAI model to use (overrides config)."),
):
    """
    Decide if a category sweep is needed for a menu page.

    Analyzes the provided menu page (HTML or text) to determine if all menu items are visible
    or if a "sweep" (clicking through category tabs) is required to capture full content.
    Output is a JSON file containing the decision and reasoning.
    """
    try:
        if output_path is None:
            output_path = _generate_output_path(input_path, ".json")

        logger.info(f"Starting Menu Sweep task for {input_path}")
        with open(input_path, encoding="utf-8") as f:
            text = f.read()
        
        decider = SweepDecider(model_name=model_name)
        decision = decider.decide(text)
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(decision.model_dump_json(indent=2))
            
        logger.info(f"Menu sweep decision: {decision.sweep_needed}")
        logger.info(f"Output saved to {output_path}")
        
    except Exception as e:
        logger.error(f"Menu task failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    app()
