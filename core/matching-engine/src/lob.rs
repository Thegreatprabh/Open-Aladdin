//! Price–time priority limit order book (LOB) with iceberg order support.
//!
//! Design notes:
//! * Prices are integer ticks (`1 tick = 0.01` currency units) so matching is
//!   exact and free of floating-point drift.
//! * Each side is a `BTreeMap<Price, PriceLevel>`; the best bid is the greatest
//!   key of the bid map and the best ask is the smallest key of the ask map.
//! * Within a level, orders sit in a `VecDeque` — strict FIFO (time priority).
//! * An [`OrderId`] → `(Side, Price)` index makes cancel/amend O(level size).
//! * Iceberg orders expose only `display_qty`; when the visible slice is fully
//!   consumed and reserve quantity remains, the order is refreshed with a new
//!   visible slice and re-queued at the **back** of its level (it loses time
//!   priority), exactly as on real exchanges (NSE/CME style).

use std::collections::{BTreeMap, HashMap, VecDeque};

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Price in integer ticks (1 tick = 0.01 currency units, e.g. paise).
pub type Price = i64;
/// Order quantity in units (shares / contracts).
pub type Qty = u64;
/// Unique order identifier assigned by the gateway / OMS.
pub type OrderId = u64;

/// Side of the book an order rests on / a trade is initiated from.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Side {
    Bid,
    Ask,
}

impl Side {
    /// The opposite side of the book.
    pub fn opposite(self) -> Side {
        match self {
            Side::Bid => Side::Ask,
            Side::Ask => Side::Bid,
        }
    }
}

/// A single limit order.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Order {
    pub id: OrderId,
    pub side: Side,
    /// Limit price in ticks.
    pub price: Price,
    /// Total order quantity (visible + iceberg reserve).
    pub qty: Qty,
    /// Quantity already executed against this order.
    pub filled_qty: Qty,
    /// Iceberg peak: maximum quantity visible in the book at any time.
    /// `None` means a plain (fully visible) limit order.
    pub display_qty: Option<Qty>,
    /// Monotonic arrival timestamp (nanoseconds or gateway sequence number).
    /// Informational — true priority is queue position.
    pub timestamp: u64,
}

impl Order {
    /// Create an order. A `display_qty` of `Some(0)` is normalized to `None`
    /// (a zero peak would deadlock the matcher); a peak larger than `qty` is
    /// clamped by [`Order::visible_qty`] at match time.
    pub fn new(
        id: OrderId,
        side: Side,
        price: Price,
        qty: Qty,
        display_qty: Option<Qty>,
        timestamp: u64,
    ) -> Self {
        Order {
            id,
            side,
            price,
            qty,
            filled_qty: 0,
            display_qty: display_qty.filter(|d| *d > 0),
            timestamp,
        }
    }

    /// Convenience constructor for a plain visible limit order.
    pub fn limit(id: OrderId, side: Side, price: Price, qty: Qty, timestamp: u64) -> Self {
        Order::new(id, side, price, qty, None, timestamp)
    }

    /// Convenience constructor for an iceberg order.
    pub fn iceberg(
        id: OrderId,
        side: Side,
        price: Price,
        qty: Qty,
        display_qty: Qty,
        timestamp: u64,
    ) -> Self {
        Order::new(id, side, price, qty, Some(display_qty), timestamp)
    }

    /// Quantity not yet executed (visible + reserve).
    pub fn remaining(&self) -> Qty {
        self.qty.saturating_sub(self.filled_qty)
    }

    /// Quantity currently exposed to the market.
    pub fn visible_qty(&self) -> Qty {
        match self.display_qty {
            Some(peak) => peak.min(self.remaining()),
            None => self.remaining(),
        }
    }

    /// Whether this order hides reserve quantity behind a display peak.
    pub fn is_iceberg(&self) -> bool {
        self.display_qty.is_some()
    }

    /// Whether the order has been completely executed.
    pub fn is_filled(&self) -> bool {
        self.remaining() == 0
    }
}

/// An execution produced by the matching engine.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Trade {
    pub trade_id: u64,
    /// Aggressor (incoming) order id.
    pub taker_id: OrderId,
    /// Resting order id.
    pub maker_id: OrderId,
    /// Side of the aggressor.
    pub taker_side: Side,
    /// Execution price — always the maker's (resting) price.
    pub price: Price,
    pub qty: Qty,
    pub timestamp: u64,
}

/// One aggregated price level in a depth snapshot.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct LevelSnapshot {
    pub price: Price,
    /// Sum of visible (display) quantity at the level.
    pub visible_qty: Qty,
    /// Sum of total remaining quantity including iceberg reserve.
    pub total_qty: Qty,
    pub order_count: usize,
}

/// Errors returned by book operations.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum BookError {
    #[error("order {0} not found in book")]
    OrderNotFound(OrderId),
    #[error("duplicate order id {0}")]
    DuplicateOrder(OrderId),
    #[error("order quantity must be positive")]
    InvalidQuantity,
}

/// All resting orders at one price, in arrival (FIFO) order.
#[derive(Default)]
struct PriceLevel {
    queue: VecDeque<Order>,
}

impl PriceLevel {
    fn visible_qty(&self) -> Qty {
        self.queue.iter().map(Order::visible_qty).sum()
    }
    fn total_qty(&self) -> Qty {
        self.queue.iter().map(Order::remaining).sum()
    }
}

/// The limit order book.
pub struct OrderBook {
    bids: BTreeMap<Price, PriceLevel>,
    asks: BTreeMap<Price, PriceLevel>,
    /// Fast lookup for cancel/amend.
    index: HashMap<OrderId, (Side, Price)>,
    next_trade_id: u64,
    /// Internal logical clock used to re-stamp amended orders.
    seq: u64,
}

impl Default for OrderBook {
    fn default() -> Self {
        OrderBook {
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            index: HashMap::new(),
            next_trade_id: 1,
            seq: 0,
        }
    }
}

impl OrderBook {
    pub fn new() -> Self {
        Self::default()
    }

    fn next_seq(&mut self) -> u64 {
        self.seq += 1;
        self.seq
    }

    /// Number of orders currently resting in the book.
    pub fn open_order_count(&self) -> usize {
        self.index.len()
    }

    /// Whether an order id is currently resting in the book.
    pub fn contains(&self, id: OrderId) -> bool {
        self.index.contains_key(&id)
    }

    /// Look up a resting order by id.
    pub fn get_order(&self, id: OrderId) -> Option<&Order> {
        let (side, price) = self.index.get(&id)?;
        let level = match side {
            Side::Bid => self.bids.get(price),
            Side::Ask => self.asks.get(price),
        }?;
        level.queue.iter().find(|o| o.id == id)
    }

    /// Best (highest) bid price, if any.
    pub fn best_bid(&self) -> Option<Price> {
        self.bids.keys().next_back().copied()
    }

    /// Best (lowest) ask price, if any.
    pub fn best_ask(&self) -> Option<Price> {
        self.asks.keys().next().copied()
    }

    /// Mid price in ticks (average of best bid/ask), if both sides exist.
    pub fn mid_price(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some(b), Some(a)) => Some((b as f64 + a as f64) / 2.0),
            _ => None,
        }
    }

    /// Bid–ask spread in ticks, if both sides exist.
    pub fn spread(&self) -> Option<Price> {
        match (self.best_bid(), self.best_ask()) {
            (Some(b), Some(a)) => Some(a - b),
            _ => None,
        }
    }

    /// Insert an order, matching against the opposite side while it crosses.
    ///
    /// Returns every [`Trade`] generated, in execution order. Any unfilled
    /// remainder rests in the book (for an iceberg, only its display peak is
    /// visible). Matching follows strict price–time priority; trade price is
    /// the resting (maker) price.
    pub fn insert_order(&mut self, mut taker: Order) -> Result<Vec<Trade>, BookError> {
        if taker.qty == 0 {
            return Err(BookError::InvalidQuantity);
        }
        if self.index.contains_key(&taker.id) {
            return Err(BookError::DuplicateOrder(taker.id));
        }
        let mut trades = Vec::new();

        while taker.remaining() > 0 {
            let best_price = match taker.side {
                Side::Bid => self.asks.keys().next().copied(),
                Side::Ask => self.bids.keys().next_back().copied(),
            };
            let Some(best_price) = best_price else { break };
            let crosses = match taker.side {
                Side::Bid => best_price <= taker.price,
                Side::Ask => best_price >= taker.price,
            };
            if !crosses {
                break;
            }

            let mut filled_makers: Vec<OrderId> = Vec::new();
            let level_empty = {
                let level = match taker.side {
                    Side::Bid => self.asks.get_mut(&best_price),
                    Side::Ask => self.bids.get_mut(&best_price),
                }
                .expect("best price level must exist");

                while taker.remaining() > 0 {
                    let Some(mut maker) = level.queue.pop_front() else { break };
                    let visible_before = maker.visible_qty();
                    let qty = taker.remaining().min(visible_before);
                    if qty == 0 {
                        // Defensive: a zero-visible maker must never spin.
                        level.queue.push_front(maker);
                        break;
                    }
                    maker.filled_qty += qty;
                    taker.filled_qty += qty;
                    trades.push(Trade {
                        trade_id: self.next_trade_id,
                        taker_id: taker.id,
                        maker_id: maker.id,
                        taker_side: taker.side,
                        price: best_price,
                        qty,
                        timestamp: taker.timestamp,
                    });
                    self.next_trade_id += 1;

                    if maker.remaining() == 0 {
                        filled_makers.push(maker.id);
                    } else if maker.is_iceberg() && qty == visible_before {
                        // Visible slice exhausted, reserve remains: refresh
                        // with a new peak and re-queue at the back of the
                        // level (time priority is lost on refresh).
                        level.queue.push_back(maker);
                    } else {
                        // Plain order partially filled: the taker is now
                        // exhausted; maker keeps its front position.
                        level.queue.push_front(maker);
                        break;
                    }
                }
                level.queue.is_empty()
            };
            for id in filled_makers {
                self.index.remove(&id);
            }
            if level_empty {
                match taker.side {
                    Side::Bid => {
                        self.asks.remove(&best_price);
                    }
                    Side::Ask => {
                        self.bids.remove(&best_price);
                    }
                }
            }
        }

        if taker.remaining() > 0 {
            let (side, price, id) = (taker.side, taker.price, taker.id);
            self.index.insert(id, (side, price));
            match side {
                Side::Bid => self.bids.entry(price).or_default().queue.push_back(taker),
                Side::Ask => self.asks.entry(price).or_default().queue.push_back(taker),
            }
        }
        Ok(trades)
    }

    /// Remove a resting order from the book, returning it if found.
    fn remove_order(&mut self, id: OrderId) -> Option<Order> {
        let (side, price) = self.index.remove(&id)?;
        let map = match side {
            Side::Bid => &mut self.bids,
            Side::Ask => &mut self.asks,
        };
        let order = {
            let level = map.get_mut(&price)?;
            let pos = level.queue.iter().position(|o| o.id == id)?;
            level.queue.remove(pos)
        };
        let empty = map.get(&price).map(|l| l.queue.is_empty()).unwrap_or(false);
        if empty {
            map.remove(&price);
        }
        order
    }

    /// Cancel a resting order. Returns the cancelled order.
    pub fn cancel_order(&mut self, id: OrderId) -> Result<Order, BookError> {
        self.remove_order(id).ok_or(BookError::OrderNotFound(id))
    }

    /// Amend a resting order's price and/or total quantity.
    ///
    /// Exchange-realistic priority rules:
    /// * Quantity **decrease** at the same price is applied in place and the
    ///   order keeps its time priority.
    /// * Price change or quantity **increase** cancels and re-inserts the
    ///   order (new time priority); the re-inserted order may match
    ///   immediately, in which case the resulting trades are returned.
    pub fn amend_order(
        &mut self,
        id: OrderId,
        new_price: Option<Price>,
        new_qty: Option<Qty>,
    ) -> Result<Vec<Trade>, BookError> {
        let (side, price) = *self.index.get(&id).ok_or(BookError::OrderNotFound(id))?;
        let current = self
            .get_order(id)
            .ok_or(BookError::OrderNotFound(id))?
            .clone();

        let price_changed = new_price.map(|p| p != price).unwrap_or(false);
        let qty_increase = new_qty.map(|q| q > current.qty).unwrap_or(false);

        if !price_changed && !qty_increase {
            if let Some(q) = new_qty {
                let map = match side {
                    Side::Bid => &mut self.bids,
                    Side::Ask => &mut self.asks,
                };
                let level = map.get_mut(&price).expect("indexed level must exist");
                let order = level
                    .queue
                    .iter_mut()
                    .find(|o| o.id == id)
                    .expect("indexed order must exist");
                // Never amend below what has already executed.
                order.qty = q.max(order.filled_qty);
            }
            return Ok(Vec::new());
        }

        let mut order = self.remove_order(id).ok_or(BookError::OrderNotFound(id))?;
        if let Some(p) = new_price {
            order.price = p;
        }
        if let Some(q) = new_qty {
            order.qty = q.max(order.filled_qty);
        }
        order.timestamp = self.next_seq();
        self.insert_order(order)
    }

    /// Aggregated depth snapshot for one side, best prices first, up to
    /// `levels` price levels.
    pub fn depth(&self, side: Side, levels: usize) -> Vec<LevelSnapshot> {
        let mut out = Vec::with_capacity(levels);
        match side {
            Side::Bid => {
                for (price, level) in self.bids.iter().rev().take(levels) {
                    out.push(LevelSnapshot {
                        price: *price,
                        visible_qty: level.visible_qty(),
                        total_qty: level.total_qty(),
                        order_count: level.queue.len(),
                    });
                }
            }
            Side::Ask => {
                for (price, level) in self.asks.iter().take(levels) {
                    out.push(LevelSnapshot {
                        price: *price,
                        visible_qty: level.visible_qty(),
                        total_qty: level.total_qty(),
                        order_count: level.queue.len(),
                    });
                }
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bid(id: OrderId, price: Price, qty: Qty) -> Order {
        Order::limit(id, Side::Bid, price, qty, id)
    }
    fn ask(id: OrderId, price: Price, qty: Qty) -> Order {
        Order::limit(id, Side::Ask, price, qty, id)
    }

    #[test]
    fn resting_orders_do_not_cross() {
        let mut book = OrderBook::new();
        assert!(book.insert_order(bid(1, 9_900, 10)).unwrap().is_empty());
        assert!(book.insert_order(ask(2, 10_100, 10)).unwrap().is_empty());
        assert_eq!(book.best_bid(), Some(9_900));
        assert_eq!(book.best_ask(), Some(10_100));
        assert_eq!(book.spread(), Some(200));
        assert_eq!(book.mid_price(), Some(10_000.0));
        assert_eq!(book.open_order_count(), 2);
    }

    #[test]
    fn simple_full_match_at_maker_price() {
        let mut book = OrderBook::new();
        book.insert_order(ask(1, 10_000, 50)).unwrap();
        let trades = book.insert_order(bid(2, 10_050, 50)).unwrap();
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].price, 10_000); // maker price, not taker price
        assert_eq!(trades[0].qty, 50);
        assert_eq!(trades[0].maker_id, 1);
        assert_eq!(trades[0].taker_id, 2);
        assert_eq!(book.open_order_count(), 0);
        assert_eq!(book.best_ask(), None);
    }

    #[test]
    fn partial_fill_leaves_remainder_resting() {
        let mut book = OrderBook::new();
        book.insert_order(ask(1, 10_000, 10)).unwrap();
        let trades = book.insert_order(bid(2, 10_000, 25)).unwrap();
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].qty, 10);
        // Taker remainder rests on the bid side.
        assert_eq!(book.best_bid(), Some(10_000));
        let d = book.depth(Side::Bid, 10);
        assert_eq!(d.len(), 1);
        assert_eq!(d[0].total_qty, 15);
    }

    #[test]
    fn price_priority_beats_time_priority() {
        let mut book = OrderBook::new();
        book.insert_order(ask(1, 10_100, 10)).unwrap(); // earlier but worse price
        book.insert_order(ask(2, 10_000, 10)).unwrap(); // later but better price
        let trades = book.insert_order(bid(3, 10_100, 10)).unwrap();
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].maker_id, 2);
        assert_eq!(trades[0].price, 10_000);
    }

    #[test]
    fn time_priority_within_a_level() {
        let mut book = OrderBook::new();
        book.insert_order(ask(1, 10_000, 10)).unwrap();
        book.insert_order(ask(2, 10_000, 10)).unwrap();
        let trades = book.insert_order(bid(3, 10_000, 15)).unwrap();
        assert_eq!(trades.len(), 2);
        assert_eq!(trades[0].maker_id, 1);
        assert_eq!(trades[0].qty, 10);
        assert_eq!(trades[1].maker_id, 2);
        assert_eq!(trades[1].qty, 5);
    }

    #[test]
    fn iceberg_refreshes_and_keeps_trading() {
        let mut book = OrderBook::new();
        // Maker iceberg: 100 total, only 10 visible at a time.
        book.insert_order(Order::iceberg(1, Side::Ask, 10_000, 100, 10, 1))
            .unwrap();
        let d = book.depth(Side::Ask, 10);
        assert_eq!(d[0].visible_qty, 10);
        assert_eq!(d[0].total_qty, 100);

        let trades = book.insert_order(bid(2, 10_000, 25)).unwrap();
        let total: Qty = trades.iter().map(|t| t.qty).sum();
        assert_eq!(total, 25);
        assert_eq!(
            trades.iter().map(|t| t.qty).collect::<Vec<_>>(),
            vec![10, 10, 5]
        );
        // 75 remain in reserve, a fresh peak of 10 is visible again.
        let d = book.depth(Side::Ask, 10);
        assert_eq!(d[0].total_qty, 75);
        assert_eq!(d[0].visible_qty, 10);
    }

    #[test]
    fn iceberg_refresh_loses_time_priority() {
        let mut book = OrderBook::new();
        book.insert_order(Order::iceberg(1, Side::Ask, 10_000, 30, 10, 1))
            .unwrap();
        book.insert_order(ask(2, 10_000, 10)).unwrap();
        // Taker buys 15: 10 from iceberg peak, then order 2 (now ahead of the
        // refreshed iceberg), not the refreshed peak.
        let trades = book.insert_order(bid(3, 10_000, 15)).unwrap();
        assert_eq!(trades.len(), 2);
        assert_eq!(trades[0].maker_id, 1);
        assert_eq!(trades[1].maker_id, 2);
    }

    #[test]
    fn cancel_removes_order() {
        let mut book = OrderBook::new();
        book.insert_order(bid(1, 9_900, 10)).unwrap();
        let cancelled = book.cancel_order(1).unwrap();
        assert_eq!(cancelled.id, 1);
        assert_eq!(book.best_bid(), None);
        assert_eq!(book.cancel_order(42), Err(BookError::OrderNotFound(42)));
    }

    #[test]
    fn amend_qty_decrease_keeps_priority() {
        let mut book = OrderBook::new();
        book.insert_order(bid(1, 10_000, 10)).unwrap();
        book.insert_order(bid(2, 10_000, 10)).unwrap();
        let trades = book.amend_order(1, None, Some(5)).unwrap();
        assert!(trades.is_empty());
        // Order 1 is still at the front of the queue.
        let trades = book.insert_order(ask(3, 10_000, 5)).unwrap();
        assert_eq!(trades[0].maker_id, 1);
        assert_eq!(trades[0].qty, 5);
    }

    #[test]
    fn amend_price_reinserts_and_can_match() {
        let mut book = OrderBook::new();
        book.insert_order(ask(1, 10_000, 10)).unwrap();
        book.insert_order(bid(2, 9_900, 10)).unwrap();
        let trades = book.amend_order(2, Some(10_000), None).unwrap();
        assert_eq!(trades.len(), 1);
        assert_eq!(trades[0].qty, 10);
        assert_eq!(book.open_order_count(), 0);
    }

    #[test]
    fn depth_snapshot_is_best_first_and_capped() {
        let mut book = OrderBook::new();
        for i in 0..12u64 {
            book.insert_order(bid(i + 1, 9_900 - (i as i64) * 5, 10)).unwrap();
        }
        let d = book.depth(Side::Bid, 10);
        assert_eq!(d.len(), 10);
        assert_eq!(d[0].price, 9_900);
        assert!(d.windows(2).all(|w| w[0].price > w[1].price));
    }

    #[test]
    fn duplicate_and_zero_qty_rejected() {
        let mut book = OrderBook::new();
        book.insert_order(bid(1, 9_900, 10)).unwrap();
        assert_eq!(
            book.insert_order(bid(1, 9_900, 10)),
            Err(BookError::DuplicateOrder(1))
        );
        assert_eq!(
            book.insert_order(bid(2, 9_900, 0)),
            Err(BookError::InvalidQuantity)
        );
    }
}
