import os
import sys
sys.path.insert(0, os.environ.get("FLOW2API_DIR", os.path.expanduser("~/Documents/flow2api")))
from src.services.generation_handler import MODEL_CONFIG

img_models = sorted([k for k, v in MODEL_CONFIG.items() if v.get('type') == 'image'])
vid_models = sorted([k for k, v in MODEL_CONFIG.items() if v.get('type') == 'video'])

print("IMG:", len(img_models), img_models)
print("VID:", len(vid_models), vid_models[:30])
print("omni:", MODEL_CONFIG.get('omni'))
print("omni_portrait:", MODEL_CONFIG.get('omni_portrait'))
print("veo_t2v:", MODEL_CONFIG.get('veo_3_1_t2v_fast_landscape'))
print("veo_i2v:", MODEL_CONFIG.get('veo_3_1_i2v_s_fast_fl'))
print("veo_r2v:", MODEL_CONFIG.get('veo_3_1_r2v_fast_landscape'))

