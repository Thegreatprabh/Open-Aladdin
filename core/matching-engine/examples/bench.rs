//! Micro-benchmark for the OPEN-ALADDIN limit order book.
//!
//! Run with:
//!     cargo run --release --example bench
//!
//! Benchmarks three things on the current machine:
//!   1. resting-order inserts (no match — orders rest in the book)
//!   2. crossing inserts (every order matches immediately against the book)
//!   3. cancels
//!
//! Numbers are wall-clock averages over the whole loop on whatever machine
//! runs it. They are reported, not promised — README quotes the machine and
//! the exact command so anyone can reproduce or beat them.

use std::time::Instant;

use open_aladdin_matching_engine::{Order, OrderBook, Side};

const N: u64 = 200_000;

fn main() {
    // ---- 1. resting inserts ----
    let mut book = OrderBook::new();
    let t0 = Instant::now();
    for i in 0..N {
        // bids at descending prices so nothing crosses
        let price = 10_000 - (i % 500) as i64;
        let _ = book.insert_order(Order::limit(i + 1, Side::Bid, price, 10, i));
    }
    let d1 = t0.elapsed();
    println!("resting inserts : {:>10} orders in {:?}  ->  {:.0} orders/sec, {:.0} ns/order",
             N, d1, N as f64 / d1.as_secs_f64(), d1.as_nanos() as f64 / N as f64);
    println!("  open orders in book: {}", book.open_order_count());

    // ---- 2. crossing inserts (each taker matches a resting order) ----
    let mut book2 = OrderBook::new();
    for i in 0..N {
        let _ = book2.insert_order(Order::limit(i + 1, Side::Ask, 10_000, 10, i));
    }
    let t1 = Instant::now();
    let mut trades = 0usize;
    for i in 0..N {
        let out = book2
            .insert_order(Order::limit(N + i + 1, Side::Bid, 10_000, 10, N + i))
            .expect("crossing insert");
        trades += out.len();
    }
    let d2 = t1.elapsed();
    println!("crossing inserts: {:>10} orders in {:?}  ->  {:.0} orders/sec, {:.0} ns/order ({} trades)",
             N, d2, N as f64 / d2.as_secs_f64(), d2.as_nanos() as f64 / N as f64, trades);

    // ---- 3. cancels ----
    let t2 = Instant::now();
    for i in 0..N {
        let _ = book.cancel_order(i + 1);
    }
    let d3 = t2.elapsed();
    println!("cancels         : {:>10} orders in {:?}  ->  {:.0} orders/sec, {:.0} ns/order",
             N, d3, N as f64 / d3.as_secs_f64(), d3.as_nanos() as f64 / N as f64);
}
