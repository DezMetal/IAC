# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright (c) 2026 D-Net Lab <https://lab.dnet.live>
#
# Part of IAC (Integrated Agent Core), created and maintained by D-Net Lab.
# Attribution is required on redistribution; the D-Net Lab name is not
# licensed by Apache-2.0 (see TRADEMARKS.md).
import base64, json, requests, argparse, sys, re, os, time
from pathlib import Path
from typing import Any, Dict, List

def resolve_resource(source, is_list=True):
    """Recursively resolves file pointers or raw data into a flat structure."""
    if not source: return [] if is_list else {}
    final = [] if is_list else {}
    items = source if isinstance(source, list) else [source]
    for item in items:
        resolved = item
        if isinstance(item, str) and os.path.isfile(item):
            with open(item, 'r', encoding="utf-8") as f: resolved = json.load(f)
        if is_list:
            if isinstance(resolved, list): final.extend(resolved)
            else: final.append(resolved)
        else:
            if isinstance(resolved, dict): final.update(resolved)
    return final

_seen_hashes = set()

def get_skip_duplicates_option(config: dict) -> bool:
    # "skip_duplicates" can be under config or config["ai_config"] / config["ai_core"]
    val = config.get("skip_duplicates")
    if val is None:
        # Check inside nested configs if available
        for key in ["ai_config", "ai_core"]:
            if isinstance(config.get(key), dict):
                nested_val = config[key].get("skip_duplicates")
                if nested_val is not None:
                    val = nested_val
                    break
    
    if val is None:
        return True # Default/missing = True/Skip
        
    if isinstance(val, bool):
        return val
        
    if isinstance(val, str):
        return val.lower() not in ("none", "false")
            
    return bool(val)

def get_duplicate_threshold_option(config: dict) -> int:
    val = config.get("duplicate_threshold")
    if val is None:
        for key in ["ai_config", "ai_core"]:
            if isinstance(config.get(key), dict):
                nested_val = config[key].get("duplicate_threshold")
                if nested_val is not None:
                    val = nested_val
                    break
    if val is None:
        return 15 # Default strict threshold
    try:
        return int(val)
    except:
        return 15

def task_encode(key: str, info: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """Universal encoder for image/binary resources.
    Supports URLs, file paths, and raw bytes."""
    sources = []
    
    # 1. Direct raw bytes check (e.g. from some other process)
    if info.get("raw_bytes") and isinstance(info["raw_bytes"], bytes):
        info["encoded"] = base64.b64encode(info["raw_bytes"]).decode('utf-8')
        return True

    # 2. Gather potential sources from 'src'
    if info.get("src"):
        if isinstance(info["src"], list):
            sources.extend([s for s in info["src"] if isinstance(s, str)])
        elif isinstance(info["src"], str):
            sources.append(info["src"])

    # 3. Automatically detect other image sources in common keys or by pattern
    is_image_key = any(x in str(key).lower() for x in ("image", "photo", "pic", "src"))
    for k, v in info.items():
        if is_image_key or k in ("links", "images", "files", "data") or "image" in k.lower() or "src" in k.lower() or "photo" in k.lower() or "pic" in k.lower():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, str) and (item.startswith("http") or os.path.exists(item)):
                        if item not in sources: sources.append(item)
            elif isinstance(v, str) and (v.startswith("http") or os.path.exists(v)):
                if v not in sources: sources.append(v)

    # 1.5 Direct file path check on key or payload_key/key from info
    workspace_dir = config.get("workspace_dir") or config.get("workspace")
    for candidate in [key, info.get("payload_key"), info.get("key")]:
        if isinstance(candidate, str):
            if os.path.isfile(candidate):
                if candidate not in sources:
                    sources.append(candidate)
            elif workspace_dir:
                joined = os.path.join(workspace_dir, candidate)
                if os.path.isfile(joined):
                    if joined not in sources:
                        sources.append(joined)

    if not sources: return False

    # 4. Check if already encoded
    if info.get("encoded"):
        current_encoded = info["encoded"]
        if isinstance(current_encoded, list) and len(current_encoded) == len(sources):
            return True
        if isinstance(current_encoded, str) and len(sources) == 1:
            return True

    encoded_list = []
    s_time = time.time()

    for src in sources:
        try:
            if src.startswith("http"):
                print(f"    [FETCH] Downloading: {src[:50]}...")
                res = requests.get(src, timeout=30)
                res.raise_for_status()
                image_bytes = res.content
            elif os.path.exists(src):
                with open(Path(src), "rb") as f:
                    image_bytes = f.read()
            else:
                continue

            encoded_list.append(base64.b64encode(image_bytes).decode('utf-8'))
        except Exception as e:
            print(f"    [!] Encode Error [{key}] for {src}: {e}")

    if not encoded_list:
        return False

    if len(encoded_list) == 1:
        info["encoded"] = encoded_list[0]
    else:
        info["encoded"] = encoded_list

    print(f"    [ENCODE] Success: {key} ({len(encoded_list)} images) in {time.time()-s_time:.2f}s")
    return True

def load_env_file():
    # Look for .env, .vnv, vnv in current directory or parents
    search_names = [".env", ".vnv", "vnv"]
    curr = Path(os.getcwd()).resolve()
    for _ in range(5):  # search up to 5 levels up
        for name in search_names:
            env_path = curr / name
            if env_path.is_file():
                try:
                    with open(env_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                k, v = line.split("=", 1)
                                k = k.strip()
                                v = v.strip()
                                # Strip optional quotes
                                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                                    v = v[1:-1]
                                if k and k not in os.environ:
                                    os.environ[k] = v
                    return True
                except:
                    pass
        if curr.parent == curr:
            break
        curr = curr.parent
    return False

def call_gemini_api(prompt: str, images: List[str] = None, model: str = "gemini-1.5-flash", api_key: str = None, config: dict = None) -> str:
    load_env_file()
    if not api_key:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEM_API_KEY")
    if not api_key:
        raise ValueError("Google API Key or Gemini API Key must be set in environment variables or config.")

    # Strip gemini: prefix if present (e.g. gemini:gemini-1.5-flash)
    if model.startswith("gemini:"):
        model = model.split(":", 1)[1]

    try:
        import google.generativeai as genai
    except ImportError:
        raise ImportError("Please install the google-generativeai library (e.g. pip3 install google-generativeai)")

    genai.configure(api_key=api_key)

    contents = []
    if images:
        if isinstance(images, str): images = [images]
        for img_b64 in images:
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            contents.append({
                "mime_type": "image/jpeg",
                "data": base64.b64decode(img_b64)
            })
    contents.append(prompt)

    generation_config = {}
    if config:
        temp = config.get("temperature") or config.get("temp")
        if temp is not None:
            generation_config["temperature"] = float(temp)
        max_tokens = config.get("max_tokens") or config.get("max_output_tokens")
        if max_tokens is not None:
            generation_config["max_output_tokens"] = int(max_tokens)

    generative_model = genai.GenerativeModel(model_name=model)
    
    retries = 4
    delay = 2.0
    for attempt in range(retries):
        try:
            response = generative_model.generate_content(
                contents,
                generation_config=genai.types.GenerationConfig(**generation_config) if generation_config else None
            )
            return response.text.strip()
        except Exception as e:
            if attempt < retries - 1:
                print(f"    [AI-API] Request exception: {e}. Retrying in {delay:.1f}s (Attempt {attempt+1}/{retries})...")
                time.sleep(delay)
                delay *= 2
                continue
            raise e

def call_openai_api(prompt: str, images: List[str] = None, model: str = "gpt-4o", api_key: str = None, config: dict = None) -> str:
    host = config.get("host") if config else None
    is_local = host and host.startswith("http") and "api.openai.com" not in host
    
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "local-key" if is_local else None)
    if not api_key:
        raise ValueError("OpenAI API Key must be set in environment variables or config.")
    
    if is_local:
        base_url = host.rstrip("/")
        if base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        else:
            url = f"{base_url}/v1/chat/completions"
    else:
        url = "https://api.openai.com/v1/chat/completions"
        
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    content = [{"type": "text", "text": prompt}]
    if images:
        if isinstance(images, str): images = [images]
        for img_b64 in images:
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{img_b64}"
                }
            })
        
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}]
    }
    
    if config:
        temp = config.get("temperature") or config.get("temp")
        if temp is not None:
            payload["temperature"] = float(temp)
            
    res = requests.post(url, json=payload, headers=headers, timeout=90)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"].strip()

def call_anthropic_api(prompt: str, images: List[str] = None, model: str = "claude-3-5-sonnet-20241022", api_key: str = None, config: dict = None) -> str:
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("Anthropic API Key must be set in environment variables or config.")
    
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }
    
    content = []
    if images:
        if isinstance(images, str): images = [images]
        for img_b64 in images:
            if "," in img_b64:
                img_b64 = img_b64.split(",", 1)[1]
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_b64
                }
            })
    content.append({"type": "text", "text": prompt})
        
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": content}]
    }
    
    if config:
        temp = config.get("temperature") or config.get("temp")
        if temp is not None:
            payload["temperature"] = float(temp)
            
    res = requests.post(url, json=payload, headers=headers, timeout=90)
    res.raise_for_status()
    return res.json()["content"][0]["text"].strip()

def task_ai_process(key: str, info: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """Core AI processing unit. Handles auto-encoding, vision detection,
    and multi-provider inference."""
    # --- AUTO-DETECTION & RESTORED DEPENDENCY CHECK ---
    is_vision = config.get("vision") is True or info.get("vision") is True

    # Auto-detect vision if not explicitly set
    if not is_vision:
        is_img_ext = isinstance(key, str) and any(key.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"))
        if info.get("src") or info.get("encoded") or is_img_ext or any(x in str(key).lower() for x in ("image", "photo", "pic", "src")):
            is_vision = True
        else:
            for k, v in info.items():
                if k in ("links", "images", "files", "data") or "image" in k.lower() or "src" in k.lower() or "photo" in k.lower() or "pic" in k.lower():
                    if isinstance(v, list) and any(isinstance(x, str) and (x.startswith("http") or os.path.exists(x)) for x in v):
                        is_vision = True; break
                    if isinstance(v, str) and (v.startswith("http") or os.path.exists(v)):
                        is_vision = True; break

    if is_vision and not info.get("encoded"):
        if not task_encode(key, info, config):
            if config.get("vision") is True or info.get("vision") is True:
                print(f"    [!] AI Aborted: Vision requested but encoding failed for {key}")
                return False

    prompt = info.get("prompt") or config.get("prompt", "Analyze this.")

    encoded = info.get("encoded")
    if isinstance(encoded, list):
        images = encoded
    elif isinstance(encoded, str):
        images = [encoded]
    else:
        images = []

    has_images = len(images) > 0
    target_model = config.get("vision_model") if has_images and "vision_model" in config else config.get("model")
    
    if has_images and "vision_provider" in config:
        provider = config.get("vision_provider", "").lower()
    else:
        provider = config.get("provider", "").lower()
    
    is_gemini = (provider == "gemini") or (target_model and (target_model.startswith("gemini") or "gemini" in target_model.lower()))
    is_openai = (provider == "openai") or (target_model and (target_model.startswith("gpt") or "openai" in target_model.lower()))
    is_anthropic = (provider == "anthropic") or (target_model and (target_model.startswith("claude") or "anthropic" in target_model.lower()))

    try:
        if is_gemini:
            print(f"    [AI-REQ] Task: {key} | Gemini Vision: {has_images} ({len(images)} imgs)")
            api_key = config.get("api_key") or config.get("gemini_api_key")
            s_time = time.time()
            out = call_gemini_api(prompt, images, target_model or "gemini-1.5-flash", api_key, config)
            print(f"    [AI-RES] Received in {time.time()-s_time:.2f}s")
        elif is_openai:
            print(f"    [AI-REQ] Task: {key} | OpenAI Vision: {has_images} ({len(images)} imgs)")
            api_key = config.get("api_key") or config.get("openai_api_key")
            s_time = time.time()
            out = call_openai_api(prompt, images, target_model or "gpt-4o", api_key, config)
            print(f"    [AI-RES] Received in {time.time()-s_time:.2f}s")
        elif is_anthropic:
            print(f"    [AI-REQ] Task: {key} | Anthropic Vision: {has_images} ({len(images)} imgs)")
            api_key = config.get("api_key") or config.get("anthropic_api_key")
            s_time = time.time()
            out = call_anthropic_api(prompt, images, target_model or "claude-3-5-sonnet-20241022", api_key, config)
            print(f"    [AI-RES] Received in {time.time()-s_time:.2f}s")
        else:
            host = config.get('host', 'http://127.0.0.1:11434')
            ai_options = {"temperature": config.get("temp", 0.2)}
            if config.get("options"):
                ai_options.update(config["options"])

            extras = {}
            for key_extra in ["think", "stream"]:
                if key_extra in config:
                    extras[key_extra] = config[key_extra]

            print(f"    [AI-REQ] Task: {key} | Ollama Vision: {has_images}")
            
            final_prompt = prompt
            if info.get("input_data"):
                final_prompt += f"\n\nContext Data:\n{info['input_data']}"
            if config.get("schema"):
                final_prompt += f"\n\nResponse must strictly adhere to this JSON schema: {json.dumps(config['schema'])}"

            if config.get("debug"):
                print(f"    [DEBUG] Options: {json.dumps(ai_options)}")
                print(f"    [DEBUG] Prompt: {final_prompt}")
            
            max_retries = config.get("retries", 3)
            attempt = 0
            out = ""
            
            try:
                from inference import ollama_request
            except ImportError:
                from .inference import ollama_request

            while attempt < max_retries:
                s_time = time.time()
                out = ollama_request(
                    host=host,
                    model=target_model,
                    prompt=final_prompt,
                    images=images,
                    options=ai_options,
                    extras=extras if extras else None,
                    timeout=config.get("ai_timeout", 90)
                )
                print(f"    [AI-RES] Received in {time.time()-s_time:.2f}s")
                
                if out: 
                    break
                    
                attempt += 1
                if attempt < max_retries:
                    print(f"    [!] AI returned blank. Retrying ({attempt}/{max_retries})...")
                    time.sleep(1)

        # Data registration
        o_key = config.get("output_key", "raw_output")
        if config.get("debug"):
            print(f"    [DEBUG] AI Raw (Len: {len(out)}): {out[:200]}...")

        match = re.search(r'\{.*\}', out, re.DOTALL)
        if match: 
            try:
                info.update(json.loads(match.group()))
            except:
                info[o_key] = out
        else: 
            info[o_key] = out
        
        # --- SANITIZATION PROTOCOL ---
        drop_list = config.get("drop", ["encoded"])
        if isinstance(drop_list, str):
            drop_list = [d.strip() for d in drop_list.split(",")]

        for d_key in drop_list:
            if d_key in info:
                info.pop(d_key)
        
        return True
    except Exception as e:
        print(f"    [!] AI Error: {e}")
        return False


def _clean_ai_output(text: str, fmt: str = "auto") -> str:
    """Strip markdown fences and, for JSON output, extract and normalise the object.

    fmt:
        "auto" — infer from the payload shape (default)
        "json" — force JSON extraction; error surfaces if it will not parse
        any other value (html/css/text/md) — fences only, contents untouched

    JSON extraction is deliberately NOT applied to non-JSON output. The greedy
    `{...}` scan will happily match a JSON-LD block inside an HTML document and
    replace the entire page with a reformatted fragment, silently destroying a
    generated site. Only text that actually opens as a JSON value is treated
    as JSON.
    """
    cleaned = text.strip()

    # Strip markdown code fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()

    fmt = (fmt or "auto").lower()
    if fmt not in ("auto", "json"):
        return cleaned

    # Only treat this as JSON when the document itself opens as a JSON value.
    # HTML starts with "<!DOCTYPE"/"<html>", CSS with a selector or ":root" —
    # neither should ever be run through the extractor.
    if not cleaned.startswith(("{", "[")):
        if fmt == "json":
            # Explicitly requested JSON but the model wrapped it in prose.
            json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', cleaned)
            if json_match:
                try:
                    return json.dumps(json.loads(json_match.group(1)), indent=2)
                except json.JSONDecodeError:
                    pass
        return cleaned

    try:
        return json.dumps(json.loads(cleaned), indent=2)
    except json.JSONDecodeError:
        pass

    # Truncated or trailing-prose JSON — recover the outermost balanced value.
    json_match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', cleaned)
    if json_match:
        try:
            return json.dumps(json.loads(json_match.group(1)), indent=2)
        except json.JSONDecodeError:
            pass

    return cleaned


def task_ai_plan(args: dict, payload: dict, config: dict) -> dict:
    """Standalone AI text inference. Sends a prompt to the LLM with optional
    payload context injection and stores the response in the payload."""
    prompt = args.get("prompt", "")
    output_key = args.get("output_key", "ai_plan_result")
    inject_keys = args.get("inject_keys", [])

    # Build context injection from payload
    context_parts = []
    images = []

    if isinstance(inject_keys, str):
        inject_keys_str = inject_keys.strip()
        if inject_keys_str.startswith("{") or inject_keys_str.startswith("["):
            try:
                inject_keys = json.loads(inject_keys_str)
            except Exception:
                pass
        else:
            inject_keys = [k.strip() for k in inject_keys.split(",") if k.strip()]

    drop_list = args.get("drop") or config.get("drop", ["encoded"])
    if isinstance(drop_list, str):
        drop_list = [d.strip() for d in drop_list.split(",")]

    def _clean_drop_keys(item):
        if isinstance(item, dict):
            return {k: _clean_drop_keys(v) for k, v in item.items() if k not in drop_list}
        elif isinstance(item, list):
            return [_clean_drop_keys(i) for i in item]
        return item

    def _process_val(k, val):
        if isinstance(val, dict):
            # Auto-encode images if present
            was_encoded = "encoded" in val
            if (val.get("src") or any(x in k.lower() for x in ("image", "photo", "pic", "src"))) and not val.get("encoded"):
                task_encode(k, val, config)

            if val.get("encoded"):
                if isinstance(val["encoded"], list): images.extend(val["encoded"])
                else: images.append(val["encoded"])

                # If we newly encoded it for this task, clean it up so we don't bloat the payload
                if not was_encoded:
                    val.pop("encoded", None)

        clean_val = _clean_drop_keys(val)
        if isinstance(clean_val, (dict, list)):
            context_parts.append(f"[{k}]\n{json.dumps(clean_val, indent=2)}")
        else:
            context_parts.append(f"[{k}]\n{clean_val}")

    if isinstance(inject_keys, dict):
        for k, v in inject_keys.items():
            val = payload.get(v) if (isinstance(v, str) and v in payload) else v
            _process_val(k, val)
    elif isinstance(inject_keys, list):
        for ik in inject_keys:
            if isinstance(ik, str):
                if ik in payload:
                    _process_val(ik, payload[ik])
                else:
                    context_parts.append(f"[context]\n{ik}")
            elif isinstance(ik, dict):
                for k, v in ik.items():
                    val = payload.get(v) if (isinstance(v, str) and v in payload) else v
                    _process_val(k, val)
            else:
                context_parts.append(f"[context]\n{ik}")

    if context_parts:
        prompt += "\n\n--- SCRAPED DATA ---\n" + "\n\n".join(context_parts)

    ai_options = {"temperature": args.get("temperature", config.get("temp", 0.3))}
    if args.get("options"):
        ai_options.update(args["options"])

    model = args.get("model") or (config.get("vision_model") if images and config.get("vision_model") else config.get("model"))
    
    if images and (args.get("vision_provider") or config.get("vision_provider")):
        provider = args.get("vision_provider", config.get("vision_provider", "")).lower()
    else:
        provider = args.get("provider", config.get("provider", "")).lower()
    is_gemini = (provider == "gemini") or (model and (model.startswith("gemini") or "gemini" in model.lower()))
    is_openai = (provider == "openai") or (model and (model.startswith("gpt") or "openai" in model.lower()))
    is_anthropic = (provider == "anthropic") or (model and (model.startswith("claude") or "anthropic" in model.lower()))

    max_retries = config.get("retries", 3)
    attempt = 0
    out = ""

    try:
        if is_gemini:
            api_key = args.get("api_key") or config.get("api_key") or config.get("gemini_api_key")
            while attempt < max_retries:
                s_time = time.time()
                print(f"    [AI-PLAN] Gemini Model: {model} | Images: {len(images)} | Attempt {attempt+1}/{max_retries}")
                out = call_gemini_api(prompt, images, model or "gemini-1.5-flash", api_key, config)
                print(f"    [AI-PLAN] Received {len(out)} chars in {time.time()-s_time:.2f}s")
                if out:
                    break
                attempt += 1
                if attempt < max_retries:
                    print(f"    [!] AI returned blank. Retrying ({attempt}/{max_retries})...")
                    time.sleep(1)
        elif is_openai:
            api_key = args.get("api_key") or config.get("api_key") or config.get("openai_api_key")
            while attempt < max_retries:
                s_time = time.time()
                print(f"    [AI-PLAN] OpenAI Model: {model} | Images: {len(images)} | Attempt {attempt+1}/{max_retries}")
                out = call_openai_api(prompt, images, model or "gpt-4o", api_key, config)
                print(f"    [AI-PLAN] Received {len(out)} chars in {time.time()-s_time:.2f}s")
                if out:
                    break
                attempt += 1
                if attempt < max_retries:
                    print(f"    [!] AI returned blank. Retrying ({attempt}/{max_retries})...")
                    time.sleep(1)
        elif is_anthropic:
            api_key = args.get("api_key") or config.get("api_key") or config.get("anthropic_api_key")
            while attempt < max_retries:
                s_time = time.time()
                print(f"    [AI-PLAN] Anthropic Model: {model} | Images: {len(images)} | Attempt {attempt+1}/{max_retries}")
                out = call_anthropic_api(prompt, images, model or "claude-3-5-sonnet-20241022", api_key, config)
                print(f"    [AI-PLAN] Received {len(out)} chars in {time.time()-s_time:.2f}s")
                if out:
                    break
                attempt += 1
                if attempt < max_retries:
                    print(f"    [!] AI returned blank. Retrying ({attempt}/{max_retries})...")
                    time.sleep(1)
        else:
            host = config.get("host", "http://127.0.0.1:11434")
            url = f"{host}/api/chat"

            request_payload = {
                "model": model,
                "messages": [{"role": "user", "content": prompt, "images": images or None}],
                "stream": False,
                "options": ai_options
            }

            for key_extra in ["think", "stream"]:
                if key_extra in args:
                    request_payload[key_extra] = args[key_extra]

            while attempt < max_retries:
                s_time = time.time()
                print(f"    [AI-PLAN] Model: {model} | Attempt {attempt+1}/{max_retries}")
                res = requests.post(url, json=request_payload, timeout=config.get("ai_timeout", 90))
                res.raise_for_status()
                out = res.json().get("message", {}).get("content", "").strip()
                print(f"    [AI-PLAN] Received {len(out)} chars in {time.time()-s_time:.2f}s")

                if out:
                    break

                attempt += 1
                if attempt < max_retries:
                    print(f"    [!] AI returned blank. Retrying ({attempt}/{max_retries})...")
                    time.sleep(1)

        if not out:
            print(f"    [!] AI Plan failed: No output after {max_retries} attempts")
            return {"status": "error", "error": "No AI output"}

        # Post-process: strip fences; JSON extraction only when the output is JSON.
        # Callers generating HTML/CSS/text can pin this with args.format.
        cleaned = _clean_ai_output(out, args.get("format", "auto"))
        payload[output_key] = cleaned
        print(f"    [AI-PLAN] Stored {len(cleaned)} chars to '{output_key}'")
        return {"status": "ok", "output_key": output_key, "length": len(cleaned), "message": cleaned}

    except Exception as e:
        print(f"    [!] AI Plan Error: {e}")
        return {"status": "error", "error": str(e)}


def task_ai_batch(payload: dict, config: dict) -> int:
    """Iterates through payload, discards duplicates, and processes all vision-related items."""
    prefix = config.get("prefix", "image_")
    count = 0

    # 1. Duplicate Image Deduplication
    if get_skip_duplicates_option(config):
        try:
            from utils import is_duplicate
        except ImportError:
            try:
                from .utils import is_duplicate
            except ImportError:
                is_duplicate = None

        if is_duplicate:
            seen_hashes = set()
            threshold = get_duplicate_threshold_option(config)
            for key in list(payload.keys()):
                info = payload[key]
                if not isinstance(info, dict): continue

                # Check if matches prefix or has vision indicators
                is_match = key.startswith(prefix)
                if not is_match:
                    if info.get("vision") or info.get("src") or info.get("encoded"):
                        is_match = True
                    else:
                        for k in info.keys():
                            if any(x in k.lower() for x in ("image", "photo", "pic", "src")):
                                is_match = True; break

                if is_match:
                    src = info.get("src")
                    if src:
                        try:
                            if src.startswith("http"):
                                res = requests.get(src, timeout=30)
                                res.raise_for_status()
                                img_bytes = res.content
                            elif os.path.exists(src):
                                with open(Path(src), "rb") as f:
                                    img_bytes = f.read()
                            else:
                                img_bytes = None

                            if img_bytes:
                                is_dup, current_hash = is_duplicate(img_bytes, seen_hashes, threshold=threshold)
                                if is_dup:
                                    print(f"  [DUPLICATE] Discarding duplicate image: {key}")
                                    payload.pop(key, None)
                                    continue
                                else:
                                    # Pre-encode so task_encode/task_ai_process doesn't download it again
                                    info["encoded"] = base64.b64encode(img_bytes).decode('utf-8')
                        except Exception as e:
                            print(f"  [!] Duplicate Check Error for {key}: {e}")

            # Re-index remaining image keys to be sequential
            img_keys = []
            for k in list(payload.keys()):
                if k.startswith(prefix) and k[len(prefix):].isdigit():
                    img_keys.append((int(k[len(prefix):]), k))
            if img_keys:
                img_keys.sort()
                new_images = {}
                for idx, (_, old_key) in enumerate(img_keys):
                    new_images[f"{prefix}{idx}"] = payload[old_key]
                for _, old_key in img_keys:
                    payload.pop(old_key, None)
                payload.update(new_images)

    # 2. Process remaining unique images
    for key in list(payload.keys()):
        info = payload[key]
        if not isinstance(info, dict): continue

        # Check if it matches prefix or has vision indicators
        is_match = key.startswith(prefix)
        if not is_match:
            if info.get("vision") or info.get("src") or info.get("encoded"):
                is_match = True
            else:
                for k in info.keys():
                    if any(x in k.lower() for x in ("image", "photo", "pic", "src")):
                        is_match = True; break

        if is_match:
            print(f"  [*] Batch Processing: {key}")
            if task_ai_process(key, info, config):
                count += 1
    return count

TASK_REGISTRY = {"encode": task_encode, "ai_process": task_ai_process}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?")
    parser.add_argument("-c", "--config")
    parser.add_argument("-t", "--tasks", nargs="+")
    parser.add_argument("-o", "--output")
    parser.add_argument("-p", "--prefix")
    parser.add_argument("-n", "--no-overwrite", action="store_true")
    args = parser.parse_args()

    # Configuration extraction
    config_source = args.config or args.file
    master = {}

    if config_source and os.path.isfile(config_source):
        try:
            with open(config_source, "r", encoding="utf-8") as f: 
                master = json.load(f)
        except: pass
    
    final_cfg = {
        "tasks": ["encode", "ai_process"], "prefix": "image_",
        "host": "http://127.0.0.1:11434", "model": "qwen3.5:4b",
        "prompt": "Analyze.", "drop": [], "ai_timeout": 300
    }

    # Priority: ai_core block > root of file > defaults
    final_cfg.update(master.get("ai_core", master if isinstance(master, dict) else {}))
    
    # CLI Overrides
    if args.tasks: final_cfg["tasks"] = args.tasks
    if args.prefix: final_cfg["prefix"] = args.prefix

    target_input = args.file or master.get("data_payload", {})
    data = resolve_resource(target_input, is_list=False)

    # Reflexive Unwrap: If the input file itself defines a data_payload, resolve that instead.
    if "data_payload" in data:
        data = resolve_resource(data["data_payload"], is_list=False)

    for key in list(data.keys()):
        info = data[key]
        if key.startswith(final_cfg["prefix"]) and isinstance(info, dict):
            # 1. Duplicate Check
            if get_skip_duplicates_option(final_cfg):
                src = info.get("src")
                if src:
                    try:
                        if src.startswith("http"):
                            res = requests.get(src, timeout=30)
                            res.raise_for_status()
                            img_bytes = res.content
                        elif os.path.exists(src):
                            with open(Path(src), "rb") as f:
                                img_bytes = f.read()
                        else:
                            img_bytes = None
                        
                        if img_bytes:
                            from utils import is_duplicate
                            is_dup, current_hash = is_duplicate(img_bytes, _seen_hashes)
                            if is_dup:
                                print(f"  [DUPLICATE] Discarding duplicate image: {key}")
                                data.pop(key, None)
                                continue
                            else:
                                # Pre-encode so task_encode doesn't download it again!
                                info["encoded"] = base64.b64encode(img_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"  [!] Duplicate Check Error for {key}: {e}")

            # 2. Proceed with tasks if not duplicate
            for t_name in final_cfg["tasks"]:
                if t_name in TASK_REGISTRY:
                    if args.no_overwrite and t_name == "ai_process" and "raw_output" in info: continue
                    print(f"  [*] Processing {key}: {t_name}")
                    TASK_REGISTRY[t_name](key, info, final_cfg)

    # Cleanup
    for key, info in data.items():
        if isinstance(info, dict):
            for d_key in final_cfg.get("drop", []): info.pop(d_key, None)

    out = args.output or final_cfg.get("output") or "processed_output.json"
    with open(out, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
    print(f"--- Completed. Saved to {out} ---")

if __name__ == "__main__": main()