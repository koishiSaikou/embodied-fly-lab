# AI 二次开发交接 / AI developer handoff

本文件是给接手本项目的 AI 和开发者的实现说明。先理解当前计算链路和限制，再修改。不要根据页面外观猜测模型已经具有完整感觉、学习或生物行为能力。

## 1. Start here


当前 Demo 发布范围（2026-10-16）：新增代码已由维护者选为 GPL-3.0-or-later；第三方与数据保留自己的条件。许可入口为 /licenses，静态页 web/许可与来源.html；来源清单 web/morphology_sources.json 和 prepare_morphology.py 使用同一固定来源与署名信息。不要再次用旧的未定位许可的原注释文档生成公开说明。当前模型/积分/学习机制未在本轮改变。

版本身份来自根目录 版本信息.json，通过 project_info.py 按进程冻结后用于页面状态、启动日志、配置和新报告 project 字段。旧记录不补写版本。当前为 0.1.0-alpha.1 Pre-release；源码 source_commit 保持 null，运行包记录实际构建提交。原样保存依赖锁和模型顺序。

维护者明确将长时内存/磁盘保护暂缓，并要求此次 Demo 不新增/运行测试；本轮仅做来源/文本/文件静态核对，不执行仿真、安装、GPU 或 CI，也不能把此前验证当作最新修改的测试保证。维护者随后已授权公开源码、创建 Release 并更新 Windows 交付包；这些步骤已完成。当前请求只补充介绍和文档，不自动重建 ZIP 或改动已发布标签。

For this independent GitHub source copy, first read `docs/源码安装说明.md`. Root `Setup.cmd` invokes `tools/setup_source.py`, using existing base Python 3.13/3.12 and hashed Windows locks without modifying those base installations. `delivery/bootstrap.py` installs the versioned Windows kit from bundled runtimes/wheels plus the two documented external wheels; it is separate from the source installer. Source diagnostics use `tools/check_source.py` and `delivery/doctor.py --source`. The user subsequently clarified that runtime/functionality fixes must also be synchronized to the active sibling FlyWire project, with source backups and verification in its existing environment. Source installation/locks and repository/release organization stay here; never copy environments, caches, brain data/weights or previous experiments, interrupt existing runs, or rebuild delivery ZIPs. See docs/功能同步记录.json for authorized file/backup checksums; tools/verify_repository.py preserves the immutable initial-copy baseline and verifies authorized synchronization separately.

Startup now uses `Start-*.cmd` → `Start.cmd` → `launcher.py`; `delivery/launch.py` is a compatibility adapter. `startup_support.py` bounds default port scanning, rejects explicit occupied ports, opens browsers only when each app's server is ready, and defers Ctrl+C during habitat final output. Headless rendering and browser opening are independent; `--no-browser` suppresses it, same-service restarts do not reopen. The launcher waits for the interrupted child instead of killing it. Habitat brain workers use a separate Windows process group and are closed by their owning body process; cancellation during the constructor also closes the worker. Keep every Start CMD in CRLF. See docs/启动与停止说明.md; tests/test_startup.py and tests/startup_live_check.py cover the contract.

Habitat now imports `BrainClient` from `brain_client.py`: separate configurable 300 s startup / 30 s pending-step wall deadlines include pipe writes, with owned reader/writer threads for Windows pipes. No watchdog advances or times out a paused idle brain. Preserve the successful JSON command/reply values, ordering and numerical model. Constructor failure closes its worker even before assignment. Invalid/EOF/non-finite replies raise BrainResponseError with last successful brain time. `experiment_recovery.py` isolates cleanup actions and atomically replaces JSON via .partial; failure must remain visible in the console, report and live lifecycle state. On error retain --keep-open UI, disable controls while saving, then allow only explicit restart into a new recording directory after successful brain cleanup. Never automatically reset, claim rollback, or fake missing neural events. A cleanup failure must not skip brain/log/report attempts. Unwritable report falls back independently to error.json; physical full disk can defeat all writes. See docs/异常处理与恢复说明.md and tests/test_recovery.py / tests/recovery_live_check.py. Old play_fly coupling still uses its old transport; do not claim these guarantees there.

Read `AI协作约定.md`, `docs/项目详情.md`, and this file. For a packaged copy, the entrypoint is `delivery/launch.py`; the actual simulation entrypoint is `habitat.py`. All paths in new code must resolve from `Path(__file__)` or the current project root, never the sender's absolute path.

Runtime profiles:

- Body/web/data: `.venv/Scripts/python.exe`, CPython 3.13 x64, FlyGym 2.1.0, MuJoCo 3.9.0.
- Brain: `.brain-venv/Scripts/python.exe`, CPython 3.12 x64, torch 2.11.0+cu128. Import `pyarrow` before `torch` on Windows.
- Optional JS checks: Node.js 24 tested. No Node/npm server or build step is required to use the app.
- The Windows kit `Setup.cmd` uses bundled `_runtime` / uv. This source checkout's `Setup.cmd` instead reuses existing base interpreters and `_tools/uv.exe` (or an explicitly chosen matching uv). Do not activate or modify a system environment to fix this project.
- Habitat defaults to continuous mode (`--seconds 0`, `target_steps=None`). Finite experiments use `.02 <= seconds <= 3600`; other launch profiles still require positive durations. Preserve the 200-tick chunks, explicit manual stop/save, pause/resume, and same-service restart into a new recording directory. Ctrl+C records `interrupted` and closes the service after saving. Do not encode infinity in JSON or silently reset neural state every 30 seconds.
- Windows MuJoCo mesh loading failed under a Chinese project path despite the files existing. This release requires an ASCII root path (spaces are supported); bootstrap checks it before installing. Do not remove that guard without an actual Unicode-path rendering regression test.

## 2. Current implementation map

| File / directory | Responsibility |
| --- | --- |
| `habitat.py` | Main body process; CLI, environment config, command queue, brain subprocess, 20 ms loop, logging, web monitor lifecycle |
| `habitat_world.py` | Arena geometry, `HabitatConfig`, actual sensor sampling, engineering `ForagingPolicy` |
| `arena_appearance.py` | Shared primitive prop specifications, fixed part names and visual bounds; no extra collisions |
| `behavior_model.py`, `web/behavior_controls.js` | Validated engineering behavior settings, needs, odor-value learning, arbitration and UI |
| `grooming_motor.py` | Scratch-data right-foreleg IK, standing transition and real position-actuator grooming |
| `arena_elements.py` | Validated editable floor cue layouts, queued projection reservations, scalar sensory fields and kind profiles |
| `web/arena_tools.js` | Shared map transforms/drawing, element editor, proxy-sense toggles/readouts |
| `visual_navigation.py` | Image-only retinal contrast/occupancy features and risk/turn contribution |
| `habitat_visuals.py` | Export solved MuJoCo geometry/pose; actual retina image and sample values |
| `brain_worker.py` | Persistent CUDA brain, state stepping, P9/taste stimulation, count readout, event capture and annotation labels |
| `brain_client.py`, `experiment_recovery.py` | Habitat-only deadline-bound subprocess transport, ownership, independent cleanup and failure records |
| `brain_controls.py` | Immutable validated runtime engineering controls, input encoding and clamped motor readout; defaults preserve the old bridge |
| `transmitter_modulation.py` | Model-order predicted presynaptic NT grouping, reversible temporary CSR values, baseline sign/cache preservation |
| `web/brain_controls.js` | Live parameter form; applied values come from server snapshots, drafts survive telemetry updates |
| `brain-model/code/run_pytorch.py` | Upstream LIF / synapse / Poisson / sparse weight implementation |
| `brain-model/code/benchmark.py` | Upstream data paths and fixed P9/sugar neuron sets; not the full benchmark installation |
| `play_fly.py` | Native body viewer, manual commands, body/brain coupling and reusable controller imports |
| `activity_monitor.py` | Thread-safe state/history/event buffer; legacy read-only HTTP monitor |
| `habitat_monitor.py` | FastAPI loopback server, static assets, state/scene/retina endpoints, command validation and WebSocket |
| `web/workbench.js`, `web/workbench.css` | Desktop shell, categorized inspector, persistent movable/resizable/collapsible windows; UI-only state |
| `web/lab.js`, `web/lab.css`, `web/index.html` | Browser Three.js body/brain, controls, map, pose/retina/status display |
| `web/neural_activity_core.js` | Pure event filtering, classification, window aggregation and rate calculation |
| `web/neural_activity.js`, `.css` | Shared grouped activity dashboard on the habitat and `/brain` pages |
| `activity_monitor.html` | Standalone grouped activity page shell |
| `web/annotations.js`, `.css` | Annotation query, pagination, per-neuron source detail |
| `neuron_annotations.py` | Read-only exact model-index annotation store, checksum validation |
| `prepare_annotations.py` | Reproducible pinned TSV → model-order Parquet + coverage report |
| `prepare_activity_catalog.py` | Export all model-index classes/types/IDs and population counts for browser aggregation |
| `prepare_morphology.py` | Cached raw skeleton/template → downsampled browser geometry; optional `--ids` or `--snapshot` |
| `calibrate_habitat.py` | Local controller calibration utility; examine CLI before use |
| `delivery/` | Installation, diagnostics and launch helpers for the Windows kit |
| `build_windows_package.py` | Release staging, local runtimes/wheels, hashed locks, manifest and ZIP |

FlyGym source is included in `flygym/`, but the installer normally installs the bundled wheel. Editing root project Python/JS files applies on the next process/page reload. Editing `flygym/src` does **not** automatically change an installed wheel; see section 9.

## 3. Do not confuse these model layers

The full v783 node/edge table is loaded into a simplified uniform LIF network. There are 138,639 nodes and 15,091,983 weighted neuron-pair rows, representing 54,492,922 synaptic contacts when source counts are summed. Do not call the 15 million rows separate anatomical synapses without specifying the counting unit.

Visual/odor navigation is currently engineering policy. It decides P9 stimulation and some movement signs. Measured P9 firing rates set smoothed CPG amplitudes. Taste drives the upstream 21 sugar GRNs and an explicit dwell rule. A CPG/hybrid controller approximates VNC motor control. The brain has no plasticity or learned policy. Type/NT annotations are metadata, not newly implemented cell-type physiology.

Do not advertise a running sensory neural pathway merely because its neurons have labels. Do not color static brain ROIs with invented activity. Do not silently teleport/reset the body to make behavior look successful.

## 4. Brain protocol and critical invariants

`BrainClient` spawns `.brain-venv/Scripts/python.exe brain_worker.py --habitat --seed N` with cwd at the project root. Protocol is UTF-8 JSON lines on stdin/stdout; all diagnostic logging belongs on stderr.

First response:

```json
{"ready":{"neurons":138639,"weighted_edges":15091983,"dt_ms":0.1}}
```

Actual metadata includes GPU/torch versions, annotations, string IDs and smoothing. Example request:

```json
{"rate_hz":[100,100],"duration_ms":20,"capture_activity":true,"sugar_rate_hz":0,"drive_smoothing_ms":100,"transmitter_gains":{"acetylcholine":1,"glutamate":1,"gaba":1,"dopamine":1,"serotonin":1,"octopamine":1}}
```

Response includes `brain_time_ms`, `chunk_ms`, `chunk_wall_seconds`, `p9_hz`, `drive`, `chunk_spikes`, `total_spikes`, `active_neurons`, `sugar_spikes` and, when captured, `activity`.

Optional `drive_smoothing_ms` changes only the P9-to-CPG exponential readout time constant (20–500 model ms). Omission preserves the worker's existing value (habitat default 100 ms; native mode default 40 ms). Each response reports the actual current time constant. It must not reset state or modify LIF integration/weights; a regression compares full neural states and events across different smoothing values.

Habitat accepts `{"type":"brain_settings","values":{"p9_gain_a":1,"p9_gain_b":1,"p9_bias_hz":0,"sugar_gain":1,"motor_gain":1,"smoothing_ms":100,"feedback_enabled":true}}`. Use the schema in `brain_controls.py`; validate the entire patch before queueing, reject unknown keys/nonfinite numbers/booleans used as numeric values, and apply on the body process thread (including while paused). Do not reset the persistent brain. Raw policy stimulation is retained as `policy_p9_rate_hz` / `policy_sugar_rate_hz`, while `p9_rate_hz` / `sugar_rate_hz` are actual applied inputs. Gain then bias is applied per P9 channel. Motor gain is applied to the worker readout, clamped to 1.2, with the existing direction sign. CLI `--disconnect-brain-output` takes precedence. Record before/after settings with model time in `events.jsonl`; config and final report retain initial/final settings. Same-service restart returns to defaults.

The same settings patch accepts six `nt_` fields (for example `nt_gaba:0.5`, `nt_acetylcholine:0`), finite in [0,2], default 1. `BrainControls.transmitter_gains()` removes the prefix for the worker protocol. Omitted/null worker `transmitter_gains` preserves the existing gains; a nonempty patch is validated atomically. Worker responses report the complete actual gains. GUI configured values can change while paused, but only the next `advance` applies them to the GPU; the final report separately retains `brain_effective_transmitter_gains` from the last computed chunk.

`W` is [postsynaptic, presynaptic]. The only intervention is `W_eff[post,pre] = W_base[post,pre] * gain[top_nt(pre)]`, i.e. **CSR columns**, not rows. Group via exact model-order annotation rows; six predicted groups plus unknown (fixed 1, no confidence threshold). Never infer a new sign from NT labels. Keep the original CSR tensor immutable. On an actual gain change, reuse CSR indices and create new values from the base, never compound gains on previously scaled values; do not rebuild per timestep or densify the full matrix. All-one restoration must use the exact base tensor. GPU tests verify source-only scaling, sign preservation, source-file hashes, unchanged default probe and changed full neural state under blockade. Existing alpha-synapse currents and delayed signals persist; restoring gains does not reset history/state or implement learning. Source topology and `_nnz()` stay unchanged even when values are zero. UI group counts describe the base connections. Metadata includes group populations and original positive/negative counts; mixed signs are preserved even within one predicted group.

Display-label regressions keep the NT grouping rows fixed while disabling per-event labels; their spike/state expectations remain unchanged at all-one gains. The model-order annotation import is required by this local worker.

- `activity.events`: `[model_time_ms, tensor_index]` pairs for actual spikes, capped at 20,000 per chunk.
- `activity.active_this_chunk`: number of neurons with at least one spike in this chunk, before display truncation.
- `activity.top_neurons`: up to 20 with exact string FlyWire IDs, counts/rates and annotations.
- `active_neurons` at top level is cumulative unique participants, not simultaneously active neurons.
- Rates use model duration, never wall duration or browser frame rate.
- State is persistent between chunks. `{"quit":true}` ends the worker.
- Stimulation must be two finite nonnegative rates, no more than 1,000 Hz. Duration is validated; event capture accepts chunks at most 100 ms.
- Preserve `@torch.no_grad()` here. On this Windows build `inference_mode()` broke sparse CSR transpose tensor version counters.
- Preserve `pyarrow` import before torch.

P9 order must remain:

| Channel | FlyWire ID | Official anatomical side |
| --- | --- | --- |
| A | `720575940627652358` | right |
| B | `720575940635872101` | left |

Upstream old left/right comments disagree with the annotation sides. Do not swap motor channels as a label cleanup.

## 5. Data and annotation invariants

- The CSV order in `2025_Completeness_783.csv` defines tensor indices; do not sort by ID during joins.
- All 18-digit IDs are strings in browser/JSON lookup code. Never pass them through JS Number or floating point.
- `neuron_annotations.parquet` is a left join in full model order. It preserves all nodes, including 14 unmatched IDs.
- Primary `cell_type` is missing for 1,492 nodes. There are 8,845 named types; the unknown type bucket is additional.
- 10 known `super_class` groups exist; the 14 unknown class nodes are separate from unknown specific type.
- `hemibrain_type` remains separate from primary `cell_type` and may disagree. Identity search matches both; activity type filtering uses primary types.
- `top_nt`/`top_nt_conf` are predictions. `known_nt`/`known_nt_source` retain literature annotation separately and can contain multiple entries.
- `side` means soma side for intrinsic neurons and nerve-entry side for sensory/ascending neurons.
- Anchor/soma coordinates in the source TSV use 4×4×40 nm voxels, not direct micrometres.
- `weight_coo.pkl`/`weight_csr.pkl` are derived sparse caches. Regenerate them when changing connection data; do not load an incompatible cache from a different graph/order. Pickle inputs should be trusted local artifacts.

After an intentional annotation change, rebuild both the annotation table and `web/activity_catalog.json`. After morphology changes, keep IDs, tensor indices, geometry and provenance in sync.

## 6. Web API and UI contracts

Server binds only `127.0.0.1` by default. Do not expose the control API externally without an explicit task to implement the corresponding access controls.

| API | Data |
| --- | --- |
| `GET /api/state` | `status`, `revision`, metadata, latest brain chunk, bounded history/events, habitat snapshot |
| `GET /api/scene` | Actual MuJoCo mesh/primitive data; no browser physics |
| `GET /api/brain-controls` | Read-only parameter labels, bounds and defaults for the live settings form |
| `GET /api/arena` | Element kind profiles, edit capacity and allowed sensory switches |
| `GET /api/retina.png` | Current image, with model-time header, or 204 before available |
| `POST /api/command` | Food, pause, input switches, stop/restart; validated origin, size, geometry and experiment status |
| `GET /api/annotations/summary` | Version, population/coverage and field counts |
| `GET /api/annotations/search` | Bounded query / class / NT / unknown filter with pagination |
| `GET /api/annotations/neuron/{id}` | Exact string-ID identity lookup |
| `GET /api/annotations/report`, `/unknown.csv` | Coverage/provenance and unknown-type list |
| `WS /ws` | Habitat display updates at at most 10 Hz wall time; excludes the full global event buffer |

The grouped activity component polls `/api/state` every 500 ms by default (browser-selectable 250/500/1000/2000 ms) to obtain the complete available one-second event buffer. Display windows are 100/200/500/1000 model ms, aligned to whole history chunks; all statistics use the actual aligned duration and selected population. Raster limits 50/100/300 affect drawing only. These settings and units persist in browser localStorage; they do not alter capture, event buffers or neural integration. Do not aggregate only the top-20 neuron list or only the 16 displayed skeletons.

### Desktop workbench layout

The local habitat homepage is a desktop-only single-viewport shell (1366×768, 1920×1080, 2560×1440 verified). Root scrolling is disabled; inspector/long-table scrolling stays local. User explicitly does not require portrait stacking. `workbench.css` loads after shared component CSS and scopes its changes under `.workbench`; do not apply it to standalone `/brain`. Left observers retain the fixed 76px neuron readout. Three.js resize handling must ignore zero-sized hidden containers.

`workbench.js` registers eight non-modal floating windows: map, raster, classes, types, activity, identity, rate, about. It handles pointer/keyboard move and resize, minimize/restore, close/Escape, viewport clamping and localStorage `flywire-workbench-v1`. Save CSS dimensions/coordinates even when hidden (hidden getBoundingClientRect is zero). Layout reset only changes browser layout; never post a model command or clear parameter drafts. The inspector uses accessible tabs and collapsible input/feedback/transmitter groups. Invalid numeric fields must expand their group before reportValidity. Remove the two unwanted retina headings; retain concise observer title and actual eye/time labels.

`window.Workbench.mountActivity(root)` runs immediately after activity markup creation, before its data awaits. It moves the three actual plot nodes into independent windows and wraps remaining tables in disclosure groups. Preserve unique existing IDs, global getElementById lookups, original listeners, shared filters, event statistics and ResizeObservers: moved plots are no longer descendants of neuralActivityPanel. On standalone `/brain`, Workbench is absent and the existing full-page component stays intact. `workbench-layout` events redraw map/rate canvases even while paused. Map stays square with the shared 600/35/530 transform; selecting a zone opens arena inspector, add/move opens its floating map. Brain control form retains all 13 inputs and backend schema validation. Initial arena telemetry fetch failure must not remove already mounted form nodes; websocket updates can recover them.

Homepage script version query strings and lab.js's arena_tools import must agree, or duplicate module instances can mount the editor twice. Follow `docs/工作台布局说明.md` for user-visible behavior; evidence is in `output/workbench`.

### Adaptive behavior and external odor learning

`HabitatConfig.behavior_controls` contains a full `BehaviorControls` snapshot (18 fields). ForagingPolicy delegates to AdaptiveBehavior only for enabled rich arenas; `_step_legacy` preserves the original behavior when disabled/classic. Settings are validated immutable replacements, then applied on the body thread. Needs and odor memory live per policy instance and reset with a new round. Never call this full-brain plasticity: learning only changes two bounded odor values and their input weights; the uniform LIF equations, IDs, recurrent weight tensor and NT controls stay unchanged.

`GET /api/behavior-controls` exposes fields/defaults. POST commands: `behavior_settings {values}`, `behavior_needs {values:{hunger?,thirst?,fatigue?,dust?}}` (finite numeric 0–1, not bool), `behavior_reward {reward:-1..1}` (finite numeric), `behavior_memory_reset`. Validate before queueing; increment `behavior_revision` when applied, including while paused. Publish `habitat.behavior` and revision; keep model/brain time unchanged for paused edits. Record changes and reward applied flag in events.jsonl, each block in behavior.jsonl, final state in report.json. Reward is ignored without sufficient eligibility or when learning/master is off. Settings reset does not clear memory; explicit memory reset does.

Actual sugar/fruit antenna samples produce ~1.5s eligibility traces. Feeding taste is positive reward and bitter is negative. Automatic learning rate scales by model dt, manual reinforcement is one explicit update; values clamp [-1,1]. Weight `clip(1+.8*value,.2,1.8)` affects odor input, never recurrent connections. Satiety can end feeding but actual foot taste still drives the sugar GRNs (subject to existing taste/gain settings). Intermittent contact retains dwell hysteresis. Needs are proxies, with model-time rates and actual movement-distance contributions; not neural fatigue, metabolic measurements or calibrated ingestion.

Uniform wind is generated from direction/strength and actual yaw; raw wind remains recorded when gated off, with effective strength/vector zero. No fluid/advection or wind force is implemented. New `cool` kind reuses existing slots/parts and adds a -10°C proxy field, ice primitives and snowflake glyph. Default seven-zone layout, collision semantics and map transforms remain intact. Visual startle uses actual VisualNavigator expansion/occupancy, never object IDs/coordinates; hazard/contact recovery and visual escape retain safety priority. Needs/master/individual cue toggles must be respected.

Grooming is an explicit right-front motor program, with the other five feet in support. `GroomingMotor` waits briefly at zero walking drive, plans on scratch MjData and sends blended position targets; never edit live qpos. Plan again for a new bout, abort immediately for safety/global feedback off, gain zero or disconnect. The program does not claim a restored grooming circuit or physical dirt removal. `sense.foreleg_head_distance_mm` is measured from actual body positions. Keep no teleport or fake visual animation. New behavior UI stays within the four-tab landscape inspector, preserves drafts, expands invalid groups and uses `novalidate` plus explicit validity reporting. Need inputs step .01 match rounded live values. Old backends show restart guidance without breaking the other panels. Match homepage/import cache query versions.

Checks: `.venv/Scripts/python.exe verify_behavior.py` covers deterministic learning/needs/cues plus actual eye-generated startle and physical grooming; verify_arena.py explicitly uses the legacy policy baseline for original arena checks. `output/behavior_live_check.py` runs an owned free-port full-GPU experiment, paused settings/reward, real grooming, logs and same-service restart; never terminate the user's existing simulation. See docs/行为与感官驱动说明.md and output/behavior for evidence.

### Editable arena and proxy senses

`habitat.py` defaults to `--arena foraging` (7 zones); `--arena classic` retains the two-zone baseline and disables new proxy feedback. `HabitatConfig()` used directly still defaults to classic. `elements` use stable IDs, kind, label, x/y in mm, radius [.8,4], strength [0,1], and server-owned slots. `ArenaEditor.prepare()` validates an entire projected layout under a lock before queueing; consecutive accepted additions reserve distinct IDs/slots even before the body thread applies them. Validation rejects wall/obstacle intersections and non-light surface overlaps. Keep primary `food` in slot 0 and edible; unknown kinds/fields/nonfinite values/boolean numerics are rejected. At most 24 zones.

`POST /api/command` accepts `{type:'arena_edit',action:'add'|'update'|'delete',id?,values:{kind?,x?,y?,radius?,strength?,label?}}`. The main body thread commits full prepared layouts, increments `arena_revision`, synchronizes primary food coordinates/radius for legacy readouts and updates existing MuJoCo geoms. Legacy `{type:'food',x,y}` routes through the same projected editor. `sensory_settings` accepts a nonempty boolean-only patch of `light_enabled`, `humidity_enabled`, `thermal_enabled`, `bitter_enabled`.

Preallocate 24 logical prop slots, each with `PARTS_PER_ELEMENT=8` non-colliding primitives (192 geoms). `arena_appearance.prop_parts()` is the shared millimetre-size/position/quaternion/RGBA/type specification: sugar cubes on a tray, fruit and leaf, puddle, warning sign, lamp, canopy and checker tile. Part 0 retains `food_zone` / `sham_zone` / `arena_element_N`; other parts use `arena_prop_SLOT_PART`. Hidden padding is transparent below the floor. All parts must remain in `HabitatVisuals.geom_ids` and `dynamic_indices`, including unused slots. Live edits update geom type (4 ellipsoid, 5 cylinder, 6 box), position, size, quaternion, RGBA and rbound, then call `mj_forward`; never recompile/reset body or brain. Actual qpos/time remain unchanged. `pose.appearance` carries renderer-list index, current type, size and RGBA for every part. The browser caches unit primitive geometries by type, swaps on live kind changes, and scales ellipsoid/box by xyz sizes; cylinder uses [radius,radius,half-height]. Pose rotation/position already contain actual world transforms; never apply local quaternion/offset or radius twice. Older poses without appearance.type fall back to the scene geom type. Do not change circular sensory sampling merely to match a visual silhouette. Map glyphs are distinct shapes; dashed circles preserve actual footprint selection. Keep the 600 logical px / 35 margin / 530 span map transforms. `/map` resolves to the current map.

`sample_fields()` uses real antenna XY and foot XYZ. `sense.channels_raw` and `sense.channels` separate the raw field/contact samples from gated proxy feedback. Odor channels are sugar, fruit and repellent; attraction uses sugar+fruit clipped to [0,1]. Proxy light/humidity/temperature and geometric bitter/water contacts modulate the engineering P9 policy, never an invented sensory-neuron mapping. Fruit sweetness .6 yields 120 Hz to the existing sugar GRNs; sugar yields 200 Hz, before user sugar gain. Danger bitter/high-temperature proxy triggers short reverse hysteresis; light/water/hazard gradient terms use sensor differences, not direct target bearing. New cues do not implement fluid flow, temperature dynamics, radiometry, wet materials or learning. Flags gate feedback, not the visibility of surface markers. Keep raw values for ablation interpretation.

Log layouts and revision in `arena_edit` events, raw/effective channels each computed block in `senses.jsonl`, initial layout in config and final layout in report (`arena_final`, `arena_revision`). A same-service new round restores the initial preset, not the edited layout, and preserves prior run files. `nearest_food_distance_mm` is null when all edible sources are disabled; do not fall back to primary distance in that case.

`activity_monitor.py` history keeps 250 chunks and up to 50,000 events, cropped to the last model second. Frontend checks its window event count against summed original `chunk_spikes`; if incomplete, it reports all grouped counts/active numbers/curves as lower bounds. Do not fill missed events with fabricated zeros.

Group mean Hz divides by **all selected model nodes of that group**, including silent ones. The raster displays at most 300 rows but aggregate statistics cover all captured events. Grouped curves use ten equal bins in the observed model window. Zero-event groups must remain visible.

UI events:

- `annotation-select`: `detail.row` contains the neuron identity; the habitat can append `detail.note` and focus existing geometry.
- `neural-filter-change`: `detail.matches(index)` and `population`; habitat filters only downloaded skeletons, without altering neural computation.

Layout rules from actual bug fixes:

- Keep `#neuronReadout` at a stable 76 px, with one count line and three fixed 18 px label slots. Long labels use ellipsis + title. Do not reintroduce a variable-height concatenated live paragraph.
- `#arena` fills its responsive panel and remains square. Draw and food-click mapping share a 600×600 logical space, margin 35, arena span 530. Never stretch only one axis.
- Map redraw uses ResizeObserver plus resize handling even when paused/finished. Keep backing pixels in sync with CSS size and DPR.
- Scoped `na-*` styles isolate grouped activity from main UI and identity tables. Check both wide (1920/2560) and narrow views.

## 7. Validation commands

Run checks appropriate to the change, without repeatedly rerunning unrelated GPU simulations.

```powershell
.\.venv\Scripts\python.exe -m py_compile habitat.py habitat_monitor.py brain_worker.py visual_navigation.py
.\.venv\Scripts\python.exe verify_annotations.py
.\.venv\Scripts\python.exe verify_brain_controls.py
.\.venv\Scripts\python.exe verify_arena.py
.\.venv\Scripts\python.exe verify_behavior.py
node verify_arena_map.mjs
.\.brain-venv\Scripts\python.exe verify_annotation_dynamics.py
.\.brain-venv\Scripts\python.exe verify_transmitter_modulation.py --gpu
node --check web\lab.js
node --check web\neural_activity.js
node verify_activity_groups.mjs http://127.0.0.1:8472
```

The final JS check reads the actual API URL; replace it with the currently printed port. It first tests synthetic event windows and then conservation on the real state. `verify_annotations.py` temporarily uses ports 8475/8476; choose unused ports if necessary. Original weight-cache bytes use a hash fast path; rebuilt pickles can differ due to process-dependent storage IDs. When rebuilt caches exist, it invokes `.brain-venv` to compare every COO index/signed value against the full source table and CSR against COO (`verify_weight_cache.py`). Missing caches are explicitly marked not generated; run the brain probe before checking numerical caches.

GPU regression compares identical seeded chunks with and without annotation lookup. Expected legacy short probe on the validated build: baseline 0, stimulated cumulative 104 spikes / 32 unique active neurons. This only tests that path and seed, not biological validity or all hardware.

Short body and full-loop smoke:

```powershell
.\.venv\Scripts\python.exe play_fly.py --headless --seconds 0.1 --video
.\.brain-venv\Scripts\python.exe brain_worker.py --probe
.\.venv\Scripts\python.exe habitat.py --headless --monitor --no-browser --seconds 0.2 --port 8480
```

Preserve generated reports. For controls, use a short dedicated run rather than overwriting an active user's experiment. Test food coordinate conversion with browser-only mocked command responses if no environment change is intended.

## 8. Suggested extension paths

- Real sensory neural routes: introduce an explicit sensory encoding/interface before replacing engineering P9 stimulation. Define which real cells receive what signal and validate against controlled stimuli.
- Full-brain learning: current AdaptiveBehavior already provides external two-odor value memory. Any future connectome plasticity needs its own explicit reward/plasticity module and evaluation; do not duplicate the current memory or call policy-level learning neural plasticity.
- Type-specific physiology: maintain a versioned parameter mapping, unknown defaults and data provenance. Annotation predictions alone do not establish receptor/sign/dynamics.
- More morphology: select bounded IDs and cache them; do not request all 138k skeletons by default. Keep visual scale/coordinate conventions and rendering limits clear.
- Performance: profile brain, physics, capture, serialization and rendering separately; preserve model timing and numerical checks when optimizing.

## 9. Editing FlyGym and rebuilding delivery

Normal installation is a wheel install to keep offline setup simple. For editing `flygym/src`, either rebuild that wheel or explicitly create an editable developer installation using local setuptools and offline wheels. Do not claim source edits have applied until `flygym.__file__` points to the intended code.

Rebuild wheel using the bundled tools after setup (provide a Python with setuptools or allow a local build dependency environment):

```powershell
.\_tools\uv.exe build flygym --wheel --python .brain-venv\Scripts\python.exe --no-build-isolation --out-dir .package-cache\wheels\body
```

For release rebuilding, collect pinned wheels under `.package-cache/wheels/body` and `brain` first; these build caches are not included in the release. The builder includes runtime files, source, public data, raw morphology/annotations, browser assets and new lock files. It excludes venvs, caches, experiments, .git, and the separately supplied torch wheel.

```powershell
.\.venv\Scripts\python.exe build_windows_package.py --uv .\_tools\uv.exe --stage-only --output dist\FlyWire-Windows-custom
.\.venv\Scripts\python.exe build_windows_package.py --zip-stage dist\FlyWire-Windows-custom
```

For a received kit, rebuild source wheel caches from `vendor/wheels` by grouping filenames according to the two `requirements/*lock.txt` profiles, or provide `--wheel-cache` with prepared body/brain subfolders. Do not guess a new dependency version to get a build unstuck; preserve working locks unless the task explicitly involves upgrading.

After modifying source, the original release manifest will no longer match. Keep a development copy and rebuild a new release manifest/ZIP through the builder. Do not silently suppress checksum failures in the end-user installer.

## 10. Copyable prompt for another AI

> You are working on this local Windows fruit-fly simulation. Read AI协作约定.md, docs/项目详情.md and docs/AI二次开发文档.md first. Use the project's two Python environments and relative root paths. Inspect the actual code before proposing biological functionality. Preserve exact string neuron IDs and tensor order, the P9 A/B convention, honest model-time/event statistics, and the distinction between engineering policy and neural mechanism. Implement the requested change, run scoped checks, preserve user experiment records, and explain what changed and what remains unimplemented. Do not modify system Python/PATH or replace the connectome/dynamics unless explicitly requested.


## 0.1.0-alpha.1 运行包补充

维护者已授权本次 Pre-release 和运行包更新；未来更新仍需请求。运行包含独立 Python、连接表和普通依赖，PyTorch 与 imageio-ffmpeg wheel 由接收方按 docs/下载说明.md 另行补齐。delivery/外部依赖.json 记录录像 wheel 的固定 URL、大小和哈希；bootstrap 在创建环境前校验。构建在 GitHub 准备目录进行，只从已提交的干净源码生成，实际提交写入包内版本信息与清单；不改写源码提交自身的 source_commit。本轮未运行安装、仿真、GPU 或性能测试。
