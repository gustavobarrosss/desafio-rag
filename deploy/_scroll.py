import sys, json
d = json.load(sys.stdin)
pts = d["result"]["points"]
print(f"chunks: {len(pts)}")
for p in pts[:20]:
    pl = p["payload"]
    art = pl.get("article_ref")
    art_short = art[:80] if art else None
    txt = pl.get("text", "")
    ps = pl.get("page_start")
    pe = pl.get("page_end")
    print(f"  pg={ps}-{pe} chars={len(txt)}")
    print(f"    art={art_short}")
    print(f"    text[:250]={txt[:250]!r}")
    print()
