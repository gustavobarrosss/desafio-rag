import sys, json, collections

d = json.load(sys.stdin)
pts = d["result"]["points"]

# arquivos indexed for REN 414/2010 or 687/2015
hits_414 = []
hits_687 = []
arts_in_414_refs = collections.Counter()

for p in pts:
    pl = p["payload"]
    arq = (pl.get("arquivo") or "").lower()
    text = (pl.get("text") or "").lower()
    art = pl.get("article_ref") or ""
    if "20101414" in arq or "ren201414" in arq or "ren414" in arq:
        hits_414.append(pl.get("doc_id"))
    if "2015687" in arq or "ren687" in arq:
        hits_687.append(pl.get("doc_id"))
    # chunks that MENTION art. 121 of REN 414 anywhere
    if "art. 121" in text and ("414/2010" in text or "n 414" in text or "n. 414" in text):
        arts_in_414_refs[pl.get("doc_id")] += 1

print(f"total chunks scanned: {len(pts)}")
print(f"arquivos with REN 414/2010 direct: {len(hits_414)}")
print(f"arquivos with REN 687/2015 direct: {len(hits_687)}")
print(f"chunks mentioning 'art. 121' + 'REN 414': {sum(arts_in_414_refs.values())}")
print(f"  top docs: {arts_in_414_refs.most_common(3)}")
