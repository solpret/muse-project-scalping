#!/usr/bin/env python3
"""Cek kepemilikan + status jual NFT (READ-ONLY, tanpa tx).

Dibuat 2026-11-11 untuk tugas parent: verifikasi NFT di 3 wallet user
(agent 0xeb23, utama 0xcf3c, wallet 3 0x71bc) + nilai mana bisa jadi uang.
Sumber daftar: ~/workspace/nft-rarity-inventory.md +
~/workspace/nft-main-wallet-list.md.
Semua bacaan live via BSC RPC (dataseed1/2/3/4, nodereal, ninicoin).
TANPA Ankr, TANPA BscScan login, TANPA harga dari memori.
"""
import json
import subprocess
import sys
import time

AGENT = "0xeb237F6f9FB4e826739F0954e9768914F28b2c2E"
MAIN = "0xcf3c6fc1bf670a8d3f03436395286f86dbee7418"
W3 = "0x71bcee31054137448e78f2e28dbe3160aae3e235"
WALLETS = {"agent": AGENT, "utama": MAIN, "w3": W3}

RPCS = ["https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed3.binance.org",
        "https://bsc-dataseed4.binance.org",
        "https://bsc.nodereal.io",
        "https://bsc-dataseed1.ninicoin.io"]

# selector ownerOf(uint256) / balanceOf(address,uint256) / balanceOf(address)
SEL_OWNER = "0x6352211e"
SEL_BAL1155 = "0x00fdd58e"
SEL_BAL721 = "0x70a08231"
SEL_GETAPPROVED = "0x081812fc"
SEL_ISAPPROVEDFORALL = "0xe985e9c5"
from Crypto.Hash import keccak


def sel(sig):
    return "0x" + keccak.new(data=sig.encode(), digest_bits=256).hexdigest()[:8]


SEL_LISTINGS = sel("listings(uint256)")  # dipakai banyak market template
SEL_LISTEDCOUNT = sel("listedCount()")
SEL_SALEFINALIZED = sel("saleFinalized()")


def pad_addr(a):
    return "0" * 24 + a[2:].lower()


def u(n):
    return format(n, "064x")


def rpc_batch(calls):
    """calls: list of (to, data). Kirim sebagai satu batch JSON-RPC (chunk <=20).
    Kembalikan list hasil mentah (hex string atau {'error': ...})."""
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                "params": [{"to": to, "data": data}, "latest"]}
               for i, (to, data) in enumerate(calls)]
    body = json.dumps(payload)
    last_err = None
    for rpc in RPCS:
        try:
            r = subprocess.run(["curl", "-s", "-m", "35", "-X", "POST", rpc,
                                "-H", "Content-Type: application/json",
                                "-d", body],
                               capture_output=True, text=True, timeout=40)
            d = json.loads(r.stdout)
            if isinstance(d, list) and len(d) == len(calls):
                out = []
                for item in d:
                    if "result" in item:
                        out.append(item["result"])
                    else:
                        out.append({"error": item.get("error", {}).get("message", "unknown")})
                return out
            last_err = f"bad batch shape from {rpc}: {str(d)[:120]}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{rpc}: {e}"
    # fallback: singles via curl
    out = []
    for (to, data) in calls:
        out.append(rpc_single(to, data))
    return out


def rpc_single(to, data):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                       "params": [{"to": to, "data": data}, "latest"]})
    for rpc in RPCS:
        try:
            r = subprocess.run(["curl", "-s", "-m", "30", "-X", "POST", rpc,
                                "-H", "Content-Type: application/json",
                                "-d", body],
                               capture_output=True, text=True, timeout=35)
            d = json.loads(r.stdout)
            if "result" in d:
                return d["result"]
        except Exception:  # noqa: BLE001
            continue
    return {"error": "all RPC failed"}


def parse_addr(word):
    if isinstance(word, dict):
        return None
    w = word[2:] if word.startswith("0x") else word
    if len(w) < 64:
        return None
    return "0x" + w[-40:]


def parse_uint(word):
    if isinstance(word, dict):
        return None
    try:
        return int(word, 16)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- daftar NFT
# kind: erc721 | erc1155 ; market: alamat market proyek (bila diketahui)
# listed_recorded: harga tercatat (BUKAN harga live — hanya referensi catatan)
# note: status khusus dari inventory
ITEMS = [
    # --- ter-listing tercatat (verifikasi live via listings()/approval) ---
    ("AgentsEstates #4611", "0x439643cfd023c247519518B7F73D06823E1F2fCe", 4611, "erc721",
     "0x1EeA3629C7505Dcf8c8d247558F6E244D19C60dE", 0.0035, "market off-chain orderbook; expiry 2026-12-01"),
    ("AlienID #3333", "0xd12C42935F06B37337588B9169ba1593b534eB86", 3333, "erc721",
     "0x8336317f0f05e840209c4f8f3d05f9fe8c045bc9", 0.001, "market AlienID"),
    ("AlienID #3684", "0xd12C42935F06B37337588B9169ba1593b534eB86", 3684, "erc721",
     "0x8336317f0f05e840209c4f8f3d05f9fe8c045bc9", 0.001, "market AlienID"),
    ("Brutes #3967", "0x44aB9F4687d5D6cd2454266b1E548552FB924908", 3967, "erc721",
     "0x2199ea11ac7d11f5ef50c8d8e30c256b51bdbed4", 0.000076, "market Brutes"),
    ("SAFU Bots #3073", "0x763aaD3528b408bc42300DBab4Ec755cdE1D0192", 3073, "erc721",
     "0x85d03dc8e84fd6ed316beaec09538e8488fd0f59", 0.0001425, "market SAFU"),
    ("Hirelings #3927", "0x6b1D77D3DeDE3FC775ca8C7BDe08C2C2EAAb9BEA", 3927, "erc721",
     None, 0.000133, "market proyek sendiri, alamat market belum tercatat"),
    ("Hirelings #4342", "0x6b1D77D3DeDE3FC775ca8C7BDe08C2C2EAAb9BEA", 4342, "erc721",
     None, 0.000133, "market proyek sendiri, alamat market belum tercatat"),
    ("Agent Periodic Lab O2 #1021", "0xfE5Ab5ce9b1D86a55d70E7D0573a14D559029C18", 1021, "erc1155",
     "0xD69e644DD0DbC22cE20448E1F8646D95e097ccDd", 0.000171, "LabMarket, fee 2%"),
    ("Agent Cards #1720", "0x19f125dA1985033f102CA7746Cbd1F6f512CFACd", 1720, "erc721",
     None, 0.00085, "market proyek sendiri, alamat market belum tercatat"),
    ("TRACE Genesis #2256", "0xe5E2c43746F3A5E713C246592095D7cf82E808Cc", 2256, "erc721",
     "0x30cAc2eA33fA5B06EDC44fF01f9B8c768D8DFEB3", 0.000495, "market TRACE, fee 1%"),
    ("Motor Agent #1282", "0x86F83E98437A1DcF0e4A62828f68C1D53B3c7AB5", 1282, "erc721",
     "0x1C2743449C177D608d3B19C88160fc1B402f7BAC", 0.00118, "MotoAgentMarket, fee 3%, floor live tercatat"),
    ("GLOOP #2021", "0x183CBb52B3C7BCB2433d50dE0Add99B59f4DE4fE", 2021, "erc721",
     "0x923e0125eBbc846a9EBe50640628664846D67854", 0.001, "GloopMarket; inventory 08 Nov bilang TIDAK listing, ringkasan 11 Nov bilang TER-LISTING — verifikasi live"),
    # --- market diketahui, belum listing ---
    ("BOBBIE #4023", "0x3BF121dC4c5a1Ba567F3eE801d0e447601726207", 4023, "erc721",
     "0xfA239D919Db60E1b497E9E8CA26CA79689a180AF", None, "transfer terkunci sampai saleFinalized; list @0,0075 direncanakan"),
    ("ACAT #1817", "0x28a38A372639E8C481aD41B9B16E52c3c6366ACC", 1817, "erc721",
     "0x3424De685702E8d63BaAC20BB67d1a205db89427", None, "ACATMarket live; floor tercatat 24.000 ACN; klaim 40.000 ACN SUDAH diklaim"),
    ("Jinchan #305", "0xccc127D1A7Ef1e385757784a9878F7279BF3B19D", 305, "erc721",
     "0x7Cb7Fce1d512A6dF4ACB293B06CA4769484d2cA7", None, "market built-in, 0 listing saat mint"),
    ("ADVAGENT #554", "0x7635ef16370ef490298649a188c9205d1d7fda67", 554, "erc721",
     "0x9d4f73c7c92bb4f680c770d0ed3c71527297a256", None, "AdventureMarketplace, 0 listing saat mint"),
    ("Lumen Genesis #2321", "0xC996A35D6dA945519eF5d3572C5A8cdc845AE842", 2321, "erc721",
     "0xcA67924b905e89f98651E2d9FF69bBA2fbb9cF73", None, "market situs ada, 0 listing saat mint"),
    ("KERNELMONK #509", "0x016b9CE7508628e4Fb19E4fAF3129Fdc41b150d7", 509, "erc721",
     "0xb8599513fb217c9a2A51BAe997E29ba516FE6268", None, "listedCount 0 saat mint"),
    ("BYTEBLOOM #508", "0x74bbDAD7c027C56bE30A7fEaC6F7371B95f1c32B", 508, "erc721",
     "0x8c1c800Fc3079E9dEa13a9c5F4c20d0e5e2f2675", None, "listedCount 0 saat mint"),
    ("ORBITJANITOR #552", "0x8d5a0555446d37afaba75e36da318646a4b32420", 552, "erc721",
     "0x75b264fdf7566f81367dcb257675fd073c0b3df1", None, "Salvage Bay listedCount 0 saat mint"),
    ("RIMEVAULT #504", "0x96e879c79b1191a0954022c7483c44b2ee522ba1", 504, "erc721",
     "0xef298eaf9d6c32406c40d3400cf33a7dd531ace0", None, "Ice Market listedCount 0 saat mint; UNREVEALED"),
    ("MYCELIX #508", "0xb1636010fb87f50574f76f6644b7cd306a6a4d1f", 508, "erc721",
     "0x8204044ce97ad507e5033b2e67b0aeceb1e61363", None, "Rot Market listedCount 0 saat mint; UNREVEALED"),
    ("DRIFTWIRE #745", "0x485be9daa4620fcee4e2baed7cc85ea57acb44b0", 745, "erc721",
     "0x5FEF88E9E7eb0899f3F70EF16d50A08E0fd8bd09", None, "Night Market listedCount 0 saat mint"),
    ("EMBERANVIL #946", "0x7673c596a6702d32f844266dc2ca7b1fd6d7df21", 946, "erc721",
     "0xb8aa1d77778a107f59906bae4a0b1443e0cd53af", None, "Slag Market listedCount 1, floor debu ~0,00000284 saat mint; UNREVEALED"),
    # --- tanpa market live ---
    ("Agent Punks #741", "0x3B2C00e1dFe1a1B45F93E990fFe56C5dCa4670b7", 741, "erc721", None, None, ""),
    ("Bubblebruin #757", "0xc205F8c74733D914776D66cBc4Cf10BF701D96Aa", 757, "erc721", None, None, ""),
    ("Cathodex #421", "0x0AC5591d1B980C7bE16861d722E59E21E2624868", 421, "erc721", None, None, ""),
    ("CharMasks #1529", "0xf197F509137db1eb45b17a362bc446D48E6dAC4f", 1529, "erc721", None, None, "non-transferable"),
    ("Crop Pals #5449", "0xd43f328560aE9bA09B27375d1b83F7FA7A0d091b", 5449, "erc721", None, None, "trading terkunci s.d. 10.000 minted"),
    ("Cryonyx #407", "0x787b522d48084A83b8B1b0C206aA4A12B1FD1cc2", 407, "erc721", None, None, ""),
    ("DIGG Copper Ore id 2", "0xB6407991b76E712d15EdEF06e125abcC730364F7", 2, "erc1155", None, None, "listing pernah diblokir 351803"),
    ("Gearotter #204", "0xF25E1c4dBE48383e93f5992B2E7FE7331B5a9AD0", 204, "erc721", None, None, ""),
    ("Gelede #540", "0xA06427Fb1c58fE5313696630383d8A875Eb2a7dE", 540, "erc721", None, None, ""),
    ("Ghoulipop #541", "0xb2d236EC0473792eC6727e5bf41084Df3eA0137F", 541, "erc721", None, None, ""),
    ("Hachiko #839", "0x48c977d13ef156475F086F907D8da9583E1E072E", 839, "erc721", None, None, ""),
    ("HashDroids #5782", "0x0EED2C5e866A3792f587CAf0839941EA57fe6915", 5782, "erc721", None, None, "soulbound"),
    ("HIVE Agents #808", "0x1458b0C132Fb51BDC65B1Ca7a3BE484BCd9A2f77", 808, "erc721", None, None, ""),
    ("Hotpotsage #398", "0xEbAdb5Be9692b0BF25E8A5F3351C35DdC58f195d", 398, "erc721", None, None, ""),
    ("Ioforge #486", "0x259b5Da3f44a44C89bcA5EE2297582c41c758d0F", 486, "erc721", None, None, ""),
    ("Kirinova #377", "0x68a971C9D2b54Ae86dAF59CD665E40f3E087fb7F", 377, "erc721", None, None, ""),
    ("Lensorb #392", "0x1edfd1e6c6b132e88f5b09b1dbe025037b5b0539", 392, "erc721", None, None, ""),
    ("Mars Nexus #1448", "0xC4059D2F9F6F86695896e68F3E73E1e6cC3791c8", 1448, "erc721", None, None, ""),
    ("Mummibo #216", "0x666830a348414D3E8B2d341D1ec9D562211034af", 216, "erc721", None, None, ""),
    ("Neijuanbo #697", "0xcc67bDae1457f8B0914FBFD3fD93b4b1831F0401", 697, "erc721", None, None, ""),
    ("Neonkoi #273", "0x3ADfBBa26d3674477aA21B0CF2E4C2BC643ff85b", 273, "erc721", None, None, ""),
    ("Neonraven #370", "0xcc5608A7F5A5Bc8F1bEABcD010A8C0066a0243Ae", 370, "erc721", None, None, ""),
    ("Neptuno #203", "0x6e2dFd975dA6A54d18d1816029C6657454E61c9a", 203, "erc721", None, None, ""),
    ("NianNian #1195", "0x4A2bb7B24C39CA548b050aD0C0aFfea5e6e04aF2", 1195, "erc721", None, None, ""),
    ("Phoenixi #207", "0x0c4370EdE2500511Ec9FD5a7c69c47e7EE4118E4", 207, "erc721", None, None, ""),
    ("Plutopop #516", "0xE4cA7AaA82253717C0d897C10d58D59821631509", 516, "erc721", None, None, ""),
    ("Ptah POA #29643", "0xD87ADb2E47979d6cb323896B1A9320E0dd132213", 29643, "erc721", None, None, ""),
    ("Qilinora #689", "0x952B7C0b8e63B3dB965d49ec91d4Eb73D8BBC264", 689, "erc721", None, None, ""),
    ("SeanceSociety #326", "0x8F8705A91cb8af615CE35179B0D33200AdE71DAb", 326, "erc721", None, None, ""),
    ("Servobun #517", "0xbd297cb5158F4DcA02C336A59556FDBfa6609A70", 517, "erc721", None, None, ""),
    ("SEVY Genesis #9841", "0xF054C8Ca0119A2955FeF43669Ee68e7d2ab7e49A", 9841, "erc721", None, None, "hak redeem 100 SEVY; jangan jual (aturan)"),
    ("SEVY Genesis #9843", "0xF054C8Ca0119A2955FeF43669Ee68e7d2ab7e49A", 9843, "erc721", None, None, "hak redeem 100 SEVY; jangan jual (aturan)"),
    ("SEVY Genesis #9845", "0xF054C8Ca0119A2955FeF43669Ee68e7d2ab7e49A", 9845, "erc721", None, None, "hak redeem 100 SEVY; jangan jual (aturan)"),
    ("SEVY Genesis #9846", "0xF054C8Ca0119A2955FeF43669Ee68e7d2ab7e49A", 9846, "erc721", None, None, "hak redeem 100 SEVY; jangan jual (aturan)"),
    ("SEVY Genesis #9848", "0xF054C8Ca0119A2955FeF43669Ee68e7d2ab7e49A", 9848, "erc721", None, None, "hak redeem 100 SEVY; jangan jual (aturan)"),
    ("Strawhex #628", "0x61C360b0fA3fef5c61728756e0fb574c66D73663", 628, "erc721", None, None, ""),
    ("SynapseSquad #184", "0xD65913933ada0b429de8f17eDBAdf5FA24aa94E2", 184, "erc721", None, None, ""),
    ("Tempestara #656", "0xDAA48EED214eDBcf1fF76A9540C819b8afe4BED7", 656, "erc721", None, None, ""),
    ("Voltmohawk #936", "0x06aB825D14454174819E1c08a6E72C4b5bBF78fE", 936, "erc721", None, None, ""),
    ("Veyanth #938", "0x3f73b5A2B56a16E4204053C6a2CD51f2b3E4188E", 938, "erc721", None, None, ""),
    ("Veyanth #939", "0x3f73b5A2B56a16E4204053C6a2CD51f2b3E4188E", 939, "erc721", None, None, ""),
    ("Veyanth #940", "0x3f73b5A2B56a16E4204053C6a2CD51f2b3E4188E", 940, "erc721", None, None, ""),
    ("Xianyulo #507", "0x5bF46eF8E5e09fFFfd928e30077b4d4674CBA4b0", 507, "erc721", None, None, ""),
    ("Zapcourier #387", "0x672c1dF4F7121DaCBD3504995441C47d2fAbCdDC", 387, "erc721", None, None, ""),
    ("DigDown #5361", "0xF464550f1d744793893C0ce09b89269Ed726d796", 5361, "erc721", None, None, ""),
    ("Agentic Bull #322", "0xcd4472Af68801DE87E970A333C71da5d12E825A3", 322, "erc721", None, None, "Legendary"),
    ("Merith #1144", "0x3dF52d687a7a5F79Fc1BE45fECD1B2Ec1Df622A7", 1144, "erc721", None, None, ""),
    ("Merith #1145", "0x3dF52d687a7a5F79Fc1BE45fECD1B2Ec1Df622A7", 1145, "erc721", None, None, ""),
    ("Merith #1146", "0x3dF52d687a7a5F79Fc1BE45fECD1B2Ec1Df622A7", 1146, "erc721", None, None, ""),
    ("Merith #1147", "0x3dF52d687a7a5F79Fc1BE45fECD1B2Ec1Df622A7", 1147, "erc721", None, None, ""),
    ("Merith #1148", "0x3dF52d687a7a5F79Fc1BE45fECD1B2Ec1Df622A7", 1148, "erc721", None, None, ""),
    ("Vault-Crack #752", "0x1a7a8BbB49e3e8Cf951996789Fd69d4C7524A2B4", 752, "erc721", None, None, ""),
    ("Ghost Frog #22", "0x29EE92a8D6Dc62035D0C2b4D53B5df504D3Ebe91", 22, "erc721", None, None, ""),
    ("Agent Oil Rig #1977", "0xF00D595F9fbb8308Fb03eeEC7406e6883B157413", 1977, "erc721", None, None, "STAKED pool 6 (custody OilDrilling)"),
    ("The Orzo #6702", "0x39f61c0a379f132d66fb8c2adab293e6a7595b0a", 6702, "erc721", None, None, "soulbound pasca-klaim ORZO"),
    ("Agentic Node #1391", "0x0691Dd61f84bda232BcD0bd80F6559EcC2c00131", 1391, "erc721", None, None, ""),
    ("HACKED #1395", "0xCD355D1191Ce9849B3A935C821A66d9DF37cFE77", 1395, "erc721", None, None, ""),
    ("HACKED #1396", "0xCD355D1191Ce9849B3A935C821A66d9DF37cFE77", 1396, "erc721", None, None, ""),
    ("402 #174", "0xd80D0095746999c93AE837B1DE1569d5814405dC", 174, "erc721", None, None, ""),
    ("THYRK #6849", "0x4D90fFC3075FB8897EDFf6d7c6269c906664a20e", 6849, "erc721", None, None, ""),
    ("ELSY #880", "0x9e2c16ee581e0d4f0a36ae70205c5599bbb9b22f", 880, "erc721", None, None, ""),
    ("ELSY #881", "0x9e2c16ee581e0d4f0a36ae70205c5599bbb9b22f", 881, "erc721", None, None, ""),
    ("FOSSIL DRAWER #581", "0x747e7ca26921bc1bde97cb27b445983c8269bb72", 581, "erc721", None, None, "UNREVEALED"),
    ("PICKGRID #243", "0xc2BEAaD13Ef2833D976957E41B03b6B2661cFAB0", 243, "erc721", None, None, "terkunci transfer s.d. 17 Nov 17:13 WIB"),
    ("BEWORK #1274", "0x78eDD42101e889cC3Ce4B24CBb0Fc774082BcAee", 1274, "erc721", None, None, ""),
    ("BEWORK #1275", "0x78eDD42101e889cC3Ce4B24CBb0Fc774082BcAee", 1275, "erc721", None, None, ""),
    ("HACKOUCOINPASS #539", "0xA96f20BCeD857d3174Bb40e806b0151daeEaa62e", 539, "erc721", None, None, ""),
    ("LANTERNFIELD #516", "0x9aab6e84096A780F9400CdD4E230FaA41B4545f2", 516, "erc721", None, None, ""),
    ("Tri333 #590", "0x157D860e30b6c6d4dC0abe3bD6e66e659A9d524f", 590, "erc721", None, None, ""),
    ("HOG ORBIT #421", "0x7bc0b487febcbb1caa781148bf05a8cd72d02ec5", 421, "erc721", None, None, ""),
    ("WAR3 #2511", "0x1Aa9797cC762b838388566Daa45A56b1709F4Bbe", 2511, "erc721", None, None, "UNREVEALED; reveal ~12 Nov"),
    ("HokiKoi #64", "0x74055b4Afe2d8c7347e8D4c0fc23e704405F7A36", 64, "erc721", None, None, ""),
    ("Oddlode Drills #814", "0xce18531F9F639ca2351CA15FB2032D925b917472", 814, "erc721",
     "0xce18531F9F639ca2351CA15FB2032D925b917472", None, "market = kontrak sendiri; free drill TIDAK bisa di-list"),
    ("Pokechain #5381", "0x4EF929aF00cB93993c3D01834C4DA79200d31e32", 5381, "erc721", None, None, "percobaan listing diblokir 351803"),
    ("DRAGON #91", "0x7F00b3Aa3A3e41dc145180C4ED8a24eE568eaED4", 91, "erc721", None, None, ""),
    ("DOSSIER #397", "0xaC3f9A33c653FB3CA49056cFA5eDf19101DeBf97", 397, "erc721", None, None, ""),
    ("Stampooor #751", "0xCFB72b0E311796C478c6b74C1cd4592BbAa17aab", 751, "erc721", None, None, "UNREVEALED; reveal 14 Nov"),
    ("Lorith #498", "0x65B3b58C6fe1B98087b022AB41c54a2987494ddF", 498, "erc721", None, None, ""),
    ("Skunk Hollow #708", "0x88E4b613609af6C479F11D78e5a696d84B78993B", 708, "erc721", None, None, ""),
    ("Marbleworks #458", "0xCc844748C64b9b8e45aC4504553C9757c7CDB7E0", 458, "erc721", None, None, "UNREVEALED"),
    ("BAOracle #4292 (burned)", "0xcBe2DBBFF4264Dc67Bf557c64e29997B82439354", 4292, "erc721", None, None, "DIBAKAR 2026-11-02; ownerOf diharapkan revert"),
    # --- tambahan: NFT wallet tujuan (utama 0xcf3c) + item inventory lain ---
    ("Coracle #897", "0x87c28a7b2069e4af252568cfc8c859faf93230cb", 897, "erc721",
     "0x598f5AF7Ccb5520F51848C7189402C25Ee2222b7", None, "tercatat di wallet utama 0xcf3c; CoracleMarket"),
    ("Coracle #898", "0x87c28a7b2069e4af252568cfc8c859faf93230cb", 898, "erc721",
     "0x598f5AF7Ccb5520F51848C7189402C25Ee2222b7", None, "tercatat di wallet utama 0xcf3c; CoracleMarket"),
    ("Coracle #897 (alt contract)", "0x2FCefA216dA37751E102399aCbe1dCC86Dc96eCf", 897, "erc721",
     None, None, "kandidat kontrak alternatif dari catatan situs; dicek untuk disambiguasi"),
    ("Coracle #898 (alt contract)", "0x2FCefA216dA37751E102399aCbe1dCC86Dc96eCf", 898, "erc721",
     None, None, "kandidat kontrak alternatif dari catatan situs; dicek untuk disambiguasi"),
    ("Botmolt #2628", "0x6f44f58e483a8616a84c9f1e49fbfc8477736461", 2628, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("SOLVD #869", "0x59661d7F8f996F687CD98c116cCaf66eFd254076", 869, "erc721", None, None, "tercatat di wallet utama 0xcf3c; Tier T2"),
    ("SOLVD #2137", "0x59661d7F8f996F687CD98c116cCaf66eFd254076", 2137, "erc721", None, None, "tercatat di wallet utama 0xcf3c; Tier T4"),
    ("Hexhive #5468", "0xf96bff5c71852f30ddcf97040fd996c7ef0f88ac", 5468, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("Hexhive #6311", "0xf96bff5c71852f30ddcf97040fd996c7ef0f88ac", 6311, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("Hexhive #6312", "0xf96bff5c71852f30ddcf97040fd996c7ef0f88ac", 6312, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("Jellume #4407", "0xbcb1af97b791d563b57ce481a7164d52060ba373", 4407, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("Jellume #4408", "0xbcb1af97b791d563b57ce481a7164d52060ba373", 4408, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("Jellume #4409", "0xbcb1af97b791d563b57ce481a7164d52060ba373", 4409, "erc721", None, None, "kontrak dari catatan grep (keyakinan sedang)"),
    ("GLYPHMIND #4481", "0xf9e4f7810dbb6dd4a85ff521ca86a65a8250aa6a", 4481, "erc721", None, None, "Common; kontrak dari catatan grep"),
    ("QUIRKZ #4325", "0x98cCA5607B896034804369d476Afcb46Cd1f0423", 4325, "erc721", None, None, "kontrak dari catatan grep"),
    ("QUIRKZ #4326", "0x98cCA5607B896034804369d476Afcb46Cd1f0423", 4326, "erc721", None, None, "kontrak dari catatan grep"),
    ("Agentlings #5151", "0xbf3488e453c68e9771c26d448e99d0ec5395135f", 5151, "erc721", None, None, "Rare; kontrak dari catatan grep"),
    ("Cogladius #4860", "0x2bbad2A3421a0BBd62A319a9Db70122047839483", 4860, "erc721", None, None, "Tier Silver; kontrak dari catatan grep"),
    ("Synklings #2597 (cand A)", "0x48250aD93B4fCbB54EA70dCb97B198BbA7d548c2", 2597, "erc721", None, None, "kandidat A; dicek untuk disambiguasi"),
    ("Synklings #2597 (cand B)", "0xea527ebd17172b51ca7d951f6a68e8e49c3862c7", 2597, "erc721", None, None, "kandidat B; dicek untuk disambiguasi"),
    ("DigiWarriors #4104 (cand A)", "0x1b2b86eab83ac6b5cd2bf4d6d6b578415a4edf5e", 4104, "erc721", None, None, "tercatat SUDAH keluar (owner pihak ketiga); kandidat A"),
    ("DigiWarriors #4104 (cand B)", "0x2106674431f6672d71777356bD7AB2999562d5b1", 4104, "erc721", None, None, "tercatat SUDAH keluar (owner pihak ketiga); kandidat B"),
    ("Agent Koi #3596", "0xa34138d2ab47d889c36cd1639f92943a8d8d1c51", 3596, "erc721", None, None, "Common; tercatat di wallet utama 0xcf3c"),
    ("Agent Pandas #2038", "0x212297d63a93f6de8ef9967ef81954233ae94c02", 2038, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Ascension #2326", "0x43f9480b9d6803d5ff93aaea82b17c10aadc578b", 2326, "erc721", None, None, "Common; tercatat di wallet utama 0xcf3c"),
    ("BLOB Miner #2814", "0x4ADDBd796eDF3d4d186Fb5AA314cAfF3F98984A6", 2814, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Black Hole #6781", "0xbef57ff802a6b43fab43d6f90ed7d8968c412fb9", 6781, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Blue Sky #4985", "0x39c001368b64f02acb7d6d39ea7778236a6856e7", 4985, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Bound #2176", "0x24319db53d49c7f48e6f79be910ba4af8ba40312", 2176, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Eagle #1123", "0x24d7be1c027c9298ace72833ce9fd9c117131ea3", 1123, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Flaggies #2266", "0x72cd36cf86b015e337fe476aab524e79bd82b210", 2266, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Fragment #4155", "0x1b2b3b68d9518d1347b0ccada1868cb743fca129", 4155, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("HALCYX #1992", "0x42dBe11400A6DD3C92710FD275f8ca1193830022", 1992, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("HALCYX #1993", "0x42dBe11400A6DD3C92710FD275f8ca1193830022", 1993, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("HALLU #4170", "0x668fa7cba5c2dff9db911f40c8d051c511ee0a45", 4170, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("HALLU #4171", "0x668fa7cba5c2dff9db911f40c8d051c511ee0a45", 4171, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("LeadLoop #5555", "0x3ee0bf697df940cb33cffe5581e36d1e955a660c", 5555, "erc721", None, None, "Common; tercatat di wallet utama 0xcf3c"),
    ("LeadLoop #5556", "0x3ee0bf697df940cb33cffe5581e36d1e955a660c", 5556, "erc721", None, None, "Uncommon; tercatat di wallet utama 0xcf3c"),
    ("NeoGods #926", "0x7bf463d25c9a833a17c38cd6fb1be202098769ee", 926, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Onchain Crystals #4169", "0xE0574C002D10CdF00949C9cE23614c9b2e9Dd1cE", 4169, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("Onchain Crystals #4170", "0xE0574C002D10CdF00949C9cE23614c9b2e9Dd1cE", 4170, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("PIXEL SHIBA #5076", "0xFdD6C5fF6dAbFF40cb27B39D72E5C9517Af27Fdc", 5076, "erc721", None, None, "Rare; tercatat di wallet utama 0xcf3c"),
    ("Piggy #6193", "0x85df1ed7179db06a8c48c826329b8be9f0f6bea1", 6193, "erc721", None, None, "tercatat di wallet utama 0xcf3c"),
    ("QUOGENT #3279", "0x611984D45b3FA757978449D40e3398aB7074D205", 3279, "erc721", None, None, "tercatat di wallet utama 0xcf3c (ditransfer dari agent wallet)"),
    ("Worldygg #7081", "0x2ED9E0a6992BA22B48c2fc2def6bB2365301A0D0", 7081, "erc721", None, None, "tercatat di wallet utama 0xcf3c; kontrak keyakinan sedang"),
    ("Robotic Agent #5349", "0x0da60e7f5933a5a8b5804957bc4643fe2b78be5d", 5349, "erc721", None, None, "tercatat di wallet utama 0xcf3c; kontrak keyakinan sedang"),
]


def main():
    t0 = time.time()
    # ---- 1. ownerOf untuk semua ERC721 + balanceOf ERC1155 x3 wallet ----
    calls = []   # (to, data)
    index = []   # (item_idx, kind, wallet_key)
    for i, (name, ca, tid, kind, market, listed, note) in enumerate(ITEMS):
        if kind == "erc721":
            calls.append((ca, SEL_OWNER + u(tid)))
            index.append((i, "ownerOf", None))
        else:
            for wk, wa in WALLETS.items():
                calls.append((ca, SEL_BAL1155 + pad_addr(wa) + u(tid)))
                index.append((i, "bal1155", wk))
    results = {}
    CHUNK = 20
    for s in range(0, len(calls), CHUNK):
        chunk = calls[s:s + CHUNK]
        res = rpc_batch(chunk)
        for (to, data), r in zip(chunk, res):
            pass
        for j, r in enumerate(res):
            idx = index[s + j]
            results.setdefault(idx[0], {})[(idx[1], idx[2])] = r
        print(f"ownerOf chunk {s//CHUNK+1}/{(len(calls)+CHUNK-1)//CHUNK} selesai "
              f"({time.time()-t0:.0f} dtk)", flush=True)

    # ---- 2. market views ----
    market_calls = []
    mindex = []  # (item_idx, view, market)
    for i, (name, ca, tid, kind, market, listed, note) in enumerate(ITEMS):
        if not market:
            continue
        market_calls.append((market, SEL_LISTINGS + u(tid)))
        mindex.append((i, "listings", market))
        market_calls.append((market, SEL_LISTEDCOUNT))
        mindex.append((i, "listedCount", market))
    # khusus: saleFinalized BOBBIE + approval AgentsEstates + getApproved TRACE/Motor/GLOOP
    for i, (name, ca, tid, kind, market, listed, note) in enumerate(ITEMS):
        if name.startswith("BOBBIE"):
            market_calls.append((market, SEL_SALEFINALIZED))
            mindex.append((i, "saleFinalized", market))
        if name.startswith("AgentsEstates"):
            market_calls.append((ca, SEL_ISAPPROVEDFORALL + pad_addr(AGENT) + pad_addr(market)))
            mindex.append((i, "isApprovedForAll", market))
        if name in ("TRACE Genesis #2256", "Motor Agent #1282", "GLOOP #2021"):
            market_calls.append((ca, SEL_GETAPPROVED + u(tid)))
            mindex.append((i, "getApproved", market))
    mresults = {}
    for s in range(0, len(market_calls), CHUNK):
        chunk = market_calls[s:s + CHUNK]
        res = rpc_batch(chunk)
        for j, r in enumerate(res):
            idx = mindex[s + j]
            mresults.setdefault(idx[0], {})[(idx[1], idx[2])] = r
        print(f"market chunk {s//CHUNK+1}/{(len(market_calls)+CHUNK-1)//CHUNK} selesai "
              f"({time.time()-t0:.0f} dtk)", flush=True)

    # ---- 3. susun output ----
    out = {"checked_at_wib": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
           "wallets": WALLETS, "items": []}
    for i, (name, ca, tid, kind, market, listed, note) in enumerate(ITEMS):
        entry = {"name": name, "contract": ca, "tokenId": tid, "kind": kind,
                 "market": market, "listed_recorded_bnb": listed, "note": note}
        if kind == "erc721":
            r = results.get(i, {}).get(("ownerOf", None))
            if isinstance(r, dict):
                entry["ownerOf"] = {"error": r.get("error")}
                entry["holder"] = "unknown-revert"
            else:
                owner = parse_addr(r)
                entry["ownerOf"] = owner
                entry["holder"] = ("agent" if owner and owner.lower() == AGENT.lower()
                                   else "utama" if owner and owner.lower() == MAIN.lower()
                                   else "w3" if owner and owner.lower() == W3.lower()
                                   else "other:" + (owner or "?"))
        else:
            bals = {}
            for wk in WALLETS:
                r = results.get(i, {}).get(("bal1155", wk))
                bals[wk] = parse_uint(r) if not isinstance(r, dict) else {"error": r.get("error")}
            entry["balances1155"] = bals
            holders = [wk for wk, v in bals.items() if isinstance(v, int) and v > 0]
            entry["holder"] = "+".join(holders) if holders else "none"
        # market views
        mv = {}
        for (view, mk), r in mresults.get(i, {}).items():
            if view == "listings":
                if isinstance(r, dict):
                    mv["listings"] = {"error": r.get("error")}
                else:
                    words = [r[k:k + 64] for k in range(2, len(r), 64)]
                    seller = parse_addr("0x" + words[0]) if words else None
                    price = int(words[1], 16) if len(words) > 1 else None
                    mv["listings"] = {"seller": seller, "price_wei": price,
                                      "price_bnb": price / 1e18 if price is not None else None,
                                      "raw_words": len(words)}
            elif view == "listedCount":
                mv["listedCount"] = parse_uint(r) if not isinstance(r, dict) else {"error": r.get("error")}
            elif view == "saleFinalized":
                mv["saleFinalized"] = (parse_uint(r) == 1) if not isinstance(r, dict) else {"error": r.get("error")}
            elif view == "isApprovedForAll":
                mv["isApprovedForAll_agent_market"] = (parse_uint(r) == 1) if not isinstance(r, dict) else {"error": r.get("error")}
            elif view == "getApproved":
                mv["getApproved"] = parse_addr(r) if not isinstance(r, dict) else {"error": r.get("error")}
        entry["market_live"] = mv
        out["items"].append(entry)
    out["elapsed_s"] = round(time.time() - t0, 1)
    with open("/home/hatch/workspace/scalp-agc/nft-holdings-check-2026-11-11.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    # ringkasan ke stdout
    from collections import Counter
    print("HOLDER:", Counter(e["holder"] for e in out["items"]))
    print("elapsed:", out["elapsed_s"], "dtk; item:", len(out["items"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
