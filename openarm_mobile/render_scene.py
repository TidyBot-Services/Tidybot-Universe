"""Serialize actual visual geometry; a second task reset is never a substitute.

RoboCasa fixtures use independently randomized mesh scales. Exporting the
loaded geometry/materials prevents a render replica from inventing another
kitchen even when the two environments have the same seed.
"""
import numpy as np


def entities(scene, robot):
    result=list(scene.get_all_actors())
    for articulation in scene.get_all_articulations():
        if articulation.root.entity.per_scene_id!=robot.root.entity.per_scene_id:
            result.extend(link.entity for link in articulation.links)
    return result


def material_data(material):
    fields=['base_color','emission','metallic','roughness','specular','transmission','ior']
    result={name:np.asarray(getattr(material,name)).tolist() for name in fields}
    for name in ['base_color_texture','emission_texture','metallic_texture','roughness_texture','normal_texture','transmission_texture']:
        texture=getattr(material,name)
        if texture is not None:
            if not texture.filename:raise ValueError('Render replica requires a file-backed texture')
            result[name]=texture.filename
    return result


def export_scene(kitchen):
    import sapien
    rows=[]
    for entity in entities(kitchen.scene,kitchen.rig.robot):
        body=entity.find_component_by_type(sapien.render.RenderBodyComponent)
        shapes=[]
        if body is not None:
            for shape in body.render_shapes:
                row={'pose':shape.local_pose.to_transformation_matrix().tolist(),
                     'materials':[material_data(part.material) for part in shape.parts]}
                if isinstance(shape,sapien.render.RenderShapeBox):
                    row.update(kind='box',half_size=shape.half_size.tolist())
                elif isinstance(shape,sapien.render.RenderShapeSphere):
                    row.update(kind='sphere',radius=shape.radius)
                elif isinstance(shape,(sapien.render.RenderShapeCapsule,sapien.render.RenderShapeCylinder)):
                    row.update(kind='capsule' if isinstance(shape,sapien.render.RenderShapeCapsule) else 'cylinder',
                               radius=shape.radius,half_length=shape.half_length)
                elif isinstance(shape,sapien.render.RenderShapePlane):
                    row.update(kind='plane',scale=shape.scale.tolist())
                elif isinstance(shape,sapien.render.RenderShapeTriangleMesh) and shape.filename:
                    row.update(kind='mesh',file=shape.filename,scale=shape.scale.tolist())
                else:
                    # Preserve procedural mesh geometry without replacing it by a box.
                    parts=[]
                    for part in shape.parts:
                        parts.append({'vertices':part.vertices.tolist(),'triangles':part.triangles.tolist(),
                            'normals':part.get_vertex_normal().tolist(),'uv':part.get_vertex_uv().tolist()})
                    row.update(kind='procedural',parts=parts)
                shapes.append(row)
        rows.append({'name':entity.name,'shapes':shapes,'pose':entity.pose.to_transformation_matrix().tolist()})
    destination=getattr(kitchen.native,kitchen.spec.destination_debug_key)
    return {'entities':rows,'destination':np.array(destination.pos).tolist(),
            'root_pose':kitchen.rig.robot.root_pose.to_transformation_matrix().tolist(),
            'variation':kitchen.variation}


def import_scene(scene,blueprint):
    import sapien
    result=[]
    def material(row):
        value=sapien.render.RenderMaterial()
        for name,field in row.items():
            setattr(value,name,sapien.render.RenderTexture2D(field) if name.endswith('_texture') else field)
        return value
    for row in blueprint['entities']:
        entity=sapien.Entity();entity.name=row['name'];body=sapien.render.RenderBodyComponent()
        for shape in row['shapes']:
            materials=[material(data) for data in shape['materials']]
            if shape['kind'] in {'box','sphere','capsule','cylinder','plane'}:
                kind=shape['kind']
                cls=getattr(sapien.render,'RenderShape'+kind.capitalize())
                if kind=='box':value=cls(np.array(shape['half_size']),materials[0])
                elif kind=='sphere':value=cls(shape['radius'],materials[0])
                elif kind=='plane':value=cls(np.array(shape['scale']),materials[0])
                else:value=cls(shape['radius'],shape['half_length'],materials[0])
                value.local_pose=sapien.Pose(shape['pose']);body.attach(value)
            elif shape['kind']=='mesh':
                value=sapien.render.RenderShapeTriangleMesh(shape['file'],np.array(shape['scale']))
                if len(value.parts)!=len(materials):raise ValueError('Actual scene mesh part count changed')
                for part,data in zip(value.parts,shape['materials']):
                    for name,field in data.items():
                        setattr(part.material,name,sapien.render.RenderTexture2D(field) if name.endswith('_texture') else field)
                value.local_pose=sapien.Pose(shape['pose']);body.attach(value)
            else:
                for part,mat in zip(shape['parts'],materials):
                    value=sapien.render.RenderShapeTriangleMesh(np.array(part['vertices'],np.float32),
                        np.array(part['triangles'],np.uint32),np.array(part['normals'],np.float32),
                        np.array(part['uv'],np.float32),mat)
                    value.local_pose=sapien.Pose(shape['pose']);body.attach(value)
        entity.add_component(body);entity.pose=sapien.Pose(row['pose']);scene.add_entity(entity);result.append(entity)
    return result
