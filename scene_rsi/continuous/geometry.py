"""Trusted actual-mesh geometry, raster visibility and rigid-body evaluation.

The authored library is a union of closed convex components (verified by Blender).
Each URDF retains every component. Bullet's shape-array API truncates at 16, so
it must NOT be used for these 17/51/23-component assets.
"""
from collections import deque
import json
import math
from pathlib import Path
import tempfile
import time
import xml.etree.ElementTree as xml

import numpy as np
import pybullet as p

from ..schema import SceneSpec


def rotation(euler):
    return np.array(p.getMatrixFromQuaternion(p.getQuaternionFromEuler(euler))).reshape(3,3)


def pose(name, asset, position, euler=(0.,0.,0.)):
    return {'id':name, 'asset':asset, 'position':list(map(float,position)), 'rotation':list(map(float,euler))}


class MeshWorld:
    def __init__(self, task, directory):
        self.task, self.spec = task, SceneSpec.from_dict(task['spec'])
        self.directory = Path(directory)
        self.catalog = json.loads((self.directory/'geometry.json').read_text())
        self.temporary = tempfile.TemporaryDirectory(prefix='continuous-mesh-')
        self.client = p.connect(p.DIRECT)
        self.bodies, self.com, self.vertices = {}, {}, {}
        self._silhouettes = {}
        c = self.client
        p.setPhysicsEngineParameter(fixedTimeStep=1/240, numSolverIterations=80, deterministicOverlappingPairs=1, physicsClientId=c)
        p.setGravity(0,0,-9.81,physicsClientId=c)
        floor_shape = p.createCollisionShape(p.GEOM_BOX,halfExtents=[self.spec.room.width/2,self.spec.room.depth/2,.025],physicsClientId=c)
        self.floor = p.createMultiBody(baseMass=0,baseCollisionShapeIndex=floor_shape,
                     basePosition=[self.spec.room.width/2,self.spec.room.depth/2,-.025],physicsClientId=c)
        fixed = {q['id'] for q in task['fixed']}
        for obj in self.spec.required_objects:
            a = self.spec.assets[obj.asset]
            size = np.array([a.width,a.depth,a.height])
            mesh = self.catalog[task['meshes'][obj.asset]]
            self.com[obj.id] = np.array(mesh['center_of_mass'])*size
            self.vertices[obj.id] = np.array(mesh['vertices'])*size
            urdf = self.make_urdf(obj.asset, task['meshes'][obj.asset], size, mesh)
            body = p.loadURDF(str(urdf),useFixedBase=obj.id in fixed,
                              flags=p.URDF_USE_INERTIA_FROM_FILE,physicsClientId=c)
            self.bodies[obj.id] = body
            count = len(p.getCollisionShapeData(body,-1,physicsClientId=c))
            if count != len(mesh['parts']):
                raise RuntimeError(('mesh components lost', obj.id,count,len(mesh['parts'])))
            p.changeDynamics(body,-1,lateralFriction=.8,restitution=0,linearDamping=.05,angularDamping=.05,physicsClientId=c)
        camera = task['camera']
        self.view = p.computeViewMatrix(camera['position'],camera['target'],[0,0,1])
        self.projection = p.computeProjectionMatrixFOV(camera['fov'],1,.05,100)
        self.vp = np.array(self.projection).reshape(4,4,order='F') @ np.array(self.view).reshape(4,4,order='F')

    def make_urdf(self, asset, mesh_name, size, mesh):
        filename = Path(self.temporary.name)/(asset+'.urdf')
        if filename.exists():
            return filename
        robot = xml.Element('robot',name=asset)
        link = xml.SubElement(robot,'link',name='body')
        inertial = xml.SubElement(link,'inertial')
        center = np.array(mesh['center_of_mass'])*size
        xml.SubElement(inertial,'origin',xyz=' '.join(map(str,center)))
        xml.SubElement(inertial,'mass',value='1')
        # Explicit synthetic mass model: uniform authored components, bbox inertia.
        inertia = [(size[1]**2+size[2]**2)/12,(size[0]**2+size[2]**2)/12,(size[0]**2+size[1]**2)/12]
        xml.SubElement(inertial,'inertia',ixx=str(inertia[0]),iyy=str(inertia[1]),izz=str(inertia[2]),ixy='0',ixz='0',iyz='0')
        for name in mesh['parts']:
            node = xml.SubElement(link,'collision')
            geometry = xml.SubElement(node,'geometry')
            xml.SubElement(geometry,'mesh',filename=str(self.directory/name),scale=' '.join(map(str,size)))
        visual = xml.SubElement(link,'visual')
        geometry = xml.SubElement(visual,'geometry')
        xml.SubElement(geometry,'mesh',filename=str(self.directory/(mesh_name+'.obj')),scale=' '.join(map(str,size)))
        xml.ElementTree(robot).write(filename)
        return filename

    def close(self):
        p.disconnect(self.client)
        self.temporary.cleanup()

    def set_state(self, state):
        if set(state) != {'scene_id','objects'} or state['scene_id'] != self.spec.scene_id:
            raise ValueError('scene id and objects only')
        required = self.spec.required_lookup()
        poses = {q['id']:q for q in state['objects']}
        if len(poses) != len(state['objects']) or set(poses) != set(required):
            raise ValueError('every required object exactly once')
        for name,q in poses.items():
            if set(q) != {'id','asset','position','rotation'} or q['asset'] != required[name].asset:
                raise ValueError('fixed asset identity; no scale or camera overrides')
            if any(len(q[k]) != 3 or not all(type(v) in (int,float) and math.isfinite(v) for v in q[k]) for k in ('position','rotation')):
                raise ValueError('finite continuous xyz and Euler XYZ radians required')
        if any(poses[q['id']] != q for q in self.task['fixed']):
            raise ValueError('fixed architecture cannot move')
        self.poses = poses
        for name,q in poses.items():
            r = rotation(q['rotation'])
            p.resetBasePositionAndOrientation(self.bodies[name],np.array(q['position'])+r@self.com[name],
                     p.getQuaternionFromEuler(q['rotation']),physicsClientId=self.client)
            p.resetBaseVelocity(self.bodies[name],[0,0,0],[0,0,0],physicsClientId=self.client)
        p.performCollisionDetection(physicsClientId=self.client)

    def world_vertices(self, name):
        q = self.poses[name]
        return self.vertices[name]@rotation(q['rotation']).T + q['position']

    def pixels(self):
        image = p.getCameraImage(128,128,viewMatrix=self.view,projectionMatrix=self.projection,
                   renderer=p.ER_TINY_RENDERER,flags=p.ER_SEGMENTATION_MASK_OBJECT_AND_LINKINDEX,physicsClientId=self.client)
        return np.asarray(image[4]).reshape(128,128) & ((1<<24)-1)

    def visibility(self):
        full = self.pixels()
        results = {}
        for name in self.task['visibility']:
            body = self.bodies[name]
            key = (name,*self.poses[name]['position'],*self.poses[name]['rotation'])
            if key not in self._silhouettes:
                saved = {b:p.getBasePositionAndOrientation(b,physicsClientId=self.client) for b in self.bodies.values() if b != body}
                for b in saved:
                    p.resetBasePositionAndOrientation(b,[1000,1000,1000],[0,0,0,1],physicsClientId=self.client)
                self._silhouettes[key] = int(np.count_nonzero(self.pixels() == body))
                for b,(xyz,quat) in saved.items():
                    p.resetBasePositionAndOrientation(b,xyz,quat,physicsClientId=self.client)
            isolated = self._silhouettes[key]
            vertices = self.world_vertices(name)
            clip = np.c_[vertices,np.ones(len(vertices))] @ self.vp.T
            framed = (clip[:,3] > 0) & (np.abs(clip[:,:3]) <= clip[:,3,None]+1e-7).all(axis=1)
            count = int(np.count_nonzero(full == body))
            results[name] = {'visible_pixels':count,'isolated_pixels':isolated,
                             'fraction':count/isolated if isolated else 0.,
                             'framed_vertices_fraction':float(framed.mean())}
        return results

    def evaluate(self, state, final=False):
        started = time.perf_counter()
        self.set_state(state)
        failures, collision, boundary, gaps, relation, preference = [],0.,0.,0.,0.,0.
        def fail(kind, names=()):
            failures.append({'type':kind,'objects':list(names)})
        boxes = {}
        limits = np.array([self.spec.room.width,self.spec.room.depth,self.spec.room.height])
        for name in self.bodies:
            vertices = self.world_vertices(name)
            lo,hi = vertices.min(axis=0),vertices.max(axis=0)
            boxes[name] = (lo,hi)
            excess = float(np.maximum(-lo,0).sum()+np.maximum(hi-limits,0).sum())
            boundary += excess
            if excess > .003:
                fail('mesh_bounds',[name])
        names = list(self.bodies)
        for i,a in enumerate(names):
            for b in names[i+1:]:
                distances = p.getClosestPoints(self.bodies[a],self.bodies[b],0,physicsClientId=self.client)
                penetration = max(0.,-min((q[8] for q in distances),default=0.)-.003)
                collision += penetration
                if penetration > 0:
                    fail('mesh_collision',[a,b])
        for name,parent in self.task['supports'].items():
            body = self.floor if parent == 'floor' else self.bodies[parent]
            points = p.getClosestPoints(self.bodies[name],body,.25,physicsClientId=self.client)
            gap = min((abs(q[8]) for q in points if q[7][2] > .7),default=.25)
            gaps += gap
            if gap > .012:
                fail('support_contact',[name,parent])
        for name,zone in self.task['zones'].items():
            lo,hi = boxes[name]
            error = max(0.,zone[0]-lo[0])+max(0.,zone[1]-lo[1])+max(0.,hi[0]-zone[2])+max(0.,hi[1]-zone[3])
            relation += error
            if error > .003:
                fail('spatial_zone',[name])
        for rule in self.task['relations']:
            distance = math.dist(self.poses[rule['a']]['position'],self.poses[rule['b']]['position'])
            error = max(0.,rule['min']-distance,distance-rule['max'])
            relation += error
            preference += abs(distance-rule['target'])
            if error > .003:
                fail('spatial_distance',[rule['a'],rule['b']])
        visibility = self.visibility()
        for name,v in visibility.items():
            if v['isolated_pixels'] < 20 or v['framed_vertices_fraction'] < 1.-1e-6:
                fail('camera_framing',[name])
            if v['fraction'] < self.task['visibility'][name]:
                fail('mesh_visibility',[name])
        visible = float(np.mean([v['fraction'] for v in visibility.values()]))
        framed = float(np.mean([v['framed_vertices_fraction'] for v in visibility.values()]))
        quality = {'penetration_m':collision,'boundary_excess_m':boundary,'support_gap_m':gaps,
                   'relation_error_m':relation,'preference_error_m':preference,
                   'visibility_mean':visible,'framing_mean':framed}
        # A fixed optimizer scalar, not the benchmark's hard success definition.
        loss = 100*(collision+boundary)+30*gaps+10*relation+.3*preference+2*(1-visible)+2*(1-framed)
        navigation, stability = None,None
        if final:
            if collision < 1e-8 and boundary <= .003:
                navigation = self.navigation()
                if navigation['actions'] is None:
                    fail('mesh_passage')
                stability = self.stability()
                for name,v in stability.items():
                    if not v['stable']:
                        fail('physical_stability',[name])
            else:
                fail('physics_navigation_not_admissible')
        return {'success':not failures,'failures':failures,'quality':quality,'optimizer_loss':loss,
                'visibility':visibility,'navigation':navigation,'stability':stability,
                'evaluation_seconds':time.perf_counter()-started}

    def navigation(self):
        c = self.client
        # Half-step inflation guarantees that an accepted node path also clears
        # the continuous .2m-radius cylinder between nodes; broadphase is AABB,
        # narrowphase uses every real convex mesh component.
        shape = p.createCollisionShape(p.GEOM_CYLINDER,radius=.327,height=1.6,physicsClientId=c)
        probe = p.createMultiBody(baseMass=0,baseCollisionShapeIndex=shape,basePosition=[-10,-10,-10],physicsClientId=c)
        step = .25
        start,goal = [tuple(round((v-.25)/step) for v in point) for point in (self.task['entry'],self.task['goal'])]
        cache = {}
        def free(cell):
            if cell not in cache:
                x,y = (v*step+.25 for v in cell)
                ok = .327 <= x <= self.spec.room.width-.327 and .327 <= y <= self.spec.room.depth-.327
                if ok:
                    p.resetBasePositionAndOrientation(probe,[x,y,.8],[0,0,0,1],physicsClientId=c)
                    overlapping = p.getOverlappingObjects([x-.33,y-.33,0],[x+.33,y+.33,1.6],physicsClientId=c) or []
                    ok = not any(any(q[8] < -.002 for q in p.getClosestPoints(probe,b,0,physicsClientId=c))
                                 for b,_ in overlapping if b not in (probe,self.floor))
                cache[cell] = ok
            return cache[cell]
        queue,parents = deque([start]),{start:None}
        found = free(start) and free(goal)
        while queue and found and goal not in parents:
            a = queue.popleft()
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                b = a[0]+dx,a[1]+dy
                if b not in parents and free(b):
                    parents[b] = a
                    queue.append(b)
        count,cell = 0,goal
        if goal not in parents or not found:
            count = None
        else:
            while parents[cell] is not None:
                count += 1
                cell = parents[cell]
        p.removeBody(probe,physicsClientId=c)
        return {'actions':count,'step_m':step,'tested_cells':len(cache),
                'collision_shape':'actual mesh, conservative swept .2m-radius/1.6m-high cylinder'}

    def stability(self):
        c = self.client
        for _ in range(240):
            p.stepSimulation(physicsClientId=c)
        result = {}
        for name,parent in self.task['supports'].items():
            body = self.bodies[name]
            xyz,quat = p.getBasePositionAndOrientation(body,physicsClientId=c)
            initial = self.poses[name]
            initial_quat = p.getQuaternionFromEuler(initial['rotation'])
            expected = np.array(initial['position'])+rotation(initial['rotation'])@self.com[name]
            displacement = float(np.linalg.norm(np.array(xyz)-expected))
            angle = 2*math.acos(min(1.,abs(sum(a*b for a,b in zip(quat,initial_quat)))))
            reference = self.floor if parent == 'floor' else self.bodies[parent]
            contact = any(q[7][2] > .7 and q[8] <= .012 for q in p.getContactPoints(body,reference,physicsClientId=c))
            linear,angular = p.getBaseVelocity(body,physicsClientId=c)
            resting = np.linalg.norm(linear) <= .03 and np.linalg.norm(angular) <= .15
            result[name] = {'translation_m':displacement,'rotation_rad':angle,'support_contact':contact,
                            'linear_speed_m_s':float(np.linalg.norm(linear)), 'angular_speed_rad_s':float(np.linalg.norm(angular)),
                            'stable':bool(displacement <= .04 and angle <= math.radians(8) and contact and resting)}
        return result
