# Mirror Duplicates Global — Blender Addon v2.4


---

## What It Does

Mirror Duplicates Global creates symmetrical duplicates of selected objects — including their full parent hierarchy and all animation curves — reflected across the global X, Y, or Z axis. The reflection plane is defined by the `Anim_mirror_center` empty object, which can be placed anywhere in the scene before running the operation.

Key design constraint: **scale is never applied to geometry**. Mirroring is done via Blender's Mirror modifier, so custom split normals (custom normals baked into meshes for smooth shading across hard edges) are fully preserved.

<a href="https://www.youtube.com/watch?v=2c-JAEL5wCg">
  <img src="https://github.com/user-attachments/assets/1d94df68-9509-45e8-b7d3-971d0f1ecacf" alt="Video Preview">
</a>


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

