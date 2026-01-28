import json
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from src.core.llm_client import LLMClient
from src.core.log_setup import setup_logging

logger = setup_logging(__name__)

class SweepDecision(BaseModel):
    menu_items_visible: bool
    category_nav_present: bool
    all_categories_loaded: bool
    sweep_needed: str
    reasoning: str

class SweepDecider:
    """
    Decides whether a restaurant menu page requires a 'sweep' (clicking through categories)
    to reveal all menu items.
    """
    def __init__(self, prompts_path: Optional[Path] = None, model_name: Optional[str] = None):
        """
        Initialize the SweepDecider.

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

        # Determine Model and Params
        self.model = model_name or self.config.get("model")
        self.temperature = self.config.get("temperature")
        self.timeout = self.config.get("timeout")
        self.reasoning_effort = self.config.get("reasoning_effort")
            
        self.llm = LLMClient(model=self.model)

    def decide(self, text: str) -> SweepDecision:
        """
        Decides if a sweep is needed based on the provided menu text/HTML representation.

        Args:
            text: A string representation of the menu structure (e.g., HTML snippet or simplified text).

        Returns:
            SweepDecision: The full decision object.
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
                reasoning_effort=self.reasoning_effort,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(response_text)
            
            # Validate with Pydantic
            decision = SweepDecision(**data)
            
            logger.info(f"Sweep decision logic: {decision.model_dump_json()}")
            
            if decision.sweep_needed.strip().lower() not in ["yes", "no"]:
                logger.warning(
                    f"Unexpected sweep_needed value: {decision.sweep_needed}. Defaulting to 'yes' for safety."
                )
                decision.sweep_needed = "yes"
            
            return decision
            
        except Exception as e:
            logger.error(f"Error during sweep decision: {e}")
            # Fallback safe mode
            return SweepDecision(
                menu_items_visible=False,
                category_nav_present=True,
                all_categories_loaded=False,
                sweep_needed="yes",
                reasoning=f"Error occurred: {e}"
            )
