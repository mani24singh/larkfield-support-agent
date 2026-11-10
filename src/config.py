"""Runtime configuration for the Larkfield support-agent service.

All values come from environment variables with safe defaults so the service
starts locally with no secrets configured. Nothing here is ever logged: the
Cadra token is held as a ``SecretStr``.
"""
from __future__ import annotations

import os
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, SecretStr

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    """Immutable, environment-derived service settings."""

    model_config = ConfigDict(frozen=True)

    # --- HTTP ---------------------------------------------------------
    port: int = Field(default=8000, ge=1, le=65535)
    host: str = "0.0.0.0"
    log_level: str = "INFO"

    # --- Data pack (read-only inputs) ---------------------------------
    project_root: Path = _PROJECT_ROOT
    data_dir: Path = _PROJECT_ROOT / "data"
    policies_dir: Path = _PROJECT_ROOT / "policies"
    tickets_dir: Path = _PROJECT_ROOT / "tickets"
    inbox_dir: Path = _PROJECT_ROOT / "inbox"

    # --- Generated state ---------------------------------------------
    runs_dir: Path = _PROJECT_ROOT / "runs"
    ledger_path: Path = _PROJECT_ROOT / "runs" / "ledger.jsonl"

    # --- Policy thresholds (RP-3.2) ----------------------------------
    refund_auto_approve_limit_usd: Decimal = Decimal("150.00")  # RP-3.2 3.1 / 3.2
    return_window_days: int = Field(default=30, ge=0)  # RP-3.2 2.1
    damage_window_days: int = Field(default=45, ge=0)  # RP-3.2 2.6
    lost_in_transit_days: int = Field(default=10, ge=0)  # RP-3.2 5.1

    # --- LLM provider (Cadra) ----------------------------------------
    cadra_proxy_url: str = "https://proxy.cadra.info/v1"
    cadra_token: SecretStr = SecretStr("")
    cadra_model: str = "cadra/cadra"
    cadra_timeout_seconds: float = 30.0
    cadra_max_retries: int = Field(default=3, ge=0, le=10)
    cadra_retry_backoff_seconds: float = 0.5
    cadra_input_cost_per_mtok: Decimal = Decimal("0.00")
    cadra_output_cost_per_mtok: Decimal = Decimal("0.00")

    # --- Repository tables -------------------------------------------
    customers_csv: Path | None = None
    orders_csv: Path | None = None
    order_items_csv: Path | None = None
    shipments_csv: Path | None = None
    refunds_csv: Path | None = None

    @property
    def policy_document(self) -> Path:
        """Authoritative customer-care policy (RP-3.2)."""
        return self.policies_dir / "refund_policy_RP-3.2.md"

    @property
    def price_table(self) -> dict[str, tuple[Decimal, Decimal]]:
        """Per-model price table: ``{model: (usd_per_mtok_in, usd_per_mtok_out)}``."""
        return {
            self.cadra_model: (
                self.cadra_input_cost_per_mtok,
                self.cadra_output_cost_per_mtok,
            )
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Build settings from ``env`` (defaults to ``os.environ``).

        Supports the variables documented in ``.env.example``; anything absent
        falls back to the defaults declared above.
        """
        source: Mapping[str, str] = os.environ if env is None else env

        def text(name: str, default: str) -> str:
            value = source.get(name, "").strip()
            return value or default

        def path(name: str, default: Path) -> Path:
            raw = source.get(name, "").strip()
            return Path(raw).expanduser() if raw else default

        def integer(name: str, default: int) -> int:
            raw = source.get(name, "").strip()
            return int(raw) if raw else default

        def decimal(name: str, default: Decimal) -> Decimal:
            raw = source.get(name, "").strip()
            return Decimal(raw) if raw else default

        def number(name: str, default: float) -> float:
            raw = source.get(name, "").strip()
            return float(raw) if raw else default

        return cls(
            host=text("HOST", "0.0.0.0"),
            port=integer("PORT", 8000),
            log_level=text("LOG_LEVEL", "INFO").upper(),
            data_dir=path("DATA_DIR", _PROJECT_ROOT / "data"),
            policies_dir=path("POLICIES_DIR", _PROJECT_ROOT / "policies"),
            tickets_dir=path("TICKETS_DIR", _PROJECT_ROOT / "tickets"),
            inbox_dir=path("INBOX_DIR", _PROJECT_ROOT / "inbox"),
            runs_dir=path("RUNS_DIR", _PROJECT_ROOT / "runs"),
            ledger_path=path("LEDGER_PATH", _PROJECT_ROOT / "runs" / "ledger.jsonl"),
            refund_auto_approve_limit_usd=decimal(
                "REFUND_AUTO_APPROVE_LIMIT_USD", Decimal("150.00")
            ),
            return_window_days=integer("RETURN_WINDOW_DAYS", 30),
            damage_window_days=integer("DAMAGE_WINDOW_DAYS", 45),
            lost_in_transit_days=integer("LOST_IN_TRANSIT_DAYS", 10),
            cadra_proxy_url=text("CADRA_PROXY_URL", "https://proxy.cadra.info/v1"),
            cadra_token=SecretStr(source.get("CADRA_TOKEN", "").strip()),
            cadra_model=text("CADRA_MODEL", "cadra/cadra"),
            cadra_timeout_seconds=number("CADRA_TIMEOUT_SECONDS", 30.0),
            cadra_max_retries=integer("CADRA_MAX_RETRIES", 3),
            cadra_retry_backoff_seconds=number("CADRA_RETRY_BACKOFF_SECONDS", 0.5),
            cadra_input_cost_per_mtok=decimal(
                "CADRA_INPUT_COST_PER_MTOK", Decimal("0.00")
            ),
            cadra_output_cost_per_mtok=decimal(
                "CADRA_OUTPUT_COST_PER_MTOK", Decimal("0.00")
            ),
            customers_csv=path("CUSTOMERS_CSV", _PROJECT_ROOT / "data" / "customers.csv"),
            orders_csv=path("ORDERS_CSV", _PROJECT_ROOT / "data" / "orders.csv"),
            order_items_csv=path("ORDER_ITEMS_CSV", _PROJECT_ROOT / "data" / "order_items.csv"),
            shipments_csv=path("SHIPMENTS_CSV", _PROJECT_ROOT / "data" / "shipments.csv"),
            refunds_csv=path("REFUNDS_CSV", _PROJECT_ROOT / "data" / "refunds.csv"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings.from_env()
