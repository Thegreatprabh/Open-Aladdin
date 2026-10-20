// OPEN-ALADDIN — Exact Black-Scholes-Merton engine.
// Closed-form price + 1st/2nd order Greeks (Delta, Gamma, Theta, Vega, Rho,
// Vanna, Charm, Vomma) and a Newton/bisection implied-volatility solver.

import type { Greeks } from '../types/market';

const SQRT_2PI = Math.sqrt(2 * Math.PI);

/** Standard normal PDF n(x). */
export function normPdf(x: number): number {
  return Math.exp(-0.5 * x * x) / SQRT_2PI;
}

/**
 * Standard normal CDF N(x) via the Abramowitz & Stegun 26.2.17 erf
 * approximation (|ε| ≤ 7.5e-8), accurate to ~1e-7.
 */
export function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const poly =
    t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const cdf = 1 - normPdf(x) * poly;
  return x >= 0 ? cdf : 1 - cdf;
}

export interface BsInputs {
  S: number; // spot
  K: number; // strike
  T: number; // years to expiry
  r: number; // risk-free rate (decimal)
  sigma: number; // volatility (decimal)
  type: 'CE' | 'PE';
}

function d1d2(S: number, K: number, T: number, r: number, sigma: number): { d1: number; d2: number } {
  const vol = sigma * Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / vol;
  return { d1, d2: d1 - vol };
}

/** Black-Scholes-Merton option price. */
export function bsPrice(inp: BsInputs): number {
  const { S, K, T, r, sigma, type } = inp;
  if (T <= 0) return type === 'CE' ? Math.max(S - K, 0) : Math.max(K - S, 0);
  if (sigma <= 0) {
    const disc = Math.exp(-r * T);
    return type === 'CE' ? Math.max(S - K * disc, 0) : Math.max(K * disc - S, 0);
  }
  const { d1, d2 } = d1d2(S, K, T, r, sigma);
  const disc = Math.exp(-r * T);
  if (type === 'CE') return S * normCdf(d1) - K * disc * normCdf(d2);
  return K * disc * normCdf(-d2) - S * normCdf(-d1);
}

/**
 * Full Greek stack.
 * - theta: per calendar day
 * - vega / rho / vanna / vomma: per 1 volatility / rate point
 * - charm: delta decay per calendar day
 */
export function bsGreeks(inp: BsInputs): Greeks {
  const { S, K, T, r, sigma, type } = inp;
  const zero: Greeks = { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0, vanna: 0, charm: 0, vomma: 0 };
  if (T <= 0 || sigma <= 0) {
    const itm = type === 'CE' ? S > K : S < K;
    return { ...zero, delta: itm ? (type === 'CE' ? 1 : -1) : 0 };
  }
  const sqrtT = Math.sqrt(T);
  const { d1, d2 } = d1d2(S, K, T, r, sigma);
  const pdf = normPdf(d1);
  const disc = Math.exp(-r * T);
  const isCall = type === 'CE';

  const delta = isCall ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = pdf / (S * sigma * sqrtT);

  const thetaCommon = -(S * pdf * sigma) / (2 * sqrtT);
  const thetaAnnual = isCall
    ? thetaCommon - r * K * disc * normCdf(d2)
    : thetaCommon + r * K * disc * normCdf(-d2);
  const theta = thetaAnnual / 365;

  const vegaRaw = S * pdf * sqrtT;
  const vega = vegaRaw / 100;

  const rhoRaw = isCall ? K * T * disc * normCdf(d2) : -K * T * disc * normCdf(-d2);
  const rho = rhoRaw / 100;

  // Vanna = ∂Delta/∂σ = -n(d1) * d2 / σ
  const vanna = (-pdf * d2) / sigma / 100;

  // Charm = -∂Delta/∂τ (per day)
  const charmTerm = (pdf * (2 * r * T - d2 * sigma * sqrtT)) / (2 * T * sigma * sqrtT);
  const charmAnnual = isCall
    ? -disc * (charmTerm + r * normCdf(d1))
    : -disc * (charmTerm - r * normCdf(-d1));
  const charm = charmAnnual / 365;

  // Vomma = ∂Vega/∂σ = Vega * d1 * d2 / σ
  const vomma = (vegaRaw * d1 * d2) / sigma / 10000;

  return { delta, gamma, theta, vega, rho, vanna, charm, vomma };
}

/** Implied volatility from a market price: Newton-Raphson with bisection fallback. */
export function impliedVol(
  marketPrice: number,
  S: number,
  K: number,
  T: number,
  r: number,
  type: 'CE' | 'PE',
): number {
  if (T <= 0 || marketPrice <= 0) return 0;
  const intrinsic = type === 'CE' ? Math.max(S - K * Math.exp(-r * T), 0) : Math.max(K * Math.exp(-r * T) - S, 0);
  if (marketPrice < intrinsic) return 0.0001;

  let sigma = 0.25;
  for (let i = 0; i < 60; i++) {
    const price = bsPrice({ S, K, T, r, sigma, type });
    const diff = price - marketPrice;
    if (Math.abs(diff) < 1e-8) return sigma;
    const g = bsGreeks({ S, K, T, r, sigma, type });
    const vegaPerUnit = g.vega * 100;
    if (vegaPerUnit < 1e-10) break;
    sigma -= diff / vegaPerUnit;
    if (sigma <= 0.0001) sigma = 0.0001;
    if (sigma > 5) sigma = 5;
  }
  // Bisection fallback / polish
  let lo = 0.0001;
  let hi = 5;
  for (let i = 0; i < 100; i++) {
    const mid = (lo + hi) / 2;
    const p = bsPrice({ S, K, T, r, sigma: mid, type });
    if (Math.abs(p - marketPrice) < 1e-9) return mid;
    if (p < marketPrice) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}
