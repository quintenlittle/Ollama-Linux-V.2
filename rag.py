import os
import shutil
import subprocess
import signal
import json
import termios
import sys
from datetime import datetime
import chromadb
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext, Settings, PromptTemplate
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# --- UPDATE THESE ---
DOCS_PATH = "/home/system/Documents/Library"
MODEL_NAME = "rebel"
EMBED_MODEL = "nomic-embed-text"
CHROMA_PATH = "./chroma_db"
LOG_FILE = "./skipped.log"
CHECKPOINT_FILE = "./checkpoint.json"
BATCH_SIZE = 1
MAX_FILE_SIZE_MB = 50
MIN_FREE_DISK_GB = 2
SUPPORTED_EXTENSIONS = ('.pdf', '.docx', '.txt', '.md', '.html', '.htm')
# Plain text files are loaded as a single document with no pre-chunking.
# This limit splits them manually before ingestion so nomic-embed-text
# never receives a chunk larger than its 8192 token hard limit.
PLAINTEXT_CHUNK_CHARS = 12000  # ~3000 tokens, well under nomic's 8192 limit

# ─── TTS config ───────────────────────────────────────────────────────────────
# Cori voice model — stored in ~/rag-project/voices/
# Download: wget -P voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/high/en_GB-cori-high.onnx
# Download: wget -P voices https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/high/en_GB-cori-high.onnx.json
VOICE_MODEL = "voices/en_GB-cori-high.onnx"
VOICE_SAMPLE_RATE = "22050"
# --------------------

# ─── Colors ───────────────────────────────────────────────────────────────────
RED = '\033[0;31m'
PURPLE = '\033[1;35m'  # Neon Purple
GREEN = '\033[0;32m'   # Terminal Green
RESET = '\033[0m'

# Suppress ^C characters being echoed to the terminal globally at startup.
# The signal handlers also do this but the progress bar's \r redraws can
# trigger echo before a signal ever fires.
try:
    _attrs = termios.tcgetattr(sys.stdin)
    _attrs[3] &= ~termios.ECHOCTL
    termios.tcsetattr(sys.stdin, termios.TCSANOW, _attrs)
except Exception:
    pass

def r(text):
    print(f"{RED}{text}{RESET}")

def rp(text, end='\n'):
    print(f"{RED}{text}{RESET}", end=end, flush=True)

# Read mode from environment variable
RAG_MODE = os.environ.get("RAG_MODE", "both")

Settings.llm = Ollama(model=MODEL_NAME, request_timeout=120.0)
Settings.embed_model = OllamaEmbedding(model_name=EMBED_MODEL)
Settings.chunk_size = 512
Settings.chunk_overlap = 50

# ─── Graceful shutdown (indexing only) ───────────────────────────────────────
shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    # Suppress the ^C character the terminal prints on SIGINT
    try:
        attrs = termios.tcgetattr(sys.stdin)
        attrs[3] &= ~termios.ECHOCTL
        termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
    except Exception:
        pass
    r("\n\nShutdown requested — finishing current batch then stopping safely...")
    shutdown_requested = True

def handle_query_exit(signum, frame):
    # Suppress the ^C character the terminal prints on SIGINT
    try:
        attrs = termios.tcgetattr(sys.stdin)
        attrs[3] &= ~termios.ECHOCTL
        termios.tcsetattr(sys.stdin, termios.TCSANOW, attrs)
    except Exception:
        pass
    exit(0)

# ─── Disk space check ─────────────────────────────────────────────────────────
def check_disk_space():
    stat = shutil.disk_usage(os.path.dirname(os.path.abspath(CHROMA_PATH)))
    free_gb = stat.free / (1024 ** 3)
    if free_gb < MIN_FREE_DISK_GB:
        r(f"WARNING: Only {free_gb:.1f}GB free. Recommended minimum is {MIN_FREE_DISK_GB}GB.")
        rp("Continue anyway? (y/n): ", end="")
        if input().strip().lower() != 'y':
            exit()
    else:
        r(f"Disk space OK: {free_gb:.1f}GB free.")

# ─── Logging ──────────────────────────────────────────────────────────────────
def log_skipped(files, reason):
    with open(LOG_FILE, 'a') as f:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for file in files:
            f.write(f"[{timestamp}] SKIPPED: {file}\n")
            f.write(f"           REASON:  {reason}\n\n")

# ─── TTS ──────────────────────────────────────────────────────────────────────
def clean_for_tts(text):
    import re
    # Strip ANSI escape codes
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
    # Strip markdown code fences
    text = re.sub(r'```[a-zA-Z]*', '', text)
    # Strip model artifact tags like [Insert Divider Here]
    text = re.sub(r'\[.*?\]', '', text)
    # Ensure every newline is preceded by a space to prevent word fusion
    text = re.sub(r'(\S)\n', r'\1 \n', text)
    # Remove pure separator lines
    lines = text.splitlines()
    lines = [l for l in lines if not re.match(r'^[\-=\*_\[\]\s]*$', l)]
    lines = [re.sub(r'^\s*[-*]\s*', '', l) for l in lines]
    # Join all lines into one string
    result = ' '.join(l.strip() for l in lines if l.strip())
    result = re.sub(r' +', ' ', result).strip()
    # Fix ollama streaming stutter
    # 1. Full word duplicate: 'alliances alliances' -> 'alliances'
    result = re.sub(r'\b(\w[\w\-]*) \1\b', r'\1', result)
    # 2. Prefix fragment before full word: 'interpla interplay' -> 'interplay'
    result = re.sub(r'\b(\w+) (\1\w+)\b', r'\2', result)
    # 3. Punctuation-wrapped duplicate: '(German (Germany' -> '(Germany'
    result = re.sub(r'([\(\[]?)(\w[\w\-]*) \1(\2\w*)', r'\1\3', result)
    result = re.sub(r' +', ' ', result).strip()
    return result

def speak(text):
    if not os.path.exists(VOICE_MODEL):
        return
    try:
        clean = clean_for_tts(text)
        if not clean:
            return
        proc = subprocess.Popen(
            [sys.executable, "-m", "piper", "--model", VOICE_MODEL, "--output-raw"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        audio, _ = proc.communicate(input=clean.encode())
        aplay = subprocess.Popen(
            ["aplay", "-r", VOICE_SAMPLE_RATE, "-f", "S16_LE", "-t", "raw", "-"],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        aplay.communicate(input=audio)
    except Exception:
        pass  # TTS failure should never interrupt the query session


# ─── Plain text splitter ──────────────────────────────────────────────────────
# PDFs and docx are pre-chunked by LlamaIndex's readers before hitting the
# embedder, so they never need this. Plain .txt and .md files are loaded as
# one giant document — this splits them into safe-sized pieces manually.
def split_plaintext_docs(docs, file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ('.txt', '.md'):
        return docs
    split_docs = []
    for doc in docs:
        text = doc.text
        if len(text) <= PLAINTEXT_CHUNK_CHARS:
            split_docs.append(doc)
        else:
            for i in range(0, len(text), PLAINTEXT_CHUNK_CHARS):
                chunk = text[i:i + PLAINTEXT_CHUNK_CHARS]
                from llama_index.core.schema import Document
                split_docs.append(Document(text=chunk, metadata=doc.metadata))
            r(f"  Split large text file into {len(split_docs)} chunks: {os.path.basename(file_path)}")
    return split_docs

# ─── Checkpoint ───────────────────────────────────────────────────────────────
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, 'r') as f:
            return set(json.load(f))
    return set()

def save_checkpoint(indexed_files):
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(list(indexed_files), f)

# ─── Progress bar ─────────────────────────────────────────────────────────────
def overall_progress(current, total, bar_length=40):
    percent = current / total if total > 0 else 0
    filled = int(bar_length * percent)
    bar = '█' * filled + '░' * (bar_length - filled)
    files_done = min(current * BATCH_SIZE, len(remaining_files))
    total_str = str(total)
    current_str = str(current).rjust(len(total_str))
    files_done_str = str(files_done).rjust(len(str(len(remaining_files))))
    print(f'\r{RED}Overall: [{bar}] {current_str}/{total_str} batches | ~{files_done_str}/{len(remaining_files)} files | {percent*100:5.1f}%{RESET}', end='', flush=True)

# ─── ChromaDB setup ───────────────────────────────────────────────────────────
def get_index():
    chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = chroma_client.get_or_create_collection("rebel_library")
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(
        vector_store,
        storage_context=storage_context
    )
    return index, storage_context

# ─── Query engine ─────────────────────────────────────────────────────────────
def run_query_engine(index):
    # Signal handler owns Ctrl+C — no competing except KeyboardInterrupt blocks below
    signal.signal(signal.SIGINT, handle_query_exit)
    
    # Using tree_summarize forces long-form detail stitching instead of truncation
    query_engine = index.as_query_engine(
        similarity_top_k=4,
        response_mode="tree_summarize"
    )
    
    current_date_str = datetime.now().strftime("%A, %B %d, %Y")
    
    system_prompt = (
        "You are an advanced data retrieval engine. Process queries with extreme technical diligence. "
        "Your execution profile is cold, clinical, precise, and completely devoid of emoticons or conversational padding. "
        f"The current real-world date is explicitly {current_date_str}. Adjust all temporal or relative time calculations "
        "using this anchor date. Scour context thoroughly for exhaustive facts. If data is missing, explicitly output the tag: "
        "'CONTEXT INSIGNIFICANT: Reverting to internal knowledge base.' and answer using internal weights. "
        "Skip all introductory remarks."
    )
    
    # Corrected Llama 3 structural formatting layout
    text_qa_template_str = (
        "<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n"
        f"{system_prompt}<|eot_id|>"
        "<|start_header_id|>user<|end_header_id|>\n\n"
        "Context information is below:\n"
        "---------------------\n"
        "{context_str}\n"
        "---------------------\n"
        "Given the context information, answer the query: {query_str}<|eot_id|>"
        "<|start_header_id|>assistant<|end_header_id|>\n\n"
    )
    query_engine.update_prompts({"text_qa_template": PromptTemplate(text_qa_template_str)})

    while True:
        try:
            question = input(f"{RED}INPUT: {RESET}")
        except KeyboardInterrupt:
            # Catches Ctrl+C during input() before the signal handler fires
            exit(0)

        if not question.strip():
            continue

        try:
            response = query_engine.query(question)
            if not str(response).strip() or "empty response" in str(response).lower():
                raise ValueError("No relevant docs found")
            import textwrap
            wrapped = textwrap.fill(str(response), width=72)
            print(f"\n{RED}OUTPUT: {RESET} {wrapped}\n")
            speak(str(response))
        except KeyboardInterrupt:
            exit(0)
        except Exception as e:
            result = subprocess.run(
                ["ollama", "run", "rebel"],
                input=question,
                capture_output=True,
                text=True,
                env={**os.environ, "OLLAMA_NOHISTORY": "1"}
            )
            import textwrap
            wrapped = textwrap.fill(result.stdout.strip(), width=72)
            print(f"\n{RED}Output:{RESET} {wrapped}\n")
            speak(result.stdout.strip())

# ─── Smart Multi-Extension Document Opener ────────────────────────────────────
def open_document(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext in ('.pdf', '.docx', '.html', '.htm'):
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            print(f"\n{RED}Error: 'xdg-open' command missing. Cannot launch default application.{RESET}\n")
            
    else:
        terminals = [
            ["x-terminal-emulator", "-e", "nano", file_path],
            ["gnome-terminal", "--", "nano", file_path],
            ["konsole", "-e", "nano", file_path],
            ["xfce4-terminal", "-e", f"nano '{file_path}'"],
            ["xterm", "-e", "nano", file_path]
        ]
        
        launched = False
        for term_cmd in terminals:
            if shutil.which(term_cmd[0]):
                subprocess.Popen(term_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                launched = True
                break
                
        if not launched:
            print(f"\n{RED}Error: Terminal wrapper execution failed. Trying to run nano locally...{RESET}\n")
            subprocess.run(["nano", file_path])

# ─── Filename Scan Mode ───────────────────────────────────────────────────────
if RAG_MODE == "scan":
    signal.signal(signal.SIGINT, handle_query_exit)
    r("\nReady to Scan Filenames! Type a keyword (ctrl+c to quit)\n")
    while True:
        try:
            keyword = input(f"{RED}Search Keyword: {RESET}").strip()
            if not keyword:
                continue
                
            keyword_lower = keyword.lower()
            matches = []
            
            for root, dirs, files in os.walk(DOCS_PATH):
                for file in files:
                    if keyword_lower in file.lower():
                        full_path = os.path.join(root, file)
                        matches.append((file, root, full_path))
            
            if not matches:
                print("")
                r(f"No books found matching '{keyword}'.\n")
                continue
                
            matches.sort(key=lambda x: x[0])
            
            try:
                term_width = os.get_terminal_size().columns
            except OSError:
                term_width = 120
            
            max_idx_w = max(len(str(len(matches))), 2)
            max_file_w = max(len(m[0]) for m in matches)
            max_path_w = max(len(m[1]) for m in matches)
            
            max_file_w = max(max_file_w, 9)
            max_path_w = max(max_path_w, 13)
            
            available_w = term_width - max_idx_w - 10
            if (max_file_w + max_path_w) > available_w:
                max_file_w = int(available_w * 0.45)
                max_path_w = int(available_w * 0.55)

            sep_line = f"+-{'-'*max_idx_w}-+-{'-'*max_file_w}-+-{'-'*max_path_w}-+"
            header_line = f"| {'#'.ljust(max_idx_w)} | {'FILE NAME'.ljust(max_file_w)} | {'DIRECTORY LOCATION'.ljust(max_path_w)} |"
            
            print("\n" + RED + sep_line)
            print(header_line)
            print(sep_line + RESET)
            
            for index, (filename, folderpath, fullpath) in enumerate(matches, start=1):
                idx_str = str(index).ljust(max_idx_w)
                
                if len(filename) > max_file_w:
                    file_str = (filename[:max_file_w-3] + "...").ljust(max_file_w)
                else:
                    file_str = filename.ljust(max_file_w)
                    
                if len(folderpath) > max_path_w:
                    path_str = (folderpath[:max_path_w-3] + "...").ljust(max_path_w)
                else:
                    path_str = folderpath.ljust(max_path_w)
                
                print(f"{RED}|{RESET} {idx_str} {RED}|{PURPLE} {file_str} {RED}|{GREEN} {path_str} {RED}|{RESET}")
                
            r(sep_line + "\n")
            
            while True:
                selection = input(f"{RED}Enter # to open with default app/Nano (or Press Enter to skip): {RESET}").strip()
                if not selection:
                    break
                
                if selection.isdigit():
                    sel_idx = int(selection)
                    if 1 <= sel_idx <= len(matches):
                        chosen_file_path = matches[sel_idx - 1][2]
                        print(f"\nLaunching target asset: {PURPLE}{matches[sel_idx - 1][0]}{RESET}\n")
                        open_document(chosen_file_path)
                        break
                    else:
                        r(f"Invalid selection. Choose a number between 1 and {len(matches)}.")
                else:
                    r("Please input a valid numeric index character.")
            
        except KeyboardInterrupt:
            r("\nTerminating Session... \n")
            exit(0)

# ─── Query only mode ──────────────────────────────────────────────────────────
if RAG_MODE == "query":
    if not os.path.exists(CHROMA_PATH):
        r("No index found. Please run indexing first.")
        exit()
    index, _ = get_index()
    run_query_engine(index)
    exit()

# ─── Gather files ─────────────────────────────────────────────────────────────
signal.signal(signal.SIGINT, handle_shutdown)
check_disk_space()

all_files = []
skipped_size = 0
skipped_type = 0
for root, dirs, files in os.walk(DOCS_PATH):
    for file in files:
        full_path = os.path.join(root, file)
        if not file.lower().endswith(SUPPORTED_EXTENSIONS):
            skipped_type += 1
            continue
        size_mb = os.path.getsize(full_path) / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            skipped_size += 1
            log_skipped([full_path], f"File size {size_mb:.1f}MB exceeds {MAX_FILE_SIZE_MB}MB limit")
            continue
        all_files.append(full_path)

# FIX: Swapped os.getsize(x) to os.path.getsize(x) to prevent module crash
all_files.sort(key=lambda x: os.path.getsize(x))
indexed_files = load_checkpoint()
remaining_files = [f for f in all_files if f not in indexed_files]

total_files = len(all_files)
total_batches = (len(remaining_files) + BATCH_SIZE - 1) // BATCH_SIZE
already_done = len(indexed_files)

r(f"Found {total_files} files to index.")
r(f"Already indexed: {already_done} files.")
r(f"Remaining: {len(remaining_files)} files | {total_batches} batches.\n")

r("Loading index...")
index, storage_context = get_index()
r("Index loaded.\n")

completed = 0
for i in range(0, len(remaining_files), BATCH_SIZE):
    if shutdown_requested:
        r("\nStopped safely. Progress has been saved.\n")
        exit(0)

    batch = remaining_files[i:i + BATCH_SIZE]
    overall_progress(completed, total_batches)

    try:
        documents = []
        for file in batch:
            docs = SimpleDirectoryReader(input_files=[file]).load_data()
            docs = split_plaintext_docs(docs, file)
            documents += docs

        for doc in documents:
            index.insert(doc)

        indexed_files.update(batch)
        save_checkpoint(indexed_files)
        completed += 1

    except Exception as e:
        r(f"\nBatch error: {e} — retrying individually...")
        for file in batch:
            try:
                docs = SimpleDirectoryReader(input_files=[file]).load_data()
                docs = split_plaintext_docs(docs, file)
                for doc in docs:
                    index.insert(doc)
                indexed_files.add(file)
                save_checkpoint(indexed_files)
                r(f"  Recovered: {os.path.basename(file)}")
            except Exception as e2:
                r(f"  Failed: {os.path.basename(file)} — {e2}")
                log_skipped([file], str(e2))
        completed += 1
        continue

overall_progress(completed, total_batches)
r(f"\n\nAll done! {len(indexed_files)} files indexed.")
r(f"Check {LOG_FILE} for any skipped files.\n")

if RAG_MODE == "index":
    exit(0)

run_query_engine(index)
