import os
import sys
sys.path.insert(0, os.environ.get("COMFYUI_DIR", os.path.expanduser("~/ComfyUI")))
from comfy_extras.nodes_video import SaveVideo
from comfy_api.latest import InputImpl
import os

fixture_mp4 = os.path.join(os.path.dirname(__file__), 'fixtures', 'valid_test.mp4')
video_in = InputImpl.VideoFromFile(fixture_mp4)
print('video_in:', video_in)
dims = video_in.get_dimensions()
print('dims:', dims)
res = SaveVideo.execute(video_in, 'test_save_video', 'auto')
print('SaveVideo executed:', res)
