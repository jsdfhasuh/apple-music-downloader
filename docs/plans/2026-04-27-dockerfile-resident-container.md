# Resident Container Dockerfile Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Change the Docker image so it starts as a long-lived container that keeps a fixed IP and can run `apple-music-dl` later via `docker exec`.

**Architecture:** Keep the existing multi-stage Go build and runtime dependencies unchanged. Only change the final image startup behavior from CLI entrypoint execution to a passive long-running `sleep infinity` command so the binary remains available for explicit invocation.

**Tech Stack:** Docker, Go, Ubuntu runtime image, GPAC, ffmpeg

---

### Task 1: Switch runtime startup to resident mode

**Files:**
- Modify: `Dockerfile`

**Step 1: Inspect the current runtime directives**

Confirm the final stage currently ends with:

```dockerfile
ENTRYPOINT ["/usr/local/bin/apple-music-dl"]
```

**Step 2: Define the resident-container behavior**

Replace the CLI entrypoint behavior with a long-running default command:

```dockerfile
CMD ["sleep", "infinity"]
```

This keeps the container alive so Flask can later run:

```bash
docker exec -w /app applemusic_download apple-music-dl "https://music.apple.com/..."
```

**Step 3: Make the minimal Dockerfile change**

Remove the existing `ENTRYPOINT` line and add the new `CMD` line at the end of the file. Do not change build stages, package installation, working directory, or config file handling.

**Step 4: Verify the final Dockerfile content**

Check that the file still:
- builds `/usr/local/bin/apple-music-dl`
- copies `config.yaml` into `/app`
- ends with `CMD ["sleep", "infinity"]`

**Step 5: Validate the image syntax by inspection**

Expected outcome:
- The Dockerfile remains syntactically valid
- Running the image without extra arguments will keep the container alive instead of exiting immediately
