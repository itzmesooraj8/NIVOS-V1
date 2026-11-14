import os
import sys
import json
import time
import httpx
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path("src").resolve()))

from nivos.core.config import load_config
from nivos.server.routes import _ensure_identity_prompt, _to_messages, _load_vault_me_md
from nivos.server.models import ChatMessage
from nivos.server.search_policy import prefetch_current_info
from nivos.server.tier_router import TierRouter, estimate_token_count

config = load_config()
tier_router = TierRouter(config)

sample_messages = [
    ("task_42", "What is the capital city of France?"),
    ("task_40", "explain photosynthesis in 3 lines"),
    ("task_41", "write a short email to John thanking him for yesterday's project meeting"),
    ("task_44", "wht is the deepst ocena on erath?"),
    ("task_45", "whats up? what can you do for me?"),
]

print("=== MEASURE FIRST: 5 SAMPLE MESSAGES (BASELINE) ===", flush=True)

results = []

for tid, query in sample_messages:
    # 1. Prefetched search/weather context
    user_loc = getattr(getattr(config, "user", None), "location", "Palakkad, Kerala, India")
    always_search = getattr(getattr(config, "routing", None), "always_search", False)
    prefetched_context, search_type = prefetch_current_info(
        query, default_location=user_loc, always_search=always_search
    )

    # 2. Tier routing
    selected_model, routing_reason, should_send_tools = tier_router.route_query(
        query,
        requested_model="qwen3.5:4b", # Current requested model in baseline
        has_prefetched_data=bool(prefetched_context),
    )

    # 3. Build messages list exactly as server does
    req_messages = [ChatMessage(role="user", content=query)]
    if prefetched_context:
        req_messages[-1].content = f"{req_messages[-1].content}\n\n{prefetched_context}"

    messages = _to_messages(req_messages)
    messages = _ensure_identity_prompt(messages, config)

    # Breakdown token counts
    sys_msgs = [m for m in messages if m.role.value == "system"]
    user_msgs = [m for m in messages if m.role.value == "user"]
    hist_msgs = user_msgs[:-1]

    sys_text = "\n\n".join(m.text for m in sys_msgs if m.text)
    sys_tokens = estimate_token_count(sys_text)

    context_header_tokens = estimate_token_count(prefetched_context) if prefetched_context else 0
    hist_tokens = sum(estimate_token_count(m.text) for m in hist_msgs)

    # Tool schemas
    tool_tokens = 0
    tool_count = 0
    tools_payload = None
    if should_send_tools:
        raw_tools = getattr(getattr(config, "tools", None), "enabled", [])
        if isinstance(raw_tools, str):
            enabled_tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        else:
            enabled_tools = list(raw_tools)
        if enabled_tools:
            from nivos.core.registry import ToolRegistry
            tool_objs = []
            for t_name in enabled_tools:
                try:
                    t_cls = ToolRegistry.get(t_name)
                    if t_cls:
                        tool_objs.append(t_cls().spec.to_dict())
                except Exception:
                    pass
            if tool_objs:
                tools_json = json.dumps(tool_objs)
                tool_tokens = estimate_token_count(tools_json)
                tool_count = len(tool_objs)
                tools_payload = tool_objs

    # Call Ollama API exactly as baseline does (no keep_alive override, no think: False)
    ollama_msgs = [{"role": m.role.value, "content": m.text} for m in messages]
    payload = {
        "model": selected_model,
        "messages": ollama_msgs,
        "stream": False,
    }
    if tools_payload:
        payload["tools"] = tools_payload

    t0 = time.time()
    resp = httpx.post("http://127.0.0.1:11434/api/chat", json=payload, timeout=180.0)
    elapsed = time.time() - t0
    data = resp.json()

    load_duration = data.get("load_duration", 0) / 1e9
    prompt_eval_duration = data.get("prompt_eval_duration", 0) / 1e9
    eval_duration = data.get("eval_duration", 0) / 1e9
    total_duration = data.get("total_duration", 0) / 1e9
    prompt_eval_count = data.get("prompt_eval_count", 0)
    eval_count = data.get("eval_count", 0)

    msg_obj = data.get("message", {})
    thinking_present = bool(msg_obj.get("thinking"))
    thinking_len = len(msg_obj.get("thinking", "")) if thinking_present else 0

    item = {
        "id": tid,
        "query": query,
        "model": selected_model,
        "sys_tokens": sys_tokens,
        "context_header_tokens": context_header_tokens,
        "tool_tokens": tool_tokens,
        "tool_count": tool_count,
        "hist_tokens": hist_tokens,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "thinking": thinking_present,
        "thinking_len_chars": thinking_len,
        "load_s": round(load_duration, 3),
        "prompt_eval_s": round(prompt_eval_duration, 3),
        "eval_s": round(eval_duration, 3),
        "total_s": round(total_duration, 3),
        "elapsed_s": round(elapsed, 3),
    }
    print(f"[{tid}] prompt_eval_count={prompt_eval_count} eval_count={eval_count} thinking={thinking_present} load={load_duration:.2f}s prompt_eval={prompt_eval_duration:.2f}s eval={eval_duration:.2f}s elapsed={elapsed:.2f}s", flush=True)
    results.append(item)

with open("evals/baseline_measurements.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

print("\n=== FINAL BASELINE TABLE JSON ===", flush=True)
print(json.dumps(results, indent=2), flush=True)
