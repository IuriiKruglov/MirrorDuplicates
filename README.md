# Mirror Duplicates Global — Blender Addon v1.0

![MirrorDemo2](https://github.com/user-attachments/assets/0382a008-7b91-4519-a911-5b8c4c27edce)


---

## What It Does

This addon duplicates selected objects and mirrors the copies across the **global X, Z or Y axis**. It handles everything a plain mirror would miss:

- **Full parent–child hierarchy** — relationships between duplicated objects are preserved and correctly re-wired to their mirrored counterparts. If a selected object's parent was not selected (e.g. a master root), the duplicate is re-parented back to that same unselected parent.
- **Custom normals** — geometry is mirrored using Blender's Mirror modifier, which preserves custom split normals internally. No manual face flipping is performed.
- **Animation curves** — all keyframes are rebuilt with correct mirrored values, preserving interpolation mode, easing, and bezier handle types and positions.
- **All rotation modes** — Euler (all orders), Quaternion and Axis-Angle are all supported.
- **All UV channels** — every UV channel on every mirrored object is correctly restored after the operation.

---

## Installation

1. In Blender go to **Edit → Preferences → Add-ons**.
2. Click **Install…** and select `mirror_duplicates.py`.
3. Enable the addon by ticking the checkbox next to **Object: Mirror Duplicates Global**.

---

## How to Use

1. Select the objects you want to mirror. Select the full hierarchy you want duplicated — do **not** select a root parent you want to keep shared between both sides.
2. Open the **N panel** in the 3D Viewport (press `N` if it is hidden).
3. Go to the **Mirror Dup** tab.
4. Click one of the three buttons:

| Button | Mirrors across | Negates |
|--------|---------------|---------|
| **Mirror X** | Global YZ plane | X coordinates |
| **Mirror Z** | Global XZ plane | Y coordinates (Blender modifier Y axis) |
| **Mirror Y** | Global XY plane | Z coordinates (Blender modifier Z axis) |

The addon duplicates the selected objects, mirrors their geometry, origins and animation, then leaves the new duplicates selected when finished.

---

## The Anim_mirror_center Object

When the operator runs it automatically creates a **Plain Axes empty** named `Anim_mirror_center` at the world origin `(0, 0, 0)`. This empty is used internally as the reference object for the Mirror modifier and is **automatically deleted** at the end of the operation — it will not appear in your scene after the addon finishes.

---

## How It Works — Technical Overview

The addon runs in 12 steps inside a single undo block.

### Step 1 — Create mirror center
`Anim_mirror_center` is created at `(0, 0, 0)`. Its transforms are always reset to the world origin so the mirror plane is consistently aligned to global axes.

### Step 2 — Sort by hierarchy depth
Selected objects are sorted so parents are always processed before their children.

### Step 3 — Bake source data
Before anything is duplicated or modified, the addon captures:
- All **FCurves** from each source object's action.
- **`matrix_local`** at every keyframe and at the current frame, by stepping through time with `frame_set()`. This must happen while the original hierarchy is intact so `matrix_local` includes the full parent-chain contribution.
- **`matrix_parent_inverse`** (mpi) — Blender's internal correction matrix stored on each object.

### Step 4 — Duplicate
Each object is duplicated one at a time with `bpy.ops.object.duplicate()`, building a `source → duplicate` map.

### Step 5 — Clear animation
The duplicated action is removed from all duplicates. Animation is rebuilt from scratch in Step 9.

### Step 6 — Mirror mesh geometry
For each mesh duplicate (still at its original world position):

1. A temporary UV map `anim_X` is created. **Smart UV Project** is used to project all faces into UV tile V 0–1. Smart UV Project is chosen over regular Unwrap because it works on any mesh regardless of whether UV seams exist — regular Unwrap can fail or produce incorrect results on seamless geometry.
2. A **Mirror modifier** is added with `mirror_object = Anim_mirror_center` and `offset_v = 5`. The modifier mirrors geometry using the empty's position as the mirror plane reference. The `offset_v = 5` shifts the mirrored copy's UVs to V+5, outside the 0–1 tile.
3. The modifier is applied — the mesh now contains both the original and the mirrored geometry.
4. In Edit mode, all faces whose UV V coordinate is inside 0–1 (the originals) are selected and deleted, leaving only the mirrored copy.
5. The temporary UV map `anim_X` is removed.

Using the Mirror modifier rather than manual vertex manipulation is essential because it correctly handles **custom split normals** internally. Manual face flipping via bmesh destroys this data.

### Step 7 — Mirror origins and fix negative scale
With all mesh duplicates selected:

- **Affect Only Origins** mode is enabled. The 3D cursor is placed at `(0, 0, 0)` and `transform.mirror` is called on the chosen axis. This moves each origin to its mirrored world position but introduces a −1 scale on that axis as a side effect.
- Still in Affect Only Origins mode, the pivot is switched to **Individual Origins** and `transform.resize` is called with `−1` on the same axis in **Local** space. Each object scales around its own origin, flipping the scale sign back to positive without moving any geometry.

### Step 8 — Set parent hierarchy and transforms
For each duplicate, using proven matrix math:
- Parent is set to the mirrored counterpart (if the original parent was also selected) or kept as the original parent.
- `matrix_parent_inverse` is set to `M @ src_mpi @ M` where `M` is the reflection matrix for the chosen axis.
- Location, rotation and scale are computed as:
  ```
  TRS_basis = inv(new_mpi) @ (M @ src_matrix_local @ M)
  ```
  and assigned directly to `location`, `rotation_*` and `scale` — **not** via `matrix_local` — to avoid a Blender depsgraph timing bug where the new mpi is not yet evaluated when `matrix_local` is decomposed, causing incorrect rotation values on child objects.

### Step 9 — Rebuild mirrored animation
For each keyframe of each source object:
- The baked `matrix_local` is mirrored: `M @ src_local @ M`.
- The mpi is removed: `TRS_basis = inv(new_mpi) @ mirrored_local`.
- Location and rotation are decomposed and written to new FCurves on the duplicate's action.
- Interpolation mode, easing, handle type and handle position are all copied from the source keyframe point.
- For Euler rotation, `rot_q.to_euler(mode, prev_euler)` is used at each frame to maintain continuity and prevent sudden gimbal flips.

### Step 10 — Restore UV positions
The Mirror modifier's `offset_v = 5` shifted all mirrored UVs to V+5. Now that the original faces have been deleted, every UV channel of every mesh duplicate is shifted back by −5. All UV channels are processed by iterating `mesh.uv_layers` so objects with any number of channels are handled correctly.

### Step 11 — Delete Anim_mirror_center
The empty is removed with `bpy.data.objects.remove(mc, do_unlink=True)`, which unlinks it from all collections and purges it from `bpy.data`. Nothing is left over in the scene.

### Step 12 — Restore frame and selection
The current frame is restored to where it was before the operation. All duplicates are selected and the last one becomes the active object.

---

## Mirror Matrix Reference

The reflection matrix `M` for each axis satisfies `dst_world = M @ src_world @ M`:

| Button | M | Effect |
|--------|---|--------|
| Mirror X | diag(−1, 1, 1, 1) | Negates X coordinates |
| Mirror Z | diag(1, −1, 1, 1) | Negates Y coordinates (modifier Y axis) |
| Mirror Y | diag(1, 1, −1, 1) | Negates Z coordinates (modifier Z axis) |

---


