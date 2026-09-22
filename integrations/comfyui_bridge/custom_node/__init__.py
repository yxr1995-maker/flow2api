from .nodes import Flow2API_ImageNode, Flow2API_VideoNode

NODE_CLASS_MAPPINGS = {
    "Flow2API_ImageNode": Flow2API_ImageNode,
    "Flow2API_VideoNode": Flow2API_VideoNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Flow2API_ImageNode": "Flow2API Image Generation",
    "Flow2API_VideoNode": "Flow2API Video Generation",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

