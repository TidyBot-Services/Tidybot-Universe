#!/usr/bin/env bash
# Tidybot Universe — one-command setup
#
# Clone all repos, create conda env, install everything.
#
# Usage:
#   bash setup.sh ENV_NAME [WORKSPACE_DIR]
#
# Examples:
#   bash setup.sh tidybot                    # workspace = ./tidybot_uni
#   bash setup.sh tidybot ~/my/workspace     # workspace = ~/my/workspace
#
# Or download and run directly:
#   curl -fsSL https://raw.githubusercontent.com/TidyBot-Services/Tidybot-Universe/master/setup.sh | bash -s -- ENV_NAME [WORKSPACE_DIR]
#
set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
  echo "Error: conda env name required." >&2
  echo "Usage: bash setup.sh ENV_NAME [WORKSPACE_DIR]" >&2
  echo "  ENV_NAME       Name for the new conda environment" >&2
  echo "  WORKSPACE_DIR  Where to clone repos (default: ./tidybot_uni)" >&2
  exit 1
fi

ENV_NAME="$1"
WORKSPACE="${2:-./tidybot_uni}"
WORKSPACE="$(mkdir -p "$WORKSPACE" && cd "$WORKSPACE" && pwd)"

ORG="https://github.com/TidyBot-Services"

echo "==> Workspace: $WORKSPACE"
echo "==> Conda env: $ENV_NAME"
echo ""

# ── Clone repos ───────────────────────────────────────────────────────────────

clone() {
  local repo="$1" dest="$2"
  if [ -d "$dest/.git" ]; then
    echo "    skip $dest (already cloned)"
  else
    echo "    clone $repo → $dest"
    git clone --depth 1 -q "$ORG/$repo.git" "$dest"
  fi
}

echo "==> Cloning repos into $WORKSPACE ..."

clone Tidybot-Universe                     "$WORKSPACE/Tidybot-Universe"
clone TidyBot-Services.github.io           "$WORKSPACE/TidyBot-Services.github.io"
clone agent_server                         "$WORKSPACE/agent_server"
clone system_logger                        "$WORKSPACE/system_logger"
clone common                               "$WORKSPACE/common"
clone maniskill_sim                        "$WORKSPACE/sims/maniskill"
clone maniskill-tidyverse                  "$WORKSPACE/sims/maniskill_tidyverse"
clone maniskill-robocasa-tasks             "$WORKSPACE/sims/robocasa_tasks"

mkdir -p "$WORKSPACE/sims/bridges/maniskill"
clone arm_franka_maniskill_service         "$WORKSPACE/sims/bridges/maniskill/arm_franka"
clone base_tidybot_maniskill_service       "$WORKSPACE/sims/bridges/maniskill/base_tidybot"
clone gripper_robotiq_maniskill_service    "$WORKSPACE/sims/bridges/maniskill/gripper_robotiq"
clone camera_realsense_maniskill_service   "$WORKSPACE/sims/bridges/maniskill/camera_realsense"

# Protocol packages (shared client + message definitions for all backends)
mkdir -p "$WORKSPACE/protocols"
clone franka-protocol                      "$WORKSPACE/protocols/franka_protocol"
clone gripper-protocol                     "$WORKSPACE/protocols/gripper_protocol"
clone camera-protocol                      "$WORKSPACE/protocols/camera_protocol"

# ── Symlinks (from common/) ──────────────────────────────────────────────────

echo "==> Setting up symlinks ..."
[ -f "$WORKSPACE/common/setup.sh" ] && (cd "$WORKSPACE" && bash common/setup.sh)

# ── Conda env ─────────────────────────────────────────────────────────────────

echo "==> Creating conda env '$ENV_NAME' (Python 3.11) ..."
conda create -n "$ENV_NAME" -y python=3.11 -c conda-forge

# ── Pip requirements (inline — no external file needed) ───────────────────────

echo "==> Installing pip packages ..."
conda run -n "$ENV_NAME" pip install -q \
  torch==2.10.0 \
  numpy==1.26.4 \
  \
  sapien==3.0.0b1 \
  fast_kinematics==0.2.2 \
  mplib==0.2.1 \
  pytorch-kinematics==0.7.6 \
  transforms3d==0.4.2 \
  toppra==0.6.3 \
  arm_pytorch_utilities==0.5.0 \
  nvidia-ml-py \
  \
  gymnasium==0.29.1 \
  "h5py>=3.16" \
  "lxml>=6.0" \
  "trimesh>=4.11" \
  "rtree>=1.4" \
  \
  "fastapi>=0.135.0" \
  "uvicorn[standard]>=0.41.0" \
  "pyzmq>=27.0" \
  "msgpack>=1.1" \
  "websockets>=16.0" \
  "httpx>=0.28.0" \
  "httpx-sse>=0.4.0" \
  "pydantic>=2.12" \
  "pydantic-settings>=2.13" \
  "python-multipart>=0.0.22" \
  "python-dotenv>=1.2" \
  "sse-starlette>=3.3" \
  \
  "opencv-python>=4.11" \
  "pillow>=12.1" \
  "imageio>=2.37" \
  "imageio-ffmpeg>=0.6" \
  \
  "scipy>=1.17" \
  "matplotlib>=3.10" \
  "requests>=2.32" \
  "pyyaml>=6.0" \
  "tqdm>=4.67" \
  "psutil>=7.2" \
  "tabulate>=0.10" \
  "rich>=14.3" \
  "tyro>=1.0" \
  "typer>=0.24" \
  "click>=8.3" \
  "GitPython>=3.1" \
  "huggingface_hub>=1.7" \
  "jsonschema>=4.26" \
  "sympy>=1.14" \
  "networkx>=3.6" \
  "cloudpickle>=3.1" \
  "dacite>=1.9" \
  \
  "PyJWT>=2.12" \
  "cryptography>=46.0" \
  "ipython>=9.10" \
  "setuptools<81" \
  claude-agent-sdk

# mani_skill pins mplib==0.1.1 but we need 0.2.1
echo "==> Installing mani_skill (--no-deps) ..."
conda run -n "$ENV_NAME" pip install -q --no-deps mani_skill==3.0.0b22

# ── Editable local packages ──────────────────────────────────────────────────

echo "==> Installing local packages (editable) ..."
conda run -n "$ENV_NAME" pip install -q --no-deps \
  -e "$WORKSPACE/protocols/franka_protocol" \
  -e "$WORKSPACE/protocols/gripper_protocol" \
  -e "$WORKSPACE/protocols/camera_protocol" \
  -e "$WORKSPACE/sims/maniskill" \
  -e "$WORKSPACE/sims/maniskill_tidyverse" \
  -e "$WORKSPACE/sims/robocasa_tasks" \
  -e "$WORKSPACE/sims/bridges/maniskill/arm_franka" \
  -e "$WORKSPACE/sims/bridges/maniskill/base_tidybot" \
  -e "$WORKSPACE/sims/bridges/maniskill/gripper_robotiq" \
  -e "$WORKSPACE/sims/bridges/maniskill/camera_realsense" \
  -e "$WORKSPACE/system_logger"

# ── URDF mesh symlinks (needs mani_skill installed) ──────────────────────────

echo "==> Setting up URDF mesh symlinks ..."
MANI_SKILL_ASSETS="$(conda run -n "$ENV_NAME" python3 -c "import mani_skill; print(mani_skill.__path__[0])" 2>/dev/null)/assets/robots/panda"
if [ -d "$MANI_SKILL_ASSETS" ]; then
  ln -sfn "$MANI_SKILL_ASSETS/franka_description"     "$WORKSPACE/sims/maniskill_tidyverse/franka_description"
  ln -sfn "$MANI_SKILL_ASSETS/realsense2_description"  "$WORKSPACE/sims/maniskill_tidyverse/realsense2_description"
fi
if [ -d "$HOME/.maniskill/data/robots/robotiq_2f/meshes" ]; then
  ln -sfn "$HOME/.maniskill/data/robots/robotiq_2f/meshes" "$WORKSPACE/sims/maniskill_tidyverse/robotiq_meshes"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "Next step — open a NEW terminal and run:"
echo ""
echo "  cd $WORKSPACE/Tidybot-Universe/skill-agent-setup/claude-code"
echo "  claude --dangerously-skip-permissions"
echo ""
echo "The --dangerously-skip-permissions flag lets the agent"
echo "launch services, run sim code, and execute skills without"
echo "prompting you for every command. Recommended for this workflow."
echo ""
echo "Then type /xbot-plan to start planning skills."
echo "The agent will launch the sim and servers automatically."
echo ""
echo "If you ran this inside Claude Code, exit it first (Ctrl+C)."
echo "============================================"
