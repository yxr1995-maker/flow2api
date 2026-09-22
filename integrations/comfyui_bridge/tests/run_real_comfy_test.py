import os
import sys
import time
import json
import base64
import urllib.request
import urllib.error
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from PIL import Image
import io

MOCK_PORT = 18088
COMFY_TEST_PORT = 18188

# Base64 1x1 image
TINY_PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
TINY_PNG_B64 = base64.b64encode(TINY_PNG_BYTES).decode("utf-8")
FIXTURE_MP4 = os.path.join(os.path.dirname(__file__), 'fixtures', 'valid_test.mp4')
try:
    with open(FIXTURE_MP4, "rb") as _f:
        TINY_MP4_BYTES = _f.read()
except FileNotFoundError:
    raise SystemExit("mock needs real mp4 at tests/fixtures/valid_test.mp4; generate it with ffmpeg first")

class MockFlowServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            model = body.get("model", "")
            messages = body.get("messages", [])

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            # Stream chunks
            chunk_start = {
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": "✨ 任务已启动\n"},
                    "finish_reason": None
                }]
            }
            self.wfile.write(f"data: {json.dumps(chunk_start)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.05)

            if "image" in model:
                content = f"![Generated Image](data:image/png;base64,{TINY_PNG_B64})"
            else:
                content = f"<video src='http://127.0.0.1:{MOCK_PORT}/fake.mp4' controls></video>"

            chunk_final = {
                "choices": [{
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": "stop"
                }]
            }
            self.wfile.write(f"data: {json.dumps(chunk_final)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    def do_GET(self):
        if self.path == "/fake.mp4":
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(TINY_MP4_BYTES)))
            self.end_headers()
            self.wfile.write(TINY_MP4_BYTES)

def start_mock_server():
    httpd = HTTPServer(("127.0.0.1", MOCK_PORT), MockFlowServer)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd

def wait_for_comfy(port, timeout=30):
    start = time.time()
    url = f"http://127.0.0.1:{port}/system_stats"
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False

def queue_prompt(port, prompt_graph):
    url = f"http://127.0.0.1:{port}/prompt"
    data = json.dumps({"prompt": prompt_graph}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))

def wait_for_prompt_completion(port, prompt_id, timeout=30):
    start = time.time()
    url = f"http://127.0.0.1:{port}/history/{prompt_id}"
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if prompt_id in data:
                    return data[prompt_id]
        except Exception:
            pass
        time.sleep(0.5)
    return None

def main():
    print(f"=== Starting Isolated Integration Test on ComfyUI 127.0.0.1:{COMFY_TEST_PORT} ===")
    mock_server = start_mock_server()
    print(f"[1] Mock Flow2API server listening on 127.0.0.1:{MOCK_PORT}")

    # Prepare input image for reference tests
    comfy_dir = os.environ.get("COMFYUI_DIR", os.path.expanduser("~/ComfyUI"))
    input_dir = os.path.join(comfy_dir, "input")
    os.makedirs(input_dir, exist_ok=True)
    test_input_path = os.path.join(input_dir, "test_flow2api_input.png")
    with open(test_input_path, "wb") as f:
        f.write(TINY_PNG_BYTES)

    # Launch isolated ComfyUI instance with --cpu and isolated env vars
    env = os.environ.copy()
    env["FLOW2API_BASE_URL"] = f"http://127.0.0.1:{MOCK_PORT}"
    env["FLOW2API_KEY"] = "mock-isolated-key-never-leaked"

    cmd = [
        os.path.join(comfy_dir, ".venv/bin/python"),
        os.path.join(comfy_dir, "main.py"),
        "--listen", "127.0.0.1",
        "--port", str(COMFY_TEST_PORT),
        "--cpu",
        "--disable-auto-launch"
    ]
    print(f"[2] Launching test ComfyUI process: {' '.join(cmd)}")
    comfy_proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        print("[3] Waiting for test ComfyUI server to become ready...")
        if not wait_for_comfy(COMFY_TEST_PORT, timeout=35):
            print("ERROR: Test ComfyUI failed to start in time!")
            out, err = comfy_proc.communicate(timeout=5)
            print("Stdout:", out.decode("utf-8", errors="ignore")[-2000:])
            print("Stderr:", err.decode("utf-8", errors="ignore")[-2000:])
            sys.exit(1)
        print("  -> Test ComfyUI server is READY!")

        # Verify object_info contains Flow2API nodes
        info_url = f"http://127.0.0.1:{COMFY_TEST_PORT}/object_info"
        req = urllib.request.Request(info_url)
        with urllib.request.urlopen(req) as resp:
            object_info = json.loads(resp.read().decode("utf-8"))
        assert "Flow2API_ImageNode" in object_info, "Flow2API_ImageNode not registered!"
        assert "Flow2API_VideoNode" in object_info, "Flow2API_VideoNode not registered!"
        print("  -> Flow2API_ImageNode & Flow2API_VideoNode discovered in /object_info successfully!")

        history_records = {}

        # TEST A: Real Image Queue
        print("\n[Test A] Submitting Flow2API Image generation to real queue...")
        graph_img = {
            "1": {
                "inputs": {
                    "prompt": "Isolated mock test prompt for image",
                    "model": "gemini-3.1-flash-image-landscape",
                    "rerun_nonce": 1
                },
                "class_type": "Flow2API_ImageNode"
            },
            "2": {
                "inputs": {
                    "filename_prefix": "flow2api_test_img",
                    "images": ["1", 0]
                },
                "class_type": "SaveImage"
            }
        }
        res_a = queue_prompt(COMFY_TEST_PORT, graph_img)
        prompt_id_a = res_a["prompt_id"]
        print(f"  -> Queued prompt_id: {prompt_id_a}")
        hist_a = wait_for_prompt_completion(COMFY_TEST_PORT, prompt_id_a)
        assert hist_a is not None, "Prompt A timed out!"
        assert hist_a.get("status", {}).get("status_str") == "success", f"Prompt A failed: {hist_a}"
        print(f"  -> Prompt A completed with SUCCESS! Output: {hist_a.get('outputs')}")
        history_records["test_image"] = hist_a

        # TEST B: Real Video Queue (Omni Text-to-Video)
        print("\n[Test B] Submitting Flow2API Video generation (omni) to real queue...")
        graph_vid = {
            "1": {
                "inputs": {
                    "prompt": "Isolated mock test prompt for video omni",
                    "model": "omni",
                    "rerun_nonce": 2
                },
                "class_type": "Flow2API_VideoNode"
            },
            "2": {
                "inputs": {
                    "filename_prefix": "flow2api_test_vid",
                    "format": "mp4",
                    "video": ["1", 0]
                },
                "class_type": "SaveVideo"
            }
        }
        res_b = queue_prompt(COMFY_TEST_PORT, graph_vid)
        prompt_id_b = res_b["prompt_id"]
        print(f"  -> Queued prompt_id: {prompt_id_b}")
        hist_b = wait_for_prompt_completion(COMFY_TEST_PORT, prompt_id_b)
        assert hist_b is not None, "Prompt B timed out!"
        assert hist_b.get("status", {}).get("status_str") == "success", f"Prompt B failed: {hist_b}"
        print(f"  -> Prompt B completed with SUCCESS! Output: {hist_b.get('outputs')}")
        history_records["test_video_omni"] = hist_b

        # TEST C: Real Reference Video Queue (LoadImage -> Flow2API_VideoNode omni_portrait)
        print("\n[Test C] Submitting Flow2API Video generation with reference image to real queue...")
        graph_vid_ref = {
            "1": {
                "inputs": {
                    "image": "test_flow2api_input.png",
                    "upload": "image"
                },
                "class_type": "LoadImage"
            },
            "2": {
                "inputs": {
                    "prompt": "Isolated mock test prompt for video with ref",
                    "model": "omni_portrait",
                    "rerun_nonce": 3,
                    "first_frame": ["1", 0]
                },
                "class_type": "Flow2API_VideoNode"
            }
        }
        res_c = queue_prompt(COMFY_TEST_PORT, graph_vid_ref)
        prompt_id_c = res_c["prompt_id"]
        print(f"  -> Queued prompt_id: {prompt_id_c}")
        hist_c = wait_for_prompt_completion(COMFY_TEST_PORT, prompt_id_c)
        assert hist_c is not None, "Prompt C timed out!"
        assert hist_c.get("status", {}).get("status_str") == "success", f"Prompt C failed: {hist_c}"
        print(f"  -> Prompt C completed with SUCCESS! Output: {hist_c.get('outputs')}")
        history_records["test_video_ref"] = hist_c

        # TEST D: Validation Failure Interception on T2V
        print("\n[Test D] Testing server-side validation error interception on T2V model with image input...")
        graph_invalid = {
            "1": {
                "inputs": {
                    "image": "test_flow2api_input.png",
                    "upload": "image"
                },
                "class_type": "LoadImage"
            },
            "2": {
                "inputs": {
                    "prompt": "Should fail",
                    "model": "veo_3_1_t2v_fast_landscape",
                    "rerun_nonce": 4,
                    "first_frame": ["1", 0]
                },
                "class_type": "Flow2API_VideoNode"
            }
        }
        res_d = queue_prompt(COMFY_TEST_PORT, graph_invalid)
        prompt_id_d = res_d["prompt_id"]
        print(f"  -> Queued prompt_id: {prompt_id_d}")
        hist_d = wait_for_prompt_completion(COMFY_TEST_PORT, prompt_id_d)
        assert hist_d is not None, "Prompt D timed out!"
        status_d = hist_d.get("status", {})
        assert status_d.get("status_str") == "error", f"Prompt D should have failed but got: {status_d}"
        print("  -> Prompt D correctly intercepted with error! Error status details:", status_d.get("messages", []))
        history_records["test_invalid_intercept"] = hist_d

        # Save history evidence
        evidence_path = "work/flow2api-integration/real_comfy_test_history.json"
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump(history_records, f, indent=2, ensure_ascii=False)
        print(f"\n[Evidence] Saved full execution history to {evidence_path}")

        print("\n🎉 ALL REAL COMFY QUEUE INTEGRATION TESTS COMPLETED SUCCESSFULLY!")

    finally:
        print("\n[4] Stopping test ComfyUI process...")
        comfy_proc.terminate()
        try:
            comfy_proc.wait(timeout=5)
        except Exception:
            comfy_proc.kill()
        print("  -> Test ComfyUI process terminated.")

        print("[5] Stopping mock Flow2API server...")
        mock_server.shutdown()
        print("  -> Mock server stopped.")

if __name__ == "__main__":
    main()
