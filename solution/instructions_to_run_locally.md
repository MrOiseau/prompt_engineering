# Instructions to Run Locally

Follow these steps to set up the environment and run the solution scripts.

## 1. Clone the Repository

Open your terminal and clone the repository:

```bash
git clone https://github.com/MrOiseau/prompt_engineering.git
cd prompt_engineering
```

---

## 2. Install Python 3.13

### macOS
**Recommended (using Homebrew):**
```bash
brew install python@3.13
```
*Alternatively, download the installer from the [official Python website](https://www.python.org/downloads/macos/).*

### Windows
**Recommended (using Winget):**
```powershell
winget install Python.Python.3.13
```
*Alternatively, download the installer from the [official Python website](https://www.python.org/downloads/windows/). Ensure you check "Add Python to PATH" during installation.*

### Linux (Ubuntu/Debian)
```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.13 python3.13-venv
```

---

## 3. Setup Project Environment

Open your terminal (Terminal, PowerShell, or Git Bash) and navigate to the project root directory.

### A. Create Virtual Environment (`.venv`)

**macOS / Linux:**
```bash
python3.13 -m venv .venv
```
*(Note: Use `python3` or `python` if `python3.13` is not explicitly aliased, but verify version with `python --version`)*

**Windows:**
```powershell
py -3.13 -m venv .venv
```

### B. Activate Virtual Environment

**macOS / Linux:**
```bash
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate.bat
```

*You should see `(.venv)` appear at the start of your command prompt line.*

---

## 4. Install Dependencies

With the virtual environment activated, install the required packages:

```bash
pip install -r requirements.txt
```

---

## 5. Setup Environment Variables

You need an OpenAI API key to run the scripts.

- **Option A (Interactive):** Run the script directly. It will prompt you to enter your key if it's not found in the environment.
- **Option B (Environment Variable):**
    - **macOS/Linux:** `export OPENAI_API_KEY="sk-..."` (Add to `~/.zshrc` or `~/.bashrc` to persist)
    - **Windows:** `$env:OPENAI_API_KEY="sk-..."`

---

## 6. Run the Solutions

### Menu Sweep
This script analyzes an HTML file to decide if a category sweep is needed.

```bash
python solution/menu_sweep.py "solution/test_examples/input/menu_sweep_example_accordion_bootstrap.html"
```

### PII Redaction
This script redacts PII from a text file and saves the output as `*.json` (redacted text + metadata) and `*.vault.json` (ID mapping).

```bash
python solution/redacting_pii.py "solution/test_examples/input/pii_redaction_example_long_intervew.txt"
```
