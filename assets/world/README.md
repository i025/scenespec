# Fixed workshop assets

Project-authored meshes: a blue wheeled Agent, a timber shipping crate, and a steel storage cabinet (static obstacle). Built once by `scripts/build_world_assets.py`; runtime export loads the saved `library.blend`, not scene-supplied code. No external asset downloads or textures.

Each asset is a detailed joined mesh with Principled materials, normalized to XY `[-0.5, 0.5]`, Z `[0, 1]`, with its front in +Y. The exporter scales it to the existing SceneSpec collision envelope. These are visual assets only; PyBullet and SceneVerifier continue to use the original geometry. Door handles, wheels and trim stay inside that envelope. The closed cabinet is visually solid: shelves and open doors do not introduce new navigable space.

The floor, low cutaway wall, goal paint, lights and three cameras are presentation geometry created by the exporter. The wall is cut down for visibility; room bounds and collision rules remain unchanged.

The optional continuous Mesh benchmark additionally exports these same authored
closed components for collision, contact, gravity and segmentation evaluation.
That separate task contract does not change the proxy geometry used by historical
SceneVerifier/PyBullet tasks. See `experiments/continuous_mesh/PROTOCOL.md`.
