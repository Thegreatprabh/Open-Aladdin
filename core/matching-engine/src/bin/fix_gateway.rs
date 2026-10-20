//! fix_gateway — FIX 4.4 application-level gateway for OPEN-ATLAS.
//!
//! Every `NewOrderSingle` (35=D) is parsed, validated, passed through the
//! pre-trade risk gate, and matched on the shared limit order book.
//! `ExecutionReport`s are generated from **real engine outcomes** — this
//! binary never fabricates a fill. `OrderCancelRequest` (35=F) cancels a
//! resting order by its original ClOrdID.
//!
//! Session scope: Logon (35=A) is required before any application
//! message and is authenticated by SenderCompID plus an optional shared
//! password (`FIX_GATEWAY_PASSWORD`, tag 554). Heartbeat, test-request
//! and logout are handled; inbound sequence numbers are tracked
//! (duplicates close the session, gaps are audit-logged); outbound
//! messages carry per-session sequence numbers; connections are capped
//! and rate-limited; security-relevant events go to an audit log.
//!
//! Still NOT implemented: sequence-gap recovery, resend requests,
//! store-and-forward, and TLS. Deploy only on private networks (the
//! Compose file publishes the port on host loopback only) and never
//! describe this as a certified FIX session endpoint.
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
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Instant;
use std::time::{SystemTime, UNIX_EPOCH};

use open_atlas_matching_engine::fix_parser::{
    execution_report_with_seq, FixBuilder, FixMessage, MAX_MESSAGE_BYTES, SOH,
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
/// Per-connection FIX session state (see the file-level session scope).
struct Session {
    logged_on: bool,
    sender: String,
    expected_in: u64,
    out_seq: u64,
    close: bool,
    win_start: Instant,
    win_count: u32,
}

impl Session {
    fn new() -> Self {
        Self {
            logged_on: false,
            sender: String::new(),
            expected_in: 1,
            out_seq: 1,
            close: false,
            win_start: Instant::now(),
            win_count: 0,
        }
    }
    fn next_out(&mut self) -> u64 {
        let s = self.out_seq;
        self.out_seq += 1;
        s
    }
    fn peer(&self, msg: &FixMessage) -> String {
        if self.sender.is_empty() {
            msg.get(49).unwrap_or("UNKNOWN").to_string()
        } else {
            self.sender.clone()
        }
    }
    /// Rolling-window rate limit: at most 600 messages per 10 seconds.
    fn allow(&mut self) -> bool {
        let now = Instant::now();
        if now.duration_since(self.win_start).as_secs() >= 10 {
            self.win_start = now;
            self.win_count = 0;
        }
        self.win_count += 1;
        self.win_count <= 600
    }
}

fn gateway_password() -> Option<String> {
    std::env::var("FIX_GATEWAY_PASSWORD")
        .ok()
        .filter(|p| !p.is_empty())
}

fn password_ok(expected: Option<&str>, provided: Option<&str>) -> bool {
    match expected {
        None => true,
        Some(exp) => provided == Some(exp),
    }
}

/// FIX Reject (35=3) for session-level problems.
fn session_reject(session: &mut Session, msg: &FixMessage, text: &str) -> String {
    let seq = session.next_out();
    let peer = session.peer(msg);
    let ref_seq = msg.get(34).unwrap_or("0").to_string();
    FixBuilder::new("3")
        .set(45, ref_seq)
        .set(58, text)
        .set(373, "5")
        .header("ATLAS", &peer, seq, &now_ts())
        .build()
}

fn rejected_report(msg: &FixMessage, state: &mut State, seq: u64, text: &str) -> String {
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
        b = b.header(&sender, &target, seq, &now_ts());
    }
    b.build()
}

/// Handle one incoming message within a session. Returns FIX replies.
fn handle_message(state: &mut State, session: &mut Session, raw: &str) -> Vec<String> {
    let msg = match FixMessage::parse(raw) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[audit] parse error: {e}");
            return Vec::new();
        }
    };
    // Inbound sequence enforcement applies to every message, Logon incl.
    match msg.get_i64(34) {
        Ok(Some(n)) if n >= 1 => {
            let n = n as u64;
            if n < session.expected_in {
                eprintln!(
                    "[audit] duplicate/old seq {n} from {} (expected {}), closing",
                    session.peer(&msg),
                    session.expected_in
                );
                session.close = true;
                return Vec::new();
            }
            if n > session.expected_in {
                eprintln!(
                    "[audit] seq gap from {}: got {n}, expected {} (resend recovery unsupported)",
                    session.peer(&msg),
                    session.expected_in
                );
            }
            session.expected_in = n + 1;
        }
        _ => {
            return vec![session_reject(
                session,
                &msg,
                "MsgSeqNum (34) missing or invalid",
            )]
        }
    }
    match msg.msg_type() {
        Some("A") => {
            let sender = msg.get(49).unwrap_or("").to_string();
            if sender.is_empty() {
                return vec![session_reject(
                    session,
                    &msg,
                    "Logon missing SenderCompID (49)",
                )];
            }
            if msg.get(56) != Some("ATLAS") {
                return vec![session_reject(session, &msg, "unknown TargetCompID (56)")];
            }
            if !password_ok(gateway_password().as_deref(), msg.get(554)) {
                eprintln!("[audit] logon authentication FAILED for {sender}");
                session.close = true;
                return vec![session_reject(session, &msg, "authentication failed")];
            }
            session.logged_on = true;
            session.sender = sender.clone();
            eprintln!("[audit] logon accepted: {sender}");
            let seq = session.next_out();
            vec![FixBuilder::new("A")
                .set(98, "0")
                .set(108, "30")
                .header("ATLAS", &sender, seq, &now_ts())
                .build()]
        }
        Some("0") => Vec::new(), // Heartbeat: no reply required.
        Some("1") => {
            // TestRequest: answer with a Heartbeat echoing tag 112.
            let seq = session.next_out();
            let mut b = FixBuilder::new("0").header("ATLAS", &session.peer(&msg), seq, &now_ts());
            if let Some(id) = msg.get(112) {
                let id = id.to_string();
                b = b.set(112, id);
            }
            vec![b.build()]
        }
        Some("5") => {
            let seq = session.next_out();
            session.close = true;
            vec![FixBuilder::new("5")
                .header("ATLAS", &session.peer(&msg), seq, &now_ts())
                .build()]
        }
        Some(_) if !session.logged_on => vec![session_reject(
            session,
            &msg,
            "Logon (35=A) required before application messages",
        )],
        Some("D") => handle_new_order(state, session, &msg),
        Some("F") => handle_cancel(state, session, &msg),
        other => {
            eprintln!(
                "[audit] unsupported MsgType {other:?} from {}",
                session.peer(&msg)
            );
            vec![session_reject(session, &msg, "unsupported MsgType")]
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

fn handle_new_order(state: &mut State, session: &mut Session, msg: &FixMessage) -> Vec<String> {
    let (side, qty, price) = match validate_new_order(msg) {
        Ok(v) => v,
        Err(reason) => return vec![rejected_report(msg, state, session.next_out(), &reason)],
    };
    let cl_ord_id = msg.cl_ord_id().unwrap_or("").to_string();
    if cl_ord_id.is_empty() {
        return vec![rejected_report(
            msg,
            state,
            session.next_out(),
            "missing ClOrdID (11)",
        )];
    }
    if state.cl_to_internal.contains_key(&cl_ord_id) {
        return vec![rejected_report(
            msg,
            state,
            session.next_out(),
            "duplicate ClOrdID (11)",
        )];
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
        return vec![rejected_report(
            msg,
            state,
            session.next_out(),
            &e.to_string(),
        )];
    }

    let ticks = (price * 100.0).round() as i64;
    if ticks <= 0 {
        return vec![rejected_report(
            msg,
            state,
            session.next_out(),
            "price rounds to zero ticks",
        )];
    }
    let internal_id = state.next_internal_id;
    state.next_internal_id += 1;
    let order_id = format!("OA-{internal_id}");
    let order = Order::limit(internal_id, side, ticks, qty, internal_id);

    match state.book.insert_order(order) {
        Err(e) => vec![rejected_report(
            msg,
            state,
            session.next_out(),
            &e.to_string(),
        )],
        Ok(trades) => {
            state.cl_to_internal.insert(cl_ord_id, internal_id);
            let mut replies = Vec::new();
            if trades.is_empty() {
                // Resting, no execution yet: New (150=0 / 39=0).
                replies.push(execution_report_with_seq(
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
                    session.next_out(),
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
                replies.push(execution_report_with_seq(
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
                    session.next_out(),
                ));
            }
            replies
        }
    }
}

fn handle_cancel(state: &mut State, session: &mut Session, msg: &FixMessage) -> Vec<String> {
    let orig_cl = match msg.get(41) {
        Some(v) => v.to_string(),
        None => {
            return vec![rejected_report(
                msg,
                state,
                session.next_out(),
                "missing OrigClOrdID (41)",
            )]
        }
    };
    let internal = match state.cl_to_internal.get(&orig_cl) {
        Some(id) => *id,
        None => {
            return vec![rejected_report(
                msg,
                state,
                session.next_out(),
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
            let (sender, seq) = (session.sender.clone(), session.next_out());
            let b = b.header("ATLAS", &sender, seq, &now_ts());
            vec![b.build()]
        }
        Err(e) => vec![rejected_report(
            msg,
            state,
            session.next_out(),
            &e.to_string(),
        )],
    }
}

/// Extract complete FIX messages from the buffer using proper FIX
/// framing: the message length is taken from BodyLength (tag 9), never
/// from line boundaries or TCP read sizes. A frame is emitted only when
/// its full declared length has arrived AND the bytes at the computed
/// boundary are the `10=` checksum field. Incomplete tails stay buffered;
/// corrupt frames are dropped and the stream resynchronizes on the next
/// `8=FIX` header. Buffer growth is bounded by the caller.
fn extract_messages(buf: &mut String) -> Vec<String> {
    let mut out = Vec::new();
    while let Some(start) = buf.find("8=FIX") {
        if start > 0 {
            buf.drain(..start);
        }
        // Locate the tag-9 field: SOH "9=" <digits> SOH.
        let Some(rel9) = buf.find("\u{1}9=") else {
            if buf.len() > MAX_MESSAGE_BYTES {
                buf.clear();
            }
            break;
        };
        let len_start = rel9 + 3;
        let Some(rel_end) = buf[len_start..].find(SOH) else {
            if buf.len() > MAX_MESSAGE_BYTES {
                buf.clear();
            }
            break;
        };
        let digits = &buf[len_start..len_start + rel_end];
        let Ok(body_len) = digits.parse::<usize>() else {
            // Corrupt header: skip one byte and resynchronize.
            buf.drain(..1);
            continue;
        };
        let body_start = len_start + rel_end + 1;
        // The trailer field "10=ddd" + SOH is exactly 7 bytes.
        let Some(total) = body_start
            .checked_add(body_len)
            .and_then(|n| n.checked_add(7))
        else {
            buf.drain(..1);
            continue;
        };
        if buf.len() < total {
            break; // incomplete frame: wait for more bytes
        }
        let candidate: String = buf.drain(..total).collect();
        let b = candidate.as_bytes();
        if b[total - 7] == b'1' && b[total - 6] == b'0' && b[total - 5] == b'=' {
            out.push(candidate);
        } else {
            eprintln!("[gateway] frame boundary mismatch, frame dropped");
        }
    }
    out
}

fn handle_conn(mut stream: TcpStream, state: Arc<Mutex<State>>) {
    let mut session = Session::new();
    let mut buf = String::new();
    let mut chunk = [0u8; 8192];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => return, // peer closed
            Ok(n) => {
                buf.push_str(&String::from_utf8_lossy(&chunk[..n]));
                if buf.len() > MAX_MESSAGE_BYTES * 2 {
                    eprintln!("[audit] oversized buffer, dropping connection");
                    return;
                }
                for raw in extract_messages(&mut buf) {
                    if !session.allow() {
                        eprintln!("[audit] rate limit exceeded, closing connection");
                        return;
                    }
                    let replies = {
                        let mut st = state.lock().unwrap();
                        handle_message(&mut st, &mut session, &raw)
                    };
                    for r in replies {
                        if stream.write_all(r.as_bytes()).is_err() {
                            return;
                        }
                    }
                    if session.close {
                        return;
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
        "[gateway] session: logon auth + seq tracking; no resend recovery / no TLS; paper margin ₹{PAPER_MARGIN:.0}"
    );
    match gateway_password() {
        Some(_) => println!("[gateway] logon password authentication: ENABLED"),
        None => println!(
            "[gateway] WARNING: FIX_GATEWAY_PASSWORD not set — Logon accepts any SenderCompID; internal networks only"
        ),
    }
    static ACTIVE: AtomicUsize = AtomicUsize::new(0);
    const MAX_CONNECTIONS: usize = 32;
    let state = Arc::new(Mutex::new(State::new()));
    for stream in listener.incoming() {
        match stream {
            Ok(s) => {
                if ACTIVE.fetch_add(1, Ordering::SeqCst) >= MAX_CONNECTIONS {
                    ACTIVE.fetch_sub(1, Ordering::SeqCst);
                    eprintln!("[audit] connection cap ({MAX_CONNECTIONS}) reached, refusing");
                    continue;
                }
                let st = Arc::clone(&state);
                thread::spawn(move || {
                    handle_conn(s, st);
                    ACTIVE.fetch_sub(1, Ordering::SeqCst);
                });
            }
            Err(e) => eprintln!("[gateway] accept error: {e}"),
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use open_atlas_matching_engine::fix_parser::new_order_single;

    fn nos(cl: &str, side: char, qty: f64, price: f64) -> String {
        nos_seq(cl, side, qty, price, 1)
    }

    fn nos_seq(cl: &str, side: char, qty: f64, price: f64, seq: u64) -> String {
        new_order_single(
            "TEST",
            "ATLAS",
            seq,
            "20261020-03:30:00.000",
            cl,
            "NIFTY",
            side,
            qty,
            price,
            "20261020-03:30:00.000",
        )
    }

    fn logon(seq: u64) -> String {
        FixBuilder::new("A")
            .set(98, "0")
            .set(108, "30")
            .header("TEST", "ATLAS", seq, "20261020-03:30:00.000")
            .build()
    }

    #[test]
    fn fragmented_message_is_reassembled() {
        let wire = nos("F1", '1', 5.0, 100.0);
        let mut buf = String::new();
        let mut got = Vec::new();
        // Feed three bytes at a time, as a fragmented TCP stream would.
        for chunk in wire.as_bytes().chunks(3) {
            buf.push_str(std::str::from_utf8(chunk).unwrap());
            got.extend(extract_messages(&mut buf));
        }
        assert_eq!(got.len(), 1);
        assert_eq!(got[0], wire);
    }

    #[test]
    fn concatenated_messages_are_split() {
        let mut buf = nos("A1", '2', 10.0, 100.0);
        buf.push_str(&nos("A2", '1', 4.0, 100.0));
        let got = extract_messages(&mut buf);
        assert_eq!(got.len(), 2);
        assert!(got[0].contains("11=A1"));
        assert!(got[1].contains("11=A2"));
        assert!(buf.is_empty());
    }

    #[test]
    fn garbage_prefix_is_skipped_and_partial_tail_kept() {
        let wire = nos("G1", '1', 5.0, 100.0);
        let half = wire.len() / 2;
        let mut buf = format!("random noise\r\n{}", &wire[..half]);
        assert!(extract_messages(&mut buf).is_empty());
        buf.push_str(&wire[half..]);
        let got = extract_messages(&mut buf);
        assert_eq!(got.len(), 1);
    }

    #[test]
    fn password_policy() {
        assert!(password_ok(None, None));
        assert!(password_ok(None, Some("anything")));
        assert!(password_ok(Some("s3cret"), Some("s3cret")));
        assert!(!password_ok(Some("s3cret"), Some("wrong")));
        assert!(!password_ok(Some("s3cret"), None));
    }

    #[test]
    fn session_flow_routes_through_risk_and_book() {
        let mut st = State::new();
        let mut sess = Session::new();
        // Order before Logon: session Reject, nothing reaches the book.
        let r0 = handle_message(&mut st, &mut sess, &nos_seq("X1", '1', 5.0, 100.0, 1));
        assert_eq!(r0.len(), 1);
        assert!(r0[0].contains("35=3"), "pre-logon reject: {}", r0[0]);
        assert_eq!(st.book.open_order_count(), 0);
        // Logon is accepted and answered with a Logon.
        let r1 = handle_message(&mut st, &mut sess, &logon(2));
        assert_eq!(r1.len(), 1);
        assert!(r1[0].contains("35=A"), "logon reply: {}", r1[0]);
        assert!(sess.logged_on);
        // Resting sell, then a crossing buy: the fill is at maker price.
        let r2 = handle_message(&mut st, &mut sess, &nos_seq("S1", '2', 10.0, 100.0, 3));
        assert!(r2[0].contains("150=0"), "resting ack: {}", r2[0]);
        let r3 = handle_message(&mut st, &mut sess, &nos_seq("B1", '1', 4.0, 100.50, 4));
        assert!(r3[0].contains("150=F") && r3[0].contains("31=100"));
        // Oversized order: risk rejection, not an acknowledgement.
        let r4 = handle_message(&mut st, &mut sess, &nos_seq("B2", '1', 200_000.0, 100.0, 5));
        assert!(r4[0].contains("150=8"));
        // TestRequest is answered by a Heartbeat echoing tag 112.
        let tr = FixBuilder::new("1")
            .set(112, "PING7")
            .header("TEST", "ATLAS", 6, "20261020-03:30:00.000")
            .build();
        let r5 = handle_message(&mut st, &mut sess, &tr);
        assert!(r5[0].contains("35=0") && r5[0].contains("112=PING7"));
        // A duplicate (old) sequence number closes the session silently.
        let r6 = handle_message(&mut st, &mut sess, &nos_seq("B3", '1', 1.0, 100.0, 3));
        assert!(r6.is_empty() && sess.close);
    }

    #[test]
    fn garbage_never_replies() {
        let mut st = State::new();
        let mut sess = Session::new();
        assert!(handle_message(&mut st, &mut sess, "not fix at all").is_empty());
    }
}
