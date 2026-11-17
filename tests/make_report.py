"""Builds report.html in the test folder (tests\\_out) from the test outputs (all paths relative, so the folder
can be moved)."""
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402

CSS = """
body{margin:0;background:#141414;color:#e9e6df;font:14px/1.45 "Segoe UI",system-ui,sans-serif}
header{padding:28px 36px 18px;border-bottom:1px solid #2b2b2e;background:#19191a}
h1{margin:0 0 6px;font:bold 26px "Courier New",monospace;letter-spacing:3px}
h1 span{background:#f4f3ef;color:transparent;margin-left:3px}
h2{font:bold 15px "Courier New",monospace;letter-spacing:2px;text-transform:uppercase;margin:38px 0 12px;color:#d9d4ca}
main{padding:0 36px 60px}
.sum{display:flex;gap:26px;flex-wrap:wrap;margin-top:10px;color:#bdb9b1}
.sum b{color:#fff}
.ok{color:#7fd08d}.bad{color:#ff7b6b}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}
.grid.wide{grid-template-columns:repeat(auto-fill,minmax(420px,1fr))}
figure{margin:0;background:#1c1c1e;border:1px solid #2b2b2e;border-radius:8px;overflow:hidden}
figure img,figure video{display:block;width:100%;background:#0e0e0e}
figcaption{padding:8px 10px;font-size:12.5px;color:#c9c5bd}
figcaption small{display:block;color:#8e8a83;margin-top:2px}
.trio{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.trio figure img,.trio figure video{aspect-ratio:9/16;object-fit:contain}
.row{border:1px solid #2b2b2e;border-radius:10px;padding:12px;margin-bottom:16px;background:#18181a}
.row h3{margin:0 0 10px;font-size:14px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border-bottom:1px solid #2b2b2e;padding:5px 8px;text-align:left;vertical-align:top}
th{color:#8e8a83;font-weight:600}
td.pass{color:#7fd08d}td.FAIL,td.ERROR{color:#ff7b6b}
pre{white-space:pre-wrap;color:#ff9c8f;font-size:11px;margin:4px 0 0}
p.note{color:#bdb9b1;max-width:980px}
a{color:#a8c7e8}
"""


def esc(s):
    return html.escape(str(s))


def src(rel):
    return esc(rel.replace("\\", "/"))


def img(rel, cap="", small=""):
    return (f'<figure><a href="{src(rel)}"><img loading="lazy" src="{src(rel)}"></a>'
            f'<figcaption>{esc(cap)}{f"<small>{esc(small)}</small>" if small else ""}</figcaption></figure>')


def vid(rel, cap="", small="", muted=True):
    m = " muted" if muted else ""
    return (f'<figure><video src="{src(rel)}" controls loop playsinline preload="metadata"{m}></video>'
            f'<figcaption>{esc(cap)}{f"<small>{esc(small)}</small>" if small else ""}</figcaption></figure>')


def build():
    info = C.info_load()
    try:
        with open(os.path.join(C.TEST_DIR, "_data", "results.json"), encoding="utf-8") as f:
            res = json.load(f)
    except (OSError, ValueError):
        res = {"tests": [], "passed": 0, "total": 0, "when": "", "seconds": 0}
    ok = res["passed"] == res["total"] and res["total"] > 0
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>Declassified - test report</title>"
           f"<style>{CSS}</style></head><body>"]
    out.append("<header><h1>DECLAS<span>SIFIED</span></h1><div>Test report: redacted-document portraits from "
               "photos and videos. Dark areas become redaction bars on the text-line grid, mid-tones become toner "
               "specks and grey dots, the paper changes every 2 frames.</div><div class='sum'>"
               f"<div>Tests: <b class='{'ok' if ok else 'bad'}'>{res['passed']} / {res['total']} passed</b></div>"
               f"<div>Run: <b>{esc(res['when'])}</b> ({res['seconds']} s)</div></div></header><main>")

    out.append("<h2>1 · Photos → still + animated loop</h2>")
    for p in info.get("photos", []):
        pr = p["probe"]
        out.append(f"<div class='row'><h3>{esc(p['name'])}</h3><div class='trio'>"
                   f"{img(p['input'], 'Input photo')}{img(p['png'], 'Export PNG (1080×1920)')}"
                   f"{vid(p['mp4'], 'Export MP4 loop', f'{pr['frames']} frames · {pr['fps']:g} fps · {pr['codec']} · export {p['export_s']} s')}"
                   "</div></div>")

    out.append("<h2>2 · Videos → declassified video (sound kept)</h2>")
    for v in info.get("videos", []):
        pr, s = v["probe"], v["source"]
        small = (f"{pr['frames']}/{s['frames']} frames · {pr['fps']:.3f} fps (source {s['fps']:.3f}) · "
                 f"{pr['duration']:.2f} s · audio {'yes' if pr['audio'] else 'no'} (source "
                 f"{'yes' if s['audio'] else 'no'}) · {v['pages']} sheets · export {v['export_s']} s")
        out.append(f"<div class='row'><h3>{esc(v['name'])}</h3><div class='trio'>"
                   f"{vid(v['input'], 'Input video')}{vid(v['mp4'], 'Export MP4', small, muted=False)}"
                   f"{img(v['png'], 'Still from the middle (Export PNG)')}</div>"
                   f"<p class='note'>Stop-motion check: frames inside a hold differ by {v['hold_diff']} grey levels "
                   f"on average (the same picture), new sheets by {v['page_diff']}.</p></div>")

    for key, title in (("styles", "3 · Paper styles"), ("presets", "4 · Presets"),
                       ("formats", "5 · Formats and sizes"), ("fits", "5b · Framing (Antonello portrait)")):
        items = info.get(key, [])
        if not items:
            continue
        out.append(f"<h2>{title}</h2><div class='grid'>")
        for it in items:
            out.append(img(it["png"], it.get("name", ""), it.get("hint") or it.get("size") or ""))
        out.append("</div>")

    ui = info.get("ui", [])
    if ui:
        out.append("<h2>6 · The app window (driven by the UI test)</h2><div class='grid wide'>")
        for u in ui:
            out.append(img(u["png"], os.path.splitext(os.path.basename(u["png"]))[0], u["caption"]))
        out.append("</div>")

    out.append("<h2>7 · Every test</h2><table><tr><th>Suite</th><th>Test</th><th>Result</th><th>Time</th></tr>")
    for t in res["tests"]:
        err = f"<pre>{esc(t['error'])}</pre>" if t.get("error") else ""
        out.append(f"<tr><td>{esc(t['suite'])}</td><td>{esc(t['name'])}{err}</td>"
                   f"<td class='{esc(t['status'])}'>{esc(t['status'])}</td><td>{t['seconds']} s</td></tr>")
    out.append("</table></main></body></html>")
    path = os.path.join(C.TEST_DIR, "report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


if __name__ == "__main__":
    print(build())
