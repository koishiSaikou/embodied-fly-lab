"""Fetch a small, version-matched anatomy subset and export browser geometry.

Uses the public precomputed-skeleton endpoint documented by fafbseg. This
does not download the full connectome morphology or require a CAVE token.
"""
import concurrent.futures
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
import trimesh
from neuron_annotations import annotations, morphology_label

ROOT=Path(__file__).resolve().parent
CACHE=ROOT/"downloads"/"morphology"
OUT=ROOT/"web"
BASE="https://flyem.mrc-lmb.cam.ac.uk/flyconnectome/flywire_skeletons_783"
ROI_COMMIT="d0da95123ee606e204ae2c702e7bc78538646fbd"
ROI_URL=f"https://raw.githubusercontent.com/navis-org/fafbseg-py/{ROI_COMMIT}/fafbseg/data/JFRC2NP.surf.fw.zip"
ROI_NOTICES={
    "roi_source_commit":ROI_COMMIT,
    "roi_original_reference":"https://github.com/VirtualFlyBrain/DrosAdultBRAINdomains/tree/0f79bb86d199d54785fe6ad21cbd42ea373d9013",
    "roi_template_image_reference":"https://doi.org/10.5281/zenodo.10567",
    "roi_original_license":"CC-BY-4.0 for underlying BrainName labels/shapes; retain upstream exceptions and attribution",
    "roi_distribution_license":"fafbseg software/distribution GPL-3.0; not a replacement for original data rights",
    "roi_license_notes":"https://raw.githubusercontent.com/VirtualFlyBrain/DrosAdultBRAINdomains/0f79bb86d199d54785fe6ad21cbd42ea373d9013/LICENSE-NOTES.md",
    "roi_distribution_notes":f"https://raw.githubusercontent.com/navis-org/fafbseg-py/{ROI_COMMIT}/fafbseg/data/README.md",
    "roi_distribution_data_source":"precomputed://gs://flywire_neuropil_meshes/neuropils/neuropil_mesh_v141_v6",
    "roi_attribution":["Arnim Jenett","Kazunori Shinomiya","Kei Ito","Virtual Fly Brain","Ito et al. 2014; 10.1016/j.neuron.2013.12.017","Jenett et al. 2012; 10.1016/j.celrep.2012.09.011","fafbseg contributors and FlyWire mesh distribution"],
    "mixed_file_notice":"morphology.json combines FlyWire-derived skeletons/annotations and template shapes; do not apply a single code license to the whole file. See 数据使用与署名.md.",
    "local_modifications":"Unit conversion, common translation, rounding and skeleton downsampling; static anatomy is not activity.",
    "license_review_date":"2026-10-16",
}
P9=["720575940627652358","720575940635872101"]


def fetch(name,url):
    target=CACHE/name
    if not target.exists():
        with urllib.request.urlopen(url,timeout=35) as response:
            target.write_bytes(response.read())
    return target


def segments_from_skeleton(data):
    nv,ne=np.frombuffer(data,dtype="<u4",count=2).tolist()
    if not 0<nv<5_000_000 or not 0<ne<5_000_000:
        raise ValueError("Unexpected precomputed skeleton header")
    vertices=np.frombuffer(data,dtype="<f4",count=nv*3,offset=8).reshape(-1,3)/1000
    edges=np.frombuffer(data,dtype="<u4",count=ne*2,offset=8+nv*12).reshape(-1,2)
    if edges.max()>=nv or not np.isfinite(vertices).all():
        raise ValueError("Invalid skeleton geometry")
    # Resample degree-two chains to ~1.2 um, retaining all branch/end points.
    adjacency=[[] for _ in range(nv)]
    for a,b in edges:
        adjacency[a].append(int(b));adjacency[b].append(int(a))
    visited=set();result=[]
    starts=[i for i,x in enumerate(adjacency) if len(x)!=2]
    for start in starts+list(range(nv)):
        for nxt in adjacency[start]:
            key=(min(start,nxt),max(start,nxt))
            if key in visited:continue
            previous,current,anchor=start,nxt,start
            visited.add(key)
            while True:
                endpoint=len(adjacency[current])!=2
                if np.linalg.norm(vertices[current]-vertices[anchor])>=1.2 or endpoint:
                    result.extend([vertices[anchor],vertices[current]])
                    anchor=current
                if endpoint:break
                candidates=[n for n in adjacency[current] if n!=previous]
                if not candidates:break
                nxt=candidates[0];key=(min(current,nxt),max(current,nxt))
                if key in visited:
                    if anchor!=current:result.extend([vertices[anchor],vertices[current]])
                    break
                visited.add(key);previous,current=current,nxt
    return np.array(result).reshape(-1,3),nv,ne


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,help='Optional recorded monitor_snapshot.json for selecting active neurons')
    parser.add_argument('--ids',nargs='+',help='Explicit FlyWire IDs; default uses the bundled morphology source manifest')
    args=parser.parse_args()
    annotation_store=annotations()
    CACHE.mkdir(parents=True,exist_ok=True)
    with (ROOT/"brain-model/data/2025_Completeness_783.csv").open() as stream:
        rows=list(csv.reader(stream))[1:]
    mapping={r[0]:i for i,r in enumerate(rows)}
    if args.ids:
        candidates=list(dict.fromkeys(P9+[str(i) for i in args.ids]))
    elif args.snapshot:
        snapshot=json.loads(args.snapshot.read_text(encoding='utf-8-sig'))
        sugar=snapshot['metadata']['sugar_grn_ids']
        candidates=list(dict.fromkeys(P9+sugar[:4]+[n['flywire_id'] for n in snapshot['latest']['activity']['top_neurons']]))[:16]
    else:
        manifest=json.loads((OUT/'morphology_sources.json').read_text(encoding='utf-8'))
        candidates=list(dict.fromkeys(P9+[n['id'] for n in manifest['neurons']]))
    if len(candidates)>64 or any(identifier not in mapping for identifier in candidates):
        raise ValueError('Choose at most 64 IDs, all present in the current v783 brain model')
    def download(identifier):
        try:
            path=fetch(identifier+".skel",BASE+"/"+identifier)
            return identifier,path,None
        except Exception as exc:
            return identifier,None,str(exc)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fetched=list(pool.map(download,candidates))
    archive=fetch("neuropils.zip",ROI_URL)
    regions=[]
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not name.endswith('.ply') or name.startswith('__'):continue
            mesh=trimesh.load(io.BytesIO(bundle.read(name)),file_type='ply',process=False)
            regions.append({"name":Path(name).stem,"vertices":np.asarray(mesh.vertices)/1000,"faces":np.asarray(mesh.faces)})
    all_vertices=np.concatenate([r["vertices"] for r in regions])
    center=(all_vertices.min(axis=0)+all_vertices.max(axis=0))/2
    neurons=[];sources=[];failures=[]
    for identifier,path,error in fetched:
        if error:
            failures.append({"id":identifier,"error":error});continue
        try:
            segments,nv,ne=segments_from_skeleton(path.read_bytes())
            annotation=annotation_store.get(identifier)
            label=morphology_label(annotation,identifier)
            neurons.append({"id":identifier,"index":mapping[identifier],"label":label,
                            "annotation":annotation,
                            "segments":np.round(segments-center,2).ravel().tolist(),"original_vertices":nv})
            sources.append({"id":identifier,"tensor_index":mapping[identifier],"url":BASE+"/"+identifier,
                            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"vertices":nv,"edges":ne})
        except Exception as exc:
            failures.append({"id":identifier,"error":str(exc)})
    assert all(identifier in {n['id'] for n in neurons} for identifier in P9)
    geometry={"dataset":"FlyWire FAFB v783","units":"um","center_fafb_um":center.tolist(),
              "annotations":annotation_store.summary,
              "regions":[{"name":r['name'],"vertices":np.round(r['vertices']-center,2).ravel().tolist(),"faces":r['faces'].ravel().tolist()} for r in regions],
              "neurons":neurons}
    (OUT/'morphology.json').write_text(json.dumps(geometry,separators=(',',':')),encoding='utf-8')
    provenance={"dataset":"FlyWire FAFB public v783","coordinate_space":"FAFB14.1; nm converted to um, common translation only",
                "annotation_source":annotation_store.summary,
                "skeleton_format":"Neuroglancer precomputed; radius attribute omitted from line display",
                "skeleton_license":"FlyWire public release CC BY-NC 4.0; retain upstream attribution",
                "citation":"Dorkenwald et al. 2024, Neuronal wiring diagram of an adult brain; Schlegel et al. 2024, Whole-brain annotation and multi-connectome cell typing of Drosophila",
                "access_method_reference":"https://fafbseg-py.readthedocs.io/en/latest/source/tutorials/flywire_neurons.html",
                "roi_source":ROI_URL,"roi_origin":"JFRC2 neuropil templates transformed into FAFB14.1 by fafbseg; anatomical reference, not measured activity",
                **ROI_NOTICES,
                "roi_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
                "neurons":sources,"failures":failures,
                "limits":"Only this small subset is rendered. No inferred activity is assigned to unannotated brain regions. Skeleton line geometry is downsampled for display."}
    (OUT/'morphology_sources.json').write_text(json.dumps(provenance,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({"regions":len(regions),"neurons":len(neurons),"failures":failures,
                      "bytes":(OUT/'morphology.json').stat().st_size,"region_names":[r['name'] for r in regions]},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
