"""A declared image-based engineering reflex, not a biological optic-lobe model.

Inputs are only sampled retinal intensities and their fixed 2-D layout. No
obstacle coordinates, segmentation IDs, depth maps or target positions enter.
"""
import numpy as np


class VisualNavigator:
    def __init__(self, retina):
        ids=retina.ommatidia_id_map
        count=retina.num_ommatidia_per_eye
        y,x=np.indices(ids.shape)
        valid=ids>0
        sizes=np.bincount(ids[valid]-1,minlength=count)
        self.x=np.bincount(ids[valid]-1,weights=x[valid],minlength=count)/sizes/ids.shape[1]
        self.y=np.bincount(ids[valid]-1,weights=y[valid],minlength=count)/sizes/ids.shape[0]
        # The eyes look outward. Their forward-facing portions lie toward the
        # inner edges of the displayed eye pair. Keep the upper horizon band
        # to reduce reactions to floor texture, shadows and the fly's legs.
        band=(self.y>.28)&(self.y<.50)
        self.weights=np.array([np.clip((self.x-.35)/.45,0,1)*band,
                               np.clip((.65-self.x)/.45,0,1)*band])
        self.weights/=self.weights.sum(axis=1,keepdims=True)
        self.previous=None
        self.time=None
        self.risk=np.zeros(2)

    def update(self, samples, time_s):
        values=np.asarray(samples,dtype=float)
        if values.shape!=(2,len(self.x),2) or not np.isfinite(values).all():
            raise ValueError("Expected finite left/right retinal samples")
        gray=values.sum(axis=-1)
        reference=max(.55,float(np.percentile(gray[:,self.y<.45],95)))
        contrast=np.clip((reference-gray)/reference,0,1)
        occupancy=(np.clip((contrast-.12)/.50,0,1)*self.weights).sum(axis=1)
        dt=0 if self.time is None else time_s-self.time
        expansion=np.zeros(2) if self.previous is None or dt<=0 else np.maximum(0,(occupancy-self.previous)/dt)
        target=np.clip((occupancy-.18)/.45+.04*np.minimum(expansion,2),0,1)
        alpha=1 if self.time is None or dt<=0 else 1-np.exp(-dt/.06)
        self.risk+=alpha*(target-self.risk)
        self.previous=occupancy.copy();self.time=time_s
        return {"time_s":time_s,"risk":self.risk.tolist(),"dark_occupancy":occupancy.tolist(),
                "expansion":expansion.tolist(),"reference_luminance":reference,
                "source":"retinal contrast/positive occupancy change; no geometry or object labels",
                "model":"engineering retinal-contrast reflex; not Flyvis"}
