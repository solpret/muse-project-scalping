#!/usr/bin/env python3
"""Bulk listing executor: ACAT #1817 + ADVAGENT #554 (user tap 2026-11-11 06:59 WIB).
One step per invocation: preview -> (parent inspects) -> execute -> verify.
Usage: python3 bulk_list_exec.py <step>
Steps: prev-approve-acat, exec-approve-acat, prev-list-acat, exec-list-acat,
       prev-approve-adv, exec-approve-adv, prev-list-adv, exec-list-adv
State/requestIds kept in bulk_list_exec_state.json (same dir).
Calldata built programmatically (keccak selector + 064x encoding). No manual hex.
"""
import json, subprocess, sys, time, urllib.request, os
from Crypto.Hash import keccak

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "bulk_list_exec_state.json")
BAW = "/home/hatch/workspace/.baw-cli/bin/baw"
AGENT = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
ACATM = "0x3424De685702E8d63BaAC20BB67d1a205db89427"
ACATN = "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC"
ADVM = "0x9d4f73c7c92bb4f680c770d0ed3c71527297a256"
ADVN = "0x7635ef16370ef490298649a188c9205d1d7fda67"
ACAT_PRICE_RAW = 19000 * 10**18   # 19.000 ACN, verified live listings(3106)
ADV_PRICE_WEI = 10**15            # 0,001 BNB, verified live listings(0), Currency.BNB=0
RPCS = ["https://bsc-dataseed1.binance.org", "https://bsc-dataseed2.binance.org",
        "https://bsc.nodereal.io", "https://bsc-dataseed3.binance.org"]

def sel(sig):
    k = keccak.new(digest_bits=256); k.update(sig.encode())
    return "0x" + k.hexdigest()[:8]
def enc_u(n): return format(n, "064x")
def enc_addr(a): return "000000000000000000000000" + a[2:].lower()
def enc_u8(n): return format(n, "064x")

def load_state():
    try:
        with open(STATE) as f: return json.load(f)
    except Exception: return {}
def save_state(s):
    with open(STATE, "w") as f: json.dump(s, f, indent=2)

def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode()
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.load(r)
                if "result" in out and out["result"] is not None: return out["result"]
                last = out
        except Exception as e:
            last = str(e)
    raise RuntimeError(f"RPC failed all endpoints: {last}")

def eth_call(to, data):
    return rpc("eth_call", [{"to": to, "data": data}, "latest"])
def words(h): return [h[i:i+64] for i in range(2, len(h), 64)] if h else []

def baw_preview(to, data, value=0):
    cmd = [BAW, "contract-call", "preview", "--binanceChainId", "56", "--from", AGENT,
           "--to", to, "--value", str(value), "--inputData", data, "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    print("STDOUT:", p.stdout)
    print("STDERR:", p.stderr)
    print("EXIT:", p.returncode)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"_raw": p.stdout, "_exit": p.returncode}

def baw_execute(request_id):
    cmd = [BAW, "contract-call", "execute", "--requestId", request_id, "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print("STDOUT:", p.stdout)
    print("STDERR:", p.stderr)
    print("EXIT:", p.returncode)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"_raw": p.stdout, "_exit": p.returncode}

def wait_receipt(txhash, tries=40):
    for i in range(tries):
        try:
            r = rpc("eth_getTransactionReceipt", [txhash])
            if r: return r
        except Exception as e:
            print("receipt poll err:", e)
        time.sleep(5)
    return None

def assess_preview(j):
    """Print cleanliness verdict; parent decides. Returns True if looks clean."""
    sim = (j.get("data") or {}).get("simulationResult") or j
    code = sim.get("simulationCode")
    err = sim.get("simulationErrorDetail")
    risks = (j.get("data") or {}).get("risks") or {}
    risk_hits = risks.get("riskDetails") or risks.get("riskBehaviors") or []
    print("--- PREVIEW ASSESS ---")
    print(" simulationCode:", code)
    print(" simulationErrorDetail:", err)
    print(" risks:", json.dumps(risks)[:500])
    print(" balanceChanges:", json.dumps(sim.get("balanceChanges"), indent=1)[:2000])
    print(" authorityChanges:", sim.get("authorityChanges"))
    print(" allowanceChanges:", json.dumps(sim.get("allowanceChanges"), indent=1)[:1500])
    print(" requestId:", (j.get("data") or {}).get("requestId"))
    clean = (str(code) == "000000000" and not err and not risk_hits)
    print(" VERDICT:", "CLEAN" if clean else "DIRTY/UNCLEAR - DO NOT EXECUTE without parent review")
    return clean

STEPS = {}

def step_prev_approve_acat(s):
    data = sel("approve(address,uint256)") + enc_addr(ACATM) + enc_u(1817)
    print("approve ACAT #1817 -> ACATMarket, data:", data)
    j = baw_preview(ACATN, data, 0)
    rid = (j.get("data") or {}).get("requestId")
    if assess_preview(j) and rid:
        s["prev_approve_acat"] = rid; save_state(s)
    return j

def step_exec_approve_acat(s):
    rid = s.get("prev_approve_acat")
    assert rid, "no preview requestId - run prev-approve-acat first"
    j = baw_execute(rid)
    tx = (j.get("data") or {}).get("txHash") or j.get("txHash") or j.get("hash") or j.get("transactionHash")
    print("TX:", tx)
    if tx:
        r = wait_receipt(tx)
        print("RECEIPT:", json.dumps(r, indent=1) if r else "TIMEOUT - verify manually")
        ga = eth_call(ACATN, sel("getApproved(uint256)") + enc_u(1817))
        print("getApproved(1817) now:", ga, "-> market?", ga[-40:].lower() == ACATM[2:].lower())
        s["tx_approve_acat"] = tx; save_state(s)
    return j

def step_prev_list_acat(s):
    data = sel("list(uint256,uint256)") + enc_u(1817) + enc_u(ACAT_PRICE_RAW)
    print("list ACAT #1817 @ 19000 ACN raw", ACAT_PRICE_RAW, "data:", data)
    j = baw_preview(ACATM, data, 0)
    rid = (j.get("data") or {}).get("requestId")
    if assess_preview(j) and rid:
        s["prev_list_acat"] = rid; save_state(s)
    return j

def step_exec_list_acat(s):
    rid = s.get("prev_list_acat")
    assert rid, "no preview requestId - run prev-list-acat first"
    j = baw_execute(rid)
    tx = (j.get("data") or {}).get("txHash") or j.get("txHash") or j.get("hash") or j.get("transactionHash")
    print("TX:", tx)
    if tx:
        r = wait_receipt(tx)
        print("RECEIPT:", json.dumps(r, indent=1) if r else "TIMEOUT - verify manually")
        w = words(eth_call(ACATM, sel("listings(uint256)") + enc_u(1817)))
        print("listings(1817): seller 0x" + w[0][-40:], "price", int(w[1], 16))
        print("isValid(1817):", int(eth_call(ACATM, sel("isValid(uint256)") + enc_u(1817)), 16))
        s["tx_list_acat"] = tx; save_state(s)
    return j

def step_prev_approve_adv(s):
    data = sel("approve(address,uint256)") + enc_addr(ADVM) + enc_u(554)
    print("approve ADVAGENT #554 -> AdventureMarketplace, data:", data)
    j = baw_preview(ADVN, data, 0)
    rid = (j.get("data") or {}).get("requestId")
    if assess_preview(j) and rid:
        s["prev_approve_adv"] = rid; save_state(s)
    return j

def step_exec_approve_adv(s):
    rid = s.get("prev_approve_adv")
    assert rid, "no preview requestId - run prev-approve-adv first"
    j = baw_execute(rid)
    tx = (j.get("data") or {}).get("txHash") or j.get("txHash") or j.get("hash") or j.get("transactionHash")
    print("TX:", tx)
    if tx:
        r = wait_receipt(tx)
        print("RECEIPT:", json.dumps(r, indent=1) if r else "TIMEOUT - verify manually")
        ga = eth_call(ADVN, sel("getApproved(uint256)") + enc_u(554))
        print("getApproved(554) now:", ga, "-> market?", ga[-40:].lower() == ADVM[2:].lower())
        s["tx_approve_adv"] = tx; save_state(s)
    return j

def step_prev_list_adv(s):
    data = sel("list(address,uint256,uint256,uint8)") + enc_addr(ADVN) + enc_u(554) + enc_u(ADV_PRICE_WEI) + enc_u8(0)
    print("list ADVAGENT #554 @ 0,001 BNB (currency 0=BNB), data:", data)
    j = baw_preview(ADVM, data, 0)
    rid = (j.get("data") or {}).get("requestId")
    if assess_preview(j) and rid:
        s["prev_list_adv"] = rid; save_state(s)
    return j

def step_exec_list_adv(s):
    rid = s.get("prev_list_adv")
    assert rid, "no preview requestId - run prev-list-adv first"
    j = baw_execute(rid)
    tx = (j.get("data") or {}).get("txHash") or j.get("txHash") or j.get("hash") or j.get("transactionHash")
    print("TX:", tx)
    if tx:
        r = wait_receipt(tx)
        print("RECEIPT:", json.dumps(r, indent=1) if r else "TIMEOUT - verify manually")
        print("nextListingId now:", int(eth_call(ADVM, sel("nextListingId()")), 16))
        w = words(eth_call(ADVM, sel("listings(uint256)") + enc_u(1)))
        print("listings(1): seller 0x" + w[0][-40:], "nft 0x" + w[1][-40:],
              "tokenId", int(w[2], 16), "price", int(w[3], 16), "currency", int(w[4], 16), "active", int(w[5], 16))
        s["tx_list_adv"] = tx; save_state(s)
    return j

STEPS = {
    "prev-approve-acat": step_prev_approve_acat,
    "exec-approve-acat": step_exec_approve_acat,
    "prev-list-acat": step_prev_list_acat,
    "exec-list-acat": step_exec_list_acat,
    "prev-approve-adv": step_prev_approve_adv,
    "exec-approve-adv": step_exec_approve_adv,
    "prev-list-adv": step_prev_list_adv,
    "exec-list-adv": step_exec_list_adv,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STEPS:
        print("usage: bulk_list_exec.py <" + "|".join(STEPS) + ">")
        sys.exit(2)
    state = load_state()
    STEPS[sys.argv[1]](state)
