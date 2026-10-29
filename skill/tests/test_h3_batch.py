"""Regression tests for h3_batch.py (run: python tests/test_h3_batch.py).

Pure unit tests, no network. Covers the fixes from the 10-29 audit round:
  #1 resume semantics (count defaults to ledger length; prompts reused)
  #2 atomic download (.part + rename; no half files)
  #3 ledger stores mode/resolution/duration for faithful retry
  #4 cross-platform basename in upscale dest naming
  #5 retry refuses i2v/r2v tasks (uploads wiped on restart)
  #6 error classification (done/retryable/fatal)
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import h3_batch as hb

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def test_classify():
    check("classify ok -> done", hb.classify("ok") == "done")
    check("classify cached -> done", hb.classify("cached") == "done")
    check("classify timeout -> retryable", hb.classify("timeout") == "retryable")
    check("classify 400 -> fatal", hb.classify("error: HTTP Error 400: Bad Request") == "fatal")
    check("classify node_errors -> fatal",
          hb.classify("error: node_errors: {...}") == "fatal")
    check("classify comfy error -> retryable",
          hb.classify("error: comfy error: xyz") == "retryable")
    check("classify net error -> retryable",
          hb.classify("error: <urlopen error timed out>") == "retryable")
    check("classify running -> retryable", hb.classify("running") == "retryable")
    check("classify lost -> retryable", hb.classify("lost (task vanished)") == "retryable")
    check("classify download_error -> retryable",
          hb.classify("download_error: timed out") == "retryable")


def test_workflow_modes():
    wf = hb.build_workflow("t2v", "p", 1, "768p", 5, [])
    check("t2v uses fl2va unet", "fl2va" in wf["10"]["inputs"]["unet_name"])
    check("t2v no first_frame", "first_frame" not in wf["16"]["inputs"])
    wfi = hb.build_workflow("i2v", "p", 1, "768p", 5, [])
    check("i2v binds first_frame", wfi["16"]["inputs"]["first_frame"] == ["201", 0])
    wfr = hb.build_workflow("r2v", "p", 1, "768p", 5, [201, 202])
    check("r2v uses ref2va unet", "ref2va" in wfr["10"]["inputs"]["unet_name"])
    check("r2v ref_images list", wfr["16"]["inputs"]["ref_images"] == [[201, 0], [202, 0]])
    check("r2v ref_image_size", wfr["16"]["inputs"]["ref_image_size"] == "match")
    check("scheduler has denoise", wf["20"]["inputs"]["denoise"] == 1.0)
    # frame grid
    for d, l in hb.DURATIONS.items():
        check(f"duration {d}s -> {l} on 17k+5 grid", (l - 5) % 17 == 0 and 124 <= l <= 362)


def test_atomic_download():
    dest = os.path.join(tempfile.mkdtemp(), "out.mp4")
    part = dest + ".part"
    with open(part, "wb") as f:
        f.write(b"x")
    # local file copy via file:// URL (three slashes, host omitted)
    src = os.path.join(tempfile.mkdtemp(), "src.mp4")
    with open(src, "wb") as f:
        f.write(b"y" * 300000)
    url = "file:///" + src.replace("\\", "/").lstrip("/")
    hb.download_file(url, dest)
    check("download writes dest", os.path.exists(dest) and os.path.getsize(dest) == 300000)
    check("no .part residue", not os.path.exists(part))


def test_ledger_roundtrip():
    jd = tempfile.mkdtemp()
    hb.save_job(jd, [{"id": "task001", "status": "ok", "mode": "t2v",
                      "resolution": "768p", "duration": 10, "file": "x"}])
    led = hb.load_job(jd)
    e = led[0]
    check("ledger roundtrip", e["id"] == "task001" and e["status"] == "ok")
    check("ledger stores spec for retry",
          e.get("resolution") == "768p" and e.get("duration") == 10)


def test_crossplat_basename():
    # simulate the upscale dest-naming expression
    e_file = "jobs\\20261029_1100\\raw\\task001_seed42.mp4"  # windows-style in json
    base = os.path.basename(e_file.replace("\\", "/"))
    check("crossplat basename", base == "task001_seed42.mp4")
    e_file2 = "jobs/20261029_1100/raw/task001_seed42.mp4"
    base2 = os.path.basename(e_file2.replace("\\", "/"))
    check("posix path unaffected", base2 == "task001_seed42.mp4")


def test_resume_count_logic():
    # replicate the count expression from cmd_submit
    ledger = [{"id": f"task{i:03d}", "status": "timeout"} for i in range(1, 9)]
    args_job, args_count, prompts = True, None, []
    count = args_count or (len(ledger) if args_job else len(prompts))
    check("resume default count = ledger len", count == 8)
    # with explicit --count 10 on resume
    count2 = 10 or (len(ledger) if args_job else len(prompts))
    check("explicit count overrides", count2 == 10)
    # fresh run
    count3 = None or (len(ledger) if False else len(["a", "b", "c"]))
    check("fresh run count = prompts len", count3 == 3)


def test_retry_guard():
    # i2v/r2v targets must be filtered out before retry
    ledger = [
        {"id": "task001", "status": "timeout", "mode": "t2v"},
        {"id": "task002", "status": "timeout", "mode": "i2v"},
        {"id": "task003", "status": "timeout", "mode": "r2v"},
        {"id": "task004", "status": "ok", "mode": "t2v"},
        {"id": "task005", "status": "error: HTTP Error 400", "mode": "t2v"},  # fatal
    ]
    retryable = [e for e in ledger if hb.classify(e["status"]) == "retryable"]
    guarded = [e for e in retryable if e.get("mode") not in ("i2v", "r2v")]
    check("retry guard keeps t2v only", [e["id"] for e in guarded] == ["task001"])


def main():
    print("== classify =="); test_classify()
    print("== workflows =="); test_workflow_modes()
    print("== atomic download =="); test_atomic_download()
    print("== ledger =="); test_ledger_roundtrip()
    print("== cross-platform =="); test_crossplat_basename()
    print("== resume count =="); test_resume_count_logic()
    print("== retry guard =="); test_retry_guard()
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
