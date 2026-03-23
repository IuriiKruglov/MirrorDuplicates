# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║        MIRROR DUPLICATES GLOBAL — Blender Addon v2.4                       ║
# ║                                                                              ║
# ║  Mirrors selected objects (with full hierarchy and animation) across the    ║
# ║  global X, Y or Z axis, preserving custom normals, parent relationships,    ║
# ║  animation curves with their original interpolation and handle types.       ║
# ║                                                                              ║
# ║  v2.0 additions:                                                             ║
# ║   • New objects get "_mirrored" suffix                                       ║
# ║   • Anim_mirror_center gets axis suffix (_X / _Y / _Z), stays in scene      ║
# ║   • Anim_mirror_center location can be set manually before running          ║
# ║   • Debug mode: collects full object/anim data and writes a log file        ║
# ║                                                                              ║
# ║  v2.2 fix:                                                                   ║
# ║   • Hierarchy-dependent origin drift with manual Anim_mirror_center.        ║
# ║     (superseded by v2.3 full rewrite)                                       ║
# ║                                                                              ║
# ║  v2.3 fix — root cause: all math was done in local/parent space             ║
# ║   • Mirror Y/Z axis swap when parent has non-default rotation: fixed.       ║
# ║   • Mirror center ignored parent world offset: fixed.                       ║
# ║   • New approach: bake matrix_world, set dst.matrix_world = M_at@W@M_lin.  ║
# ║                                                                              ║
# ║  v2.4 fix — animated parent world not resampled per keyframe                ║
# ║   • build_mirrored_action used a single static dst_parent_world snapshot    ║
# ║     (captured at saved_frame). When the parent is itself animated, its      ║
# ║     world matrix differs at each keyframe, making mir_local wrong for any   ║
# ║     frame other than saved_frame. The initial state looked correct because  ║
# ║     saved_frame was frame 0; end-state keyframes were wrong.                ║
# ║   • Fix: bake src.parent.matrix_world at every child keyframe in STEP 3.   ║
# ║     build_mirrored_action now computes dst_parent_world per frame:          ║
# ║       parent in sel:     M_at @ src_par_world_f @ M_lin                     ║
# ║       parent outside:    src_par_world_f  (unchanged)                       ║
# ║                                                                              ║
# ║  Authors: Claude (Anthropic AI) & Kruglov Iurii                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

bl_info = {
    "name": "Mirror Duplicates Global",
    "author": "Claude (Anthropic AI) & Kruglov Iurii",
    "version": (2, 4, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Mirror Dup",
    "description": "Mirror selected objects (with hierarchy & animation) across global X, Y or Z axis",
    "category": "Object",
}

import bpy
import bmesh
import os
import json
import datetime
from mathutils import Matrix, Vector


# ══════════════════════════════════════════════════════════════════════════════
#  MIRROR MATRIX HELPERS
#
#  Two matrices are needed for an offset mirror (center ≠ origin):
#
#  M_lin = get_mirror_matrix(axis)
#    Pure linear reflection through the WORLD ORIGIN — just flips one axis sign.
#    This is an involution: M_lin @ M_lin = I.
#    Used for: mirroring mpi, mirroring local_mat (right-multiply side).
#
#  M_at  = get_mirror_matrix_at(axis, center_loc)
#    Affine reflection through center_loc.  T(c) @ M_lin @ T(-c).
#    When center_loc == (0,0,0), M_at == M_lin.
#    Used for: left-multiplying world matrices.
#
#  WHY TWO MATRICES?
#    The correct formula to mirror a 4×4 local matrix depends on whether
#    the object's parent is also being mirrored:
#
#    Parent NOT in selection (parent world unchanged):
#        pred_local = M_at @ src_local @ M_lin
#      The full affine offset must be carried by this object's local matrix
#      because nothing above it in the hierarchy moves.
#
#    Parent IN selection (parent world is ALSO mirrored with M_at):
#        pred_local = M_lin @ src_local @ M_lin
#      The parent's mirrored world already contains the M_at offset.
#      Applying M_at again at child level would add the offset a second time,
#      producing a cumulative error of  2*center_X  per hierarchy level.
#
#    In both cases mpi uses the pure linear mirror:
#        new_mpi = M_lin @ src_mpi @ M_lin
#    because mpi is parent-relative and does not carry world-space offsets.
# ══════════════════════════════════════════════════════════════════════════════

def get_mirror_matrix(axis):
    """Return the 4×4 linear reflection through the world ORIGIN for axis."""
    if axis == 'Y':
        return Matrix([[ 1, 0, 0, 0],
                       [ 0,-1, 0, 0],
                       [ 0, 0, 1, 0],
                       [ 0, 0, 0, 1]])
    if axis == 'Z':
        return Matrix([[ 1, 0, 0, 0],
                       [ 0, 1, 0, 0],
                       [ 0, 0,-1, 0],
                       [ 0, 0, 0, 1]])
    # Default: X axis
    return Matrix([[-1, 0, 0, 0],
                   [ 0, 1, 0, 0],
                   [ 0, 0, 1, 0],
                   [ 0, 0, 0, 1]])


def get_mirror_matrix_at(axis, center_loc):
    """
    Return the 4×4 affine reflection matrix for axis with the mirror plane
    through center_loc.  T(c) @ M_lin @ T(-c).
    """
    M_lin = get_mirror_matrix(axis)
    c     = Vector(center_loc)
    T     = Matrix.Translation( c)
    T_i   = Matrix.Translation(-c)
    return T @ M_lin @ T_i


def m_mat_m(M_lin, mat):
    """M_lin @ mat @ M_lin — correct for mpi mirroring (linear matrix only)."""
    return M_lin @ mat @ M_lin


def mirror_world_mat(M_at, M_lin, W):
    """
    Mirror world matrix W:  M_at @ W @ M_lin.
    Use this for all world-space matrix operations when center ≠ origin.
    For center == origin M_at == M_lin and this equals m_mat_m(M_lin, W).
    """
    return M_at @ W @ M_lin



# ══════════════════════════════════════════════════════════════════════════════
#  MIRROR CENTER
#
#  v2.0 changes:
#   • Name includes axis suffix: Anim_mirror_center_X / _Y / _Z
#   • Location is NOT forced to (0,0,0) — it is read from the existing object
#     if present, allowing the user to pre-position it manually.
#   • The object is NOT deleted at the end of the operation.
# ══════════════════════════════════════════════════════════════════════════════

CENTER_BASE = "Anim_mirror_center"

def center_name_for_axis(axis):
    return f"{CENTER_BASE}_{axis}"

def get_or_create_mirror_center(scene, axis):
    """
    Return the Anim_mirror_center_<axis> object.
    If it already exists its current location is kept (user-set).
    If it does not exist it is created at the world origin.
    Rotation and scale are always reset to identity so the mirror plane is clean.
    """
    name = center_name_for_axis(axis)
    if name in bpy.data.objects:
        mc = bpy.data.objects[name]
        # Ensure it is linked to the scene collection
        if mc.name not in scene.collection.objects:
            scene.collection.objects.link(mc)
    else:
        mc = bpy.data.objects.new(name, None)
        mc.empty_display_type = 'PLAIN_AXES'
        mc.empty_display_size = 0.5
        mc.location           = (0.0, 0.0, 0.0)
        scene.collection.objects.link(mc)

    # Always reset rotation/scale — only location is user-controlled
    mc.rotation_euler = (0.0, 0.0, 0.0)
    mc.scale          = (1.0, 1.0, 1.0)
    return mc


# ══════════════════════════════════════════════════════════════════════════════
#  MESH MIRROR — Mirror modifier + UV offset trick
#
#  (Logic unchanged from v1.0; see comments in v1.0 for full explanation.)
# ══════════════════════════════════════════════════════════════════════════════

UV_NAME = "anim_X"

def apply_mirror_modifier_to_mesh(obj, mirror_center, axis, context):
    """
    Mirror a mesh object's geometry using the Mirror modifier with
    Anim_mirror_center as the mirror reference object on the given axis.
    Preserves custom normals.
    """
    mesh = obj.data
    context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)

    # ── 1. Temp UV map + Smart UV Project ────────────────────────────────────
    uv_layer = mesh.uv_layers.new(name=UV_NAME)
    mesh.uv_layers.active = uv_layer
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.reveal()
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 2. Mirror modifier ────────────────────────────────────────────────────
    mod = obj.modifiers.new(name="_MirrorX_tmp", type='MIRROR')
    mod.use_axis[0]      = (axis == 'X')
    mod.use_axis[1]      = (axis == 'Y')
    mod.use_axis[2]      = (axis == 'Z')
    mod.use_mirror_merge = False
    mod.use_clip         = False
    mod.mirror_object    = mirror_center
    mod.offset_v         = 5

    # ── 3. Apply the modifier ─────────────────────────────────────────────────
    bpy.ops.object.modifier_apply(modifier=mod.name)

    # ── 4. Delete the original geometry (UV V inside 0-1) ────────────────────
    if UV_NAME in mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[UV_NAME]
    bpy.ops.object.mode_set(mode='EDIT')
    context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_all(action='DESELECT')
    context.tool_settings.mesh_select_mode = (False, False, True)

    bm = bmesh.from_edit_mesh(mesh)
    uv_lay = bm.loops.layers.uv.get(UV_NAME)
    if uv_lay:
        for face in bm.faces:
            face.select = False
        for face in bm.faces:
            if all(0.0 <= loop[uv_lay].uv.y <= 1.0 for loop in face.loops):
                face.select = True
        bmesh.update_edit_mesh(mesh)

    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 5. Remove the temporary UV map ───────────────────────────────────────
    if UV_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[UV_NAME])


# ══════════════════════════════════════════════════════════════════════════════
#  ORIGIN MIRROR + NEGATIVE SCALE FIX  (unchanged from v1.0)
# ══════════════════════════════════════════════════════════════════════════════

def mirror_origins_and_fix_scale(context, dst_mesh_objects, axis, center_loc):
    """
    Mirror origins of dst_mesh_objects across the chosen axis, using
    center_loc as the reflection plane origin (mirrors the 3D cursor there).
    Then fix the resulting negative scale.

    center_loc must match the location of Anim_mirror_center so that origin
    mirroring is consistent with the Mirror modifier result.
    """
    scene = context.scene
    bpy.ops.object.select_all(action='DESELECT')
    for obj in dst_mesh_objects:
        obj.select_set(True)
    context.view_layer.objects.active = dst_mesh_objects[0]

    saved_pivot      = scene.tool_settings.transform_pivot_point
    saved_cursor_loc = scene.cursor.location.copy()
    saved_cursor_rot = scene.cursor.rotation_euler.copy()

    constraint = (axis == 'X', axis == 'Y', axis == 'Z')

    try:
        # ── Step A: Mirror origins through center_loc ─────────────────────────
        # Set the 3D cursor to center_loc so transform.mirror reflects through it.
        scene.cursor.location       = center_loc
        scene.cursor.rotation_euler = (0.0, 0.0, 0.0)
        scene.tool_settings.transform_pivot_point = 'CURSOR'
        context.tool_settings.use_transform_data_origin = True

        bpy.ops.transform.mirror(
            orient_type='GLOBAL',
            orient_matrix=((1, 0, 0), (0, 1, 0), (0, 0, 1)),
            orient_matrix_type='GLOBAL',
            constraint_axis=constraint,
        )

        # ── Step B: Fix negative scale ────────────────────────────────────────
        scene.tool_settings.transform_pivot_point = 'INDIVIDUAL_ORIGINS'
        resize_val = (-1 if axis == 'X' else 1,
                      -1 if axis == 'Y' else 1,
                      -1 if axis == 'Z' else 1)
        bpy.ops.transform.resize(
            value=resize_val,
            orient_type='LOCAL',
            constraint_axis=constraint,
        )

    finally:
        context.tool_settings.use_transform_data_origin = False
        scene.tool_settings.transform_pivot_point = saved_pivot
        scene.cursor.location       = saved_cursor_loc
        scene.cursor.rotation_euler = saved_cursor_rot


# ══════════════════════════════════════════════════════════════════════════════
#  CORE TRANSFORM MATH — world-space approach
#
#  All mirroring is done in WORLD SPACE: new_world = M_at @ src_world @ M_lin
#
#  This eliminates two bugs that arise from working in local/parent space:
#   1. Parent-rotation axis confusion: a parent with Y=up(globalZ) makes
#      local-space "mirror Y" actually negate global Z and vice-versa.
#   2. Center accumulation: M_at's translation offset stacks once per
#      hierarchy level when applied to parent-relative local matrices.
#
#  STATIC TRANSFORM (STEP 8):
#      dst.matrix_world = M_at @ src.matrix_world @ M_lin
#
#  MPI — needed so that when the parent moves later (animation) the child
#  follows correctly.
#      parent IN selection:     new_mpi = M_lin @ src_mpi @ M_lin
#      parent NOT in selection: new_mpi = M_lin @ src_mpi @ P_inv @ M_at @ P
#      where P = src_parent.matrix_world (captured before any changes)
#
#  ANIMATION (STEP 9):
#      Bake matrix_world at each keyframe.
#      mir_world = M_at @ baked_world @ M_lin
#      mir_local = new_mpi @ inv(dst_parent_world) @ mir_world
#      Decompose mir_local → loc/rot/scale for fcurves.
# ══════════════════════════════════════════════════════════════════════════════

def compute_new_mpi(src_mpi, src_parent, sel_set, M_at, M_lin):
    """Compute the mirrored matrix_parent_inverse."""
    if src_parent is None or src_parent not in sel_set:
        P     = src_parent.matrix_world.copy() if src_parent else Matrix.Identity(4)
        P_inv = P.inverted()
        return M_lin @ src_mpi @ P_inv @ M_at @ P
    else:
        return M_lin @ src_mpi @ M_lin


def apply_mirrored_transform(dst, src_world, M_at, M_lin):
    """Set dst world matrix to the mirrored version. Blender updates local automatically."""
    dst.matrix_world = M_at @ src_world @ M_lin


# ══════════════════════════════════════════════════════════════════════════════
#  BAKING — world matrices at every keyframe
# ══════════════════════════════════════════════════════════════════════════════

def get_keyframe_frames(obj):
    frames = set()
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                frames.add(round(kp.co[0]))
    return sorted(frames)

def get_source_fcurves(obj):
    result = {}
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            result[(fc.data_path, fc.array_index)] = fc
    return result

def bake_world_matrices(obj, frames, scene):
    """Return {frame: matrix_world} by stepping through each frame."""
    result = {}
    for f in frames:
        scene.frame_set(f)
        result[f] = obj.matrix_world.copy()
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  ANIMATION REBUILD — all math in world space
# ══════════════════════════════════════════════════════════════════════════════

def build_mirrored_action(dst_obj, src_fcurves, src_world_mats,
                          src_kf_frames, new_mpi,
                          src_parent_world_mats, parent_in_sel,
                          M_at, M_lin, action_name):
    """
    Build mirrored action from baked world matrices.

    src_parent_world_mats : {frame: matrix_world} of the SOURCE parent,
                            baked at every child keyframe frame.
    parent_in_sel         : True if the parent is in the mirrored selection
                            (its world will be M_at @ P @ M_lin at each frame).
                            False if parent is outside selection (world unchanged).

    Per-frame parent world is used so that animated parents are handled correctly.
    Using a static parent snapshot (saved_frame only) gives wrong local-space
    values at frames where the parent has moved.
    """
    action = bpy.data.actions.new(name=action_name + "_mirrored")
    if dst_obj.animation_data is None:
        dst_obj.animation_data_create()
    dst_obj.animation_data.action = action

    rot_mode  = dst_obj.rotation_mode
    src_paths = {dp for (dp, _) in src_fcurves}
    has_loc   = 'location' in src_paths
    has_rot   = any(p in src_paths for p in
                    ('rotation_euler', 'rotation_quaternion', 'rotation_axis_angle'))

    inv_new_mpi = new_mpi.inverted()
    frame_locs  = []
    frame_rots  = []
    prev_euler  = None

    for frame in src_kf_frames:
        src_world = src_world_mats.get(frame)
        if src_world is None:
            continue

        # Mirror the child world matrix
        mir_world = M_at @ src_world @ M_lin

        # Compute dst parent world AT THIS FRAME
        src_par_w_f = src_parent_world_mats.get(frame)
        if src_par_w_f is None:
            # Fall back to first available frame (shouldn't normally happen)
            src_par_w_f = next(iter(src_parent_world_mats.values()))
        if parent_in_sel:
            dst_par_w_f = M_at @ src_par_w_f @ M_lin
        else:
            dst_par_w_f = src_par_w_f   # parent world unchanged

        # Convert mirrored world → mirrored local for fcurve storage
        mir_local  = inv_new_mpi @ dst_par_w_f.inverted() @ mir_world
        loc, rot_q, _ = mir_local.decompose()

        if has_loc:
            frame_locs.append((frame, [loc.x, loc.y, loc.z]))
        if has_rot:
            if rot_mode == 'QUATERNION':
                frame_rots.append((frame, [rot_q.w, rot_q.x, rot_q.y, rot_q.z]))
            elif rot_mode == 'AXIS_ANGLE':
                axis_vec, angle = rot_q.to_axis_angle()
                frame_rots.append((frame, [angle, axis_vec.x, axis_vec.y, axis_vec.z]))
            else:
                euler = (rot_q.to_euler(rot_mode, prev_euler)
                         if prev_euler else rot_q.to_euler(rot_mode))
                prev_euler = euler
                frame_rots.append((frame, [euler.x, euler.y, euler.z]))

    def src_kp_at(data_path, array_index, frame):
        fc = src_fcurves.get((data_path, array_index))
        if fc is None:
            return None
        for kp in fc.keyframe_points:
            if round(kp.co[0]) == frame:
                return kp
        return None

    def write_curve(data_path, array_index, frame_vals):
        fc = action.fcurves.new(data_path=data_path, index=array_index)
        fc.keyframe_points.add(len(frame_vals))
        for j, (frame, val) in enumerate(frame_vals):
            dkp = fc.keyframe_points[j]
            skp = src_kp_at(data_path, array_index, frame)
            dkp.co = (frame, val)
            if skp:
                dkp.interpolation     = skp.interpolation
                dkp.easing            = skp.easing
                dkp.handle_left_type  = skp.handle_left_type
                dkp.handle_right_type = skp.handle_right_type
                ratio = (val / skp.co[1]) if abs(skp.co[1]) > 1e-8 else 1.0
                dkp.handle_left  = (skp.handle_left[0],  skp.handle_left[1]  * ratio)
                dkp.handle_right = (skp.handle_right[0], skp.handle_right[1] * ratio)
            else:
                dkp.interpolation = 'BEZIER'
        fc.update()

    if has_loc:
        for i in range(3):
            write_curve('location', i, [(f, v[i]) for f, v in frame_locs])
    if has_rot:
        if rot_mode == 'QUATERNION':
            path, n = 'rotation_quaternion', 4
        elif rot_mode == 'AXIS_ANGLE':
            path, n = 'rotation_axis_angle', 4
        else:
            path, n = 'rotation_euler', 3
        for i in range(n):
            write_curve(path, i, [(f, v[i]) for f, v in frame_rots])


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def clear_animation(obj):
    if obj.animation_data:
        obj.animation_data.action = None
        obj.animation_data_clear()

def depth_of(o):
    d, p = 0, o.parent
    while p:
        d += 1
        p = p.parent
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  DEBUG DATA COLLECTOR
#
#  Collects a full snapshot of every source and destination object:
#    • Name, type, parent name
#    • Local and world transforms (loc / rot / scale / matrix)
#    • Euler rotation order
#    • All animation keyframes (location, rotation, scale channels)
#    • Anim_mirror_center location
#    • Ordered log of operations performed
#
#  Results are written as a JSON file next to the .blend file (or /tmp if
#  the .blend has not been saved yet).
# ══════════════════════════════════════════════════════════════════════════════

def vec3(v):
    return [round(v.x, 6), round(v.y, 6), round(v.z, 6)]

def vec4(v):
    return [round(v.w, 6), round(v.x, 6), round(v.y, 6), round(v.z, 6)]

def mat4(m):
    return [[round(v, 6) for v in row] for row in m]

def collect_object_data(obj, scene):
    """Return a dict with full transform + animation data for obj."""
    d = {
        "name":          obj.name,
        "type":          obj.type,
        "parent":        obj.parent.name if obj.parent else None,
        "rotation_mode": obj.rotation_mode,
        "local": {
            "location":          vec3(obj.location),
            "scale":             vec3(obj.scale),
            "rotation_euler":    vec3(obj.rotation_euler),
            "rotation_quat":     vec4(obj.rotation_quaternion),
            "matrix_local":      mat4(obj.matrix_local),
            "matrix_parent_inv": mat4(obj.matrix_parent_inverse),
        },
        "world": {
            "matrix_world":   mat4(obj.matrix_world),
            "location":       vec3(obj.matrix_world.to_translation()),
            "scale":          vec3(obj.matrix_world.to_scale()),
            "rotation_euler": vec3(obj.matrix_world.to_euler()),
        },
        "animation": {},
    }

    if obj.animation_data and obj.animation_data.action:
        act = obj.animation_data.action
        d["animation"]["action_name"] = act.name
        curves = {}
        for fc in act.fcurves:
            key = f"{fc.data_path}[{fc.array_index}]"
            curves[key] = [
                {
                    "frame":              round(kp.co[0], 3),
                    "value":             round(kp.co[1], 6),
                    "interpolation":     kp.interpolation,
                    "easing":            kp.easing,
                    "handle_left_type":  kp.handle_left_type,
                    "handle_right_type": kp.handle_right_type,
                    "handle_left":       [round(kp.handle_left[0], 3),  round(kp.handle_left[1], 6)],
                    "handle_right":      [round(kp.handle_right[0], 3), round(kp.handle_right[1], 6)],
                }
                for kp in fc.keyframe_points
            ]
        d["animation"]["fcurves"] = curves
    return d


def write_debug_log(debug_data, axis):
    """Write debug_data as JSON. Saves next to .blend or to /tmp."""
    blend_path = bpy.data.filepath
    if blend_path:
        base_dir = os.path.dirname(blend_path)
    else:
        base_dir = "/tmp"

    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"mirror_debug_{axis}_{ts}.json"
    path = os.path.join(base_dir, name)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(debug_data, f, indent=2, ensure_ascii=False)

    return path


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN OPERATOR
# ══════════════════════════════════════════════════════════════════════════════

class OBJECT_OT_mirror_duplicates(bpy.types.Operator):
    """Mirror selected objects (with hierarchy & animation) across global X, Y or Z axis"""
    bl_idname  = "object.mirror_duplicates"
    bl_label   = "Mirror Duplicates"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        name="Axis",
        description="Global axis to mirror across",
        items=[('X', "X", "Mirror across the global YZ plane (negate X)"),
               ('Y', "Y", "Mirror across the global XZ plane (negate Y)"),
               ('Z', "Z", "Mirror across the global XY plane (negate Z)")],
        default='X',
    )

    def execute(self, context):
        scene    = context.scene
        selected = list(context.selected_objects)
        if not selected:
            self.report({'WARNING'}, "No objects selected.")
            return {'CANCELLED'}
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        saved_autokey = scene.tool_settings.use_keyframe_insert_auto
        scene.tool_settings.use_keyframe_insert_auto = False
        try:
            return self._run(context, scene, selected)
        finally:
            scene.tool_settings.use_keyframe_insert_auto = saved_autokey

    def _run(self, context, scene, selected):
        axis       = self.axis
        debug_mode = scene.mirror_dup_debug
        ops_log    = []   # ordered list of operations for debug output

        # ── STEP 1: Create / reuse Anim_mirror_center_<axis> ──────────────────
        mirror_center = get_or_create_mirror_center(scene, axis)
        center_loc    = tuple(mirror_center.location)   # user-set position

        # Two matrices are required:
        #   M_lin — pure origin reflection (no translation). Used for mpi.
        #   M_at  — affine reflection through center_loc.   Used for world mats.
        # When center_loc == (0,0,0), M_at == M_lin and all formulas reduce to v1.0.
        M_lin = get_mirror_matrix(axis)
        M_at  = get_mirror_matrix_at(axis, center_loc)
        ops_log.append(f"STEP 1: mirror_center='{mirror_center.name}' location={center_loc}")

        # ── STEP 2: Sort parents before children ──────────────────────────────
        ordered     = sorted(set(selected), key=depth_of)
        sel_set     = set(ordered)
        saved_frame = scene.frame_current
        ops_log.append(f"STEP 2: ordered sources = {[o.name for o in ordered]}")

        # ── STEP 3: Collect source data ────────────────────────────────────────
        src_fcurves           = {}
        src_kf_frames         = {}
        src_world_mats        = {}   # {src: {frame: matrix_world}}
        src_parent_world_mats = {}   # {src: {frame: parent.matrix_world}} baked at child kf frames
        src_mpi               = {}
        src_parent_world      = {}   # {src: parent world at saved_frame}

        debug_before = {}
        for src in ordered:
            src_fcurves[src]   = get_source_fcurves(src)
            kf                 = get_keyframe_frames(src)
            src_kf_frames[src] = kf
            bake_frames        = sorted(set(kf) | {saved_frame})
            src_world_mats[src] = bake_world_matrices(src, bake_frames, scene)
            src_mpi[src]        = src.matrix_parent_inverse.copy()
            if src.parent:
                src_parent_world[src] = src.parent.matrix_world.copy()
                # Bake parent world at every frame the child has a keyframe.
                # The parent may be animated (e.g. a link strut driving a door panel),
                # so a single saved-frame snapshot would give wrong local matrices
                # at other keyframe times.
                src_parent_world_mats[src] = bake_world_matrices(
                    src.parent, bake_frames, scene)
            else:
                src_parent_world[src]      = Matrix.Identity(4)
                src_parent_world_mats[src] = {f: Matrix.Identity(4) for f in bake_frames}
            if debug_mode:
                debug_before[src.name] = collect_object_data(src, scene)

        scene.frame_set(saved_frame)
        ops_log.append("STEP 3: source data collected (fcurves, baked world matrices, mpi, parent worlds)")

        # ── STEP 4: Duplicate each object individually ────────────────────────
        src_to_dst = {}
        for src in ordered:
            bpy.ops.object.select_all(action='DESELECT')
            src.select_set(True)
            context.view_layer.objects.active = src
            bpy.ops.object.duplicate(linked=False)
            dst = context.active_object
            # ── NEW: rename with _mirrored suffix ─────────────────────────────
            dst.name = src.name + "_mirrored"
            if dst.data:
                dst.data.name = src.data.name + "_mirrored"
            src_to_dst[src] = dst

        ops_log.append(f"STEP 4: duplicated — {[d.name for d in src_to_dst.values()]}")

        # ── STEP 5: Clear animation on all duplicates ─────────────────────────
        for dst in src_to_dst.values():
            clear_animation(dst)
        ops_log.append("STEP 5: animation cleared on duplicates")

        # ── STEP 6: Mirror mesh geometry via Mirror modifier ──────────────────
        for src in ordered:
            if src.type != 'MESH':
                continue
            dst = src_to_dst[src]
            apply_mirror_modifier_to_mesh(dst, mirror_center, axis, context)
        ops_log.append(f"STEP 6: Mirror modifier applied (axis={axis}, ref='{mirror_center.name}')")

        # ── STEP 7: Mirror origins + fix negative scale ───────────────────────
        dst_meshes = [src_to_dst[src] for src in ordered if src.type == 'MESH']
        if dst_meshes:
            mirror_origins_and_fix_scale(context, dst_meshes, axis, center_loc)
        ops_log.append("STEP 7: origins mirrored, negative scale fixed")

        # ── STEP 8: Set parent hierarchy + mpi + world transform ─────────────
        for src in ordered:
            dst         = src_to_dst[src]
            orig_parent = src.parent

            if orig_parent is not None:
                dst_parent = (src_to_dst[orig_parent]
                              if orig_parent in sel_set else orig_parent)
                dst.parent = dst_parent

            dst.rotation_mode         = src.rotation_mode
            new_mpi = compute_new_mpi(src_mpi[src], orig_parent, sel_set, M_at, M_lin)
            dst.matrix_parent_inverse = new_mpi
            # Set world matrix directly — Blender recomputes local automatically
            apply_mirrored_transform(dst, src_world_mats[src][saved_frame], M_at, M_lin)

        ops_log.append("STEP 8: parent hierarchy, mpi, and transforms applied")

        # ── STEP 9: Rebuild mirrored animation curves ─────────────────────────
        for src in ordered:
            if not src_kf_frames[src]:
                continue
            dst         = src_to_dst[src]
            orig_parent = src.parent
            new_mpi     = compute_new_mpi(src_mpi[src], orig_parent, sel_set, M_at, M_lin)
            dst.rotation_mode = src.rotation_mode
            parent_in_sel = (orig_parent is not None and orig_parent in sel_set)

            aname = (src.animation_data.action.name
                     if src.animation_data and src.animation_data.action
                     else src.name)
            build_mirrored_action(
                dst_obj               = dst,
                src_fcurves           = src_fcurves[src],
                src_world_mats        = src_world_mats[src],
                src_kf_frames         = src_kf_frames[src],
                new_mpi               = new_mpi,
                src_parent_world_mats = src_parent_world_mats[src],
                parent_in_sel         = parent_in_sel,
                M_at                  = M_at,
                M_lin                 = M_lin,
                action_name           = aname,
            )

        ops_log.append("STEP 9: mirrored animation actions rebuilt")

        # ── STEP 10: Shift UV V by -5 to undo mirror modifier offset ──────────
        for src in ordered:
            if src.type != 'MESH':
                continue
            dst  = src_to_dst[src]
            mesh = dst.data
            for uv_layer in mesh.uv_layers:
                for loop_uv in uv_layer.data:
                    loop_uv.uv.y -= 5.0

        ops_log.append("STEP 10: UV V coordinates shifted back by -5")

        # ── STEP 11: (v2.0) Anim_mirror_center is NOT deleted ─────────────────
        ops_log.append(f"STEP 11: '{mirror_center.name}' retained in scene at {center_loc}")

        # ── STEP 12: Restore frame and selection ──────────────────────────────
        scene.frame_set(saved_frame)
        bpy.ops.object.select_all(action='DESELECT')
        for dst in src_to_dst.values():
            dst.select_set(True)
        if src_to_dst:
            context.view_layer.objects.active = list(src_to_dst.values())[-1]

        ops_log.append(f"STEP 12: frame restored to {saved_frame}, duplicates selected")

        # ── DEBUG OUTPUT ──────────────────────────────────────────────────────
        if debug_mode:
            debug_after = {}
            for src, dst in src_to_dst.items():
                debug_after[dst.name] = collect_object_data(dst, scene)

            debug_data = {
                "timestamp":     datetime.datetime.now().isoformat(),
                "axis":          axis,
                "mirror_center": {
                    "name":     mirror_center.name,
                    "location": list(center_loc),
                },
                "operations":    ops_log,
                "source_objects": debug_before,
                "mirrored_objects": debug_after,
            }

            log_path = write_debug_log(debug_data, axis)
            self.report({'INFO'}, f"Debug log: {log_path}")
        else:
            self.report({'INFO'}, f"Mirrored {len(src_to_dst)} object(s) across {axis}.")

        return {'FINISHED'}


# ══════════════════════════════════════════════════════════════════════════════
#  CREATE MIRROR CENTER OPERATOR
#
#  Lets the user create (or reset to origin) the Anim_mirror_center_<axis>
#  empty from the panel, without running the full mirror operation.
#  After creation the user can move it anywhere before mirroring.
# ══════════════════════════════════════════════════════════════════════════════

class OBJECT_OT_create_mirror_center(bpy.types.Operator):
    """Create (or reset) the Anim_mirror_center empty for the chosen axis"""
    bl_idname  = "object.create_mirror_center"
    bl_label   = "Create Mirror Center"
    bl_options = {'REGISTER', 'UNDO'}

    axis: bpy.props.EnumProperty(
        name="Axis",
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", "")],
        default='X',
    )

    def execute(self, context):
        # Force-create at origin (clear any existing location)
        name = center_name_for_axis(self.axis)
        if name in bpy.data.objects:
            mc = bpy.data.objects[name]
        else:
            mc = bpy.data.objects.new(name, None)
            mc.empty_display_type = 'PLAIN_AXES'
            mc.empty_display_size = 0.5
            context.scene.collection.objects.link(mc)
        mc.location       = (0.0, 0.0, 0.0)
        mc.rotation_euler = (0.0, 0.0, 0.0)
        mc.scale          = (1.0, 1.0, 1.0)

        # Select it so user can immediately move it
        bpy.ops.object.select_all(action='DESELECT')
        mc.select_set(True)
        context.view_layer.objects.active = mc

        self.report({'INFO'}, f"'{name}' created at origin — move it before mirroring.")
        return {'FINISHED'}


# ══════════════════════════════════════════════════════════════════════════════
#  SCENE PROPERTY — debug toggle
# ══════════════════════════════════════════════════════════════════════════════

def register_props():
    bpy.types.Scene.mirror_dup_debug = bpy.props.BoolProperty(
        name="Debug Mode",
        description=(
            "When enabled, collect full object/animation data before and after "
            "mirroring and write a JSON log file next to the .blend"
        ),
        default=False,
    )

def unregister_props():
    del bpy.types.Scene.mirror_dup_debug


# ══════════════════════════════════════════════════════════════════════════════
#  UI PANEL
# ══════════════════════════════════════════════════════════════════════════════

class VIEW3D_PT_mirror_duplicates(bpy.types.Panel):
    bl_label       = "Mirror Duplicates"
    bl_idname      = "VIEW3D_PT_mirror_duplicates"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = "Mirror Dup"

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        # ── Mirror buttons ─────────────────────────────────────────────────────
        layout.label(text="Mirror selected objects:")
        row = layout.row(align=True)
        op_x = row.operator("object.mirror_duplicates", text="Mirror X", icon='MOD_MIRROR')
        op_x.axis = 'X'
        op_y = row.operator("object.mirror_duplicates", text="Mirror Y", icon='MOD_MIRROR')
        op_y.axis = 'Y'
        op_z = row.operator("object.mirror_duplicates", text="Mirror Z", icon='MOD_MIRROR')
        op_z.axis = 'Z'

        layout.separator()

        # ── Mirror center setup ────────────────────────────────────────────────
        layout.label(text="Mirror Center (manual placement):")
        col = layout.column(align=True)
        for ax in ('X', 'Y', 'Z'):
            name = center_name_for_axis(ax)
            exists = name in bpy.data.objects
            row2 = col.row(align=True)
            op = row2.operator(
                "object.create_mirror_center",
                text=f"{'Reset' if exists else 'Create'} {name}",
                icon='EMPTY_AXIS',
            )
            op.axis = ax
            # Show current location if the object exists
            if exists:
                mc = bpy.data.objects[name]
                loc = mc.location
                row2.label(text=f"({loc.x:.2f}, {loc.y:.2f}, {loc.z:.2f})")

        layout.separator()

        # ── Debug toggle ───────────────────────────────────────────────────────
        box = layout.box()
        box.prop(scene, "mirror_dup_debug", icon='CONSOLE')
        if scene.mirror_dup_debug:
            blend_path = bpy.data.filepath
            if blend_path:
                box.label(text=f"Log → {os.path.dirname(blend_path)}", icon='INFO')
            else:
                box.label(text="Log → /tmp  (save .blend first)", icon='ERROR')


classes = (
    OBJECT_OT_mirror_duplicates,
    OBJECT_OT_create_mirror_center,
    VIEW3D_PT_mirror_duplicates,
)

def register():
    register_props()
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    unregister_props()

if __name__ == "__main__":
    register()
