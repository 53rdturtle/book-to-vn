"""Centralized API call logger for all Gemini interactions.

Records prompt, response summary, token usage, and cost for every API call.
Written to the build logs directory at the end of a pipeline run.
"""
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

_FALLBACK_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "prompts"

# Pricing per 1M tokens (USD). Override via env if needed.
_PRICING = {
    "gemini-3-flash-preview": {"input": 0.10, "output": 0.40},
    "gemini-3.1-flash-image-preview": {"input": 0.10, "output": 0.40},
}
_DEFAULT_PRICING = {"input": 0.10, "output": 0.40}


@dataclass
class ApiCall:
    timestamp: float
    model: str
    call_type: str  # timeline, splitter, visual_desc, image_baseline, image_char, image_bg
    prompt_summary: str
    response_summary: str
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    cost_usd: float


@dataclass
class ApiLog:
    calls: list[ApiCall] = field(default_factory=list)

    def record(
        self,
        model: str,
        call_type: str,
        prompt_summary: str,
        response_summary: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_s: float,
    ) -> ApiCall:
        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        cost = (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000
        entry = ApiCall(
            timestamp=time.time(),
            model=model,
            call_type=call_type,
            prompt_summary=prompt_summary,
            response_summary=response_summary,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            elapsed_s=elapsed_s,
            cost_usd=cost,
        )
        self.calls.append(entry)
        return entry

    def write(self, out_dir: Path) -> None:
        log_dir = out_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        rows = [asdict(c) for c in self.calls]
        (log_dir / "api_calls.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        total_input = sum(c.input_tokens for c in self.calls)
        total_output = sum(c.output_tokens for c in self.calls)
        total_cost = sum(c.cost_usd for c in self.calls)
        total_time = sum(c.elapsed_s for c in self.calls)

        lines = [
            "=" * 60,
            "API USAGE SUMMARY",
            "=" * 60,
            f"Total calls:         {len(self.calls)}",
            f"Total input tokens:  {total_input:,}",
            f"Total output tokens: {total_output:,}",
            f"Total tokens:        {total_input + total_output:,}",
            f"Total API time:      {total_time:.2f}s",
            f"Total cost (est):    ${total_cost:.4f}",
            "",
            "-" * 60,
            "BREAKDOWN BY CALL TYPE",
            "-" * 60,
        ]

        by_type: dict[str, list[ApiCall]] = {}
        for c in self.calls:
            by_type.setdefault(c.call_type, []).append(c)

        for ctype, calls in sorted(by_type.items()):
            inp = sum(c.input_tokens for c in calls)
            out = sum(c.output_tokens for c in calls)
            cost = sum(c.cost_usd for c in calls)
            lines.append(f"  {ctype}: {len(calls)} calls, {inp:,} in / {out:,} out, ${cost:.4f}")

        lines.append("")
        lines.append("-" * 60)
        lines.append("INDIVIDUAL CALLS")
        lines.append("-" * 60)

        for i, c in enumerate(self.calls, 1):
            lines.append(
                f"  [{i}] {c.call_type} ({c.model}) "
                f"{c.input_tokens:,} in / {c.output_tokens:,} out "
                f"${c.cost_usd:.4f} ({c.elapsed_s:.2f}s)"
            )
            lines.append(f"      prompt: {c.prompt_summary[:120]}")
            lines.append(f"      response: {c.response_summary[:120]}")

        lines.append("")
        (log_dir / "api_usage.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        print(f"\n{'=' * 50}")
        print(f"API Usage: {len(self.calls)} calls, "
              f"{total_input + total_output:,} tokens, "
              f"${total_cost:.4f} est. cost")
        print(f"{'=' * 50}")


# Module-level singleton so all modules can record to the same log.
current: ApiLog = ApiLog()

# Output directory for the currently running build. Per-call prompt/response
# dumps from gemini_client are written under <OUT_DIR>/logs/calls/ when set.
OUT_DIR: Path | None = None


def reset() -> None:
    global current
    current = ApiLog()


def set_out_dir(path: Path | None) -> None:
    global OUT_DIR
    OUT_DIR = Path(path) if path else None


def dump_call(call_type: str, prompt: str, response: str, note: str = "") -> Path:
    """Write a prompt/response pair to ``<OUT_DIR>/logs/calls/`` for inspection.

    Shared by text-LLM callers (gemini_client) and image callers (nanobanana),
    so every external API request has one on-disk record with the exact inputs.
    """
    base = (OUT_DIR / "logs" / "calls") if OUT_DIR else _FALLBACK_LOG_DIR
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = base / f"{ts}_{call_type}.txt"
    header = (
        f"# call_type={call_type}\n"
        f"# timestamp={ts}\n"
        f"# note={note}\n"
        f"# prompt_length={len(prompt)}\n"
        f"# response_length={len(response)}\n"
        f"--- PROMPT ---\n"
    )
    path.write_text(header + prompt + "\n--- RESPONSE ---\n" + response + "\n", encoding="utf-8")
    return path
