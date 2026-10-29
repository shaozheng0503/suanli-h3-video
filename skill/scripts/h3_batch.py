#!/usr/bin/env python3
"""suanli-h3-video batch executor: submit / status / retry / upscale / done.

Pure standard library (Python 3.8+). No third-party deps.

Subcommands:
  submit   --comfy URL [--mode t2v|i2v|r2v] --prompt "..." [--prompt-file f] [--count N]
           [--resolution 480p|720p|768p] [--duration 5|10|15] [--seed auto|N]
           [--ref-images a.png b.png] [--first-frame f.png] [--out jobs/] [--job jobs/<ts>/]
           (--job resumes an existing job: done tasks are skipped)
  status   --job jobs/<ts>/ [--json]
  retry    --comfy URL --job jobs/<ts>/ [--only task003 ...]   (re-runs timeout/error tasks)
  upscale  --vsr URL --job jobs/<ts>/ [--scale 2.0] [--quality HIGH]
  done     --job jobs/<ts>/ [--task-id N]   (--task-id pauses the deployment via Open API)

Job layout (resumable):
  jobs/<ts>/job.json        ledger: [{id, prompt, seed, prompt_id, status, file, upscaled}]
  jobs/<ts>/raw/            downloaded 768p mp4s
  jobs/<ts>/2k/             upscaled mp4s
"""

import argparse
import json
import mimetypes
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

# --- constants (single source of truth) ---
RESOLUTIONS = {"480p": (864, 480), "720p": (1280, 720),
               "768p": (1344, 768), "1080p": (1920, 1080)}
DURATIONS = {5: 124, 10: 243, 15: 362}  # 17k+5 grid, 24fps
MODELS = {
    "fl2va_unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "ref2va_unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "fl2va_lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
    "ref2va_lora": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
}
TASK_GAP_S = 8          # RAM-cache breathing between generations (single-5090 OOM mitigation)
MAX_INFLIGHT = 2        # sliding-window depth: submit ahead while polling (validated on 5090x2/126GB)
POLL_INTERVAL_S = 12
POLL_DEADLINE_S = 1500
DL_TIMEOUT_S = 600      # big-file download (2K mp4 ~50MB)


def http_json(url, payload=None, timeout=120, retries=5, is_get=False):
    for attempt in range(retries):
        try:
            if is_get or payload is None:
                req = urllib.request.Request(url, method="GET")
            else:
                req = urllib.request.Request(
                    url, data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (502, 503) and attempt < retries - 1:
                time.sleep(6)
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(10)  # cold-start connection refused
                continue
            raise


def download_file(url, dest, timeout=DL_TIMEOUT_S, retries=3):
    """Robust download: chunked read + retries + atomic .part rename
    (a half-written file must never masquerade as a complete result)."""
    part = dest + ".part"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(part, dest)  # atomic on both POSIX & Windows
            return
        except Exception as e:
            if attempt < retries - 1:
                print(f"        [dl] retry {attempt+1} after: {str(e)[:80]}")
                time.sleep(5)
                continue
            try:
                os.remove(part)
            except OSError:
                pass
            raise


def multipart_body(fields, file_field, file_path):
    """Build a multipart/form-data body. fields: dict; file_field: name for the file part."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        data = f.read()
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
             f"filename=\"{os.path.basename(file_path)}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode() + data + \
            f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


def wait_alive(comfy):
    """ComfyUI has no /health; poll /system_stats until 200 (cold start 1-3 min)."""
    for i in range(20):
        try:
            http_json(comfy + "/system_stats", is_get=True, retries=1)
            return
        except Exception:
            print(f"  [alive] waiting ComfyUI... ({i+1}/20)")
            time.sleep(30)
    raise SystemExit("ComfyUI not alive after 10 min")


def upload_image(comfy, path):
    """POST /upload/image (multipart). Client timeout >= 600s for big files."""
    body, boundary = multipart_body({}, "image", path)
    req = urllib.request.Request(comfy + "/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())["name"]


def build_workflow(mode, prompt, seed, resolution, duration, ref_node_ids):
    w, h = RESOLUTIONS[resolution]
    length = DURATIONS[duration]
    unet = MODELS["fl2va_unet"] if mode in ("t2v", "i2v") else MODELS["ref2va_unet"]
    lora = MODELS["fl2va_lora"] if mode in ("t2v", "i2v") else MODELS["ref2va_lora"]
    wf = {
        "10": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "11": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["10", 0], "lora_name": lora, "strength_model": 1.0}},
        "12": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["11", 0], "shift_video": 12.0, "shift_audio": 3.0}},
        "13": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["clip"], "type": "minimax"}},
        "14": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["video_vae"]}},
        "15": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["audio_vae"]}},
        "16": {"class_type": "MiniMaxH3ImageToVideo" if mode in ("t2v", "i2v") else "MiniMaxH3ReferenceToVideo",
               "inputs": {"clip": ["13", 0], "vae": ["14", 0], "prompt": prompt,
                          "width": w, "height": h, "length": length}},
        "17": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "18": {"class_type": "BasicGuider", "inputs": {"model": ["12", 0], "conditioning": ["16", 0]}},
        "19": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "20": {"class_type": "BasicScheduler", "inputs": {"model": ["12", 0], "scheduler": "simple", "steps": 4, "denoise": 1.0}},
        "21": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["17", 0], "guider": ["18", 0],
                                                                  "sampler": ["19", 0], "sigmas": ["20", 0],
                                                                  "latent_image": ["16", 1]}},
        "22": {"class_type": "VAEDecode", "inputs": {"samples": ["21", 0], "vae": ["14", 0]}},
        "23": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["21", 0], "vae": ["15", 0]}},
        "24": {"class_type": "CreateVideo", "inputs": {"images": ["22", 0], "fps": 24.0, "audio": ["23", 0]}},
        "25": {"class_type": "SaveVideo", "inputs": {"video": ["24", 0], "filename_prefix": "h3skill", "format": "auto", "codec": "auto"}},
    }
    if mode == "r2v":
        wf["16"]["inputs"]["audio_vae"] = ["15", 0]
        wf["16"]["inputs"]["ref_images"] = [[nid, 0] for nid in ref_node_ids]
        wf["16"]["inputs"]["ref_image_size"] = "match"  # match=快(缩放到生成分辨率) / max=身份保真更强
        for i, nid in enumerate(ref_node_ids, 1):
            wf[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": f"__REF{i}__"}}
    elif mode == "i2v":
        # first-frame driven: single LoadImage -> first_frame input
        wf["201"] = {"class_type": "LoadImage", "inputs": {"image": "__FIRST__"}}
        wf["16"]["inputs"]["first_frame"] = ["201", 0]
    return wf


def submit_workflow(comfy, wf, client_id):
    resp = http_json(comfy + "/prompt",
                     {"prompt": wf, "client_id": client_id})
    errs = resp.get("node_errors")
    if errs:
        raise RuntimeError(f"node_errors: {json.dumps(errs)[:500]}")
    return resp["prompt_id"]


def poll_history(comfy, pid, deadline_s=POLL_DEADLINE_S):
    """Returns (image_meta | None, lost: bool). lost=True means the task vanished
    from history (instance restart wiped it) — no point waiting out the deadline."""
    t0 = time.time()
    notfound = 0
    while time.time() - t0 < deadline_s:
        time.sleep(POLL_INTERVAL_S)
        try:
            h = http_json(f"{comfy}/history/{pid}", is_get=True, retries=2)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                notfound += 1
                if notfound >= 5:   # ~1min of consistent 404 = task lost
                    return None, True
                continue
            continue
        except Exception:
            continue
        info = h.get(pid) or {}
        st = info.get("status", {}).get("status_str")
        if st == "success":
            for nid, o in info.get("outputs", {}).items():
                for im in o.get("images", []):
                    if im.get("type") == "output" and im["filename"].endswith(".mp4"):
                        return im, False
            return None, False
        if st == "error":
            msg = info.get("status", {}).get("messages", [])
            raise RuntimeError(f"comfy error: {json.dumps(msg)[:300]}")
    return None, False  # timeout


def download(comfy, im, dest):
    url = (f"{comfy}/view?filename={urllib.request.quote(im['filename'])}"
           f"&subfolder={urllib.request.quote(im.get('subfolder', ''))}&type=output")
    download_file(url, dest)


def job_dir_create(out_root):
    ts = time.strftime("%Y%m%d_%H%M%S")
    jd = os.path.join(out_root, ts)
    os.makedirs(os.path.join(jd, "raw"), exist_ok=True)
    os.makedirs(os.path.join(jd, "2k"), exist_ok=True)
    return jd


def cmd_submit(args):
    comfy = args.comfy.rstrip("/")
    wait_alive(comfy)

    # resume or create job
    if args.job:
        jd = args.job.rstrip("/\\").replace("\\", "/")
        if not os.path.isfile(os.path.join(jd, "job.json")):
            raise SystemExit(f"no job.json under {jd} (resubmit without --job to create new)")
        ledger = load_job(jd)
        print(f"resuming job {jd}: {len(ledger)} existing tasks")
    else:
        jd = job_dir_create(args.out)
        ledger = []
    done_ids = {e["id"] for e in ledger if e["status"] in ("ok", "cached")}

    prompts = []
    if args.prompt_file:
        prompts = [l.strip() for l in open(args.prompt_file, encoding="utf-8") if l.strip()]
    elif args.prompt:
        prompts = [args.prompt]
    else:
        if not args.job:
            raise SystemExit("need --prompt or --prompt-file (or --job to resume)")
    # resume semantics: failed tasks must reuse THEIR OWN original prompt from
    # the ledger (round-robin over a new prompt file would mis-assign prompts).
    # New prompts only apply when explicitly given AND the task has no ledger entry.
    if args.job:
        orig_prompts = {e["id"]: e.get("prompt") or "" for e in ledger}
        if not prompts:
            prompts = [e.get("prompt") or "" for e in ledger]
    count = args.count or (len(ledger) if args.job else len(prompts))
    if args.job and args.count is None and prompts and len(prompts) != len(ledger):
        print(f"NOTE: resume with {len(prompts)} prompts but ledger has {len(ledger)} tasks; "
              f"failed tasks rerun with their ORIGINAL prompts from the ledger")

    ref_node_ids = []
    r2v_names = []
    first_frame_name = None
    if args.mode == "r2v":
        if not args.ref_images:
            raise SystemExit("r2v mode needs --ref-images")
        for i, p in enumerate(args.ref_images, 1):
            name = upload_image(comfy, p)
            nid = 200 + i  # 201, 202, ... above the 13 core nodes
            ref_node_ids.append(nid)
            r2v_names.append(name)
            print(f"  uploaded {p} -> node {nid} ({name})")
    elif args.mode == "i2v":
        if not args.first_frame:
            raise SystemExit("i2v mode needs --first-frame")
        first_frame_name = upload_image(comfy, args.first_frame)
        print(f"  uploaded first frame: {first_frame_name}")

    client_id = uuid.uuid4().hex
    ran_new = 0
    inflight = {}  # entry_id -> prompt_id (submitted, awaiting result)

    def settle(entry):
        """Poll one inflight task to completion, download, update ledger.
        Distinguishes generation failure (rerun costs GPU) from download
        failure (rerun is free — history still has the result)."""
        pid = inflight.pop(entry["id"])
        try:
            im, lost = poll_history(comfy, pid)
            if im is None:
                entry["status"] = "lost (task vanished)" if lost else "timeout"
                print(f"        -> {entry['status']} (retryable)")
                return
            dest = os.path.join(jd, "raw", f"{entry['id']}_seed{entry['seed']}.mp4")
            entry["result_meta"] = im  # keep for download-only retry
            try:
                download(comfy, im, dest)
                entry.update(status="ok", file=dest,
                             size_mb=round(os.path.getsize(dest) / 1048576, 2))
                print(f"        -> ok {entry['size_mb']}MB")
            except Exception as de:
                # generation SUCCEEDED; only the download failed. Retrying just
                # the download is free — mark accordingly so retry doesn't regenerate.
                entry["status"] = f"download_error: {str(de)[:120]}"
                print(f"        -> {entry['status']} (result kept in history)")
        except Exception as e:
            entry["status"] = f"error: {str(e)[:150]}"
            print(f"        -> {entry['status']}")

    # resume of an empty ledger without prompts is a no-op — say so instead of
    # silently exiting with count=0
    if args.job and not ledger and not prompts:
        raise SystemExit(f"job {jd} has an empty ledger and no --prompt/--prompt-file given; nothing to resume")

    for i in range(count):
        entry_id = f"task{i+1:03d}"
        # window full: settle the oldest inflight task before submitting more
        while len(inflight) >= MAX_INFLIGHT:
            oldest = next(e for e in ledger if e["id"] in inflight)
            settle(oldest)
            save_job(jd, ledger)
        if entry_id in done_ids:
            print(f"[{i+1}/{count}] {entry_id} -> skip (done)")
            continue
        # resume: failed/lost tasks keep their own original prompt
        p = orig_prompts.get(entry_id) if (args.job and entry_id in orig_prompts) else None
        if p is None:
            p = prompts[i % len(prompts)] if prompts else None
        if not p:
            print(f"[{i+1}/{count}] {entry_id} -> no prompt available, stop")
            break
        seed = random.randint(1, 2**31 - 1) if args.seed == "auto" else int(args.seed) + i
        wf = build_workflow(args.mode, p, seed, args.resolution, args.duration, ref_node_ids)
        # bind uploaded image names into LoadImage placeholders
        if args.mode == "r2v":
            for j, nid in enumerate(ref_node_ids, 1):  # j: avoid shadowing i
                wf[str(nid)]["inputs"]["image"] = r2v_names[j-1]
        elif args.mode == "i2v":
            wf["201"]["inputs"]["image"] = first_frame_name
        entry = {"id": entry_id, "prompt": p, "seed": seed,
                 "mode": args.mode, "resolution": args.resolution,
                 "duration": args.duration,
                 "status": "pending", "file": None}
        print(f"[{i+1}/{count}] {entry_id} seed={seed} submitting...")
        try:
            pid = submit_workflow(comfy, wf, client_id)
            entry["prompt_id"] = pid
            entry["status"] = "running"
            inflight[entry_id] = pid
        except Exception as e:
            entry["status"] = f"error: {str(e)[:150]}"
            print(f"        -> {entry['status']}")
        ledger = [e for e in ledger if e["id"] != entry_id] + [entry]
        save_job(jd, ledger)
        ran_new += 1
        time.sleep(TASK_GAP_S)  # RAM-cache breathing (single-5090 OOM mitigation)
    # drain remaining inflight
    while inflight:
        oldest = next(e for e in ledger if e["id"] in inflight)
        settle(oldest)
        save_job(jd, ledger)
    print(f"\njob dir: {jd}")
    ok = sum(1 for e in ledger if e["status"] == "ok")
    print(f"done: {ok}/{len(ledger)} ok ({ran_new} new runs). "
          f"Next: h3_batch.py status/retry/upscale --job {jd}")


def load_job(jd):
    return json.load(open(os.path.join(jd, "job.json"), encoding="utf-8"))


def save_job(jd, ledger):
    json.dump(ledger, open(os.path.join(jd, "job.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def classify(status):
    """retryable: transient (timeout / network / comfy exec error). fatal: bad params."""
    s = str(status)
    if s == "ok" or s == "cached":
        return "done"
    if s.startswith("error: node_errors") or s.startswith("error: HTTP Error 400"):
        return "fatal"      # bad workflow params: retrying won't help
    if "comfy error" in s:
        return "retryable"  # execution-stage failure (often transient)
    if s == "timeout" or s.startswith("lost"):
        return "retryable"  # instance restart / queue loss
    if s.startswith("download_error"):
        return "retryable"  # generation succeeded; only the download needs redoing
    return "retryable"      # network errors etc.


def cmd_status(args):
    ledger = load_job(args.job)
    from collections import Counter
    c = Counter(e["status"] for e in ledger)
    rows = [{"id": e["id"], "status": e["status"], "class": classify(e["status"]),
             "size_mb": e.get("size_mb"), "file": e.get("file"),
             "2k": e.get("upscaled"), "seed": e.get("seed")} for e in ledger]
    if args.json:
        print(json.dumps({"job": args.job, "summary": dict(c), "tasks": rows},
                         ensure_ascii=False, indent=1))
        return
    print(f"job {args.job}: {dict(c)}")
    for e in rows:
        print(f"  {e['id']} {e['status']:10s} [{e['class']}] {e['size_mb'] or '-'}MB "
              f"{'[2K]' if e['2k'] else ''}")


def cmd_retry(args):
    comfy = args.comfy.rstrip("/")
    wait_alive(comfy)
    ledger = load_job(args.job)
    targets = [e for e in ledger
               if classify(e["status"]) == "retryable"
               and (not args.only or e["id"] in args.only)]
    if not targets:
        print("nothing retryable" + (" matching --only" if args.only else ""))
        return
    # safety: i2v/r2v tasks cannot be retried autonomously (uploads are wiped
    # on instance restart); require submit --job with image flags instead.
    skipped = [e for e in targets if e.get("mode") in ("i2v", "r2v")]
    if skipped:
        print(f"SKIP {len(skipped)} i2v/r2v task(s) — re-submit with: "
              f"submit --job {args.job} --mode <i2v|r2v> <image flags> ...")
        targets = [e for e in targets if e.get("mode") not in ("i2v", "r2v")]
        if not targets:
            return
    print(f"retrying {len(targets)} task(s)")
    client_id = uuid.uuid4().hex
    for e in targets:
        # download_error fast path: the generation result is still in history —
        # just re-download it, don't burn GPU regenerating.
        if str(e["status"]).startswith("download_error") and e.get("result_meta"):
            try:
                dest = os.path.join(args.job, "raw", f"{e['id']}_seed{e['seed']}.mp4")
                download(comfy, e["result_meta"], dest)
                e.update(status="ok", file=dest,
                         size_mb=round(os.path.getsize(dest) / 1048576, 2))
                print(f"  {e['id']} -> re-downloaded only {e['size_mb']}MB (no GPU cost)")
            except Exception as de:
                e["status"] = f"download_error: {str(de)[:120]}"
                print(f"  {e['id']} -> download failed again: {str(de)[:80]}")
            save_job(args.job, ledger)
            continue
        # restore original spec from the ledger; CLI flags only override when set
        res = args.resolution or e.get("resolution") or "768p"
        dur = args.duration or e.get("duration") or 5
        wf = build_workflow("t2v", e["prompt"], e["seed"], res, dur, [])
        print(f"  {e['id']} seed={e['seed']} ({res}/{dur}s) resubmitting...")
        try:
            pid = submit_workflow(comfy, wf, client_id)
            e["prompt_id"] = pid
            e["status"] = "running"
            save_job(args.job, ledger)
            im, _lost = poll_history(comfy, pid)
            if im:
                dest = os.path.join(args.job, "raw", f"{e['id']}_seed{e['seed']}.mp4")
                download(comfy, im, dest)
                e.update(status="ok", file=dest,
                         size_mb=round(os.path.getsize(dest) / 1048576, 2))
                print(f"        -> ok {e['size_mb']}MB")
            else:
                e["status"] = "timeout"
                print("        -> timeout again")
        except Exception as ex:
            e["status"] = f"error: {str(ex)[:150]}"
            print(f"        -> {e['status']}")
        save_job(args.job, ledger)
        time.sleep(TASK_GAP_S)
    ok = sum(1 for x in ledger if x["status"] == "ok")
    print(f"job now: {ok}/{len(ledger)} ok")


def cmd_upscale(args):
    vsr = args.vsr.rstrip("/")
    http_json(vsr + "/healthz", is_get=True)
    ledger = load_job(args.job)
    pending = [e for e in ledger if e["status"] == "ok" and e.get("file")
               and not e.get("upscaled")]
    n_not_ok = sum(1 for e in ledger if e["status"] != "ok")
    if n_not_ok:
        print(f"NOTE: {n_not_ok} task(s) not ok yet; upscaling completed ones only")
    for e in pending:
        body, boundary = multipart_body(
            {"scale": str(args.scale), "quality": args.quality,
             "vcodec": "libx264", "bitrate": "50M"},
            "file", e["file"])
        req = urllib.request.Request(vsr + "/api/v1/upscale/file", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=600) as r:
            task = json.loads(r.read())
        tid = task["task_id"]
        print(f"  {e['id']} upscale {tid} queued")
        while True:
            time.sleep(8)
            st = http_json(f"{vsr}/api/v1/tasks/{tid}", is_get=True)
            if st["status"] == "done":
                base = os.path.basename(e["file"].replace("\\", "/"))
                dest = os.path.join(args.job, "2k", base.replace(".mp4", "_2k.mp4"))
                download_file(f"{vsr}/api/v1/tasks/{tid}/result", dest)
                e["upscaled"] = dest
                e["upscale_meta"] = st.get("meta", {})
                save_job(args.job, ledger)
                print(f"        -> 2K done {round(os.path.getsize(dest)/1048576,1)}MB")
                break
            if st["status"] == "error":
                e["upscale_error"] = st.get("error", "")[:200]
                save_job(args.job, ledger)
                print(f"        -> error: {e['upscale_error'][:100]}")
                break
        time.sleep(5)


def suanli_pause(task_id):
    """Pause a deployment via Open API (reads SUANLI_TOKEN from env)."""
    token = (os.environ.get("SUANLI_TOKEN") or "").strip()
    if not token:
        return {"error": "SUANLI_TOKEN not set; pause manually via console or skill api/deployment.md"}
    url = "https://openapi.suanli.cn/api/deployment/task/pause"
    req = urllib.request.Request(url,
        data=json.dumps({"task_id": int(task_id)}).encode(),
        headers={"token": token, "version": "1.0.0",
                 "timestamp": str(int(time.time() * 1000)),
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def cmd_done(args):
    ledger = load_job(args.job)
    ok = [e for e in ledger if e["status"] == "ok"]
    up = [e for e in ledger if e.get("upscaled")]
    manifest = [{"id": e["id"], "prompt": e["prompt"], "seed": e["seed"],
                 "raw": e.get("file"), "2k": e.get("upscaled"),
                 "status": e["status"]} for e in ledger]
    json.dump(manifest, open(os.path.join(args.job, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"job {args.job}: {len(ok)}/{len(ledger)} generated, {len(up)} upscaled to 2K")
    print(f"manifest written: {os.path.join(args.job, 'manifest.json')}")
    if args.task_id:
        try:
            resp = suanli_pause(args.task_id)
            print(f"deployment {args.task_id} paused: {json.dumps(resp)[:150]}")
        except Exception as e:
            print(f"pause failed ({str(e)[:100]}) — pause manually, billed per second!")
    else:
        print("REMINDER: pause the deployment task if no more jobs (billed per second).")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit")
    s.add_argument("--comfy", required=True)
    s.add_argument("--mode", choices=["t2v", "i2v", "r2v"], default="t2v")
    s.add_argument("--prompt")
    s.add_argument("--prompt-file")
    s.add_argument("--count", type=int)
    s.add_argument("--resolution", choices=list(RESOLUTIONS), default="768p")
    s.add_argument("--duration", type=int, choices=list(DURATIONS), default=5)
    s.add_argument("--seed", default="auto")
    s.add_argument("--ref-images", nargs="+")
    s.add_argument("--first-frame")
    s.add_argument("--out", default="jobs")
    s.add_argument("--job", help="resume an existing job dir (done tasks skipped)")

    t = sub.add_parser("status")
    t.add_argument("--job", required=True)
    t.add_argument("--json", action="store_true", help="machine-readable output")

    r = sub.add_parser("retry")
    r.add_argument("--comfy", required=True)
    r.add_argument("--job", required=True)
    r.add_argument("--only", nargs="+", help="retry only these task ids (e.g. task003 task007)")
    r.add_argument("--resolution", choices=list(RESOLUTIONS))
    r.add_argument("--duration", type=int, choices=list(DURATIONS))

    u = sub.add_parser("upscale")
    u.add_argument("--vsr", required=True)
    u.add_argument("--job", required=True)
    u.add_argument("--scale", default="2.0")
    u.add_argument("--quality", default="HIGH")

    d = sub.add_parser("done")
    d.add_argument("--job", required=True)
    d.add_argument("--task-id", type=int,
                   help="also pause this deployment task via Open API (needs SUANLI_TOKEN)")

    args = ap.parse_args()
    {"submit": cmd_submit, "status": cmd_status, "retry": cmd_retry,
     "upscale": cmd_upscale, "done": cmd_done}[args.cmd](args)


if __name__ == "__main__":
    main()
