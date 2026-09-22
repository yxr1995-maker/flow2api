import os
import sys
import json
import time
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import torch

# Ensure paths
sys.path.insert(0, os.environ.get("COMFYUI_DIR", os.path.expanduser("~/ComfyUI")))
os.environ["FLOW2API_BASE_URL"] = "http://127.0.0.1:18088"
os.environ["FLOW2API_KEY"] = "mock-secret-key-123"

# Create a tiny 1x1 test image png/mp4 dummy
TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
TINY_MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42\x00\x00\x00\x08free\x00\x00\x00\x08mdat"

class MockFlowServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            model = body.get("model")

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
                # Return markdown image
                content = f"![Generated Image](data:image/png;base64,{TINY_PNG_B64})"
            else:
                # Return video HTML tag with link to fake mp4 endpoint
                content = f"<video src='http://127.0.0.1:18088/fake.mp4' controls></video>"

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

def run_server(server):
    server.serve_forever()

def main():
    print("[TEST] Starting isolated Mock Flow2API server on 127.0.0.1:18088...")
    httpd = HTTPServer(("127.0.0.1", 18088), MockFlowServer)
    t = threading.Thread(target=run_server, args=(httpd,), daemon=True)
    t.start()

    from custom_nodes.comfyui_flow2api_bridge import Flow2API_ImageNode, Flow2API_VideoNode

    print("[TEST 1] Testing Flow2API_ImageNode with prompt and reference image...")
    img_node = Flow2API_ImageNode()
    dummy_tensor = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
    img_res = img_node.generate(
        prompt="A cute cat on a table",
        model="gemini-3.1-flash-image-landscape",
        reference_image=dummy_tensor
    )
    assert "ui" in img_res, "Image result missing ui dict"
    assert "result" in img_res, "Image result missing result tuple"
    img_out_tensor, img_url = img_res["result"]
    assert img_out_tensor.shape[-1] == 3, f"Unexpected tensor shape {img_out_tensor.shape}"
    print(f"  -> Flow2API_ImageNode Success! Output tensor shape: {img_out_tensor.shape}, url prefix: {img_url[:30]}...")

    print("[TEST 2] Testing Flow2API_VideoNode with pure text (omni)...")
    vid_node = Flow2API_VideoNode()
    vid_res = vid_node.generate(
        prompt="Drone flythrough of mountain",
        model="omni"
    )
    assert "ui" in vid_res, "Video result missing ui dict"
    assert "images" in vid_res["ui"], "Video result missing preview UI field"
    print(f"  -> Flow2API_VideoNode (omni) Success! UI info: {vid_res['ui']['images']}")

    print("[TEST 3] Testing Flow2API_VideoNode with reference frames...")
    vid_res_ref = vid_node.generate(
        prompt="Drone flythrough of mountain with ref",
        model="omni_portrait",
        first_frame=dummy_tensor,
        last_frame=dummy_tensor
    )
    print(f"  -> Flow2API_VideoNode (omni_portrait with frames) Success! Path: {vid_res_ref['ui']['images']}")

    print("[TEST 4] Testing validation error on T2V model with images...")
    try:
        vid_node.generate(
            prompt="Drone flythrough",
            model="veo_3_1_t2v_fast_landscape",
            first_frame=dummy_tensor
        )
        assert False, "Should have failed on T2V model with first_frame"
    except ValueError as e:
        print(f"  -> Correctly caught expected error: {e}")

    print("[TEST 5] Testing validation error on Omni exceeding 3 reference images...")
    four_images = torch.zeros((4, 64, 64, 3), dtype=torch.float32)
    try:
        vid_node.generate(
            prompt="Drone flythrough",
            model="omni",
            first_frame=dummy_tensor,
            last_frame=dummy_tensor,
            ref_images=four_images
        )
        assert False, "Should have failed with >3 reference images"
    except ValueError as e:
        print(f"  -> Correctly caught expected error: {e}")

    httpd.shutdown()
    print("\n🎉 ALL OFFLINE TESTS PASSED WITH 100% SUCCESS! (No real Google Flow tokens used)")

if __name__ == "__main__":
    main()

