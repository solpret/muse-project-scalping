import json, subprocess, sys
BAW="/home/hatch/workspace/.baw-cli/bin/baw"
WALLET="0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
WBNB="0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
ROUTER="0x10ED43C718714eb63d5aA57B78B54704E256024E"
d=json.load(open('/home/hatch/workspace/agc-scalp/manual-buy-20261106.json'))
kind, book = sys.argv[1], sys.argv[2]  # preview|execute ; A|B|Burn
if kind == "preview":
    if book.endswith("-approve"):
        b=book.split("-")[0]; to=WBNB; data=d['books'][b]['approve_calldata']
    else:
        to=ROUTER; data=d['books'][book]['swap_calldata']
    r=subprocess.run([BAW,"contract-call","preview","--binanceChainId","56","--from",WALLET,"--to",to,"--value","0","--inputData",data,"--json"], capture_output=True, text=True, timeout=120)
    print(r.stdout or r.stderr)
elif kind == "execute":
    rid=sys.argv[2]
    r=subprocess.run([BAW,"contract-call","execute","--requestId",rid,"--json"], capture_output=True, text=True, timeout=120)
    print(r.stdout or r.stderr)
