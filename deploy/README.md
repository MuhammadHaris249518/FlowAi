# Deployment (Alternate Clouds)

Alternate Clouds builds the `Dockerfile` at the repository root, so each service tracks its own branch:

| Service | Branch | Root Dockerfile builds | Port |
|---|---|---|---|
| Talkse | `main` | `talkse/` (Python, FastAPI) | 8000 |
| FlowCRM | `flowcrm-deploy` | `flowcrm/` (Next.js + Express) | 3000 |

`flowcrm-deploy` is `main` plus one commit that copies `deploy/flowcrm.Dockerfile` to `Dockerfile`
and `deploy/flowcrm.dockerignore` to `.dockerignore`.

After changing code on `main`, update the FlowCRM branch:

```
git checkout flowcrm-deploy
git merge main
copy deploylowcrm.Dockerfile Dockerfile      # a merge does not refresh the root copy
copy deploylowcrm.dockerignore .dockerignore
git commit -am "flowcrm-deploy: refresh root Dockerfile"   # only if it changed
git push
git checkout main
```

Then Rebuild and Deploy each service.
