//! OPEN-ATLAS matching engine core.
//!
//! Three cooperating modules:
//!
//! * [`lob`] — a price–time priority limit order book with iceberg orders,
//!   amendments, cancellations and depth snapshots.
//! * [`fix_parser`] — a FIX 4.4 `tag=value` wire protocol parser/serializer
//!   with BodyLength and CheckSum validation.
//! * [`risk_gate`] — a pre-trade risk gate enforcing quantity/value limits,
//!   fat-finger deviation checks, SPAN-style margin estimation and a
//!   kill switch.
//!
//! Typical flow: a FIX `NewOrderSingle` is parsed by [`fix_parser`], checked
//! by [`risk_gate::RiskGate::check_order`], then submitted to
//! [`lob::OrderBook::insert_order`], which returns the resulting
//! [`lob::Trade`] events used to build FIX `ExecutionReport` messages.

pub mod fix_parser;
pub mod lob;
pub mod risk_gate;

pub use fix_parser::{FixBuilder, FixError, FixMessage};
pub use lob::{BookError, LevelSnapshot, Order, OrderBook, OrderId, Price, Qty, Side, Trade};
pub use risk_gate::{OrderRequest, RiskError, RiskGate};
