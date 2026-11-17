import json, urllib.request, time
EPS=["https://bsc-dataseed1.defibit.io","https://bsc.publicnode.com","https://bsc-dataseed1.bnbchain.org","https://bsc-dataseed2.defibit.io","https://bsc-dataseed1.ninicoin.io"]
WALLET="0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
WBNB="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
AGC="0x63dea5aCdbE1F27c968907D08610A5f0A0284bA6"
ROUTER="0x10ED43C718714eb63d5aA57B78B54704E256024E"
def rpc(method, params):
    last=None
    for ep in EPS:
        try:
            req=urllib.request.Request(ep, data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode(), headers={"Content-Type":"application/json"})
            r=json.load(urllib.request.urlopen(req, timeout=20))
            if "result" in r and r["result"] is not None: return r["result"]
            last=r
        except Exception as e: last=e; continue
    raise RuntimeError(f"all RPC failed: {last}")
def call(to, data):
    return rpc("eth_call",[{"to":to,"data":data},"latest"])
def enc_addr(a): return a[2:].lower().rjust(64,"0")
def enc_u256(x): return format(x,"064x")
bal=int(call(WBNB,"0x70a08231"+enc_addr(WALLET)),16)
allow=int(call(WBNB,"0xdd62ed3e"+enc_addr(WALLET)+enc_addr(ROUTER)),16)
print("WBNB balance wei:", bal, "=", bal/1e18)
print("WBNB allowance to router wei:", allow)
BOOKS={"A":9684616838908521-1135935500000000-2000000000000000,"B":1135935500000000,"Burn":2000000000000000}  # A absorbs -61091479 wei dust diff vs book sum
print("books sum wei:", sum(BOOKS.values()), " diff vs balance:", bal-sum(BOOKS.values()))
deadline=int(time.time())+1200
print("deadline (unix):", deadline)
def amounts_out(amount_in):
    # getAmountsOut(uint256,address[]) selector 0xd06ca61f
    data="0xd06ca61f"+enc_u256(amount_in)+"0000000000000000000000000000000000000000000000000000000000000040"+"0000000000000000000000000000000000000000000000000000000000000002"+enc_addr(WBNB)+enc_addr(AGC)
    r=call(ROUTER,data)
    b=bytes.fromhex(r[2:])
    off=int.from_bytes(b[0:32],'big'); ln=int.from_bytes(b[off:off+32],'big')
    return int.from_bytes(b[off+64:off+96],'big')
out={}
for name, amt in BOOKS.items():
    q=amounts_out(amt)
    minout=int(q*0.95)
    approve="0x095ea7b3"+enc_addr(ROUTER)+enc_u256(amt)
    # swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256) selector 0x5c11d795
    swap="0x5c11d795"+enc_u256(amt)+enc_u256(minout)+"00000000000000000000000000000000000000000000000000000000000000a0"+enc_addr(WALLET)+enc_u256(deadline)+"0000000000000000000000000000000000000000000000000000000000000002"+enc_addr(WBNB)+enc_addr(AGC)
    out[name]={"amount_in_wei":amt,"quote_agc":q,"min_out_agc":minout,"approve_calldata":approve,"swap_calldata":swap}
    print(name, "in", amt/1e18, "WBNB quote AGC:", q/1e18, "minOut(95%):", minout/1e18)
json.dump({"balance_wei":bal,"allowance_wei":allow,"deadline":deadline,"books":out}, open("/home/hatch/workspace/agc-scalp/manual-buy-20261106.json","w"), indent=1)
print("wrote manual-buy-20261106.json")
