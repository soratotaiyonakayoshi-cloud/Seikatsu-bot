"""#おでかけ🗾 の地図データを作る（japan-topography の GeoJSON → 軽量な JSON）
使い方: python data/build_map.py <prefectures.json と N03-21_13_210101.json があるフォルダ>
元データ: https://github.com/smartnews-smri/japan-topography （data/municipality/geojson/s0010）
出典: 「国土数値情報（行政区域データ）」（国土交通省）を加工して作成
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def area(ring):
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def outer_rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [p[0] for p in polys]


def thin(ring, digits):
    """座標を丸めて、連続する同じ点を落とす"""
    out = []
    for x, y in ring:
        pt = [round(x, digits), round(y, digits)]
        if not out or out[-1] != pt:
            out.append(pt)
    return out


def convert(path, name_of, digits, min_area):
    with open(path, encoding="utf-8") as f:
        features = json.load(f)["features"]
    out = {}
    for ft in features:
        name = name_of(ft["properties"])
        if not name:
            continue
        for ring in outer_rings(ft["geometry"]):
            r = thin(ring, digits)
            if len(r) >= 4 and area(r) >= min_area:
                out.setdefault(name, []).append(r)
    return out


def main(raw):
    japan = convert(os.path.join(raw, "prefectures.json"), lambda p: p["N03_001"], 2, 0.0015)
    tokyo = convert(os.path.join(raw, "N03-21_13_210101.json"),
                    lambda p: None if p.get("N03_004") in (None, "所属未定地") else p["N03_004"], 3, 0.00002)
    for fn, d in (("map_japan.json", japan), ("map_tokyo.json", tokyo)):
        p = os.path.join(HERE, fn)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
        print(fn, len(d), "areas", os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main(sys.argv[1])
