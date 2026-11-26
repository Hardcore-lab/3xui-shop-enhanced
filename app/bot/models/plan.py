from dataclasses import dataclass
from typing import Any

from app.bot.utils.constants import Currency


@dataclass
class Plan:
    devices: int
    prices: dict[str, dict[int, float]]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plan":
        # Convert prices dict: ensure currency keys are strings and duration keys are ints
        prices_converted: dict[str, dict[int, float]] = {}
        for currency, durations_prices in data["prices"].items():
            prices_converted[str(currency)] = {}
            for duration_key, price_val in durations_prices.items():
                # Handle both string and int duration keys
                duration_int = int(duration_key) if isinstance(duration_key, str) else duration_key
                prices_converted[str(currency)][duration_int] = float(price_val)
        
        return cls(
            devices=int(data["devices"]),
            prices=prices_converted,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "devices": self.devices,
            "prices": {k: {str(m): p for m, p in v.items()} for k, v in self.prices.items()},
        }

    def get_price(self, currency: Currency | str, duration: int) -> float | None:
        if isinstance(currency, str):
            currency = Currency.from_code(currency)

        if currency.code not in self.prices:
            return None
        
        # Handle both string and int keys for duration
        duration_dict = self.prices[currency.code]
        
        if duration in duration_dict:
            return duration_dict[duration]
        elif str(duration) in duration_dict:
            return duration_dict[str(duration)]

        return None
