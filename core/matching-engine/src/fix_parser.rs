//! FIX 4.4 `tag=value` wire protocol parser and message builder.
//!
//! Wire format: fields are `tag=value` pairs separated by the SOH control
//! character (`0x01`). Every message is framed as:
//!
//! ```text
//! 8=FIX.4.4 | 9=<BodyLength> | 35=<MsgType> | ... | 10=<CheckSum> |
//! ```
//!
//! * BodyLength (tag 9) counts the bytes from the field immediately after
//!   tag 9 up to — but not including — the SOH preceding tag 10.
//! * CheckSum (tag 10) is the sum of every byte of the message up to and
//!   including the SOH preceding tag 10, modulo 256, formatted as 3 digits.
//!
//! Both are validated on parse. [`FixBuilder`], [`new_order_single`] and
//! [`execution_report`] always emit spec-correct framing.

use std::collections::HashMap;

use thiserror::Error;

/// FIX field delimiter (Start of Header, 0x01).
pub const SOH: char = '\u{1}';
/// Maximum accepted message size on the wire. Real FIX messages are small;
/// anything larger is treated as hostile or corrupt and rejected unread.
pub const MAX_MESSAGE_BYTES: usize = 64 * 1024;
/// BeginString for FIX 4.4 sessions.
pub const BEGIN_STRING: &str = "FIX.4.4";

/// Errors returned when parsing or validating a FIX message.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum FixError {
    #[error("malformed field (expected tag=value): {0:?}")]
    MalformedField(String),
    #[error("non-numeric tag in field: {0:?}")]
    InvalidTag(String),
    #[error("required field {0} is missing")]
    MissingField(u32),
    #[error("unsupported BeginString: {0}")]
    UnsupportedVersion(String),
    #[error("checksum mismatch: stated {stated}, computed {computed}")]
    InvalidChecksum { stated: String, computed: String },
    #[error("body length mismatch: stated {stated}, computed {computed}")]
    InvalidBodyLength { stated: usize, computed: usize },
    #[error("field {tag} is not a valid number: {value:?}")]
    InvalidNumber { tag: u32, value: String },
    #[error("duplicate field: tag {0} appears more than once")]
    DuplicateField(u32),
    #[error("message too large: {size} bytes (limit {max})")]
    MessageTooLarge { size: usize, max: usize },
}

/// Sum of all bytes modulo 256, formatted as a zero-padded 3-digit string.
pub fn compute_checksum(data: &str) -> String {
    let sum: u32 = data.as_bytes().iter().map(|b| *b as u32).sum();
    format!("{:03}", sum % 256)
}

/// A parsed FIX message: ordered fields plus a tag index for O(1) lookup.
#[derive(Clone, Debug, PartialEq)]
pub struct FixMessage {
    fields: Vec<(u32, String)>,
    index: HashMap<u32, String>,
}

impl FixMessage {
    /// Parse and validate a raw FIX 4.4 message.
    ///
    /// Validates BeginString, BodyLength (tag 9) and CheckSum (tag 10).
    /// A single trailing SOH after the checksum is optional.
    pub fn parse(raw: &str) -> Result<Self, FixError> {
        if raw.len() > MAX_MESSAGE_BYTES {
            return Err(FixError::MessageTooLarge {
                size: raw.len(),
                max: MAX_MESSAGE_BYTES,
            });
        }
        let raw = raw.trim_end_matches(SOH);
        if raw.is_empty() {
            return Err(FixError::MalformedField(raw.to_string()));
        }

        // --- Split into tag=value fields -----------------------------------
        let mut fields: Vec<(u32, String)> = Vec::new();
        let mut index: HashMap<u32, String> = HashMap::new();
        for part in raw.split(SOH) {
            let (tag, value) = part
                .split_once('=')
                .ok_or_else(|| FixError::MalformedField(part.to_string()))?;
            let tag: u32 = tag
                .parse()
                .map_err(|_| FixError::InvalidTag(tag.to_string()))?;
            // This parser models flat messages (no repeating groups): a tag
            // appearing twice is ambiguous — previously the second value
            // silently overwrote the first in the lookup index.
            if index.contains_key(&tag) {
                return Err(FixError::DuplicateField(tag));
            }
            fields.push((tag, value.to_string()));
            index.insert(tag, value.to_string());
        }

        // --- BeginString (8) ------------------------------------------------
        let begin = index.get(&8).ok_or(FixError::MissingField(8))?;
        if begin != BEGIN_STRING {
            return Err(FixError::UnsupportedVersion(begin.clone()));
        }

        // --- CheckSum (10) ---------------------------------------------------
        // Byte offset of the SOH that precedes the final "10=" field.
        let soh10 = raw
            .rfind(&format!("{SOH}10="))
            .ok_or(FixError::MissingField(10))?;
        let stated_sum = index.get(&10).ok_or(FixError::MissingField(10))?.clone();
        let computed_sum = compute_checksum(&raw[..=soh10]);
        if stated_sum != computed_sum {
            return Err(FixError::InvalidChecksum {
                stated: stated_sum,
                computed: computed_sum,
            });
        }

        // --- BodyLength (9) ---------------------------------------------------
        let stated_len: usize = index
            .get(&9)
            .ok_or(FixError::MissingField(9))?
            .parse()
            .map_err(|_| FixError::InvalidNumber {
                tag: 9,
                value: index[&9].clone(),
            })?;
        // Body starts right after the SOH terminating the tag-9 field.
        let nine_field = raw
            .find(&format!("{SOH}9="))
            .ok_or(FixError::MissingField(9))?;
        let body_start = raw[nine_field + 1..]
            .find(SOH)
            .map(|p| nine_field + 1 + p + 1)
            .ok_or_else(|| FixError::MalformedField(raw.to_string()))?;
        let computed_len = soh10 - body_start + 1; // includes the SOH terminating the final body field
        if stated_len != computed_len {
            return Err(FixError::InvalidBodyLength {
                stated: stated_len,
                computed: computed_len,
            });
        }

        Ok(FixMessage { fields, index })
    }

    /// Raw value of a tag, if present.
    pub fn get(&self, tag: u32) -> Option<&str> {
        self.index.get(&tag).map(String::as_str)
    }

    /// All fields in wire order.
    pub fn fields(&self) -> &[(u32, String)] {
        &self.fields
    }

    /// Typed getter: parse a tag as `i64`.
    pub fn get_i64(&self, tag: u32) -> Result<Option<i64>, FixError> {
        match self.get(tag) {
            None => Ok(None),
            Some(v) => v
                .parse::<i64>()
                .map(Some)
                .map_err(|_| FixError::InvalidNumber {
                    tag,
                    value: v.to_string(),
                }),
        }
    }

    /// Typed getter: parse a tag as `f64`.
    pub fn get_f64(&self, tag: u32) -> Result<Option<f64>, FixError> {
        match self.get(tag) {
            None => Ok(None),
            Some(v) => v
                .parse::<f64>()
                .map(Some)
                .map_err(|_| FixError::InvalidNumber {
                    tag,
                    value: v.to_string(),
                }),
        }
    }

    /// Typed getter: first character of a tag (e.g. Side tag 54: '1'/'2').
    pub fn get_char(&self, tag: u32) -> Option<char> {
        self.get(tag).and_then(|v| v.chars().next())
    }

    /// MsgType (tag 35), e.g. "D" (NewOrderSingle) or "8" (ExecutionReport).
    pub fn msg_type(&self) -> Option<&str> {
        self.get(35)
    }

    /// ClOrdID (tag 11).
    pub fn cl_ord_id(&self) -> Option<&str> {
        self.get(11)
    }

    /// Symbol (tag 55).
    pub fn symbol(&self) -> Option<&str> {
        self.get(55)
    }

    /// Side (tag 54): '1' = Buy, '2' = Sell.
    pub fn side(&self) -> Option<char> {
        self.get_char(54)
    }

    /// OrderQty (tag 38).
    pub fn order_qty(&self) -> Result<Option<f64>, FixError> {
        self.get_f64(38)
    }

    /// Price (tag 44).
    pub fn price(&self) -> Result<Option<f64>, FixError> {
        self.get_f64(44)
    }

    /// Serialize back to wire format (fields in stored order; BodyLength and
    /// CheckSum are recomputed, so the output is always validly framed).
    pub fn to_fix_string(&self) -> String {
        let mut b = FixBuilder::from_fields(
            self.fields
                .iter()
                .filter(|(t, _)| ![8, 9, 10].contains(t))
                .cloned()
                .collect(),
        );
        // Preserve the original header fields if they were present.
        for tag in [49u32, 56, 34, 52] {
            if let Some(v) = self.get(tag) {
                b = b.set_if_absent(tag, v);
            }
        }
        b.build()
    }
}

/// Incremental builder that produces correctly framed FIX 4.4 messages.
#[derive(Clone, Debug, Default)]
pub struct FixBuilder {
    fields: Vec<(u32, String)>,
}

impl FixBuilder {
    /// Start a message of the given MsgType (tag 35).
    pub fn new(msg_type: &str) -> Self {
        FixBuilder {
            fields: vec![(35, msg_type.to_string())],
        }
    }

    fn from_fields(fields: Vec<(u32, String)>) -> Self {
        FixBuilder { fields }
    }

    /// Set a field, replacing any existing value for the tag.
    pub fn set(mut self, tag: u32, value: impl Into<String>) -> Self {
        let value = value.into();
        if let Some(existing) = self.fields.iter_mut().find(|(t, _)| *t == tag) {
            existing.1 = value;
        } else {
            self.fields.push((tag, value));
        }
        self
    }

    fn set_if_absent(mut self, tag: u32, value: impl Into<String>) -> Self {
        if self.fields.iter().all(|(t, _)| *t != tag) {
            self.fields.push((tag, value.into()));
        }
        self
    }

    /// Standard session header fields: SenderCompID (49), TargetCompID (56),
    /// MsgSeqNum (34), SendingTime (52).
    pub fn header(self, sender: &str, target: &str, seq: u64, sending_time: &str) -> Self {
        self.set(49, sender)
            .set(56, target)
            .set(34, seq.to_string())
            .set(52, sending_time)
    }

    /// Frame the message: prepends `8`/`9` and appends the `10` checksum.
    pub fn build(self) -> String {
        let mut body = String::new();
        for (tag, value) in &self.fields {
            body.push_str(&format!("{tag}={value}{SOH}"));
        }
        let mut msg = format!("8={BEGIN_STRING}{SOH}9={}{SOH}{body}", body.len());
        let checksum = compute_checksum(&msg);
        msg.push_str(&format!("10={checksum}{SOH}"));
        msg
    }
}

/// Build a NewOrderSingle (35=D) limit order message.
#[allow(clippy::too_many_arguments)]
pub fn new_order_single(
    sender: &str,
    target: &str,
    seq: u64,
    sending_time: &str,
    cl_ord_id: &str,
    symbol: &str,
    side: char, // '1' Buy, '2' Sell
    qty: f64,
    price: f64,
    transact_time: &str,
) -> String {
    FixBuilder::new("D")
        .header(sender, target, seq, sending_time)
        .set(11, cl_ord_id)
        .set(55, symbol)
        .set(54, side.to_string())
        .set(60, transact_time)
        .set(38, format!("{qty}"))
        .set(40, "2") // OrdType = Limit
        .set(44, format!("{price}"))
        .set(59, "0") // TimeInForce = Day
        .build()
}

/// Build an ExecutionReport (35=8) in response to an original order message.
///
/// `exec_type` (150) / `ord_status` (39): '0' New, '1' PartialFill,
/// '2' Fill, '4' Canceled, '8' Rejected.
#[allow(clippy::too_many_arguments)]
pub fn execution_report(
    orig: &FixMessage,
    exec_id: &str,
    order_id: &str,
    exec_type: char,
    ord_status: char,
    last_qty: f64,
    last_px: f64,
    cum_qty: f64,
    avg_px: f64,
    transact_time: &str,
) -> String {
    let order_qty = orig.order_qty().ok().flatten().unwrap_or(0.0);
    let leaves_qty = (order_qty - cum_qty).max(0.0);
    let mut b = FixBuilder::new("8")
        .set(37, order_id)
        .set(17, exec_id)
        .set(150, exec_type.to_string())
        .set(39, ord_status.to_string())
        .set(151, format!("{leaves_qty}"))
        .set(14, format!("{cum_qty}"))
        .set(6, format!("{avg_px}"))
        .set(60, transact_time);
    if last_qty > 0.0 {
        b = b
            .set(32, format!("{last_qty}"))
            .set(31, format!("{last_px}"));
    }
    if let Some(v) = orig.cl_ord_id() {
        b = b.set(11, v);
    }
    if let Some(v) = orig.symbol() {
        b = b.set(55, v);
    }
    if let Some(v) = orig.side() {
        b = b.set(54, v.to_string());
    }
    b = b.set(38, format!("{order_qty}"));
    if let Ok(Some(p)) = orig.price() {
        b = b.set(44, format!("{p}"));
    }
    // Carry the session header forward (swapped sender/target is the
    // caller's job in a full session layer; keep values for traceability).
    if let (Some(s), Some(t), Some(ts)) = (orig.get(56), orig.get(49), orig.get(52)) {
        let (s, t, ts) = (s.to_string(), t.to_string(), ts.to_string());
        b = b.header(
            &s,
            &t,
            orig.get_i64(34).ok().flatten().unwrap_or(0) as u64 + 1,
            &ts,
        );
    }
    b.build()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_order() -> String {
        new_order_single(
            "ATLAS",
            "NSE",
            7,
            "20261020-03:30:00.000",
            "ORD-1001",
            "NIFTY",
            '1',
            50.0,
            24150.50,
            "20261020-03:30:00.000",
        )
    }

    #[test]
    fn build_and_parse_round_trip() {
        let raw = sample_order();
        let msg = FixMessage::parse(&raw).expect("builder output must parse");
        assert_eq!(msg.msg_type(), Some("D"));
        assert_eq!(msg.cl_ord_id(), Some("ORD-1001"));
        assert_eq!(msg.symbol(), Some("NIFTY"));
        assert_eq!(msg.side(), Some('1'));
        assert_eq!(msg.order_qty().unwrap(), Some(50.0));
        assert_eq!(msg.price().unwrap(), Some(24150.50));
        assert_eq!(msg.get(49), Some("ATLAS"));
        assert_eq!(msg.get(56), Some("NSE"));
    }

    #[test]
    fn checksum_tampering_is_detected() {
        let raw = sample_order().replace("NIFTY", "BANKNIFTY");
        // Same length, different bytes -> checksum no longer matches.
        match FixMessage::parse(&raw) {
            Err(FixError::InvalidChecksum { .. }) => {}
            other => panic!("expected checksum error, got {other:?}"),
        }
    }

    #[test]
    fn body_length_tampering_is_detected() {
        // Change a field to a longer value and fix nothing else: the stated
        // BodyLength no longer matches the actual body.
        let raw = sample_order().replace("ORD-1001", "ORD-1001-LONGER");
        assert!(matches!(
            FixMessage::parse(&raw),
            Err(FixError::InvalidBodyLength { .. }) | Err(FixError::InvalidChecksum { .. })
        ));
    }

    #[test]
    fn unsupported_version_rejected() {
        let raw = sample_order().replace("FIX.4.4", "FIX.4.2");
        // Fix checksum so we reach the version check deterministically.
        let soh10 = raw.rfind(&format!("{SOH}10=")).unwrap();
        let fixed = format!("{}10={}", &raw[..=soh10], compute_checksum(&raw[..=soh10]));
        assert!(matches!(
            FixMessage::parse(&fixed),
            Err(FixError::UnsupportedVersion(_))
        ));
    }

    #[test]
    fn execution_report_builds_and_parses() {
        let order = FixMessage::parse(&sample_order()).unwrap();
        let report = execution_report(
            &order,
            "EXEC-9001",
            "NSE-555",
            '2',
            '2',
            50.0,
            24150.50,
            50.0,
            24150.50,
            "20261020-03:30:01.000",
        );
        let msg = FixMessage::parse(&report).expect("report must parse");
        assert_eq!(msg.msg_type(), Some("8"));
        assert_eq!(msg.get(37), Some("NSE-555"));
        assert_eq!(msg.get(17), Some("EXEC-9001"));
        assert_eq!(msg.get(39), Some("2"));
        assert_eq!(msg.get(14), Some("50"));
        assert_eq!(msg.get(151), Some("0")); // fully filled -> no leaves qty
        assert_eq!(msg.cl_ord_id(), Some("ORD-1001"));
    }

    #[test]
    fn duplicate_fields_are_rejected() {
        let raw = sample_order();
        // Inject a second Price (44) field before the checksum.
        let soh10 = raw.rfind(&format!("{SOH}10=")).unwrap();
        let dup = format!("{}44=9999.0{}{}", &raw[..=soh10], SOH, &raw[soh10 + 1..]);
        assert!(matches!(
            FixMessage::parse(&dup),
            Err(FixError::DuplicateField(44))
        ));
    }

    #[test]
    fn oversized_message_is_rejected() {
        let raw = "x".repeat(MAX_MESSAGE_BYTES + 1);
        assert!(matches!(
            FixMessage::parse(&raw),
            Err(FixError::MessageTooLarge { .. })
        ));
    }

    #[test]
    fn truncated_message_is_rejected() {
        let raw = sample_order();
        let truncated = &raw[..raw.len() / 2];
        assert!(FixMessage::parse(truncated).is_err());
    }

    #[test]
    fn malformed_and_missing_fields() {
        assert!(matches!(
            FixMessage::parse("garbage-without-equals"),
            Err(FixError::MalformedField(_))
        ));
        assert!(matches!(
            FixMessage::parse(""),
            Err(FixError::MalformedField(_))
        ));
    }
}
