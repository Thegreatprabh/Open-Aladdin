//! Pre-trade risk gate.
//!
//! Every order passes through [`RiskGate::check_order`] before it may reach
//! the matching engine. Checks, in order:
//!
//! 1. **Kill switch** — when engaged, all new orders are rejected.
//! 2. **Quantity limit** — per-order maximum quantity.
//! 3. **Value limit** — per-order maximum notional (`price × qty`).
//! 4. **Fat-finger check** — limit price may not deviate from the reference
//!    (last traded / index) price by more than a configured percentage.
//! 5. **Margin check** — the percentage-of-notional margin estimate must fit within the
//!    account's available margin.
//!
//! The margin model is a flat percentage of notional: the initial margin is
//! a *price scan range* percentage of notional plus an *extreme loss margin*
//! add-on:
//!
//! ```text
//! margin = notional × (scan_pct + extreme_loss_pct)
//! notional = underlying_price × qty
//! ```

use std::sync::atomic::{AtomicBool, Ordering};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::lob::Side;

/// An order as seen by the risk gate (prices in currency units, not ticks).
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OrderRequest {
    pub side: Side,
    /// Limit price of the order.
    pub price: f64,
    pub qty: u64,
    /// Reference price for the fat-finger check (LTP / index level).
    pub reference_price: f64,
    /// Current underlying (spot) price used for the margin estimate.
    pub underlying_price: f64,
    /// Free margin available in the account.
    pub available_margin: f64,
}

impl OrderRequest {
    /// Order notional at the limit price.
    pub fn order_value(&self) -> f64 {
        self.price * self.qty as f64
    }

    /// Percentage deviation of the limit price from the reference price.
    pub fn deviation_pct(&self) -> f64 {
        if self.reference_price <= 0.0 {
            return f64::INFINITY;
        }
        ((self.price - self.reference_price) / self.reference_price * 100.0).abs()
    }
}

/// Reasons an order can be rejected by the risk gate.
#[derive(Debug, Error, PartialEq)]
pub enum RiskError {
    #[error("invalid input: {field} must be {requirement}, got {value}")]
    InvalidInput {
        field: &'static str,
        requirement: &'static str,
        value: f64,
    },
    #[error("order quantity must be positive, got {0}")]
    ZeroQuantity(u64),
    #[error("invalid risk configuration: {0}")]
    InvalidConfig(String),
    #[error("kill switch is engaged: all trading halted")]
    KillSwitchEngaged,
    #[error("order quantity {qty} exceeds per-order limit {limit}")]
    QuantityLimitExceeded { qty: u64, limit: u64 },
    #[error("order value {value:.2} exceeds per-order limit {limit:.2}")]
    ValueLimitExceeded { value: f64, limit: f64 },
    #[error(
        "fat-finger check failed: price deviates {deviation_pct:.2}% from reference (limit {limit_pct:.2}%)"
    )]
    FatFinger { deviation_pct: f64, limit_pct: f64 },
    #[error("insufficient margin: required {required:.2}, available {available:.2}")]
    InsufficientMargin { required: f64, available: f64 },
}

/// Configurable pre-trade risk controls.
pub struct RiskGate {
    /// Maximum quantity allowed in a single order.
    pub max_order_qty: u64,
    /// Maximum notional allowed in a single order (currency units).
    pub max_order_value: f64,
    /// Maximum allowed deviation from the reference price, in percent.
    pub fat_finger_pct: f64,
    /// Price scan range as a fraction of notional (e.g. 0.08 = 8%).
    pub price_scan_pct: f64,
    /// Extreme-loss-margin add-on as a fraction of notional.
    pub extreme_loss_pct: f64,
    kill_switch: AtomicBool,
}

impl Default for RiskGate {
    /// Sensible defaults for an index-derivatives desk (NIFTY-style):
    /// 1,00,000 units / ₹5 crore per order, 5% fat-finger band, and a
    /// Percentage-of-notional margin: 8% price scan + 2% extreme loss ≈ 10% of notional.
    fn default() -> Self {
        RiskGate {
            max_order_qty: 100_000,
            max_order_value: 50_000_000.0,
            fat_finger_pct: 5.0,
            price_scan_pct: 0.08,
            extreme_loss_pct: 0.02,
            kill_switch: AtomicBool::new(false),
        }
    }
}

impl RiskGate {
    /// Build a risk gate, validating the configuration. Returns
    /// [`RiskError::InvalidConfig`] when a limit is non-finite, negative, or
    /// a quantity limit is zero.
    pub fn try_new(
        max_order_qty: u64,
        max_order_value: f64,
        fat_finger_pct: f64,
        price_scan_pct: f64,
        extreme_loss_pct: f64,
    ) -> Result<Self, RiskError> {
        let bad = |what: &str| RiskError::InvalidConfig(what.to_string());
        if max_order_qty == 0 {
            return Err(bad("max_order_qty must be positive"));
        }
        for (name, v) in [
            ("max_order_value", max_order_value),
            ("fat_finger_pct", fat_finger_pct),
            ("price_scan_pct", price_scan_pct),
            ("extreme_loss_pct", extreme_loss_pct),
        ] {
            if !v.is_finite() {
                return Err(bad(&format!("{name} must be finite")));
            }
            if v < 0.0 {
                return Err(bad(&format!("{name} must not be negative")));
            }
        }
        if max_order_value <= 0.0 {
            return Err(bad("max_order_value must be positive"));
        }
        Ok(RiskGate {
            max_order_qty,
            max_order_value,
            fat_finger_pct,
            price_scan_pct,
            extreme_loss_pct,
            kill_switch: AtomicBool::new(false),
        })
    }

    /// Convenience constructor that panics on invalid configuration.
    pub fn new(
        max_order_qty: u64,
        max_order_value: f64,
        fat_finger_pct: f64,
        price_scan_pct: f64,
        extreme_loss_pct: f64,
    ) -> Self {
        Self::try_new(
            max_order_qty,
            max_order_value,
            fat_finger_pct,
            price_scan_pct,
            extreme_loss_pct,
        )
        .expect("valid risk configuration")
    }

    /// Halt all trading immediately (e.g. on a risk breach or ops command).
    pub fn engage_kill_switch(&self) {
        self.kill_switch.store(true, Ordering::SeqCst);
    }

    /// Resume trading after the halt condition has been cleared.
    pub fn release_kill_switch(&self) {
        self.kill_switch.store(false, Ordering::SeqCst);
    }

    /// Whether the kill switch is currently engaged.
    pub fn is_killed(&self) -> bool {
        self.kill_switch.load(Ordering::SeqCst)
    }

    /// Percentage-of-notional initial margin estimate for a position of `qty` units in
    /// an instrument on `underlying_price`:
    /// `notional × (price_scan_pct + extreme_loss_pct)`.
    pub fn margin_estimate(&self, underlying_price: f64, qty: u64) -> f64 {
        let notional = underlying_price * qty as f64;
        notional * (self.price_scan_pct + self.extreme_loss_pct)
    }

    /// Validate every numeric field of the request before any arithmetic.
    fn validate_request(&self, req: &OrderRequest) -> Result<(), RiskError> {
        let finite = |field: &'static str, requirement: &'static str, v: f64| {
            if !v.is_finite() {
                return Err(RiskError::InvalidInput {
                    field,
                    requirement,
                    value: v,
                });
            }
            Ok(())
        };
        if req.qty == 0 {
            return Err(RiskError::ZeroQuantity(0));
        }
        finite("price", "finite", req.price)?;
        finite("reference_price", "finite", req.reference_price)?;
        finite("underlying_price", "finite", req.underlying_price)?;
        finite("available_margin", "finite", req.available_margin)?;
        if req.price <= 0.0 {
            return Err(RiskError::InvalidInput {
                field: "price",
                requirement: "positive",
                value: req.price,
            });
        }
        if req.reference_price <= 0.0 {
            return Err(RiskError::InvalidInput {
                field: "reference_price",
                requirement: "positive",
                value: req.reference_price,
            });
        }
        if req.underlying_price <= 0.0 {
            return Err(RiskError::InvalidInput {
                field: "underlying_price",
                requirement: "positive",
                value: req.underlying_price,
            });
        }
        if req.available_margin < 0.0 {
            return Err(RiskError::InvalidInput {
                field: "available_margin",
                requirement: "non-negative",
                value: req.available_margin,
            });
        }
        Ok(())
    }

    /// Run every pre-trade check. Returns `Ok(())` only if the order may be
    /// forwarded to the matching engine.
    pub fn check_order(&self, req: &OrderRequest) -> Result<(), RiskError> {
        if self.is_killed() {
            return Err(RiskError::KillSwitchEngaged);
        }
        // Every numeric input is validated *before* any arithmetic: a NaN or
        // infinite value makes all `>`/`<` comparisons false, which would let
        // the order sail through every quantitative check below.
        self.validate_request(req)?;
        if req.qty > self.max_order_qty {
            return Err(RiskError::QuantityLimitExceeded {
                qty: req.qty,
                limit: self.max_order_qty,
            });
        }
        let value = req.order_value();
        if value > self.max_order_value {
            return Err(RiskError::ValueLimitExceeded {
                value,
                limit: self.max_order_value,
            });
        }
        let deviation = req.deviation_pct();
        if deviation > self.fat_finger_pct {
            return Err(RiskError::FatFinger {
                deviation_pct: deviation,
                limit_pct: self.fat_finger_pct,
            });
        }
        let required = self.margin_estimate(req.underlying_price, req.qty);
        if !required.is_finite() {
            return Err(RiskError::InvalidInput {
                field: "margin_estimate",
                requirement: "finite",
                value: required,
            });
        }
        if required > req.available_margin {
            return Err(RiskError::InsufficientMargin {
                required,
                available: req.available_margin,
            });
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok_request() -> OrderRequest {
        OrderRequest {
            side: Side::Bid,
            price: 24_150.0,
            qty: 50,
            reference_price: 24_140.0,
            underlying_price: 24_140.0,
            available_margin: 1_000_000.0,
        }
    }

    #[test]
    fn well_formed_order_passes() {
        let gate = RiskGate::default();
        assert_eq!(gate.check_order(&ok_request()), Ok(()));
    }

    #[test]
    fn kill_switch_blocks_everything() {
        let gate = RiskGate::default();
        gate.engage_kill_switch();
        assert_eq!(
            gate.check_order(&ok_request()),
            Err(RiskError::KillSwitchEngaged)
        );
        gate.release_kill_switch();
        assert_eq!(gate.check_order(&ok_request()), Ok(()));
    }

    #[test]
    fn quantity_limit_enforced() {
        let gate = RiskGate::default();
        let mut req = ok_request();
        req.qty = gate.max_order_qty + 1;
        assert!(matches!(
            gate.check_order(&req),
            Err(RiskError::QuantityLimitExceeded { .. })
        ));
    }

    #[test]
    fn value_limit_enforced() {
        let gate = RiskGate::default();
        let mut req = ok_request();
        req.qty = 100_000; // within qty limit
        req.price = 1_000.0; // 100_000 × 1_000 = ₹10 cr > ₹5 cr limit
        req.reference_price = 1_000.0; // keep fat-finger check quiet
        assert!(matches!(
            gate.check_order(&req),
            Err(RiskError::ValueLimitExceeded { .. })
        ));
    }

    #[test]
    fn fat_finger_detected() {
        let gate = RiskGate::default();
        let mut req = ok_request();
        req.price = req.reference_price * 1.20; // 20% away, limit is 5%
        assert!(matches!(
            gate.check_order(&req),
            Err(RiskError::FatFinger { .. })
        ));
    }

    #[test]
    fn nan_and_infinite_inputs_are_rejected() {
        let gate = RiskGate::default();
        type ReqMod = (&'static str, fn(&mut OrderRequest));
        let mods: Vec<ReqMod> = vec![
            ("price NaN", |r| r.price = f64::NAN),
            ("price +inf", |r| r.price = f64::INFINITY),
            ("price negative", |r| r.price = -1.0),
            ("reference NaN", |r| r.reference_price = f64::NAN),
            ("reference zero", |r| r.reference_price = 0.0),
            ("underlying +inf", |r| r.underlying_price = f64::INFINITY),
            ("margin NaN", |r| r.available_margin = f64::NAN),
            ("margin negative", |r| r.available_margin = -5.0),
        ];
        for (name, make_bad) in mods {
            let mut req = ok_request();
            make_bad(&mut req);
            let err = gate.check_order(&req);
            assert!(
                matches!(err, Err(RiskError::InvalidInput { .. })),
                "{name}: expected InvalidInput rejection, got {err:?}"
            );
        }
    }

    #[test]
    fn zero_quantity_rejected() {
        let gate = RiskGate::default();
        let mut req = ok_request();
        req.qty = 0;
        assert_eq!(gate.check_order(&req), Err(RiskError::ZeroQuantity(0)));
    }

    #[test]
    fn overflowing_notional_is_rejected() {
        let gate = RiskGate::default();
        let mut req = ok_request();
        req.price = 1e308; // value = 5e309 -> +inf
        assert!(matches!(
            gate.check_order(&req),
            Err(RiskError::ValueLimitExceeded { .. }) | Err(RiskError::InvalidInput { .. })
        ));
    }

    #[test]
    fn invalid_config_rejected() {
        assert!(matches!(
            RiskGate::try_new(0, 1.0, 5.0, 0.08, 0.02),
            Err(RiskError::InvalidConfig(_))
        ));
        assert!(matches!(
            RiskGate::try_new(1, -1.0, 5.0, 0.08, 0.02),
            Err(RiskError::InvalidConfig(_))
        ));
        assert!(matches!(
            RiskGate::try_new(1, 1.0, f64::NAN, 0.08, 0.02),
            Err(RiskError::InvalidConfig(_))
        ));
        assert!(RiskGate::try_new(1, 1.0, 5.0, 0.08, 0.02).is_ok());
    }

    #[test]
    fn margin_estimate_and_shortfall() {
        let gate = RiskGate::default();
        // 50 units × ₹24,140 × (8% + 2%) = ₹120,700 required.
        let required = gate.margin_estimate(24_140.0, 50);
        assert!((required - 120_700.0).abs() < 0.01);

        let mut req = ok_request();
        req.available_margin = required - 1.0;
        assert!(matches!(
            gate.check_order(&req),
            Err(RiskError::InsufficientMargin { .. })
        ));
    }
}
