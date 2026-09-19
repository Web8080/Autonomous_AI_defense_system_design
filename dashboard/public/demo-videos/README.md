# Demo videos (symlinks)

Author: Victor.I

HTML5 sources for the Simulation investor stage. Symlinks point at
`data/videos/` so we do not duplicate large MP4s in git.

```bash
# From repo root — recreate if missing
mkdir -p dashboard/public/demo-videos
ln -sfn ../../../data/videos/site-railway-0000001.mp4 dashboard/public/demo-videos/railway-corridor.mp4
ln -sfn ../../../data/videos/site-aerial-0000001.mp4 dashboard/public/demo-videos/urban-sprawl.mp4
ln -sfn ../../../data/videos/site-aerial-0000069.mp4 dashboard/public/demo-videos/site-perimeter.mp4
```

Environments are defined in `backend/services/simulation_service/environments.py`.
