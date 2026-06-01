#!/bin/bash

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
RESET='\033[0m'

# ─── Skull ASCII Art ──────────────────────────────────────────────────────────
SKULL='                          ::x.
                    <!X!!!!HMM$$$$W.
               ---!H8MMH?M$$$$$$$$$8X.
              -<!!!MMM$$$$$$$$$$$$$$$$X!:           
            !----!!M?M$$$$$$$$$$$$$$$$MM!!
          '"'"'<M!  !!!MMM$$$$$$$$$$$$$$$MMMX!X!
           !M!--!!!MMM$$$$$$$$$$$$$$$MMM!X$!    
           -!8!:!!!MMMMM$$$$$$$$$$$$RMMMX8RX-               
         <!-!$X!-!!!MHM$$$$$$$$$$$RMMMMMM$!!!
         !!:-MRX!-!!MM$$$$8$$$$$$$$$MMMM$R!-!       
        '"'"'!X:--?X!--!!!M$$$RMR$$$$RMMRMM$R!!!X!     
         -XX  '"'"'MX:!!!!!?RRMMMMM!!XMMMM$R-<!!!!   
          !?!-X$P"````----!M!!---`#*R$$M !<!!-     
          -!MXf        -!-!!!X!        "k!!!-              
          '"'"'!!!X         !X!M?X         '"'"'!!!-
          -<!XM         X!!R!!         !M!-'"'"'
          :!XMMX  : ::s@---!!!Mbx:!!<::X8k !       
         !!!$$$MTMM8$#!!   ! MXX!R$W86SW$$!!!     
          !!!M$$#TT!!!!!-  !  X!!!!!!RR#M!!!          
           `!MW$M- -!!M!!  !  !!:!-- #$R?!!
            -:..    -XM!k:!#hHMX!!-    ::-        
             -M!   <!!!$X?XMMMB$!!!   !!
              !X!  !XR!$MM$$$$$$?MM! '"'"'!!         
              `MX  '"'"'XXX t!@!H!X8X    '"'"'!>         
               !!X!!X!" MM$M$RR*?.!!:X!            
               ?&M<!X>!MR$M> M9M5M!!XMM                
                M!?XX!!RRt?M@NRX?!XMX!R             
                 `!!MHXX!!Mt!MMXWMM!!!            
                   `!XM$$$R9M$RTMMX-               
                     #$WXXW$$MXW$"              
                        `""!""`'

# ─── Typewriter print function ────────────────────────────────────────────────
typewriter() {
    while IFS= read -r line; do
        echo -e "${RED}${line}${RESET}"
        sleep 0.04
    done <<< "$1"
}

# ─── TTS function ─────────────────────────────────────────────────────────────
speak() {
    local text="$1"
    local model="$HOME/rag-project/voices/en_GB-cori-high.onnx"
    if [ ! -f "$model" ]; then return; fi
    echo "$text" | "$HOME/rag-project/rag-env/bin/python" -m piper \
        --model "$model" \
        --output-raw 2>/dev/null | aplay -r 22050 -f S16_LE -t raw - 2>/dev/null
}

# ─── Typewriter response (character by character) ────────────────────────────
typewriter_response() {
    local text="$1"
    local delay="${2:-0.04}"
    # Wrap at 72 characters on word boundaries, then colorize with lolcat
    local colored
    colored=$(echo "$text" | fold -s -w 72 | lolcat -f 2>/dev/null || echo "$text" | fold -s -w 72)
    echo -ne "${RED}OUTPUT: ${RESET}"
    # Use Python to print char-by-char while keeping ANSI escape sequences atomic
    echo "$colored" | "$HOME/rag-project/rag-env/bin/python" -c "
import sys, time
text = sys.stdin.read()
delay = float('$delay')
i = 0
while i < len(text):
    if text[i] == '\x1b':
        # Print entire ANSI escape sequence as one atomic unit (no delay)
        j = i + 1
        while j < len(text) and not text[j].isalpha():
            j += 1
        sys.stdout.write(text[i:j+1])
        sys.stdout.flush()
        i = j + 1
    else:
        sys.stdout.write(text[i])
        sys.stdout.flush()
        if text[i] not in '\n\r':
            time.sleep(delay)
        i += 1
" 2>/dev/null
    echo ""
}

# ─── Chat with TTS ────────────────────────────────────────────────────────────
chat_with_tts() {
    trap 'stty echoctl 2>/dev/null; stty -echoctl 2>/dev/null; echo ""; trap - INT; return' INT
    while true; do
        echo -ne "${RED}INPUT: ${RESET}"
        IFS= read -r user_input || { echo ""; break; }
        [ -z "$user_input" ] && continue
        response=$(echo "$user_input" | OLLAMA_NOHISTORY=1 ollama run rebel 2>/dev/null)
        clean=$(echo "$response" | "$HOME/rag-project/rag-env/bin/python" -c "
import sys, re

text = sys.stdin.read()
# Strip all ANSI escape codes
text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
# Strip markdown code fences
text = re.sub(r'\`\`\`[a-zA-Z]*', '', text)
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
print(result)
" 2>/dev/null)
        # Start TTS in background, typewriter in foreground — both run simultaneously
        speak "$clean" &
        local tts_pid=$!
        typewriter_response "$clean" 0.04
        # Wait for TTS to finish if it's still speaking after typewriter completes
        wait $tts_pid 2>/dev/null
    done
}


# ─── Suppress ^C terminal echo globally ──────────────────────────────────────
stty -echoctl 2>/dev/null

while true; do
clear
echo -ne "\033]0;REBEL\007"
typewriter "$SKULL"
echo ""
echo -e "${RED}  [1] CHAT${RESET}"
echo -e "${RED}  [2] QUERY INDEXED FILES${RESET}"
echo -e "${RED}  [3] CONTINUE INDEXING FILES${RESET}"
echo -e "${RED}  [4] SCAN CERTAIN FILENAMES ONLY${RESET}"
echo -e "${RED}  [5] EXIT${RESET}"
echo ""
echo -ne "${RED}  Choose mode: ${RESET}"
read -r mode

clear
echo -ne "\033]0;REBEL\007"
typewriter "$SKULL"
echo ""

case $mode in
    1)
        echo -e "${RED}                        [CHAT]${RESET}"
        echo ""
        chat_with_tts
        ;;
    2)
        echo -e "${RED}                 [QUERY INDEXED FILES]${RESET}"
        echo ""
        cd ~/rag-project
        source rag-env/bin/activate
        RAG_MODE=query RAG_MODEL="mannix/llama3-8b-ablitered-v3" python rag.py
        ;;
    3)
        echo -e "${RED}                   [INDEXING MODE]${RESET}"
        echo ""
        cd ~/rag-project
        source rag-env/bin/activate
        RAG_MODE=index RAG_MODEL="mannix/llama3-8b-ablitered-v3" python rag.py
        ;;
    4)
        echo -e "${RED}              [SCAN CERTAIN FILENAMES ONLY]${RESET}"
        echo ""
        cd ~/rag-project
        source rag-env/bin/activate
        RAG_MODE=scan python rag.py
        ;;
    5)
        echo -e "${RED}                 [TERMINATING SESSION]${RESET}"
        echo ""
        sleep 1
        exit 0
        ;;
    *)
        echo -e "${RED}                        [CHAT]${RESET}"
        echo ""
        chat_with_tts
        ;;
esac

done
