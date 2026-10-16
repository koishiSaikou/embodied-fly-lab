"""Export solved MuJoCo geometry and true FlyGym retina samples for the web UI."""
import io
import numpy as np
import mujoco as mj
from PIL import Image
from arena_appearance import element_geom_names


class HabitatVisuals:
    def __init__(self, sim):
        self.sim = sim
        self.retina_frames = 0
        model = sim.mj_model
        element_names=element_geom_names()
        self.geom_ids = [g for g in range(model.ngeom)
                         if model.geom_group[g] in (0, 2) and (model.geom_rgba[g,3]>0 or
                             mj.mj_id2name(model,mj.mjtObj.mjOBJ_GEOM,g) in element_names)]
        self.dynamic_indices=[i for i,g in enumerate(self.geom_ids)
                              if mj.mj_id2name(model,mj.mjtObj.mjOBJ_GEOM,g) in element_names]
        meshes = {}
        geoms = []
        for g in self.geom_ids:
            mesh_id = int(model.geom_dataid[g]) if model.geom_type[g]==mj.mjtGeom.mjGEOM_MESH else -1
            if mesh_id>=0 and str(mesh_id) not in meshes:
                va, vn = model.mesh_vertadr[mesh_id], model.mesh_vertnum[mesh_id]
                fa, fn = model.mesh_faceadr[mesh_id], model.mesh_facenum[mesh_id]
                meshes[str(mesh_id)] = {
                    "vertices":np.round(model.mesh_vert[va:va+vn],6).ravel().tolist(),
                    "faces":model.mesh_face[fa:fa+fn].ravel().tolist(),
                }
            mat_id = model.geom_matid[g]
            rgba = (model.mat_rgba[mat_id] if mat_id>=0 else model.geom_rgba[g]).copy()
            # MuJoCo's body materials use generated cube textures with white
            # material multipliers. Export their mean RGB rather than turning
            # the entire fly white in a renderer without cube-texture mapping.
            if mat_id>=0:
                tex_id=int(model.mat_texid[mat_id,1])
                if tex_id>=0:
                    offset=model.tex_adr[tex_id]
                    count=model.tex_width[tex_id]*model.tex_height[tex_id]*model.tex_nchannel[tex_id]
                    pixels=model.tex_data[offset:offset+count].reshape(-1,model.tex_nchannel[tex_id])
                    rgba[:3]*=pixels[:,:3].mean(axis=0)/255
            geoms.append({"id":g,"name":mj.mj_id2name(model,mj.mjtObj.mjOBJ_GEOM,g) or str(g),
                          "type":int(model.geom_type[g]),"mesh":mesh_id,
                          "size":model.geom_size[g].tolist(),"rgba":rgba.tolist(),
                          "body":int(model.geom_bodyid[g]),"dynamic":len(geoms) in self.dynamic_indices})
        self.scene = {"units":"mm","geoms":geoms,"meshes":meshes,
                      "pose_source":"MuJoCo solved geom_xpos/geom_xmat; no browser physics"}

    def pose(self, time_s):
        data = self.sim.mj_data
        return {"time_s":time_s,
                "position":np.round(data.geom_xpos[self.geom_ids],6).tolist(),
                "rotation":np.round(data.geom_xmat[self.geom_ids],6).tolist(),
                "appearance":[dict(index=i,type=int(self.sim.mj_model.geom_type[self.geom_ids[i]]),size=self.sim.mj_model.geom_size[self.geom_ids[i]].tolist(),
                                   rgba=self.sim.mj_model.geom_rgba[self.geom_ids[i]].tolist()) for i in self.dynamic_indices]}

    def retina(self, fly_name, time_s):
        self.retina_frames += 1
        samples = self.sim.get_ommatidia_readouts(fly_name)
        # Each ommatidium has only one nonzero spectral-type channel. Sum the
        # channels for a grayscale display, keeping the actual samples separate.
        gray = samples.sum(axis=-1)
        panels = [self.sim.retina.hex_pxls_to_human_readable(x,color_8bit=True) for x in gray]
        canvas = np.concatenate(panels,axis=1)
        buffer = io.BytesIO()
        Image.fromarray(canvas).save(buffer,format="PNG")
        return buffer.getvalue(), {"time_s":time_s,"frame_index":self.retina_frames,"ommatidia_per_eye":int(samples.shape[1]),
                                  "channels":"yellow/pale; grayscale display sums mutually exclusive channels",
                                  "samples":samples.tolist(),"drives_behavior":False,
                                  "source":"FlyGym eye cameras + fisheye + hexagonal retina sampling"}
