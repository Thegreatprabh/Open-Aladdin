/** Formatting helpers (en-IN conventions, non-finite handling). */
import { describe, expect, it } from 'vitest';
import { fmt, fmt0, fmtCr, fmtINR, fmtPct, fmtSigned } from './format';

describe('format helpers', () => {
  it('fmt uses Indian digit grouping', () => {
    expect(fmt(1234567.5, 2)).toBe('12,34,567.50');
    expect(fmt(1000, 0)).toBe('1,000');
  });
  it('fmt0 rounds and groups', () => {
    expect(fmt0(24310.6)).toBe('24,311');
  });
  it('INR prefixing and sign handling', () => {
    expect(fmtINR(1500)).toBe('₹1,500');
    expect(fmtINR(-1500)).toBe('−₹1,500');
  });
  it('crore denomination', () => {
    expect(fmtCr(12.5)).toBe('₹12.50 Cr');
  });
  it('signed percent', () => {
    expect(fmtPct(1.234)).toBe('+1.23%');
    expect(fmtPct(-1.234)).toBe('−1.23%');
    expect(fmtSigned(-2.5)).toBe('−2.50');
  });
  it('non-finite values render as an em dash, never NaN', () => {
    expect(fmt(Number.NaN)).toBe('—');
    expect(fmtINR(Number.POSITIVE_INFINITY)).toBe('—');
    expect(fmt0(Number.NaN)).toBe('—');
  });
});
