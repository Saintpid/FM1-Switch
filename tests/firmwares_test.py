#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""tools/fm1_firmwares.py: The Firmware Hub's list (read, matched by identity, rewritten from a
catalogue) and how fm1_flash names an installed firmware with it.
  python3 tests/firmwares_test.py"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_firmwares as W  # noqa: E402
import fm1_flash as F  # noqa: E402
from fm1_fixture import unit_flash, with_app  # noqa: E402

failed = 0


def ok(cond, what):
    global failed
    print(f"{what:<70} {'ok' if cond else 'FAIL'}")
    failed += not cond


# ------------------------------------------------------------------ the list ---
e = W.entries()
ids = [x["id"] for x in e]
ok(len(e) >= 290 and len(set(ids)) == len(ids), f"the hub's list: {len(e)} firmwares, each id once")
ok(all(rx is not None for _, rx in W._compiled()), "every identity pattern compiles")
ok(all(x["chip"] in ("new", "old", "any") for x in e), "every entry says which flash chip it saves on")
ok(all(x["product"].startswith("FM-1_") for x in e), "every entry has its build's identity")
known = set(ids)
ok(all(b in known for x in e for b in x["base"]), "every base names a firmware on the list")
ok("thefirmwarehub.com" in W.CATALOG_CREDIT and W.CATALOG_LICENSE == "CC-BY-4.0", "the list is credited")

# ------------------------------------------------------------------ matching ---
ok(W.product_ids(b"x FM-1_9535\0 FM-1_9535 FM-1_12345678901") == ["FM-1_9535"],
   "identities found once, never a cut longer number")
ok([x["id"] for x in W.match(["FM-1_918"])] == ["felucca"], "FM-1_918: Felucca (its own build)")
ok([x["id"] for x in W.match(["FM-1_9535"])] == ["obervave"], "FM-1_9535: OberVave")
ok([x["id"] for x in W.match(["FM-1_9537"])] == ["obervave"], "FM-1_9537: OberVave by its pattern")
many = W.match(["FM-1_900"])
ok(len(many) > 5 and "sloop" in [x["id"] for x in many], "FM-1_900: many firmwares share it")
ok(W.describe(many).startswith("one of: ") and W.describe(many).endswith("more"), "and is told as several")
ok(W.match(["FM-1_873"]) == [], "a number only Felucca's default pattern takes names nothing")
ok(W.match(["FM-1_015"]) == [], "M-VAVE's own identity is not a hub firmware")
ok(W.known("wild")["keys"] != W.UNKNOWN and W.known("wild")["soft_key"] is True, "Wild's update-mode keys")
ok(set(W.known("zp12").values()) == {W.UNKNOWN}, "what nobody checked stays unknown")
ok(W.main(["FM-1_918"]) == 0 and W.main([]) == 2, "the command line")

# ------------------------------------------------------------- in the flash ---
unit = unit_flash()
name, hub = F.firmware_match(with_app(unit, b"..FM-1_9535\0.."))
ok(name == "OberVave, by zvenson" and [x["id"] for x in hub] == ["obervave"], "an installed OberVave is named")
ok(F.firmware_info(with_app(unit, b"Wild 0.3.38\0FM-1_873\0")) == "Wild 0.3.38", "our own firmware by its text")
name, hub = F.firmware_match(with_app(unit, b"SLOOP 2.8.5\0FM-1_900\0"))
ok(name == "SLOOP 2.8.5" and [x["id"] for x in hub] == ["sloop"], "SLOOP by its text, its hub entry kept")
ok(F.firmware_info(with_app(unit, b"FM-1_900\0")).startswith("one of: "), "an ambiguous identity is told so")
ok(F.firmware_info(with_app(unit, b"FM-1_016\0")) == "FM-1 016 (stock)", "M-VAVE's 016 is still stock")
a = F.analyse(with_app(unit, b"FM-1_9535\0"))
ok(a["firmware_hub"] == ["obervave"] and a["firmware_chip"] == "new", "the analysis carries the hub entry")

# ----------------------------------------------------------------- updating ---
tmp = Path(tempfile.mkdtemp())
mod = tmp / "fm1_firmwares.py"
shutil.copy(ROOT / "tools/fm1_firmwares.py", mod)
cat = {"license": {"license": "CC-BY-4.0"}, "updated": "2026-12-01 00:00 UTC",
       "firmwares": [dict(x, base=x["base"], extra="ignored") for x in e[:3]]
       + [{"id": "new1", "name": "New\tOne", "author": "a", "repo": "a/b", "ident": r"FM-1_12\d", "product": "FM-1_123",
           "chip": "new", "base": ["felucca"]}]}
(tmp / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
ok(W.update(tmp / "catalog.json", mod) == 4, "rewritten from a catalogue")
ns = {"__file__": str(mod), "__name__": "fm1_firmwares_copy"}
exec(compile(mod.read_text(encoding="utf-8"), str(mod), "exec"), ns)
got = ns["entries"]()
ok(got[:3] == e[:3] and got[3]["name"] == "New One" and got[3]["ident"] == r"FM-1_12\d"
   and ns["CATALOG_UPDATED"] == "2026-12-01 00:00 UTC", "the new list reads back exactly")
ok(ns["KNOWN"] == W.KNOWN and ns["match"](["FM-1_125"])[0]["id"] == "new1", "and the rest of the module is kept")
again = mod.read_text(encoding="utf-8")
ns["update"](tmp / "catalog.json", mod)
ok(mod.read_text(encoding="utf-8") == again, "rewriting twice changes nothing")
cat["license"] = {"license": "proprietary"}
(tmp / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
try:
    W.update(tmp / "catalog.json", mod)
    ok(False, "a catalogue under another licence is refused")
except SystemExit:
    ok(mod.read_text(encoding="utf-8") == again, "a catalogue under another licence is refused")
shutil.rmtree(tmp)

print("all firmware list tests passed" if not failed else f"{failed} FIRMWARE LIST TESTS FAILED")
sys.exit(1 if failed else 0)
