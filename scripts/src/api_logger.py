"""api_logger.py - Shared API call logging module for the NanoBanana pipeline.

Adapted from the FolioStax (Stax PDFs) project. Creates a self-contained
folder for every API call with the prompt, the exact input images sent, the
full response, and metadata. Each folder is named with a UTC timestamp (via
NTP), an item identifier, and a step name:

    {base_folder}/00_api_call_logs/{timestamp}_{item}_{step}/
        call.json
        prompt.txt
        image_1.jpg ...
        response.json
        response_text.txt
        response_image_1.jpg ...

Usage:
    from src.api_logger import ApiLogger, genai_usage, genai_response_files, parts_image_files

    logger = ApiLogger(book_output_folder)
    logger.log(
        page_number="0001",
        step_name="render",
        attempt=1,
        api="gemini",
        model="gemini-3-pro-image",
        usage=genai_usage(response),
        duration_seconds=42.1,
        status="success",
        files={
            "prompt.txt": prompt,
            **parts_image_files(image_parts),
            **genai_response_files(response),
        },
    )
"""

import json
import mimetypes
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

try:
    import ntplib
    HAS_NTPLIB = True
except ImportError:
    HAS_NTPLIB = False


def _is_existing_file(text):
    """True if the string is a path to an existing file. Unlike Path.is_file,
    never raises on strings too long to be a valid path."""
    try:
        return Path(text).is_file()
    except OSError:
        return False


class ApiLogger:
    """
    Creates per-call log folders under {base_folder}/00_api_call_logs/.

    Each folder is named: {datetime}_{page_number}_{step_name}
    and contains call.json plus copies of all input/output files.
    """

    def __init__(self, base_folder):
        self.logs_folder = Path(base_folder) / "00_api_call_logs"
        self.logs_folder.mkdir(parents=True, exist_ok=True)
        self.ntp_offset = self._compute_ntp_offset()
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # NTP synchronization
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ntp_offset():
        """
        Query an NTP server to compute the offset between local clock and UTC.

        Retries up to 10 times. Returns 0.0 if ntplib is not installed
        (falls back to local clock).
        """
        if not HAS_NTPLIB:
            return 0.0

        ntp_servers = [
            "pool.ntp.org",
            "time.google.com",
            "time.cloudflare.com",
        ]
        client = ntplib.NTPClient()

        for attempt in range(10):
            for server in ntp_servers:
                try:
                    response = client.request(server, timeout=2)
                    return response.offset
                except Exception:
                    continue
            time.sleep(1)

        raise RuntimeError(
            "Failed to reach NTP server after 10 attempts. "
            "Cannot guarantee accurate timestamps."
        )

    def utc_now(self):
        """Get current UTC datetime with NTP offset applied."""
        local_ts = time.time() + self.ntp_offset
        return datetime.fromtimestamp(local_ts, tz=timezone.utc)

    @staticmethod
    def _format_timestamp(dt):
        """Format datetime to filesystem-safe ISO 8601 with milliseconds."""
        # 2026-02-12T20-12-56.458Z
        return dt.strftime("%Y-%m-%dT%H-%M-%S.") + f"{dt.microsecond // 1000:03d}Z"

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def log(self, page_number, step_name, attempt, api, model,
            usage, duration_seconds, status, files=None,
            error_message=None, extra=None):
        """
        Create a log folder for a single API call.

        Args:
            page_number:     Item identifier (e.g., "0001", "wolf", "book")
            step_name:       Pipeline step (e.g., "analyze", "render")
            attempt:         Attempt number (1-based)
            api:             API provider (e.g., "gemini")
            model:           Model name
            usage:           Token usage dict (input_tokens, output_tokens, ...)
            duration_seconds: Call duration
            status:          "success" or "error"
            files:           Dict of filename -> content to save. Values can be:
                             - str that is an existing file path: file is copied
                             - str: saved as text file
                             - dict/list: serialized as JSON
                             - bytes: saved as binary file
            error_message:   Error message (for failed calls)
            extra:           Additional fields to include in call.json
        """
        ts = self.utc_now()
        folder_name = f"{self._format_timestamp(ts)}_{page_number}_{step_name}"
        with self._write_lock:
            folder_path = self.logs_folder / folder_name
            suffix = 1
            while folder_path.exists():
                folder_path = self.logs_folder / f"{folder_name}_{suffix}"
                suffix += 1
            folder_path.mkdir(parents=True, exist_ok=True)

        call_data = {
            "timestamp": ts.isoformat(),
            "page_number": page_number,
            "step": step_name,
            "attempt": attempt,
            "api": api,
            "model": model,
            "usage": usage or {},
            "duration_seconds": round(duration_seconds, 3),
            "status": status,
        }

        if error_message:
            call_data["error_message"] = error_message

        if extra:
            call_data.update(extra)

        with open(folder_path / "call.json", "w", encoding="utf-8") as f:
            json.dump(call_data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        if files:
            for filename, content in files.items():
                file_path = folder_path / filename

                if content is None:
                    continue

                file_path.parent.mkdir(parents=True, exist_ok=True)

                if isinstance(content, (dict, list)):
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(content, f, indent=2, ensure_ascii=False)
                        f.write("\n")

                elif isinstance(content, str) and _is_existing_file(content):
                    shutil.copy2(content, file_path)

                elif isinstance(content, str):
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                        if not content.endswith("\n"):
                            f.write("\n")

                elif isinstance(content, bytes):
                    with open(file_path, "wb") as f:
                        f.write(content)

        return folder_path


def safe_log(logger, files_builder=None, **kwargs):
    """
    Log an API call, but never let a logging problem break the pipeline:
    generation results are worth more than their logs. files_builder is an
    optional zero-argument callable returning the files dict, so that
    failures while assembling the files are also caught.
    """
    if logger is None:
        return None
    try:
        files = files_builder() if files_builder is not None else kwargs.pop("files", None)
        return logger.log(files=files, **kwargs)
    except Exception:
        print("Warning: failed to write API call log:")
        traceback.print_exc()
        return None


# ----------------------------------------------------------------------
# google-genai helpers
# ----------------------------------------------------------------------

def finish_reason(response) -> str | None:
    """The SDK finish_reason, readable even when JSON parsing of the reply failed.

    Same pattern as translate.py: read candidates[0].finish_reason as a string
    so "MAX_TOKENS" is visible after a truncated JSON body.
    """
    if response is None:
        return None
    try:
        candidate = (getattr(response, "candidates", None) or [None])[0]
        finish = str(getattr(candidate, "finish_reason", "") or "")
    except Exception:
        return None
    return finish or None


def genai_usage(response):
    """Extract a token usage dict from a google-genai response."""
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return {}
    usage = {
        "input_tokens": metadata.prompt_token_count,
        "cached_input_tokens": metadata.cached_content_token_count,
        "thinking_tokens": metadata.thoughts_token_count,
        "output_tokens": metadata.candidates_token_count,
        "total_tokens": metadata.total_token_count,
    }
    return {key: value for key, value in usage.items() if value is not None}


def genai_answer_text(response):
    """Concatenate the answer text, dropping the model's reasoning parts.

    Thinking models emit thought parts alongside the answer, and response.text
    splices them together, which corrupts JSON replies.
    """
    chunks = [
        part.text
        for candidate in (response.candidates or [])
        for part in (candidate.content.parts or [])
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    ]
    return "".join(chunks) or (response.text or "")


def genai_finish_reason(response) -> str:
    """The first candidate finish reason, without the SDK enum prefix."""
    for candidate in response.candidates or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None:
            return str(reason).replace("FinishReason.", "")
    return ""


def genai_response_files(response):
    """
    Build a files dict from a google-genai response: the full response as
    response.json (with inline image bytes stripped and saved as separate
    response_image_N files), plus response_text.txt if any text was returned.

    Image bytes are taken directly from the response object rather than
    decoded from the JSON dump, because the SDK's JSON serialization uses a
    base64 variant that is not reliably round-trippable.
    """
    files = {}
    texts = []
    image_count = 0

    for candidate in response.candidates or []:
        parts = candidate.content.parts if candidate.content else None
        for part in parts or []:
            if part.text:
                texts.append(part.text)
            inline = part.inline_data
            if inline is not None and inline.data:
                image_count += 1
                extension = mimetypes.guess_extension(
                    inline.mime_type or "") or ".bin"
                files[f"response_image_{image_count}{extension}"] = inline.data

    dump = response.model_dump(mode="json", exclude_none=True)
    stripped = 0
    for candidate in dump.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inline_data")
            if inline and inline.get("data"):
                stripped += 1
                inline["data"] = (f"<image bytes stripped; "
                                  f"saved as response_image_{stripped}>")

    files["response.json"] = dump
    if texts:
        files["response_text.txt"] = "\n".join(texts)
    return files


def _content_part_payload(part):
    """Kind, bytes-or-text to save, and extra inventory fields for one contents item."""
    if isinstance(part, str):
        return "text", part, {}
    text = getattr(part, "text", None)
    if isinstance(text, str) and text:
        return "text", text, {}
    file_data = getattr(part, "file_data", None)
    if file_data is not None:
        return "file", None, {
            "uri": getattr(file_data, "file_uri", None),
            "mime": getattr(file_data, "mime_type", None),
        }
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None):
        return "inline", inline.data, {
            "mime": getattr(inline, "mime_type", None),
        }
    return "unknown", repr(part), {}


def genai_request_files(
    prompt,
    parts,
    *,
    model,
    thinking,
    schema_name,
    prompt_paths=None,
    attached_paths=None,
    system_instruction=None,
    cached_content=None,
):
    """Exact copies of everything generate_content received, plus a request.json index.

    contents_NN_* files are the parts in send order. Prompt source files are copied
    under prompt_parts/. Local PDFs that were uploaded as File API parts are copied
    beside the URI they were sent as, so a later reader can see the bytes billed.
    """
    files = {}
    inventory = []
    attached = [Path(path) for path in (attached_paths or [])]
    attached_index = 0
    if prompt:
        items = [prompt, *(parts or [])]
    else:
        items = list(parts or [])
    for index, part in enumerate(items, start=1):
        kind, payload, extra = _content_part_payload(part)
        if prompt and index == 1:
            role = "prompt"
        elif kind in {"file", "inline"}:
            role = "attachment"
        else:
            role = "payload" if (not prompt and index == 1) or index == 2 else "text"
        if kind == "text":
            name = (
                f"contents_{index:02d}_prompt.txt"
                if role == "prompt"
                else f"contents_{index:02d}_text.txt"
            )
            files[name] = payload
            row = {
                "index": index,
                "role": role,
                "kind": "text",
                "file": name,
                "chars": len(payload or ""),
            }
        elif kind == "inline":
            mime = extra.get("mime") or "application/octet-stream"
            extension = mimetypes.guess_extension(mime) or ".bin"
            name = f"contents_{index:02d}_inline{extension}"
            files[name] = payload
            row = {
                "index": index,
                "role": role,
                "kind": "inline",
                "file": name,
                "bytes": len(payload or b""),
                "mime": mime,
            }
        else:
            local = None
            if attached_index < len(attached) and attached[attached_index].is_file():
                local = attached[attached_index]
                attached_index += 1
            if local is not None:
                name = f"contents_{index:02d}_{local.name}"
                files[name] = str(local)
                row = {
                    "index": index,
                    "role": role,
                    "kind": "file",
                    "file": name,
                    "bytes": local.stat().st_size,
                    "source": str(local),
                    **{key: value for key, value in extra.items() if value},
                }
            else:
                name = f"contents_{index:02d}_file.json"
                files[name] = {"kind": "file", **extra}
                row = {
                    "index": index,
                    "role": role,
                    "kind": "file",
                    "file": name,
                    **{key: value for key, value in extra.items() if value},
                }
        inventory.append(row)

    prompt_part_rows = []
    for source in prompt_paths or []:
        path = Path(source)
        if not path.is_file():
            continue
        stored = f"prompt_parts/{path.name}"
        if stored in files:
            stored = f"prompt_parts/{path.parent.name}_{path.name}"
        files[stored] = str(path)
        prompt_part_rows.append(
            {
                "file": stored,
                "source": str(path),
                "chars": path.stat().st_size,
            }
        )

    if system_instruction:
        files["system_instruction.txt"] = system_instruction
    files["request.json"] = {
        "model": model,
        "thinking_level": thinking,
        "response_mime_type": "application/json",
        "response_schema": schema_name,
        "contents": inventory,
        "prompt_parts": prompt_part_rows,
        "system_instruction": "system_instruction.txt" if system_instruction else None,
        "cached_content": cached_content,
        "how_to_read": (
            "contents_NN_* files are the generate_content contents array "
            "in send order — that is everything the model was billed for. "
            "prompt_parts/ are the files assembled into contents_01. "
            "Named JSON beside them (english_group.json, arabic_spec.json) "
            "is the same payload split for inspection, not a second send."
        ),
    }
    return files


def parts_image_files(parts):
    """Build a files dict (image_1.jpg, ...) from the exact image parts sent."""
    files = {}
    image_count = 0
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None or inline.data is None:
            continue
        image_count += 1
        extension = mimetypes.guess_extension(inline.mime_type or "") or ".bin"
        files[f"image_{image_count}{extension}"] = inline.data
    return files
