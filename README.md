# Mirror Duplicates Global — Blender Addon v2.4

**Authors:** Claude (Anthropic AI) & Kruglov Iurii  
**Blender:** 3.0 and above  
**Location:** `View3D › Sidebar › Mirror Dup` tab

---

## What It Does

Mirror Duplicates Global creates symmetrical duplicates of selected objects — including their full parent hierarchy and all animation curves — reflected across the global X, Y, or Z axis. The reflection plane is defined by the `Anim_mirror_center` empty object, which can be placed anywhere in the scene before running the operation.

Key design constraint: **scale is never applied to geometry**. Mirroring is done via Blender's Mirror modifier, so custom split normals (custom normals baked into meshes for smooth shading across hard edges) are fully preserved.

---

## Features

### Mirroring
- Mirror across global **X**, **Y**, or **Z** axis with dedicated panel buttons
- Mirrors all selected objects in one operation, preserving the full parent–child hierarchy
- Mesh geometry is mirrored using the Mirror modifier with `Anim_mirror_center` as the reference object — custom normals are never broken
- Object origins are mirrored to match geometry positions
- All UV channels are correctly restored after the modifier operation

### Hierarchy
- Parent–child relationships are reconstructed on the duplicates
- Objects whose parent is outside the selection remain parented to the same original parent
- `matrix_parent_inverse` (mpi) is correctly recomputed for each object so that the parent–child relationship behaves identically to the source hierarchy

### Animation
- All animation curves (location, rotation, scale — all channels) are mirrored
- Supports Euler (all rotation orders), Quaternion, and Axis-Angle rotation modes
- Bezier handle types, interpolation modes, and easing settings are preserved
- Animated parents are correctly handled: the parent's world matrix is re-evaluated at every child keyframe frame, not just at the saved frame

### Mirror Center
- `Anim_mirror_center_X`, `Anim_mirror_center_Y`, `Anim_mirror_center_Z` empty objects are created automatically at the world origin on first use
- These objects **remain in the scene** after the operation so they can be inspected and reused
- **Manual placement**: use the "Create / Reset" buttons in the panel to create a center empty at the origin, then move it to any position before running the mirror. The reflection plane will pass through that position
- The panel shows the current coordinates of each center object at a glance

### Naming
- All duplicated objects receive a `_mirrored` suffix (e.g. `Wing_L` → `Wing_L_mirrored`)
- Mesh data blocks are also renamed with `_mirrored`

### Debug Mode
- Toggle **Debug Mode** in the panel before running to capture a full diagnostic snapshot
- Writes a `.json` log file next to the `.blend` file (or `/tmp` if the file has not been saved)
- The log contains:
  - Timestamp and axis used
  - `Anim_mirror_center` name and location
  - Ordered list of every operation performed (STEP 1 through 12)
  - Full data for every source object: name, type, parent, rotation mode, local transforms (location / rotation / scale / matrix_local / matrix_parent_inverse), world transforms, and all FCurve keyframes with interpolation and handle data
  - Full data for every mirrored object after the operation

---

## How to Install

1. In Blender open **Edit › Preferences › Add-ons**
2. Click **Install…** and select `mirror_duplicates_v2.py`
3. Enable the addon — the **Mirror Dup** tab appears in the `N` panel of the 3D Viewport

---

## How to Use

### Basic mirror (center at world origin)

1. Select the objects you want to mirror (select the whole hierarchy)
2. Open the `N` panel › **Mirror Dup** tab
3. Click **Mirror X**, **Mirror Y**, or **Mirror Z**
4. The mirrored duplicates appear on the opposite side of the chosen axis, named with `_mirrored`

### Mirror with a custom center

1. In the **Mirror Center (manual placement)** section, click **Create Anim_mirror_center_X** (or Y / Z)
2. The empty is created at the origin and selected — move it to the desired reflection plane position (e.g. `G X -1.5 Enter` to place it at X = −1.5)
3. Select your objects again and click the corresponding **Mirror** button
4. The reflection plane passes through the empty's position

### Debugging a result

1. Enable **Debug Mode** in the panel
2. Run the mirror operation
3. Open the `.json` log written next to your `.blend` — it contains the full before/after state of every object and all animation data

---

## How It Works Internally

### Operation sequence (12 steps)

| Step | What happens |
|------|-------------|
| 1 | `Anim_mirror_center_<axis>` is created or found; its location defines the reflection plane |
| 2 | Source objects are sorted parents-before-children |
| 3 | Source data is collected: FCurves, world matrices at every keyframe, `matrix_parent_inverse`, parent world matrices at every child keyframe |
| 4 | Each object is duplicated individually with `bpy.ops.object.duplicate` |
| 5 | All animation is cleared from the duplicates |
| 6 | Mesh geometry is mirrored via the Mirror modifier (preserves custom normals) |
| 7 | Object origins are mirrored via 3D cursor pivot; negative scale introduced by origin mirror is corrected with Individual Origins resize |
| 8 | Parent hierarchy is reconstructed; `matrix_parent_inverse` is recomputed; `dst.matrix_world` is set directly |
| 9 | Mirrored animation actions are built from baked world matrices |
| 10 | UV V coordinates are shifted back by −5 (undoing the Mirror modifier's `offset_v=5` used for face identification) |
| 11 | `Anim_mirror_center` is retained in the scene |
| 12 | Frame and selection are restored |

### Mirror mathematics

Two matrices drive all transform operations:

**`M_lin`** — pure linear reflection through the world origin (just flips one axis sign). An involution: `M_lin @ M_lin = I`. Used for mirroring `matrix_parent_inverse` and as the right-hand factor in world matrix operations.

**`M_at`** — affine reflection through `center_loc`: `T(c) @ M_lin @ T(−c)`. Equals `M_lin` when `center_loc = (0,0,0)`.

All transform and animation math is done in **world space**:

```
new_world = M_at @ src_world @ M_lin
```

Working in world space means the reflection always uses global axes regardless of parent rotation, and the center offset applies once regardless of hierarchy depth.

**`matrix_parent_inverse` (mpi) mirroring:**

```
parent IN selection:     new_mpi = M_lin @ src_mpi @ M_lin
parent NOT in selection: new_mpi = M_lin @ src_mpi @ P⁻¹ @ M_at @ P
```
where `P` is the source parent's world matrix captured before any changes.

**Animation local matrix per keyframe:**

```
mir_world_f   = M_at  @ src_world_f @ M_lin
dst_par_w_f   = M_at  @ src_par_world_f @ M_lin   (parent in sel)
              = src_par_world_f                     (parent outside sel)
mir_local_f   = inv(new_mpi) @ inv(dst_par_w_f) @ mir_world_f
```
`mir_local_f` is decomposed into loc/rot/scale and written as keyframe values.

### Geometry mirroring (why no negative scale)

Applying a negative scale to fix mirrored geometry breaks custom split normals stored in mesh loops. The addon instead uses Blender's **Mirror modifier** with `Anim_mirror_center` as the mirror reference object.

To isolate only the mirrored copy (the modifier produces both original and mirror):
1. A temporary UV map (`anim_X`) is created and Smart UV Project is run so all original faces land in UV V range 0–1
2. The modifier's `offset_v = 5` shifts mirrored-copy UVs to V+5 (outside 0–1)
3. After applying, all faces with UV V inside 0–1 are deleted (originals)
4. The temporary UV map is removed
5. All remaining UV channels have V shifted back by −5 to restore correct UV positions

---

## Development History

### v1.0 — Initial release

Core functionality established:

- Mirror selected objects with full hierarchy across global X, Y, or Z
- Geometry mirrored via Mirror modifier + UV offset trick (preserves custom normals)
- Object origins mirrored via 3D cursor / Affect Only Origins
- Negative scale corrected via Individual Origins + Local resize −1
- `matrix_parent_inverse` recomputed for each duplicate
- Animation baked from `matrix_local` at every source keyframe
- Bezier handle types, interpolation, and easing copied from source curves
- Euler gimbal-lock continuity preserved via `to_euler(mode, prev_euler)`
- `Anim_mirror_center` created at origin for each run and deleted afterwards
- Auto-keyframe disabled for the duration of the operation

**Known issues in v1.0:** Y and Z buttons were swapped in the panel (`Mirror Z` label triggered axis Y and vice versa).

---

### v2.0 — New features

Four features added on top of the working v1.0 base:

- **`_mirrored` suffix** on all new objects and their mesh data blocks
- **Axis-suffixed mirror center**: `Anim_mirror_center_X / _Y / _Z`. Each axis has its own persistent empty. The center is **not deleted** after the operation — it remains in the scene for inspection and reuse
- **Manual center placement**: "Create / Reset" buttons in the panel create the center empty at the origin; the user can then move it to any position before mirroring. The Mirror modifier and all transform math use this position as the reflection plane origin
- **Debug mode**: scene-level boolean toggle. When enabled, collects full object and animation data before and after the operation and writes a timestamped `.json` log next to the `.blend` file

---

### v2.1 — Center placement fixes (two bugs)

**Bug 1:** `mirror_origins_and_fix_scale` (STEP 7) hardcoded `scene.cursor.location = (0, 0, 0)` as the pivot for `transform.mirror`. The 3D cursor is the pivot, so origins were always reflected through the world origin regardless of the center object's position. Fixed by passing `center_loc` and using it as the cursor position.

**Bug 2:** The world matrix formula used `M_at @ mat @ M_at` (the conjugate). This is only self-consistent when `M_at` has no translation. When `center_loc ≠ (0,0,0)`, the double application of the translation inside `M_at` cancels the offset — objects were reflected through zero regardless of center position. Fixed by using the asymmetric formula `M_at @ mat @ M_lin` for world matrices, while `matrix_parent_inverse` continues to use `M_lin @ mpi @ M_lin`.

---

### v2.2 — Hierarchy accumulation fix (superseded by v2.3)

Attempted fix for objects whose parent is inside the selection being shifted by an extra `2×center_X` per hierarchy level. The approach of choosing `M_left` per object (M_at when parent outside selection, M_lin when parent inside) worked in isolation but was superseded by the v2.3 world-space rewrite which solves the problem more fundamentally.

---

### v2.3 — World-space rewrite (root cause fix)

Two bugs both traced to the same root cause: all transform and animation math was performed in **parent-local space** rather than world space.

**Bug — Y/Z axis swap:** The parent object (`Mig23_98_Hull_LOD0.005`) has a non-default rotation — its local Y axis points along global Z and local Z along global Y. Applying `M_lin` for "Mirror Y" in local space negated the local-Y component of the matrix, which corresponds to global Z. So "Mirror Y" was actually mirroring in the Z direction and vice versa.

**Bug — center offset ignored when parent is offset:** `M_at` is built from global `center_loc`, but `matrix_local` is expressed relative to the parent. When the parent is translated in world space, its local-space origin is at the parent's world position, not at the world origin. Applying a global-space `M_at` to a parent-local matrix produced results as if the center were at the parent's position rather than `center_loc`.

**Fix — work entirely in world space:**

- `bake_world_matrices` replaces `bake_local_matrices`
- `dst.matrix_world = M_at @ src.matrix_world @ M_lin` replaces all local decomposition
- `compute_new_mpi` correctly derives the new `matrix_parent_inverse` accounting for whether the parent is in the selection or not
- All animation math converts baked world matrices to mirrored local matrices via `new_mpi` and the per-frame parent world

---

### v2.4 — Animated parent world resampled per keyframe (current)

**Bug:** `build_mirrored_action` computed the mirrored local matrix as:

```
mir_local = inv(new_mpi) @ inv(dst_parent_world) @ mir_world
```

where `dst_parent_world` was a **single static snapshot** of the parent's world matrix captured at `saved_frame`. When the parent is itself animated (e.g. a strut whose rotation drives a child panel), its world matrix is different at each keyframe. Using the frame-0 snapshot for all frames gave correct results only at `saved_frame`; all other keyframes produced wrong Euler angles in the mirrored local matrix.

The result: the initial state (frame 0) looked correct; the end state (e.g. frame 30, fully deployed landing gear) had completely wrong orientations.

**Fix:**

In STEP 3, `src.parent.matrix_world` is now baked at every frame the child has a keyframe (in addition to the child's own world matrix). In `build_mirrored_action`, the parent world is looked up per frame from this baked dict:

```
dst_par_w_f = M_at @ src_par_world_f @ M_lin   (parent in selection)
dst_par_w_f = src_par_world_f                   (parent outside selection)
mir_local_f = inv(new_mpi) @ inv(dst_par_w_f) @ mir_world_f
```

This correctly handles animated parents at any depth in the hierarchy.
