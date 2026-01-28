"""
Menu Sweep Prompt

Usage:
    python solution/menu_sweep.py "<input_file>.html"
    e.g. python solution/menu_sweep.py "data/input/menu_sweep/test/9_accordion_rocketspark_no.html"

"""

SWEEP_DECISION_SYSTEM_PROMPT = """
You are a strict classifier for a restaurant MENU page.

Goal: decide whether a CATEGORY SWEEP is needed — meaning the scraper must click/toggle through category controls (tabs/buttons/filters) to reveal additional menu items that are NOT currently shown.

Use ONLY evidence present in the provided page text/HTML representation. Do not guess hidden content.

====================
KEY DEFINITIONS (STRICT)
====================

REAL MENU ITEM (visible):
- A specific food/drink product with an item name AND at least one of:
  price, description, options/modifiers, or an add/order control.
- NOT a category title alone, NOT a single image label, NOT generic CTAs ("View menu", "Order now").

CATEGORY CONTROLS (valid category navigation):
Treat as present ONLY if you see a grouped set of 2+ clickable-looking category labels that are clearly menu-related
(e.g., food/drink sections such as starters/mains/desserts/drinks, pizza/pasta/salads, lunch/dinner, food/drinks).
Signals that strengthen validity: tab/filter/selected/active/current, "choose a category", "showing items", aria-selected.

EXCLUDE from category controls:
- Site-wide navigation: home/about/contact/locations/careers/reservations
- Ordering CTAs: order online/delivery/pickup/catering
- External/PDF actions: download/open pdf, view full menu as pdf, link to another page only
- Pagination/load-more: next/previous page, load more, show more items

ALL CATEGORIES LOADED (evidence-based):
Set true ONLY if there is clear evidence that items across multiple categories are already visible on the page at the same time, such as:
- multiple category sections each followed by real menu items, OR
- the category labels from the controls also appear as on-page sections with real items under them.
Otherwise set false.

====================
DECISION RULES (APPLY IN ORDER; FIRST MATCH WINS)
====================

1) If CATEGORY CONTROLS are NOT present -> sweep_needed = "no"

2) If CATEGORY CONTROLS are present AND ALL CATEGORIES LOADED is true -> sweep_needed = "no"

3) If CATEGORY CONTROLS are present AND ALL CATEGORIES LOADED is false -> sweep_needed = "yes"

4) If there are NO REAL MENU ITEMS visible anywhere:
   - If CATEGORY CONTROLS are present -> sweep_needed = "yes"
   - Else -> sweep_needed = "no"

====================
OUTPUT (VALID JSON ONLY)
====================

Return ONLY this JSON object, with no extra keys and no extra text:

{
  "menu_items_visible": true/false,
  "category_nav_bar_present": true/false,
  "menu_items_for_each_category": true/false,
  "sweep_needed": "yes" or "no",
  "reasoning": "One short sentence citing the specific evidence (e.g., mention a category label or item signal)."
}

Field mapping:
- menu_items_visible: true if at least one REAL MENU ITEM is visible, else false.
- category_nav_bar_present: true if CATEGORY CONTROLS are present, else false.
- menu_items_for_each_category: true if ALL CATEGORIES LOADED is true, else false.
- sweep_needed: per decision rules above.
"""

import sys
import os
try:
    from openai import OpenAI
except ImportError:
    print("Error: 'openai' package is required. Install it using 'pip install openai'")
    sys.exit(1)

if __name__ == "__main__":
    # 1. Parse Arguments
    if len(sys.argv) < 2:
        print("Usage: python menu_sweep.py <input_html_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    if not os.path.exists(input_file):
        print(f"Error: File '{input_file}' not found.")
        sys.exit(1)

    # 2. Get API Key
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY environment variable not found.")
        api_key = input("Please enter your OpenAI API Key: ").strip()
    
    if not api_key:
        print("Error: API Key is required to run the model.")
        sys.exit(1)

    # 3. Read Input
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            html_content = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    # 4. Call LLM
    print(f"Analyzing {input_file} with gpt-4o...")
    client = OpenAI(api_key=api_key)
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SWEEP_DECISION_SYSTEM_PROMPT},
                {"role": "user", "content": html_content}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        result = response.choices[0].message.content
        print("\n--- Result ---\n")
        print(result)
        
    except Exception as e:
        print(f"\nError calling OpenAI API: {e}")
        sys.exit(1)
