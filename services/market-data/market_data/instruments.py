"""Symbol <-> broker instrument-token resolution.

In production (P3) this table is replaced by the broker's daily
instruments dump, cached by the service at startup. The resolver
interface stays the same; only the source of the table changes.

The static table below covers the terminal's watchlist at index/stock
scale (tens of instruments, per design section 6). Token values are the
brokers' published instrument tokens for these symbols.
"""

from __future__ import annotations


class UnknownInstrument(KeyError):
    """Raised when a symbol or token is not in the instruments table."""


# symbol -> (instrument token, base price for the simulated upstream)
STATIC_INSTRUMENTS: dict[str, tuple[int, float]] = {
    "NIFTY": (256265, 25796.50),
    "BANKNIFTY": (260105, 54120.75),
    "FINNIFTY": (257801, 23980.10),
    "RELIANCE": (738561, 2985.40),
    "TCS": (884737, 4120.00),
    "INFY": (408065, 1892.55),
    "HDFCBANK": (341249, 1684.30),
}


class InstrumentRegistry:
    """Bidirectional symbol/token lookup over an instruments table."""

    def __init__(self, table: dict[str, tuple[int, float]] | None = None):
        self._by_symbol = dict(table or STATIC_INSTRUMENTS)
        self._by_token = {tok: sym for sym, (tok, _b) in self._by_symbol.items()}
        if len(self._by_token) != len(self._by_symbol):
            raise ValueError("duplicate instrument token in table")

    @classmethod
    def from_dump_rows(cls, rows: list[dict]) -> "InstrumentRegistry":
        """Build from broker dump-shaped rows (P3 path).

        Each row: {"tradingsymbol": str, "instrument_token": int,
        "last_price": float}. Rows missing keys are skipped, mirroring how
        a real dump consumer must tolerate partial data.
        """
        table: dict[str, tuple[int, float]] = {}
        for row in rows:
            try:
                table[str(row["tradingsymbol"])] = (
                    int(row["instrument_token"]), float(row["last_price"]))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(table)

    def token_for(self, symbol: str) -> int:
        try:
            return self._by_symbol[symbol][0]
        except KeyError:
            raise UnknownInstrument(f"unknown symbol {symbol!r}") from None

    def symbol_for(self, token: int) -> str:
        try:
            return self._by_token[int(token)]
        except KeyError:
            raise UnknownInstrument(f"unknown token {token!r}") from None

    def base_price(self, symbol: str) -> float:
        try:
            return self._by_symbol[symbol][1]
        except KeyError:
            raise UnknownInstrument(f"unknown symbol {symbol!r}") from None

    def symbols(self) -> list[str]:
        return list(self._by_symbol)
