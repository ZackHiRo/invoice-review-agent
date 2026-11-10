from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from invoice_agent.synth.world import BUYER


@dataclass(frozen=True)
class Settings:
    model: str
    price_in: float    # USD per 1M input tokens
    price_out: float   # USD per 1M output tokens
    state_dir: Path
    data_dir: Path
    buyer_vat_id: str

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.price_in + output_tokens * self.price_out) / 1_000_000


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        model=os.getenv("INVOICE_AGENT_MODEL", "gpt-4.1-mini"),
        price_in=float(os.getenv("INVOICE_AGENT_PRICE_IN", "0.40")),
        price_out=float(os.getenv("INVOICE_AGENT_PRICE_OUT", "1.60")),
        state_dir=Path(os.getenv("INVOICE_AGENT_STATE_DIR", ".state")),
        data_dir=Path(os.getenv("INVOICE_AGENT_DATA_DIR", "data")),
        buyer_vat_id=os.getenv("INVOICE_AGENT_BUYER_VAT_ID", BUYER["vat_id"]),
    )


def require_api_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.")
