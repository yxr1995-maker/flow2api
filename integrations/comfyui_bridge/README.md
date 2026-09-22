# ComfyUI Bridge (fork mirror)

Source of truth: the deployed custom_nodes/comfyui_flow2api_bridge directory.
This directory is a maintained mirror: node source plus workflow and test assets evolve here.

- custom_node: __init__.py, client.py, nodes.py (byte-identical to deployed source)
- workflows: API graphs ending _api.json for the /prompt queue, and
  canvas templates ending _ui.json (load via sidebar Browse workflows)
- tests: mock-based offline test plus isolated real-queue test.
  Tests resolve paths via COMFYUI_DIR / FLOW2API_DIR env vars,
  falling back to ~/ComfyUI and ~/Documents/flow2api.
  The mock video endpoint serves the real mp4 at tests/fixtures/valid_test.mp4
  (generate with ffmpeg) so the Flow2API_VideoNode to SaveVideo chain decodes.

Verified 2026-09-21: offline 5/5 plus isolated queue
(image, omni video with SaveVideo, reference video, invalid-intercept).
