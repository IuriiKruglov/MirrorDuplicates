# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║        MIRROR DUPLICATES GLOBAL — Blender Addon v1.0                       ║
# ║                                                                              ║
# ║  Mirrors selected objects (with full hierarchy and animation) across the    ║
# ║  global X, Y or Z axis, preserving custom normals, parent relationships,    ║
# ║  animation curves with their original interpolation and handle types.       ║
# ║                                                                              ║
# ║  Authors: Claude (Anthropic AI) & Kruglov Iurii                             ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

bl_info = {
    "name": "Mirror Duplicates Global",
    "author": "Claude (Anthropic AI) & Kruglov Iurii",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Mirror Dup",
    "description": "Mirror selected objects (with hierarchy & animation) across global X, Y or Z axis",
    "category": "Object",
}

import bpy
import bmesh
from mathutils import Matrix, Vector


# ══════════════════════════════════════════════════════════════════════════════
#  MIRROR MATRIX HELPER
#
#  Returns the 4x4 reflection matrix for the chosen axis:
#    X  →  negate X  (reflect across the global YZ plane)
#    Y  →  negate Y  (reflect across the global XZ plane)
#    Z  →  negate Z  (reflect across the global XY plane)
#
#  Applying  M @ mat @ M  to any world matrix gives its mirrored counterpart.
# ══════════════════════════════════════════════════════════════════════════════

def get_mirror_matrix(axis):
    """Return the 4x4 reflection matrix for the given axis ('X', 'Y' or 'Z')."""
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


def m_mat_m(M, mat):
    """Return  M @ mat @ M  (mirror mat through the plane defined by M)."""
    return M @ mat @ M


# ══════════════════════════════════════════════════════════════════════════════
#  MIRROR CENTER — create or reuse the Anim_mirror_center empty object
#
#  A Plain Axes empty at the world origin that serves as the reference object
#  for the Mirror modifier. Created automatically on first run and deleted
#  automatically at the end of the operation.
# ══════════════════════════════════════════════════════════════════════════════

CENTER_NAME = "Anim_mirror_center"

def get_or_create_mirror_center(scene):
    """
    Return the Anim_mirror_center object, creating it if it doesn't exist.
    Transforms are always reset to the world origin.
    """
    if CENTER_NAME in bpy.data.objects:
        mc = bpy.data.objects[CENTER_NAME]
    else:
        mc = bpy.data.objects.new(CENTER_NAME, None)
        mc.empty_display_type = 'PLAIN_AXES'
        mc.empty_display_size = 0.5
        scene.collection.objects.link(mc)
    mc.location       = (0.0, 0.0, 0.0)
    mc.rotation_euler = (0.0, 0.0, 0.0)
    mc.scale          = (1.0, 1.0, 1.0)
    return mc


# ══════════════════════════════════════════════════════════════════════════════
#  MESH MIRROR — Mirror modifier + UV offset trick
#
#  WHY NOT bmesh face_normal_flip()?
#  Manually flipping face normals in bmesh destroys custom normal data stored
#  in mesh loops. The Mirror modifier handles winding order internally and
#  preserves custom normals correctly.
#
#  WHY THE UV TRICK?
#  The Mirror modifier always produces BOTH the original geometry AND its
#  mirrored copy. We only want the mirrored copy. We temporarily project the
#  mesh (Smart UV Project) so all original faces land in UV V range 0-1.
#  The modifier's offset_v=5 shifts the mirrored copy's UVs to V+5 (outside
#  0-1). After applying, we delete all faces with UV V inside 0-1 (originals).
#
#  SEQUENCE:
#  1. Create temp UV map "anim_X", Smart UV Project → originals land in V 0–1.
#  2. Add Mirror modifier: mirror_object=Anim_mirror_center, correct axis,
#     offset_v=5.
#  3. Apply modifier → mesh now has original + mirrored geometry.
#  4. In Edit mode, select faces whose UV V is inside 0–1 → delete them.
#  5. Remove the temp UV map "anim_X".
# ══════════════════════════════════════════════════════════════════════════════

UV_NAME = "anim_X"  # name of the temporary UV map used for face identification

def apply_mirror_modifier_to_mesh(obj, mirror_center, axis, context):
    """
    Mirror a mesh object's geometry using the Mirror modifier with
    Anim_mirror_center as the mirror reference object on the given axis.
    Preserves custom normals. The object must still be at its original
    world position when this is called (before any transform changes).
    """
    mesh = obj.data
    context.view_layer.objects.active = obj
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)

    # ── 1. Temp UV map + Smart UV Project ────────────────────────────────────
    # Smart UV Project works on any geometry regardless of UV seams, and always
    # fits all islands inside the 0-1 tile — unlike regular Unwrap which can
    # fail on seamless meshes. We unhide all geometry first so no faces are missed.
    uv_layer = mesh.uv_layers.new(name=UV_NAME)
    mesh.uv_layers.active = uv_layer
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.reveal()                        # unhide all geometry
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(island_margin=0.001)
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 2. Mirror modifier ────────────────────────────────────────────────────
    # mirror_object=Anim_mirror_center uses the empty's position as the mirror
    # plane origin rather than the object's own origin.
    # offset_v=5 shifts the mirrored copy's UVs to V+5, outside the 0-1 tile.
    mod = obj.modifiers.new(name="_MirrorX_tmp", type='MIRROR')
    mod.use_axis[0]      = (axis == 'X')   # X axis of mirror object → negate world X
    mod.use_axis[1]      = (axis == 'Y')   # Y axis of mirror object → negate world Y
    mod.use_axis[2]      = (axis == 'Z')   # Z axis of mirror object → negate world Z
    mod.use_mirror_merge = False  # don't weld vertices at the mirror seam
    mod.use_clip         = False  # don't constrain vertices to the mirror plane
    mod.mirror_object    = mirror_center
    mod.offset_v         = 5     # push mirrored UVs out of 0-1 range

    # ── 3. Apply the modifier ─────────────────────────────────────────────────
    # Mesh now has: original geometry (UV V in 0-1) + mirrored copy (UV V ~5+)
    bpy.ops.object.modifier_apply(modifier=mod.name)

    # ── 4. Delete the original geometry (UV V inside 0-1) ────────────────────
    if UV_NAME in mesh.uv_layers:
        mesh.uv_layers.active = mesh.uv_layers[UV_NAME]
    bpy.ops.object.mode_set(mode='EDIT')
    context.scene.tool_settings.use_uv_select_sync = True
    bpy.ops.mesh.select_all(action='DESELECT')
    context.tool_settings.mesh_select_mode = (False, False, True)  # face select

    bm = bmesh.from_edit_mesh(mesh)
    uv_lay = bm.loops.layers.uv.get(UV_NAME)
    if uv_lay:
        for face in bm.faces:
            face.select = False
        for face in bm.faces:
            # A face is "original" if ALL its UV loop V values are within 0-1
            if all(0.0 <= loop[uv_lay].uv.y <= 1.0 for loop in face.loops):
                face.select = True
        bmesh.update_edit_mesh(mesh)

    bpy.ops.mesh.delete(type='FACE')
    bpy.ops.object.mode_set(mode='OBJECT')

    # ── 5. Remove the temporary UV map ───────────────────────────────────────
    if UV_NAME in mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[UV_NAME])


# ══════════════════════════════════════════════════════════════════════════════
#  ORIGIN MIRROR + NEGATIVE SCALE FIX
#
#  After the Mirror modifier is applied, origins must be moved to the mirrored
#  world position without moving the geometry.
#
#  Step A — "Affect Only Origins": mirror origins across the chosen axis using
#  the 3D cursor (at world origin) as the reflection centre. This places origins
#  correctly but introduces a -1 scale on the mirrored axis as a side effect.
#
#  Step B — Individual Origins pivot, LOCAL orientation, scale -1 on the same
#  axis: each object scales around its own origin in local space, flipping the
#  scale sign back to positive without moving any geometry.
# ══════════════════════════════════════════════════════════════════════════════

def mirror_origins_and_fix_scale(context, dst_mesh_objects, axis):
    """
    Mirror origins of dst_mesh_objects across the chosen axis (global X or Y),
    then fix the resulting negative scale. Scene cursor and pivot are restored.
    """
    scene = context.scene
    bpy.ops.object.select_all(action='DESELECT')
    for obj in dst_mesh_objects:
        obj.select_set(True)
    context.view_layer.objects.active = dst_mesh_objects[0]

    saved_pivot      = scene.tool_settings.transform_pivot_point
    saved_cursor_loc = scene.cursor.location.copy()
    saved_cursor_rot = scene.cursor.rotation_euler.copy()

    # constraint_axis: (X, Y, Z) — True on the axis we are mirroring across
    constraint = (axis == 'X', axis == 'Y', axis == 'Z')

    try:
        # ── Step A: Mirror origins ────────────────────────────────────────────
        scene.cursor.location       = (0.0, 0.0, 0.0)
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
#  CORE TRANSFORM MATH — hierarchy-aware mirror
#
#  Blender's object transform model:
#    matrix_world = parent_world @ matrix_local
#    matrix_local = matrix_parent_inverse (mpi) @ TRS_basis
#
#  To mirror across the chosen axis (matrix M):
#    dst.matrix_world = M @ src.matrix_world @ M        (proven formula)
#
#  Derived:
#    new_mpi   = M @ src.mpi @ M
#    TRS_basis = inv(new_mpi) @ (M @ src.matrix_local @ M)
#
#  We decompose TRS_basis and set location/rotation/scale directly (not via
#  matrix_local) to avoid a Blender depsgraph timing bug where mpi is not yet
#  evaluated when matrix_local is decomposed, causing wrong rotations on
#  child objects.
# ══════════════════════════════════════════════════════════════════════════════

def apply_mirrored_transform(dst, src_local, new_mpi, M):
    """
    Set dst's location/rotation/scale to the mirrored version of src_local,
    given the already-set new_mpi and the mirror matrix M.
    Handles EULER, QUATERNION and AXIS_ANGLE rotation modes.
    """
    pred_local      = m_mat_m(M, src_local)
    TRS_basis       = new_mpi.inverted() @ pred_local
    loc, rot_q, sca = TRS_basis.decompose()

    dst.location = loc
    dst.scale    = sca
    mode = dst.rotation_mode
    if mode == 'QUATERNION':
        dst.rotation_quaternion = rot_q
    elif mode == 'AXIS_ANGLE':
        axis_vec, angle = rot_q.to_axis_angle()
        dst.rotation_axis_angle = (angle, axis_vec.x, axis_vec.y, axis_vec.z)
    else:
        dst.rotation_euler = rot_q.to_euler(mode)


# ══════════════════════════════════════════════════════════════════════════════
#  BAKING — snapshot matrix_local at every keyframe before any changes
# ══════════════════════════════════════════════════════════════════════════════

def get_keyframe_frames(obj):
    """Return a sorted list of all integer frame numbers with keyframes on obj."""
    frames = set()
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                frames.add(round(kp.co[0]))
    return sorted(frames)

def get_source_fcurves(obj):
    """Return {(data_path, array_index): FCurve} for all fcurves on obj."""
    result = {}
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            result[(fc.data_path, fc.array_index)] = fc
    return result

def bake_local_matrices(obj, frames, scene):
    """Return {frame: matrix_local} by stepping through each frame."""
    result = {}
    for f in frames:
        scene.frame_set(f)
        result[f] = obj.matrix_local.copy()
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  ANIMATION REBUILD — mirror animation curves with interpolation preserved
# ══════════════════════════════════════════════════════════════════════════════

def build_mirrored_action(dst_obj, src_fcurves, src_local_mats,
                          src_kf_frames, new_mpi, M, action_name):
    """
    Build a new mirrored Action on dst_obj from baked source data.
    M is the mirror matrix for the chosen axis.
    Preserves interpolation mode, easing, and bezier handle types/positions.
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
        src_local = src_local_mats.get(frame)
        if src_local is None:
            continue
        pred_local    = m_mat_m(M, src_local)
        TRS_basis     = inv_new_mpi @ pred_local
        loc, rot_q, _ = TRS_basis.decompose()

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
    """Detach and discard the action on obj, removing all animation data."""
    if obj.animation_data:
        obj.animation_data.action = None
        obj.animation_data_clear()

def depth_of(o):
    """Return the number of ancestors of object o (0 = no parent)."""
    d, p = 0, o.parent
    while p:
        d += 1
        p = p.parent
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN OPERATOR
# ══════════════════════════════════════════════════════════════════════════════

class OBJECT_OT_mirror_duplicates(bpy.types.Operator):
    """Mirror selected objects (with hierarchy & animation) across global X, Y or Z axis"""
    bl_idname  = "object.mirror_duplicates"
    bl_label   = "Mirror Duplicates"
    bl_options = {'REGISTER', 'UNDO'}

    # The axis to mirror across — set by the two panel buttons
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
        axis = self.axis
        M    = get_mirror_matrix(axis)   # the 4x4 reflection matrix for this axis

        # ── STEP 1: Create / reuse Anim_mirror_center ─────────────────────────
        mirror_center = get_or_create_mirror_center(scene)

        # ── STEP 2: Sort parents before children ──────────────────────────────
        ordered     = sorted(set(selected), key=depth_of)
        sel_set     = set(ordered)
        saved_frame = scene.frame_current

        # ── STEP 3: Collect source data while hierarchy is intact ─────────────
        src_fcurves    = {}
        src_kf_frames  = {}
        src_local_mats = {}
        src_mpi        = {}

        for src in ordered:
            src_fcurves[src]    = get_source_fcurves(src)
            kf                  = get_keyframe_frames(src)
            src_kf_frames[src]  = kf
            bake_frames         = sorted(set(kf) | {saved_frame})
            src_local_mats[src] = bake_local_matrices(src, bake_frames, scene)
            src_mpi[src]        = src.matrix_parent_inverse.copy()

        scene.frame_set(saved_frame)

        # ── STEP 4: Duplicate each object individually ────────────────────────
        src_to_dst = {}
        for src in ordered:
            bpy.ops.object.select_all(action='DESELECT')
            src.select_set(True)
            context.view_layer.objects.active = src
            bpy.ops.object.duplicate(linked=False)
            src_to_dst[src] = context.active_object

        # ── STEP 5: Clear animation on all duplicates ─────────────────────────
        for dst in src_to_dst.values():
            clear_animation(dst)

        # ── STEP 6: Mirror mesh geometry via Mirror modifier ──────────────────
        for src in ordered:
            if src.type != 'MESH':
                continue
            dst = src_to_dst[src]
            apply_mirror_modifier_to_mesh(dst, mirror_center, axis, context)

        # ── STEP 7: Mirror origins + fix negative scale ───────────────────────
        dst_meshes = [src_to_dst[src] for src in ordered if src.type == 'MESH']
        if dst_meshes:
            mirror_origins_and_fix_scale(context, dst_meshes, axis)

        # ── STEP 8: Set parent hierarchy + mpi + loc/rot/scale ───────────────
        for src in ordered:
            dst         = src_to_dst[src]
            new_mpi     = m_mat_m(M, src_mpi[src])
            orig_parent = src.parent

            if orig_parent is not None:
                dst_parent = (src_to_dst[orig_parent]
                              if orig_parent in sel_set else orig_parent)
                dst.parent = dst_parent

            dst.matrix_parent_inverse = new_mpi
            dst.rotation_mode         = src.rotation_mode
            apply_mirrored_transform(dst, src_local_mats[src][saved_frame], new_mpi, M)

        # ── STEP 9: Rebuild mirrored animation curves ─────────────────────────
        for src in ordered:
            if not src_kf_frames[src]:
                continue
            dst     = src_to_dst[src]
            new_mpi = m_mat_m(M, src_mpi[src])
            dst.rotation_mode = src.rotation_mode
            aname = (src.animation_data.action.name
                     if src.animation_data and src.animation_data.action
                     else src.name)
            build_mirrored_action(
                dst_obj        = dst,
                src_fcurves    = src_fcurves[src],
                src_local_mats = src_local_mats[src],
                src_kf_frames  = src_kf_frames[src],
                new_mpi        = new_mpi,
                M              = M,
                action_name    = aname,
            )

        # ── STEP 10: Shift all UV shells back by V -5 in every UV channel ──────
        # The Mirror modifier's offset_v=5 shifted mirrored UVs to V+5 so we
        # could identify and delete original faces. Now shift every UV channel
        # of every mesh duplicate back by -5 to restore correct UV positions.
        # All UV channels are processed by name so objects with any number of
        # channels are handled correctly.
        for src in ordered:
            if src.type != 'MESH':
                continue
            dst  = src_to_dst[src]
            mesh = dst.data
            for uv_layer in mesh.uv_layers:
                for loop_uv in uv_layer.data:
                    loop_uv.uv.y -= 5.0

        # ── STEP 11: Delete Anim_mirror_center ────────────────────────────────
        mc = bpy.data.objects.get(CENTER_NAME)
        if mc is not None:
            bpy.data.objects.remove(mc, do_unlink=True)

        # ── STEP 12: Restore frame and selection ──────────────────────────────
        scene.frame_set(saved_frame)
        bpy.ops.object.select_all(action='DESELECT')
        for dst in src_to_dst.values():
            dst.select_set(True)
        if src_to_dst:
            context.view_layer.objects.active = list(src_to_dst.values())[-1]

        self.report({'INFO'}, f"Mirrored {len(src_to_dst)} object(s) across {axis}.")
        return {'FINISHED'}


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
        row = layout.row(align=True)
        # Three side-by-side buttons — one per axis
        op_x = row.operator("object.mirror_duplicates", text="Mirror X", icon='MOD_MIRROR')
        op_x.axis = 'X'
        op_y = row.operator("object.mirror_duplicates", text="Mirror Z", icon='MOD_MIRROR')
        op_y.axis = 'Y'
        op_z = row.operator("object.mirror_duplicates", text="Mirror Y", icon='MOD_MIRROR')
        op_z.axis = 'Z'


classes = (OBJECT_OT_mirror_duplicates, VIEW3D_PT_mirror_duplicates)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
