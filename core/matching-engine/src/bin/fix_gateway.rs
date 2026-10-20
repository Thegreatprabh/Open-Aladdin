//! fix_gateway — FIX 4.4 application-level gateway for OPEN-ATLAS.
//!
//! Every `NewOrderSingle` (35=D) is parsed, validated, passed through the
//! pre-trade risk gate, and matched on the shared limit order book.
//! `ExecutionReport`s are generated from **real engine outcomes** — this
//! binary never fabricates a fill. `OrderCancelRequest` (35=F) cancels a
//! resting order by its original ClOrdID.
//!
//! Honest scope: this is an application-level gateway, **not** a complete
//! FIX session implementation. It validates framing per message
//! (BeginString / BodyLength / CheckSum) but provides no sequence-gap
//! recovery, resend requests, or store-and-forward. Do not expose it as a
//! production FIX session endpoint.
//!
//! Documented simplifications of this paper gateway:
//! * The fat-finger reference price is the last traded price when one
//!   exists, otherwise the order's own limit price.
//! * Available margin is a fixed paper balance ([`PAPER_MARGIN`]) checked
//!   per order — a pre-trade check, not portfolio margin accounting.
//! * Only limit orders (40=2) are accepted.
//! * Matching is serialized behind a single mutex: strict ordering, no
//!   parallel matching. Suitable for paper/research workloads.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use open_atlas_matching_engine::fix_parser::{
    execution_report, FixBuilder, FixMessage, MAX_MESSAGE_BYTES,
};
use open_atlas_matching_engine::{Order, OrderBook, OrderRequest, RiskGate, Side};

/// Fixed paper balance (₹1 crore, as in the terminal) for per-order margin checks.
const PAPER_MARGIN: f64 = 10_000_000.0;
/// Listen address (override with FIX_GATEWAY_ADDR).
const DEFAULT_ADDR: &str = "0.0.0.0:9101";

struct State {
    book: OrderBook,
    gate: RiskGate,
    next_internal_id: u64,
    next_exec_seq: u64,
    last_trade_price: Option<f64>,
    /// ClOrdID -> internal order id, for cancel routing.
    cl_to_internal: HashMap<String, u64>,
}

impl State {
    fn new() -> Self {
        State {
            book: OrderBook::new(),
            gate: RiskGate::default(),
            next_internal_id: 1,
            next_exec_seq: 1,
            last_trade_price: None,
            cl_to_internal: HashMap::new(),
        }
    }

    fn next_exec_id(&mut self) -> String {
        let id = format!("EX-{}", self.next_exec_seq);
        self.next_exec_seq += 1;
        id
    }
}

/// FIX UTC timestamp `YYYYMMDD-HH:MM:SS.mmm` from the system clock.
fn now_ts() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let millis = now.subsec_millis();
    let days = secs.div_euclid(86_400);
    let secs_of_day = secs.rem_euclid(86_400);
    // Civil-from-days (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if m <= 2 { y + 1 } else { y };
    format!(
        "{:04}{:02}{:02}-{:02}:{:02}:{:02}.{:03}",
        year,
        m,
        d,
        secs_of_day / 3600,
        (secs_of_day % 3600) / 60,
        secs_of_day % 60,
        millis
    )
}

/// A rejection ExecutionReport (150=8 / 39=8) with the reason in tag 58,
/// built directly so framing (BodyLength/CheckSum) is always correct.
fn rejected_report(msg: &FixMessage, state: &mut State, text: &str) -> String {
    let mut b = FixBuilder::new("8")
        .set(37, "NONE")
        .set(17, state.next_exec_id())
        .set(150, "8")
        .set(39, "8")
        .set(58, text);
    if let Some(v) = msg.cl_ord_id() {
        b = b.set(11, v);
    }
    if let Some(v) = msg.symbol() {
        b = b.set(55, v);
    }
    if let Some(v) = msg.side() {
        b = b.set(54, v.to_string());
    }
    if let Ok(Some(q)) = msg.order_qty() {
        b = b.set(38, format!("{q}"));
    }
    if let (Some(sender), Some(target)) = (msg.get(56), msg.get(49)) {
        let (sender, target) = (sender.to_string(), target.to_string());
        b = b.header(&sender, &target, 0, &now_ts());
    }
    b.build()
}

/// Handle one incoming message. Returns zero or more FIX replies.
fn handle_message(state: &mut State, raw: &str) -> Vec<String> {
    let msg = match FixMessage::parse(raw) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[gateway] parse error: {e}");
            return Vec::new();
        }
    };
    match msg.msg_type() {
        Some("D") => handle_new_order(state, &msg),
        Some("F") => handle_cancel(state, &msg),
        other => {
            eprintln!("[gateway] ignoring unsupported MsgType {other:?}");
            Vec::new()
        }
    }
}

fn validate_new_order(msg: &FixMessage) -> Result<(Side, u64, f64), String> {
    let side = match msg.side() {
        Some('1') => Side::Bid,
        Some('2') => Side::Ask,
        _ => return Err("missing or invalid Side (54)".to_string()),
    };
    if let Some(t) = msg.get(40) {
        if t != "2" {
            return Err("only limit orders (40=2) are supported".to_string());
        }
    }
    let qty_f = msg
        .order_qty()
        .map_err(|_| "OrderQty (38) is not a number".to_string())?
        .ok_or("missing OrderQty (38)")?;
    if !qty_f.is_finite() || qty_f <= 0.0 || qty_f.fract() != 0.0 || qty_f > u64::MAX as f64 {
        return Err("OrderQty (38) must be a positive whole number".to_string());
    }
    let price = msg
        .price()
        .map_err(|_| "Price (44) is not a number".to_string())?
        .ok_or("missing Price (44) for limit order")?;
    if !price.is_finite() || price <= 0.0 {
        return Err("Price (44) must be positive and finite".to_string());
    }
    Ok((side, qty_f as u64, price))
}

fn handle_new_order(state: &mut State, msg: &FixMessage) -> Vec<String> {
    let (side, qty, price) = match validate_new_order(msg) {
        Ok(v) => v,
        Err(reason) => return vec![rejected_report(msg, state, &reason)],
    };
    let cl_ord_id = msg.cl_ord_id().unwrap_or("").to_string();
    if cl_ord_id.is_empty() {
        return vec![rejected_report(msg, state, "missing ClOrdID (11)")];
    }
    if state.cl_to_internal.contains_key(&cl_ord_id) {
        return vec![rejected_report(msg, state, "duplicate ClOrdID (11)")];
    }

    // Risk gate: reference = last trade price if any, else the limit price.
    let reference = state.last_trade_price.unwrap_or(price);
    let req = OrderRequest {
        side,
        price,
        qty,
        reference_price: reference,
        underlying_price: reference,
        available_margin: PAPER_MARGIN,
    };
    if let Err(e) = state.gate.check_order(&req) {
        return vec![rejected_report(msg, state, &e.to_string())];
    }

    let ticks = (price * 100.0).round() as i64;
    if ticks <= 0 {
        return vec![rejected_report(msg, state, "price rounds to zero ticks")];
    }
    let internal_id = state.next_internal_id;
    state.next_internal_id += 1;
    let order_id = format!("OA-{internal_id}");
    let order = Order::limit(internal_id, side, ticks, qty, internal_id);

    match state.book.insert_order(order) {
        Err(e) => vec![rejected_report(msg, state, &e.to_string())],
        Ok(trades) => {
            state.cl_to_internal.insert(cl_ord_id, internal_id);
            let mut replies = Vec::new();
            if trades.is_empty() {
                // Resting, no execution yet: New (150=0 / 39=0).
                replies.push(execution_report(
                    msg,
                    &state.next_exec_id(),
                    &order_id,
                    '0',
                    '0',
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    &now_ts(),
                ));
                return replies;
            }
            let mut cum: u64 = 0;
            let mut turnover = 0.0f64;
            for t in &trades {
                cum += t.qty;
                let px = t.price as f64 / 100.0;
                turnover += px * t.qty as f64;
                state.last_trade_price = Some(px);
                let status = if cum >= qty { '2' } else { '1' };
                replies.push(execution_report(
                    msg,
                    &state.next_exec_id(),
                    &order_id,
                    'F',
                    status,
                    t.qty as f64,
                    px,
                    cum as f64,
                    turnover / cum as f64,
                    &now_ts(),
                ));
            }
            replies
        }
    }
}

fn handle_cancel(state: &mut State, msg: &FixMessage) -> Vec<String> {
    let orig_cl = match msg.get(41) {
        Some(v) => v.to_string(),
        None => return vec![rejected_report(msg, state, "missing OrigClOrdID (41)")],
    };
    let internal = match state.cl_to_internal.get(&orig_cl) {
        Some(id) => *id,
        None => {
            return vec![rejected_report(
                msg,
                state,
                "unknown OrigClOrdID (41): order not resting",
            )]
        }
    };
    let resting = state.book.get_order(internal).cloned();
    match state.book.cancel_order(internal) {
        Ok(cancelled) => {
            state.cl_to_internal.remove(&orig_cl);
            let (filled, total) = resting
                .map(|o| (o.filled_qty, o.qty))
                .unwrap_or((cancelled.filled_qty, cancelled.qty));
            let leaves = total.saturating_sub(filled);
            let b = FixBuilder::new("8")
                .set(37, format!("OA-{internal}"))
                .set(17, state.next_exec_id())
                .set(150, "4")
                .set(39, "4")
                .set(11, msg.cl_ord_id().unwrap_or(""))
                .set(41, orig_cl)
                .set(14, format!("{filled}"))
                .set(151, format!("{leaves}"))
                .set(38, format!("{total}"));
            vec![b.build()]
        }
        Err(e) => vec![rejected_report(msg, state, &e.to_string())],
    }
}

/// Extract complete FIX messages from the buffer (framing: a message ends
/// with `10=ddd<SOH>`). Returns the messages and keeps any partial tail.
fn extract_messages(buf: &mut String) -> Vec<String> {
    let mut out = Vec::new();
    while let Some(start) = buf.find("8=FIX") {
        if start > 0 {
            buf.drain(..start);
        }
        // Find the checksum terminator: SOH "10=" ddd SOH
        let bytes = buf.as_bytes();
        let mut end: Option<usize> = None;
        let mut i = 0usize;
        while i + 7 < bytes.len() {
            if bytes[i] == 0x01 && &buf[i + 1..i + 4] == "10=" && bytes[i + 7] == 0x01 {
                let digits = &buf[i + 4..i + 7];
                if digits.chars().all(|c| c.is_ascii_digit()) {
                    end = Some(i + 8);
                    break;
                }
            }
            i += 1;
        }
        if let Some(end) = end {
            out.push(buf.drain(..end).collect());
        } else {
            break; // partial message: keep the tail for the next read
        }
    }
    out
}

fn handle_conn(mut stream: TcpStream, state: Arc<Mutex<State>>) {
    let mut buf = String::new();
    let mut chunk = [0u8; 8192];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => return, // peer closed
            Ok(n) => {
                buf.push_str(&String::from_utf8_lossy(&chunk[..n]));
                if buf.len() > MAX_MESSAGE_BYTES * 2 {
                    eprintln!("[gateway] oversized buffer, dropping connection");
                    return;
                }
                for raw in extract_messages(&mut buf) {
                    let replies = {
                        let mut st = state.lock().unwrap();
                        handle_message(&mut st, &raw)
                    };
                    for r in replies {
                        if stream.write_all(r.as_bytes()).is_err() {
                            return;
                        }
                    }
                }
            }
            Err(_) => return,
        }
    }
}

fn main() -> std::io::Result<()> {
    let addr = std::env::var("FIX_GATEWAY_ADDR").unwrap_or_else(|_| DEFAULT_ADDR.to_string());
    let listener = TcpListener::bind(&addr)?;
    println!("[gateway] OPEN-ATLAS FIX gateway listening on {addr}");
    println!(
        "[gateway] application-level only: no FIX session layer, paper margin ₹{PAPER_MARGIN:.0}"
    );
    let state = Arc::new(Mutex::new(State::new()));
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                let st = Arc::clone(&state);
                thread::spawn(move || handle_conn(s, st));
            }
            Err(e) => eprintln!("[gateway] accept error: {e}"),
        }
    }
    Ok(())
}
