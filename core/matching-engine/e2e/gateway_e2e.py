import socket, subprocess, sys, time, os, signal

SOH = "\x01"
def frame(fields):
    body = "".join(f"{t}={v}{SOH}" for t, v in fields)
    msg = f"8=FIX.4.4{SOH}9={len(body)}{SOH}{body}"
    chk = sum(msg.encode()) % 256
    return f"{msg}10={chk:03d}{SOH}"

def parse(raw):
    out = {}
    for part in raw.split(SOH):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out

def recv_msgs(sock, buf, timeout=3.0):
    sock.settimeout(timeout)
    msgs = []
    try:
        while True:
            data = sock.recv(65536)
            if not data: break
            buf += data.decode(errors="replace")
            while f"{SOH}10=" in buf:
                idx = buf.rfind(f"{SOH}10=")
                end = buf.find(SOH, idx + 4)
                if end == -1: break
                msgs.append(buf[:end + 1]); buf = buf[end + 1:]
            if msgs and len(msgs) >= 1:
                # small grace period for additional messages
                sock.settimeout(0.35)
    except socket.timeout:
        pass
    return msgs, buf

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
proc = subprocess.Popen([os.path.join(HERE, "target/release/fix_gateway")],
                        env={**os.environ, "FIX_GATEWAY_ADDR": "127.0.0.1:19101", "FIX_GATEWAY_DEV": "1"},
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    time.sleep(1.2)
    s = socket.create_connection(("127.0.0.1", 19101), timeout=5)
    buf = ""

    # 0a) Order before Logon -> session Reject (35=3), nothing accepted
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,1),(11,"C0"),(55,"NIFTY"),(54,"2"),(38,10),(40,"2"),(44,"100.00")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("0a) pre-logon rejected:", m.get("35"), "|", m.get("58", "")[:45])
    assert m.get("35") == "3", msgs

    # 0b) Logon -> Logon reply (35=A); TestRequest -> Heartbeat echoing 112
    s.sendall(frame([(35,"A"),(49,"TEST"),(56,"ATLAS"),(34,2),(98,"0"),(108,"30")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("0b) logon accepted:", m.get("35"))
    assert m.get("35") == "A", msgs
    s.sendall(frame([(35,"1"),(49,"TEST"),(56,"ATLAS"),(34,3),(112,"PING1")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("0c) heartbeat echo:", m.get("35"), m.get("112"))
    assert m.get("35") == "0" and m.get("112") == "PING1", msgs

    # 1) Resting sell: 10 @ 100.00 -> expect New ack (150=0, 39=0)
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,4),(11,"C1"),(55,"NIFTY"),(54,"2"),(38,10),(40,"2"),(44,"100.00")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("1) resting sell ack:", m.get("150"), m.get("39"), m.get("37"))
    assert m.get("150") == "0" and m.get("39") == "0", msgs

    # 2) Crossing buy: 4 @ 100.50 -> expect trade at maker price 100.00, partial (book left 6)
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,5),(11,"C2"),(55,"NIFTY"),(54,"1"),(38,4),(40,"2"),(44,"100.50")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("2) cross fill:", m.get("150"), m.get("39"), "lastPx", m.get("31"), "lastQty", m.get("32"), "cum", m.get("14"))
    assert m.get("39") == "2" and m.get("31") == "100" and m.get("32") == "4", msgs

    # 3) Fat-finger / qty-limit breach: qty 200000 > 100000 limit -> rejected (150=8)
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,6),(11,"C3"),(55,"NIFTY"),(54,"1"),(38,200000),(40,"2"),(44,"100.00")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("3) oversized rejected:", m.get("150"), m.get("39"), "|", m.get("58", "")[:60])
    assert m.get("150") == "8", msgs

    # 4) Zero qty -> rejected
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,7),(11,"C4"),(55,"NIFTY"),(54,"1"),(38,0),(40,"2"),(44,"100.00")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("4) zero qty rejected:", m.get("150"), "|", m.get("58", "")[:50])
    assert m.get("150") == "8", msgs

    # 5) Cancel the resting remainder of C1 (6 left) -> 150=4, cum filled 4
    s.sendall(frame([(35,"F"),(49,"TEST"),(56,"ATLAS"),(34,8),(11,"CX1"),(41,"C1"),(55,"NIFTY"),(54,"2")]).encode())
    msgs, buf = recv_msgs(s, buf)
    m = parse(msgs[0]) if msgs else {}
    print("5) cancel report:", m.get("150"), m.get("39"), "cum", m.get("14"), "leaves", m.get("151"))
    assert m.get("150") == "4" and m.get("14") == "4", msgs

    # 6) Garbage bytes -> no reply, connection alive; then a valid order still works
    s.sendall(b"this is not fix at all\r\n")
    time.sleep(0.4)
    s.sendall(frame([(35,"D"),(49,"TEST"),(56,"ATLAS"),(34,9),(11,"C6"),(55,"NIFTY"),(54,"2"),(38,3),(40,"2"),(44,"99.00")]).encode())
    msgs, buf = recv_msgs(s, buf)
    oks = [parse(x) for x in msgs if parse(x).get("150") == "0"]
    print("6) after garbage, valid order acked:", bool(oks))
    assert oks, msgs

    # ── fail-closed: a bare instance (no password, no dev mode) rejects logon ──
    bare = subprocess.Popen(
        [os.path.join(HERE, "target/release/fix_gateway")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "FIX_GATEWAY_ADDR": "127.0.0.1:19102"},
    )
    try:
        time.sleep(0.7)
        bs = socket.create_connection(("127.0.0.1", 19102), timeout=5)
        bs.sendall(frame([(35, "A"), (49, "TEST"), (56, "ATLAS"), (34, 1), (98, "0"), (108, "30")]).encode())
        bmsgs, _ = recv_msgs(bs, "")
        bm = parse(bmsgs[0]) if bmsgs else {}
        print("7) fail-closed instance rejects logon:", bm.get("35"), "|", bm.get("58", "")[:45])
        assert bm.get("35") == "3" and "fail-closed" in bm.get("58", ""), bmsgs
        bs.close()
    finally:
        bare.terminate()

    print("E2E GATEWAY: ALL PASS")
finally:
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=5)
    print("--- gateway log tail ---")
    print("\n".join(out.splitlines()[-4:]))
