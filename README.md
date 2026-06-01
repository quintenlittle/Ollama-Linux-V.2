# Ollama-Linux-V.2

> The full REBEL system — a local AI assistant with text-to-speech, RAG document querying, filename scanning, and a polished terminal launcher. No cloud, no API keys, everything runs on your hardware.

---

## Overview

This guide builds on [Ollama-Linux-V.1](https://github.com/quintenlittle/Ollama-Linux-V.1). Complete that setup first, then follow the steps here to add:

- **Terminal launcher** — skull ASCII art, typewriter effects, rainbow text via lolcat, full menu system
- **Text-to-speech** — LLM responses spoken aloud using a local piper voice model
- **RAG pipeline** — index your personal document library and query it with a local LLM
- **Filename scanner** — keyword search across your entire library with an interactive open-file table
- **Dual model support** — one model for chat, a different faster model for RAG queries
- **Desktop shortcut** — double-click to launch the full system

---

## Prerequisites

- Completed [Ollama-Linux-V.1](https://github.com/quintenlittle/Ollama-Linux-V.1) setup
- Ubuntu 22.04+ (or any Debian-based distro)
- Python 3.10+
- NVIDIA GPU recommended (CPU works but is significantly slower)
- At least 16GB RAM

---

## Step 1 — Install System Dependencies

```
sudo apt install python3 python3-venv python3-pip alsa-utils lolcat -y
```

---

## Step 2 — Pull the Models

You need three models — one for chat, one for RAG queries, and one for generating embeddings:

```
ollama pull dolphin-mistral
ollama pull mannix/llama3-8b-ablitered-v3
ollama pull nomic-embed-text
```

> `dolphin-mistral` is the chat model. `mannix/llama3-8b-ablitered-v3` is faster for RAG. `nomic-embed-text` converts your documents into vectors for search. Swap the first two for whatever models you prefer.

---

## Step 3 — Create a Custom Chat Model

If you completed V.1 you already have a modelfile. If not, create one:

```
nano ~/rag-project/modelfile
```

Paste the following, editing the system prompt to your preference:

```
FROM dolphin-mistral

PARAMETER temperature 0.7
PARAMETER num_ctx 16384

SYSTEM "Your system prompt goes here."
```

Create the model:

```
ollama create rebel -f ~/rag-project/modelfile
```

> **Note on num_ctx:** This controls the context window size. Higher values use more RAM/VRAM. `16384` is a safe starting point. If you get memory errors on startup, reduce to `8192`. If you have plenty of RAM (32GB+), try `32768` for better RAG retrieval.

---

## Step 4 — Set Up the RAG Project

### Create the project directory and virtual environment

```
mkdir -p ~/rag-project/voices
cd ~/rag-project
python3 -m venv rag-env
source rag-env/bin/activate
```

### Install Python dependencies

```
pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama
pip install llama-index-vector-stores-chroma chromadb
pip install piper-tts
```

### Create rag.py

```
nano ~/rag-project/rag.py
```

Paste the full `rag.py` script (included in this repo).

> **Important:** Edit the configuration block at the top of `rag.py` to match your setup:
> ```python
> DOCS_PATH = "/home/YOUR_USERNAME/Documents/Library"  # Path to your document library
> MODEL_NAME = "rebel"                                  # Your custom chat model name
> ```

---

## Step 5 — Install the TTS Voice

Download the Cori voice model (British female, high quality):

```
cd ~/rag-project
wget -P voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/high/en_GB-cori-high.onnx
wget -P voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/high/en_GB-cori-high.onnx.json
```

Test it:

```
source ~/rag-project/rag-env/bin/activate
echo "All systems online." | python -m piper --model ~/rag-project/voices/en_GB-cori-high.onnx --output-raw | aplay -r 22050 -f S16_LE -t raw -
```

You should hear the voice through your speakers.

> Browse more voices at [rhasspy.github.io/piper-samples](https://rhasspy.github.io/piper-samples/). Always download both the `.onnx` and `.onnx.json` files as a pair. Check the `.onnx.json` for `"sample_rate"` — most are `22050` but some low-quality variants use `16000`.

---

## Step 6 — Create the Launcher Script

```
nano ~/rebel.sh
```

Paste the full `rebel.sh` script (included in this repo).

Make it executable:

```
chmod +x ~/rebel.sh
```

Test it:

```
~/rebel.sh
```

You should see the skull ASCII art draw itself in red, followed by a menu.

---

## Step 7 — Index Your Documents

Place your PDF, DOCX, TXT, MD, and HTML files in your library folder (the path you set in `DOCS_PATH`).

Launch REBEL and choose `[3] CONTINUE INDEXING FILES`.

Indexing processes your documents into vectors stored in ChromaDB. This is a one-time operation per file — the system tracks what has been indexed in `checkpoint.json` and skips previously indexed files on future runs.

> **First-time indexing can take hours** depending on library size. The system supports graceful Ctrl+C — progress is saved after each file and indexing resumes where it left off on the next run.

---

## Step 8 — Desktop Shortcut

Create a `.desktop` file:

```
nano ~/Desktop/REBEL.desktop
```

Paste:

```
[Desktop Entry]
Version=1.0
Type=Application
Name=REBEL
Comment=Launch REBEL AI System
Exec=bash -c "/home/YOUR_USERNAME/rebel.sh"
Terminal=true
Icon=utilities-terminal
Categories=Utility;
```

Replace `YOUR_USERNAME` with your actual username.

Make it executable:

```
chmod +x ~/Desktop/REBEL.desktop
```

Right-click the icon on your desktop and select **Allow Launching**.

---

## How It Works

### Menu Options

| Option | What It Does |
|--------|-------------|
| **[1] CHAT** | Direct conversation with your custom model. Responses are spoken by TTS and displayed with rainbow typewriter effect. No document context. |
| **[2] QUERY INDEXED FILES** | Ask questions about your indexed documents. Uses RAG to retrieve relevant passages and generate answers grounded in your library. |
| **[3] CONTINUE INDEXING FILES** | Scans your library for new unindexed files and processes them into ChromaDB. Safe to Ctrl+C at any time. |
| **[4] SCAN CERTAIN FILENAMES ONLY** | Keyword search across filenames in your library. Displays results in a table with the option to open files directly. No LLM involved. |
| **[5] EXIT** | Exits cleanly. |

### Architecture

```
rebel.sh  (bash launcher + TTS for chat mode)
    │
    ├── [1] CHAT          → ollama run rebel (custom dolphin-mistral model)
    │                       + piper TTS + lolcat rainbow typewriter
    │
    ├── [2] QUERY         → rag.py RAG_MODE=query
    │                       (mannix/llama3-8b-ablitered-v3 + ChromaDB + piper TTS)
    │
    ├── [3] INDEXING      → rag.py RAG_MODE=index
    │                       (nomic-embed-text → ChromaDB vectors)
    │
    └── [4] SCAN          → rag.py RAG_MODE=scan
                            (filename keyword search, no LLM)
```

### Key Components

| Component | Purpose |
|-----------|---------|
| **Ollama** | Local LLM inference engine |
| **LlamaIndex** | Document ingestion, chunking, and RAG query orchestration |
| **ChromaDB** | Persistent vector store for document embeddings |
| **nomic-embed-text** | Embedding model that converts document chunks into vectors |
| **piper-tts** | Local text-to-speech — fully offline, no API |
| **lolcat** | Rainbow color gradient for terminal output |

---

## File Structure

```
~/
├── rebel.sh                    # Main launcher script
│
├── rag-project/
│   ├── rag.py                  # RAG pipeline + query engine
│   ├── modelfile               # Ollama modelfile for the rebel model
│   ├── rag-env/                # Python virtual environment
│   ├── chroma_db/              # ChromaDB vector store (do not delete)
│   ├── checkpoint.json         # Tracks indexed files by path
│   ├── skipped.log             # Log of files skipped during indexing
│   └── voices/
│       ├── en_GB-cori-high.onnx       # TTS voice model
│       └── en_GB-cori-high.onnx.json  # TTS voice config
│
├── Documents/Library/          # Your personal document library
│
└── Desktop/
    └── REBEL.desktop           # Desktop shortcut
```

---

## Changing the TTS Voice

Two files need updating — both must point to the same voice:

**rag.py** (config block at top):
```python
VOICE_MODEL = "voices/your-voice-name.onnx"
VOICE_SAMPLE_RATE = "22050"  # check .onnx.json for correct rate
```

**rebel.sh** (inside the `speak()` function):
```bash
local model="$HOME/rag-project/voices/your-voice-name.onnx"
```
And the `aplay` sample rate on the same line:
```bash
aplay -r 22050 -f S16_LE -t raw -
```

Download new voices from [huggingface.co/rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main/en). Always download both `.onnx` and `.onnx.json` files.

---

## Changing Chat vs RAG Models

**Chat model** — used by `[1] CHAT`:
- Edit the `FROM` line in your modelfile
- Rebuild with `ollama create rebel -f ~/rag-project/modelfile`

**RAG query model** — used by `[2] QUERY INDEXED FILES`:
- Edit `RAG_MODEL` in `rebel.sh` (set as env var in the case statement)
- No rebuild needed — it uses the model directly from Ollama

**Embedding model** — used during indexing only:
- Edit `EMBED_MODEL` in `rag.py`
- Changing this means you need to re-index your entire library (delete `chroma_db/` and `checkpoint.json` first)

---

## Transferring to Another Machine

The entire project is portable:

```
cd ~
tar -czvf rebel-project.tar.gz rag-project/ rebel.sh Documents/Library/
```

On the new machine:

```
# Install prerequisites
curl -fsSL https://ollama.com/install.sh | sh
sudo apt install python3 python3-venv alsa-utils lolcat -y

# Extract
tar -xzvf rebel-project.tar.gz

# Pull models
ollama pull nomic-embed-text
ollama pull mannix/llama3-8b-ablitered-v3

# Rebuild custom model
cd ~/rag-project
ollama create rebel -f modelfile

# Rebuild venv (cannot be transferred between machines)
python3 -m venv rag-env
source rag-env/bin/activate
pip install llama-index llama-index-llms-ollama llama-index-embeddings-ollama
pip install llama-index-vector-stores-chroma chromadb piper-tts

chmod +x ~/rebel.sh
```

> **Important:** If the username on the new machine is different, update `DOCS_PATH` in `rag.py`.

---

## Troubleshooting

**Model requires more system memory than is available**
Reduce `num_ctx` in your modelfile and rebuild: `ollama create rebel -f modelfile`. Try `16384`, then `8192` if still failing.

**Permission denied on rebel.sh**
```
chmod +x ~/rebel.sh
```

**No audio from TTS**
Verify the voice model exists: `ls ~/rag-project/voices/en_GB-cori-high.onnx`. If missing, re-download per Step 5.

**Voice sounds too fast or too slow**
Check the sample rate in the `.onnx.json` file matches the `aplay -r` value in both `rebel.sh` and `rag.py`. Add `--length-scale 1.3` to the piper command to slow it down.

**Indexing fails on specific files**
Check `~/rag-project/skipped.log` for details. Common causes are corrupted PDFs or files exceeding the embedding model's 8192 token limit. Skipped files are logged but don't block the rest of the indexing process.

**^C appears when exiting**
The launcher suppresses this with `stty -echoctl`. If it still appears, add `stty -echoctl` to your `~/.bashrc`.

**lolcat not found**
```
sudo apt install lolcat
```
The launcher falls back to plain text automatically if lolcat is missing — it's cosmetic only.

---

## Related

- [Ollama-Linux-V.1](https://github.com/quintenlittle/Ollama-Linux-V.1) — Base setup: install Ollama, create a custom model, desktop shortcut
- [Ollama-Windows-V.1](https://github.com/quintenlittle/Ollama-Windows-V.1) — Same base setup for Windows
- [Ollama-Windows-V.2](https://github.com/quintenlittle/Ollama-Windows-V.2) — Windows version with TTS and document viewer
- [RAG-Technique-V.1](https://github.com/quintenlittle/RAG-Technique-V.1) — The original RAG indexing pipeline

---

## License

MIT
