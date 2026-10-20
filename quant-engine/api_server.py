"""OPEN-ATLAS quant-engine HTTP API (FastAPI).

Endpoints:
  GET  /health      -> liveness
  POST /price/bsm   -> Black-Scholes-Merton price + delta/gamma/vega

All inputs are validated (positive, finite) before any arithmetic: invalid
payloads get a 422 with a structured error instead of a 500 or a NaN result.
Prices are theoretical model values, not executable market quotes.
"""
import math

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="OPEN-ATLAS Quant Engine")


def _n(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _np(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class BsmRequest(BaseModel):
    spot: float = Field(gt=0, allow_inf_nan=False)
    strike: float = Field(gt=0, allow_inf_nan=False)
    vol: float = Field(gt=0, le=5.0, allow_inf_nan=False)
    ttm: float = Field(gt=0, allow_inf_nan=False)  # years to expiry
    rate: float = Field(default=0.065, allow_inf_nan=False)
    dividend: float = Field(default=0.0, allow_inf_nan=False)
    option_type: str = "call"


@app.get("/health")
def health():
    return {"status": "ok", "engine": "quant"}


@app.post("/price/bsm")
def bsm(req: BsmRequest):
    s, k, r, q, v, t = req.spot, req.strike, req.rate, req.dividend, req.vol, req.ttm
    d1 = (math.log(s / k) + (r - q + 0.5 * v * v) * t) / (v * math.sqrt(t))
    d2 = d1 - v * math.sqrt(t)
    if req.option_type.lower().startswith("c"):
        price = s * math.exp(-q * t) * _n(d1) - k * math.exp(-r * t) * _n(d2)
        delta = math.exp(-q * t) * _n(d1)
    else:
        price = k * math.exp(-r * t) * _n(-d2) - s * math.exp(-q * t) * _n(-d1)
        delta = -math.exp(-q * t) * _n(-d1)
    gamma = math.exp(-q * t) * _np(d1) / (s * v * math.sqrt(t))
    vega = s * math.exp(-q * t) * _np(d1) * math.sqrt(t)
    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega}
