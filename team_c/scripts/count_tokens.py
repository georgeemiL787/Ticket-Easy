"""Count prompt tokens for recorded Ollama chat messages with a Qwen tokenizer (diagnostic only).

Renders the served qwen3 template for a system+user chat with think=false and no tools
(`ollama show qwen3:8b --template`). Requires the `tokenizers` package, which is not a project
dependency: uv run --no-project --with tokenizers python scripts/count_tokens.py TOKENIZER MESSAGES
"""
import json
import sys
from tokenizers import Tokenizer

tokenizer = Tokenizer.from_file(sys.argv[1])
count = lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
for system, user in ((m[0]["content"], m[1]["content"]) for m in json.loads(open(sys.argv[2], encoding="utf-8").read())):
    prompt = f"<|im_start|>system\n\n{system}<|im_end|>\n<|im_start|>user\n{user} /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    total, text = count(prompt), count(system) + count(user)
    print(json.dumps(dict(prompt_tokens=total, system_tokens=count(system), user_tokens=count(user), template_tokens=total - text,
                          prompt_text_bytes=len(prompt.encode()), bytes_per_token=round(len(prompt.encode()) / total, 2))))
