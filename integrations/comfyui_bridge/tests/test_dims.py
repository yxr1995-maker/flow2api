import os
import sys
sys.path.insert(0, os.environ.get("COMFYUI_DIR", os.path.expanduser("~/ComfyUI")))
from comfy_extras.nodes_video import SaveVideo
from comfy_api.latest import InputImpl
import os

dummy_mp4 = os.path.join(os.path.dirname(__file__), 'fixtures', 'test_dummy.mp4')
with open(dummy_mp4, 'wb') as f:
    f.write(b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00isommp42\x00\x00\x00\x08free\x00\x00\x00\x08mdat')

video_in = InputImpl.VideoFromFile(dummy_mp4)
print('video_in:', video_in)
try:
    dims = video_in.get_dimensions()
    print('dims:', dims)
except Exception as e:
    print('get_dimensions error:', e)
